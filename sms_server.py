"""
SMS 웹훅 서버 (aiohttp 기반)
안드로이드 SMS Forwarder 앱 → 이 서버 → 자동 충전
"""

import re
import os
import asyncio
from aiohttp import web
import database as db

WEBHOOK_SECRET = os.getenv("SMS_WEBHOOK_SECRET", "secret123")
SMS_PORT = int(os.getenv("SMS_PORT", 3000))

# ── 은행별 파싱 패턴 ──────────────────────────────
BANK_PATTERNS = [
    # 국민은행
    {"bank": "국민은행",   "amount": r"입금\s*([\d,]+)원",           "name": r"([가-힣a-zA-Z0-9_]+?)님?\s*입금"},
    # 신한은행
    {"bank": "신한은행",   "amount": r"입금\s*[가-힣]+\s*([\d,]+)원", "name": r"입금\s*([가-힣a-zA-Z0-9_]+)\s*[\d,]+원"},
    # 카카오뱅크
    {"bank": "카카오뱅크", "amount": r"\+([\d,]+)원",                "name": r"([가-힣a-zA-Z0-9_]+)\s*$"},
    # 토스뱅크
    {"bank": "토스뱅크",   "amount": r"([\d,]+)원\s*입금",           "name": r"토스뱅크\s*([가-힣a-zA-Z0-9_]+)"},
    # 우리은행
    {"bank": "우리은행",   "amount": r"입금\s*([\d,]+)원",           "name": r"([가-힣a-zA-Z0-9_]+)\s*입금"},
    # 범용
    {"bank": "기타",       "amount": r"(?:입금|이체).{0,10}?([\d,]+)원", "name": r"([가-힣]{2,5})님?"},
]

def parse_sms(message: str) -> dict | None:
    if not any(k in message for k in ["입금", "이체", "받기"]):
        return None

    for p in BANK_PATTERNS:
        m_amount = re.search(p["amount"], message)
        if not m_amount:
            continue

        raw = m_amount.group(1).replace(",", "")
        try:
            amount = int(raw)
        except ValueError:
            continue
        if amount < 100:
            continue

        depositor = "알 수 없음"
        m_name = re.search(p["name"], message)
        if m_name:
            depositor = m_name.group(1).strip()

        return {"amount": amount, "depositor_name": depositor, "bank": p["bank"]}

    return None

def find_pending_charge(depositor_name: str, amount: int) -> dict | None:
    pending = db.get_pending_charges()

    # 1순위: 이름 + 금액
    for c in pending:
        if c["depositor_name"] == depositor_name and c["amount"] == amount:
            return c

    # 2순위: 이름만
    for c in pending:
        if c["depositor_name"] == depositor_name:
            return c

    # 3순위: 금액만 (1개일 때)
    by_amount = [c for c in pending if c["amount"] == amount]
    if len(by_amount) == 1:
        return by_amount[0]

    return None

# ── 웹훅 핸들러 ───────────────────────────────────
async def sms_webhook(request: web.Request, bot):
    # 시크릿 검증
    secret = request.headers.get("x-webhook-secret") or (await request.post()).get("secret", "")
    if secret != WEBHOOK_SECRET:
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        data = await request.post()
    except Exception:
        data = {}

    message = data.get("message") or data.get("text") or data.get("sms") or ""
    sender  = data.get("from") or data.get("sender") or ""

    print(f"📱 SMS 수신 [{sender}]: {message}")

    if not message:
        return web.json_response({"error": "No message"}, status=400)

    parsed = parse_sms(message)
    if not parsed:
        print("💬 입금 문자 아님 - 무시")
        return web.json_response({"status": "ignored"})

    print(f"💰 입금 감지: {parsed['depositor_name']} / {parsed['amount']}원 / {parsed['bank']}")

    charge = find_pending_charge(parsed["depositor_name"], parsed["amount"])

    if not charge:
        print(f"⚠️ 매칭 실패: {parsed['depositor_name']} {parsed['amount']}원")
        await notify_admin(bot, None, parsed, "매칭 실패 - 수동 처리 필요")
        return web.json_response({"status": "no_match", "parsed": parsed})

    result = db.approve_charge(charge["id"])
    if not result:
        return web.json_response({"status": "error"}, status=500)

    print(f"✅ 자동 충전 완료: {charge['user_id']} / {charge['amount']}원")

    await send_dm(bot, charge["user_id"], charge["amount"], parsed["bank"])
    await notify_admin(bot, charge, parsed, "✅ 자동 충전 완료")

    return web.json_response({"status": "success", "charge_id": charge["id"]})

async def health(_request):
    return web.json_response({"status": "ok"})

# ── 디스코드 알림 ─────────────────────────────────
async def send_dm(bot, user_id: str, amount: int, bank: str):
    try:
        user = await bot.fetch_user(int(user_id))
        await user.send(
            f"✅ **자동 충전 완료!**\n"
            f"💰 금액: **{amount:,}원**\n"
            f"🏦 은행: {bank}\n"
            f"입금이 확인되어 자동으로 충전되었습니다."
        )
    except Exception as e:
        print(f"DM 발송 실패: {e}")

async def notify_admin(bot, charge, parsed: dict, status: str):
    ch_id = os.getenv("ADMIN_CHANNEL_ID")
    if not ch_id:
        return
    try:
        channel = bot.get_channel(int(ch_id)) or await bot.fetch_channel(int(ch_id))
        if charge:
            await channel.send(
                f"📱 **SMS 자동 충전**\n"
                f"상태: {status}\n"
                f"유저: <@{charge['user_id']}>\n"
                f"금액: **{parsed['amount']:,}원** ({parsed['bank']})\n"
                f"입금자: {parsed['depositor_name']}"
            )
        else:
            await channel.send(
                f"⚠️ **입금 감지 - 매칭 실패**\n"
                f"금액: **{parsed['amount']:,}원** ({parsed['bank']})\n"
                f"입금자: {parsed['depositor_name']}\n"
                f"→ 수동으로 `/충전승인` 처리 필요"
            )
    except Exception as e:
        print(f"관리자 알림 실패: {e}")

# ── 서버 시작 ─────────────────────────────────────
async def start_sms_server(bot):
    app = web.Application()
    app.router.add_post("/sms", lambda r: sms_webhook(r, bot))
    app.router.add_get("/health", health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", SMS_PORT)
    await site.start()
    print(f"📡 SMS 웹훅 서버 실행 중: http://0.0.0.0:{SMS_PORT}")
