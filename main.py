import discord
from discord.ext import commands
import asyncio
import os
from dotenv import load_dotenv
from sms_server import start_sms_server
from dashboard.server import start_dashboard_async

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ 봇 로그인 완료: {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"✅ 슬래시 커맨드 {len(synced)}개 등록 완료")
    except Exception as e:
        print(f"❌ 커맨드 등록 실패: {e}")
    asyncio.create_task(start_dashboard_async(bot))
    if not os.getenv("PORT"):
        # $PORT가 없을 때(로컬/포트가 여러 개 열리는 호스팅)만 별도 SMS 서버 실행
        # Render에서는 대시보드의 /sms 로 같은 포트에서 받음
        asyncio.create_task(start_sms_server(bot))

async def main():
    async with bot:
        await bot.load_extension("cogs.vending")
        await bot.load_extension("cogs.admin")
        await bot.start(os.getenv("BOT_TOKEN"))

if __name__ == "__main__":
    asyncio.run(main())
