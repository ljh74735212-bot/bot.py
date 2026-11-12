"""
계좌조회API(bankapi.co.kr) 기반 무통장 자동충전
- 웹 Settings 에서 API Key / Secret Key / 계좌 정보를 넣으면 봇이 주기적으로 입금내역을 조회해서
  대기 중인 충전 요청(금액 + 입금자명)과 맞으면 자동 충전한다.
- 제약(서비스 쪽): 국내 IP에서만 호출 가능 / 같은 계좌 5분에 1회 / 농협·국민·우리·기업 (빠른조회 등록 필요)
"""
import asyncio
import time
from datetime import datetime, timedelta, timezone

import aiohttp

import database as db
import sms_server

API_URL = "https://api.bankapi.co.kr/v1/transactions"
KST = timezone(timedelta(hours=9))
BANKS = {"NH": "농협은행", "KB": "KB국민은행", "WR": "우리은행", "IBK": "기업은행"}
MIN_INTERVAL_MIN = 5          # 서비스의 '같은 계좌 5분 1회' 제한
MAX_LOOKBACK_DAYS = 3
_last_poll: dict = {}         # guild_id -> time.monotonic()


def get_cfg(gid: str) -> dict:
    return db.get_settings(gid).get("bankapi", {})


def is_configured(cfg: dict) -> bool:
    return all(cfg.get(k) for k in ("api_key", "secret_key", "bank_code", "account_number",
                                    "account_password", "resident_number"))


def _save_status(gid: str, **fields):
    """설정을 덮어쓰지 않고 상태 값만 갱신"""
    s = db.get_settings(gid)
    s.setdefault("bankapi", {}).update(fields)
    db.save_settings(gid, s)


def _charge_created_kst(iso: str) -> datetime:
    """충전 요청 시각(서버 로컬 시간)을 한국 시간으로 변환"""
    dt = datetime.fromisoformat(iso)
    off = datetime.now().astimezone().utcoffset() or timedelta(0)
    return (dt - off).replace(tzinfo=timezone.utc).astimezone(KST)


def _tx_dt(tx: dict) -> datetime:
    d = str(tx.get("date", "")).replace("/", "-")
    t = str(tx.get("time", "")) or "23:59:59"
    try:
        return datetime.fromisoformat(f"{d}T{t}").replace(tzinfo=KST)
    except ValueError:
        try:
            return datetime.fromisoformat(d).replace(hour=23, minute=59, second=59, tzinfo=KST)
        except ValueError:
            return datetime.now(KST)


def _tx_key(tx: dict) -> str:
    return "|".join(str(tx.get(k, "")) for k in ("date", "time", "amount", "counterparty", "balance"))


def _name_ok(tx_name: str, depositor: str) -> bool:
    a, b = tx_name.replace(" ", ""), depositor.replace(" ", "")
    return a == b or (len(a) >= 2 and len(b) >= 2 and (a in b or b in a))


async def fetch_transactions(cfg: dict, start: str, end: str):
    """반환: (성공여부, 거래내역 리스트 | 오류 메시지)"""
    body = {
        "bankCode": cfg["bank_code"], "accountNumber": cfg["account_number"],
        "accountPassword": cfg["account_password"], "residentNumber": cfg["resident_number"],
        "accountType": "personal", "startDate": start, "endDate": end,
    }
    headers = {"Authorization": f"Bearer {cfg['api_key']}:{cfg['secret_key']}", "Content-Type": "application/json"}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90)) as s:
            async with s.post(API_URL, json=body, headers=headers) as r:
                try:
                    data = await r.json(content_type=None)
                except Exception:
                    data = {}
                if r.status == 403:
                    return False, "403 차단: 계좌조회API는 국내(한국) IP에서만 호출할 수 있어요. 봇 서버가 해외(Render 등)면 막힙니다."
                if not isinstance(data, dict) or not data.get("success"):
                    msg = (data or {}).get("message") or (data or {}).get("error") or f"HTTP {r.status}"
                    return False, str(msg)[:200]
                return True, data.get("transactions") or []
    except asyncio.TimeoutError:
        return False, "응답 시간 초과"
    except Exception as e:
        return False, f"연결 실패: {e}"[:200]


