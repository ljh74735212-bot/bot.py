"""
SMS 웹훅 서버 - 멀티서버 지원
입금 문자 → 자동 충전
"""
import re
import os
import asyncio
from aiohttp import web
import database as db

WEBHOOK_SECRET = os.getenv("SMS_WEBHOOK_SECRET", "")  # 비어 있으면 모든 요청을 거부 (보안)
SMS_PORT = int(os.getenv("SMS_PORT", 3000))

BANK_PATTERNS = [
    {"bank": "국민은행",   "amount": r"입금\s*([\d,]+)원",               "name": r"([가-힣a-zA-Z0-9_]+?)님?\s*입금"},
    {"bank": "신한은행",   "amount": r"입금\s*[가-힣]+\s*([\d,]+)원",    "name": r"입금\s*([가-힣a-zA-Z0-9_]+)\s*[\d,]+원"},
    {"bank": "카카오뱅크", "amount": r"\+([\d,]+)원",                    "name": r"([가-힣a-zA-Z0-9_]+)\s*$"},
    {"bank": "토스뱅크",   "amount": r"([\d,]+)원\s*입금",               "name": r"토스뱅크\s*([가-힣a-zA-Z0-9_]+)"},
    {"bank": "우리은행",   "amount": r"입금\s*([\d,]+)원",               "name": r"([가-힣a-zA-Z0-9_]+)\s*입금"},
    {"bank": "기타",       "amount": r"(?:입금|이체).{0,10}?([\d,]+)원", "name": r"([가-힣]{2,5})님?"},
]

def parse_sms(message: str):
    if not any(k in message for k in ["입금", "이체", "받기"]):
        return None
    for p in BANK_PATTERNS:
        m = re.search(p["amount"], message)
        if not m:
            continue
        try:
            amount = int(m.group(1).replace(",", ""))
        except:
            continue
        if amount < 100:
            continue
        depositor = "알 수 없음"
        mn = re.search(p["name"], message)
        if mn:
            depositor = mn.group(1).strip()
        return {"amount": amount, "depositor_name": depositor, "bank": p["bank"]}
    return None

def find_charge_in_guild(guild_id: str, depositor: str, amount: int):
    """입금액이 정확히 같은 요청만 후보로 인정한다 (이름만 같고 금액이 다르면 절대 승인 X)."""
    candidates = [c for c in db.get_pending_charges(guild_id) if c["amount"] == amount]
    if not candidates:
        return None
    same_name = [c for c in candidates if c["depositor_name"] == depositor]
    if same_name:
        return same_name[0]          # 가장 오래된 요청부터
    if len(candidates) == 1:
        return candidates[0]         # 문자에서 이름을 못 읽은 경우: 같은 금액 요청이 딱 하나일 때만
    return None

async def handle_sms(bot, message: str) -> dict:
    """입금 문자 1건 처리 (aiohttp 서버와 대시보드의 /sms 둘 다 이걸 사용)"""
    parsed = parse_sms(message)
    if not parsed:
        return {"status": "ignored"}

    print(f"💰 입금 감지: {parsed['depositor_name']} / {parsed['amount']}원")

    # 모든 활성 서버에서 매칭
    guilds = db.get_all_guilds()
    matched = False
    for g in guilds:
        if not db.is_guild_active(g["id"]):
            continue
        if not db.get_settings(g["id"]).get("auto_charge", True):
            continue  # 웹사이트 설정에서 자동충전을 끈 서버 → 관리자 버튼으로 수동 승인
        charge = find_charge_in_guild(g["id"], parsed["depositor_name"], parsed["amount"])
        if not charge:
            continue
        result = db.approve_charge(g["id"], charge["id"])
        if result:
            matched = True
            await send_dm(bot, charge["user_id"], charge["amount"], parsed["bank"], result)
            await notify_admin_guild(bot, g["id"], charge, parsed, result)
            print(f"✅ 자동충전: 서버 {g['id']} / 유저 {charge['user_id']} / {charge['amount']}원")

    if not matched:
        print(f"⚠️ 매칭 실패: {parsed['depositor_name']} {parsed['amount']}원")

    return {"status": "success" if matched else "no_match"}

async def sms_webhook(request: web.Request, bot):
    if not WEBHOOK_SECRET:
        return web.json_response({"error": "SMS_WEBHOOK_SECRET not configured"}, status=503)
    if request.headers.get("x-webhook-secret", "") != WEBHOOK_SECRET:
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        data = await request.post()
    except Exception:
        data = {}

    message = data.get("message") or data.get("text") or data.get("sms") or ""
    if not message:
        return web.json_response({"error": "No message"}, status=400)

    return web.json_response(await handle_sms(bot, message))

async def health(_):
    return web.json_response({"status": "ok"})

async def send_dm(bot, user_id: str, amount: int, bank: str, result: dict = None):
    try:
        user = await bot.fetch_user(int(user_id))
        await user.send(
            f"✅ **자동 충전 완료!**\n"
            f"💰 금액: **{amount:,}원**\n"
            f"🏦 은행: {bank}\n"
            f"입금이 확인되어 자동으로 충전되었습니다."
            + (f"\n🎉 이벤트 **{result['event_name']}** 보너스 **+{result['bonus']:,}원** 지급!" if result and result.get("bonus") else "")
        )
    except Exception as e:
        print(f"DM 실패: {e}")

async def notify_admin_guild(bot, guild_id: str, charge, parsed, result: dict = None):
    settings = db.get_settings(guild_id)
    ch_id = settings.get("admin_channel_id")
    if not ch_id:
        return
    try:
        ch = bot.get_channel(int(ch_id)) or await bot.fetch_channel(int(ch_id))
        await ch.send(
            f"📱 **SMS 자동충전 완료**\n"
            f"유저: <@{charge['user_id']}>\n"
            f"금액: **{parsed['amount']:,}원** ({parsed['bank']})\n"
            f"입금자: {parsed['depositor_name']}"
            + (f"\n🎉 이벤트 보너스 +{result['bonus']:,}원 ({result['event_name']})" if result and result.get("bonus") else "")
        )
    except Exception as e:
        print(f"관리자 알림 실패: {e}")

async def start_sms_server(bot):
    app = web.Application()
    app.router.add_post("/sms", lambda r: sms_webhook(r, bot))
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", SMS_PORT)
    await site.start()
    print(f"📡 SMS 서버 실행: http://0.0.0.0:{SMS_PORT}")
