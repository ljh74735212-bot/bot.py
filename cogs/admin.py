"""
봇 판매자 전용 관리 명령어
봇 소유자만 사용 가능
"""
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timedelta
import database as db
import os

OWNER_ID = int(os.getenv("OWNER_ID", 0))

def is_owner(interaction: discord.Interaction) -> bool:
    return interaction.user.id == OWNER_ID

class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # 서버 등록 (봇 판매 시 사용)
    @app_commands.command(name="서버등록", description="[봇주인] 서버를 자판기 봇에 등록합니다")
    @app_commands.describe(
        서버id="등록할 서버 ID",
        기간="이용 기간 (일수, 0=영구)",
        관리자역할id="관리자 역할 ID (선택)"
    )
    async def register_guild(self, interaction: discord.Interaction, 서버id: str, 기간: int = 30, 관리자역할id: str = None):
        if not is_owner(interaction):
            return await interaction.response.send_message("❌ 봇 소유자만 사용 가능합니다.", ephemeral=True)

        expires = "permanent" if 기간 == 0 else (datetime.now() + timedelta(days=기간)).isoformat()

        # 서버 이름 가져오기
        guild = self.bot.get_guild(int(서버id))
        guild_name = guild.name if guild else f"서버_{서버id}"

        db.register_guild(서버id, guild_name, expires, 관리자역할id)

        embed = discord.Embed(title="✅ 서버 등록 완료", color=0x57F287)
        embed.add_field(name="서버", value=f"{guild_name} (`{서버id}`)", inline=False)
        embed.add_field(name="만료일", value="영구" if 기간 == 0 else f"{기간}일 후", inline=True)
        embed.add_field(name="상태", value="✅ 활성", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

        # 해당 서버에 알림
        if guild:
            for ch in guild.text_channels:
                if ch.permissions_for(guild.me).send_messages:
                    try:
                        await ch.send(
                            f"✅ **자판기 봇이 활성화되었습니다!**\n"
                            f"이용 기간: {'영구' if 기간 == 0 else f'{기간}일'}\n"
                            f"`/자판기` 명령어로 시작하세요!"
                        )
                        break
                    except:
                        pass

    # 서버 비활성화
    @app_commands.command(name="서버비활성화", description="[봇주인] 서버를 비활성화합니다")
    @app_commands.describe(서버id="비활성화할 서버 ID")
    async def deactivate(self, interaction: discord.Interaction, 서버id: str):
        if not is_owner(interaction):
            return await interaction.response.send_message("❌ 봇 소유자만 사용 가능합니다.", ephemeral=True)
        db.deactivate_guild(서버id)
        await interaction.response.send_message(f"🔴 서버 `{서버id}` 비활성화 완료", ephemeral=True)

    # 서버 목록
    @app_commands.command(name="서버목록", description="[봇주인] 등록된 서버 목록")
    async def guild_list(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            return await interaction.response.send_message("❌ 봇 소유자만 사용 가능합니다.", ephemeral=True)

        guilds = db.get_all_guilds()
        embed = discord.Embed(title=f"📋 등록 서버 목록 ({len(guilds)}개)", color=0x5865F2)

        if not guilds:
            embed.description = "등록된 서버가 없습니다."
        else:
            for g in guilds[:20]:
                active = db.is_guild_active(g["id"])
                expires = g.get("expires_at", "알 수 없음")
                if expires == "permanent":
                    expires_str = "영구"
                else:
                    try:
                        d = datetime.fromisoformat(expires)
                        days_left = (d - datetime.now()).days
                        expires_str = f"{days_left}일 남음"
                    except:
                        expires_str = expires[:10]

                embed.add_field(
                    name=f"{'✅' if active else '🔴'} {g.get('guild_name', g['id'])}",
                    value=f"ID: `{g['id']}` | 만료: {expires_str}",
                    inline=False,
                )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # 서버 연장
    @app_commands.command(name="서버연장", description="[봇주인] 서버 이용 기간 연장")
    @app_commands.describe(서버id="서버 ID", 추가일수="추가할 일수")
    async def extend_guild(self, interaction: discord.Interaction, 서버id: str, 추가일수: int):
        if not is_owner(interaction):
            return await interaction.response.send_message("❌ 봇 소유자만 사용 가능합니다.", ephemeral=True)

        info = db.get_guild_info(서버id)
        if not info:
            return await interaction.response.send_message("❌ 등록되지 않은 서버입니다.", ephemeral=True)

        current = info.get("expires_at", "permanent")
        if current == "permanent":
            new_expires = "permanent"
        else:
            try:
                base = datetime.fromisoformat(current)
                if base < datetime.now():
                    base = datetime.now()
                new_expires = (base + timedelta(days=추가일수)).isoformat()
            except:
                new_expires = (datetime.now() + timedelta(days=추가일수)).isoformat()

        db.register_guild(서버id, info.get("guild_name", 서버id), new_expires, info.get("admin_role_id"))
        await interaction.response.send_message(f"✅ 서버 `{서버id}` {추가일수}일 연장 완료!", ephemeral=True)

    # 전체 통계
    @app_commands.command(name="전체통계", description="[봇주인] 전체 서버 통계")
    async def total_stats(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            return await interaction.response.send_message("❌ 봇 소유자만 사용 가능합니다.", ephemeral=True)

        guilds = db.get_all_guilds()
        total_revenue = 0
        total_users = 0
        total_purchases = 0

        for g in guilds:
            gdb = db.load_db(g["id"])
            for u in gdb.get("users", {}).values():
                total_revenue += u.get("total_purchase", 0)
                total_users += 1
                total_purchases += len(u.get("purchases", []))

        embed = discord.Embed(title="📊 전체 통계", color=0x5865F2)
        embed.add_field(name="등록 서버", value=f"{len(guilds)}개", inline=True)
        embed.add_field(name="활성 서버", value=f"{sum(1 for g in guilds if db.is_guild_active(g['id']))}개", inline=True)
        embed.add_field(name="총 유저", value=f"{total_users}명", inline=True)
        embed.add_field(name="총 거래액", value=f"{total_revenue:,}원", inline=True)
        embed.add_field(name="총 구매 건수", value=f"{total_purchases}건", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot):
    await bot.add_cog(Admin(bot))