async def check_guild(bot, gid: str, force: bool = False) -> str:
    """한 서버의 입금내역을 조회해 대기 중 충전 요청과 매칭. 결과 요약 문자열 반환."""
    cfg = get_cfg(gid)
    if not is_configured(cfg):
        return "설정이 완료되지 않았어요."
    pending = db.get_pending_charges(gid)
    if not pending and not force:
        return "대기 중인 충전 요청 없음"

    now = datetime.now(KST)
    oldest = min((_charge_created_kst(c["created_at"]) for c in pending), default=now)
    start_dt = max(oldest, now - timedelta(days=MAX_LOOKBACK_DAYS))
    ok, result = await fetch_transactions(cfg, start_dt.strftime("%Y%m%d"), now.strftime("%Y%m%d"))
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    if not ok:
        _save_status(gid, last_poll=stamp, last_status=f"❌ {result}")
        print(f"⚠️ BankAPI 조회 실패 (서버 {gid}): {result}")
        return f"조회 실패: {result}"

    used = list(get_cfg(gid).get("used", []))
    credited = 0
    deposits = sorted((t for t in result if t.get("type") == "deposit"), key=_tx_dt)
    for tx in deposits:
        key = _tx_key(tx)
        if key in used:
            continue
        try:
            amount = int(tx.get("amount", 0))
        except (TypeError, ValueError):
            continue
        tdt = _tx_dt(tx)
        # 입금액이 같고, 입금 시각이 충전 요청 이후인 요청만 후보 (이전 입금으로 승인되는 것 방지)
        cands = [c for c in db.get_pending_charges(gid)
                 if c["amount"] == amount and _charge_created_kst(c["created_at"]) <= tdt + timedelta(minutes=1)]
        if not cands:
            continue
        tx_name = str(tx.get("counterparty") or "").strip()
        if tx_name:
            cands = [c for c in cands if _name_ok(tx_name, c["depositor_name"])]
            if not cands:
                continue          # 이름이 다르면 자동 승인 X → 관리자가 수동 승인
        elif len(cands) != 1:
            continue              # 이름을 못 읽었는데 같은 금액 요청이 여러 개 → 수동 승인
        charge = cands[0]
        res = db.approve_charge(gid, charge["id"])
        if not res:
            continue
        used.append(key)
        credited += 1
        parsed = {"amount": amount, "depositor_name": tx_name or charge["depositor_name"], "bank": BANKS.get(cfg["bank_code"], "무통장")}
        await sms_server.send_dm(bot, charge["user_id"], amount, parsed["bank"], res)
        await sms_server.notify_admin_guild(bot, gid, charge, parsed, res)
        db.add_sms_log({"message": f"[BankAPI] {tx.get('date')} {tx.get('time')} {tx_name} {amount:,}원",
                        "status": "matched", "scope": gid, "amount": amount, "depositor": parsed["depositor_name"],
                        "bank": parsed["bank"],
                        "matched": [{"guild_id": gid, "charge_id": charge["id"], "user_id": charge["user_id"]}]})
        print(f"✅ BankAPI 자동충전: 서버 {gid} / 유저 {charge['user_id']} / {amount}원")

    _save_status(gid, last_poll=stamp, used=used[-500:],
                 last_status=f"✅ 정상 (입금 {len(deposits)}건 확인, 자동충전 {credited}건)")
    return f"입금 {len(deposits)}건 확인, 자동충전 {credited}건"


async def poll_loop(bot):
    """주기적으로 모든 서버 확인 (대기 중인 충전 요청이 있을 때만 조회 → 조회 한도 절약)"""
    await bot.wait_until_ready()
    print("🏦 BankAPI 자동충전 감시 시작")
    while True:
        try:
            for g in db.get_all_guilds():
                gid = g["id"]
                if not db.is_guild_active(gid):
                    continue
                s = db.get_settings(gid)
                cfg = s.get("bankapi", {})
                if not (cfg.get("enabled") and is_configured(cfg) and s.get("auto_charge", True)):
                    continue
                try:
                    interval = max(MIN_INTERVAL_MIN, int(cfg.get("interval_min", 6))) * 60
                except (TypeError, ValueError):
                    interval = 6 * 60
                if time.monotonic() - _last_poll.get(gid, -1e9) < interval:
                    continue
                if not db.get_pending_charges(gid):
                    continue
                _last_poll[gid] = time.monotonic()
                await check_guild(bot, gid)
        except Exception as e:
            print(f"BankAPI 루프 오류: {e}")
        await asyncio.sleep(30)
