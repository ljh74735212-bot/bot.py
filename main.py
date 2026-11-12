import discord
from discord.ext import commands
import asyncio
import os
from dotenv import load_dotenv
from sms_server import start_sms_server
from dashboard.server import start_dashboard_async
import bankapi

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

_bg_started = False

@bot.event
async def on_ready():
    global _bg_started
    print(f"✅ 봇 로그인 완료: {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"✅ 슬래시 커맨드 {len(synced)}개 등록 완료")
    except Exception as e:
        print(f"❌ 커맨드 등록 실패: {e}")
    if _bg_started:
        return  # 재연결 시 on_ready 가 다시 불려도 백그라운드 작업은 한 번만 시작
    _bg_started = True
    asyncio.create_task(start_dashboard_async())
    asyncio.create_task(bankapi.poll_loop(bot))  # 웹에서 설정한 계좌조회API 자동충전
    asyncio.create_task(start_sms_server(bot))

async def main():
    async with bot:
        await bot.load_extension("cogs.vending")
        await bot.load_extension("cogs.admin")
        await bot.start(os.getenv("BOT_TOKEN"))

if __name__ == "__main__":
    asyncio.run(main())
