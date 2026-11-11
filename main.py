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
        synced = await bot.tree.sync(guild=discord.Object(id=int(os.getenv("GUILD_ID"))))
        print(f"✅ 슬래시 커맨드 {len(synced)}개 등록 완료")
    except Exception as e:
        print(f"❌ 커맨드 등록 실패: {e}")
    asyncio.create_task(start_sms_server(bot))
    asyncio.create_task(start_dashboard_async())

async def main():
    async with bot:
        await bot.load_extension("cogs.vending")
        await bot.start(os.getenv("BOT_TOKEN"))

if __name__ == "__main__":
    asyncio.run(main())
