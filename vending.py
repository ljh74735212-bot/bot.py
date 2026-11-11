import discord
from discord import app_commands
from discord.ext import commands
import os
import database as db

GUILD_ID = int(os.getenv("GUILD_ID", 0))
ADMIN_ROLE_ID = int(os.getenv("ADMIN_ROLE_ID", 0)) if os.getenv("ADMIN_ROLE_ID") else None

def is_admin(interaction: discord.Interaction) -> bool:
    if interaction.user.guild_permissions.administrator:
        return True
    if ADMIN_ROLE_ID:
        return any(r.id == ADMIN_ROLE_ID for r in interaction.user.roles)
    return False

# ── 메인 패널 뷰 ──────────────────────────────────
class MainView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="제품", emoji="📦", style=discord.ButtonStyle.success, custom_id="btn_products")
    async def products(self, interaction: discord.Interaction, _button):
        cats = db.get_categories()
        await interaction.response.send_message(
            embed=category_embed("🛒 제품 조회"),
            view=CategoryView(cats, mode="view"),
            ephemeral=True,
        )

    @discord.ui.button(label="구매", emoji="🛒", style=discord.ButtonStyle.primary, custom_id="btn_buy")
    async def buy(self, interaction: discord.Interaction, _button):
        cats = db.get_categories()
        await interaction.response.send_message(
            embed=category_embed("🛍️ 제품 구매"),
            view=CategoryView(cats, mode="buy"),
            ephemeral=True,
        )

    @discord.ui.button(label="충전", emoji="💰", style=discord.ButtonStyle.danger, custom_id="btn_charge")
    async def charge(self, interaction: discord.Interaction, _button):
        embed = discord.Embed(title="💰 충전", description="충전 수단을 선택하세요", color=0xFEE75C)
        await interaction.response.send_message(embed=embed, view=ChargeView(), ephemeral=True)

    @discord.ui.button(label="정보", emoji="💎", style=discord.ButtonStyle.secondary, custom_id="btn_info")
    async def info(self, interaction: discord.Interaction, _button):
        user = db.get_user(str(interaction.user.id), interaction.user.display_name)
        embed = user_info_embed(user)
        await interaction.response.send_message(embed=embed, view=PurchaseHistoryView(user), ephemeral=True)

# ── 카테고리 선택 ─────────────────────────────────
class CategoryView(discord.ui.View):
    def __init__(self, categories: list, mode: str):
        super().__init__(timeout=60)
        self.add_item(CategorySelect(categories, mode))

class CategorySelect(discord.ui.Select):
    def __init__(self, categories: list, mode: str):
        self.mode = mode
        options = (
            [discord.SelectOption(label=c, value=c) for c in categories]
            if categories else
            [discord.SelectOption(label="상품 없음", value="none")]
        )
        super().__init__(placeholder="카테고리 선택...", options=options)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            return await interaction.response.send_message("상품이 없습니다.", ephemeral=True)
        products = db.get_products_by_category(self.values[0])
        await interaction.response.send_message(
            embed=discord.Embed(title="상품 선택", color=0x5865F2),
            view=ProductView(products, self.mode),
            ephemeral=True,
        )

# ── 상품 선택 ─────────────────────────────────────
class ProductView(discord.ui.View):
    def __init__(self, products: list, mode: str):
        super().__init__(timeout=60)
        self.add_item(ProductSelect(products, mode))

class ProductSelect(discord.ui.Select):
    def __init__(self, products: list, mode: str):
        self.mode = mode
        options = (
            [
                discord.SelectOption(
                    label=f"{p['name']} - {p['price']:,}원",
                    description=f"재고: {'무제한' if p.get('stock', -1) == -1 else p.get('stock', '?')}개",
                    value=p["id"],
                )
                for p in products
            ]
            if products else
            [discord.SelectOption(label="상품 없음", value="none")]
        )
        super().__init__(placeholder="제품 선택...", options=options)

    async def callback(self, interaction: discord.Interaction):
        pid = self.values[0]
        if pid == "none":
            return await interaction.response.send_message("상품이 없습니다.", ephemeral=True)
        product = db.get_product(pid)
        if not product:
            return await interaction.response.send_message("상품을 찾을 수 없습니다.", ephemeral=True)

        if self.mode == "view":
            stock = "무제한" if product.get("stock", -1) == -1 else f"{product['stock']}개"
            embed = discord.Embed(title=f"📦 {product['name']}", color=0x57F287)
            embed.add_field(name="카테고리", value=product["category"], inline=True)
            embed.add_field(name="가격", value=f"{product['price']:,}원", inline=True)
            embed.add_field(name="재고", value=stock, inline=True)
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            embed = discord.Embed(
                title="🛍️ 구매 확인",
                description=f"**{product['name']}** 을(를) 구매하시겠습니까?",
                color=0xFEE75C,
            )
            embed.add_field(name="가격", value=f"{product['price']:,}원")
            await interaction.response.send_message(
                embed=embed, view=BuyConfirmView(pid, product), ephemeral=True
            )

# ── 구매 확인 ─────────────────────────────────────
class BuyConfirmView(discord.ui.View):
    def __init__(self, product_id: str, product: dict):
        super().__init__(timeout=60)
        self.product_id = product_id
        self.product = product

    @discord.ui.button(label="구매 확인", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, _button):
        result = db.purchase_product(str(interaction.user.id), self.product_id)
        if not result["success"]:
            msgs = {
                "잔액 부족": "잔액이 부족합니다. 충전 후 다시 시도해주세요.",
                "재고 없음": "해당 상품의 재고가 없습니다.",
            }
            await interaction.response.edit_message(
                content=f"❌ {msgs.get(result['reason'], result['reason'])}",
                embed=None, view=None,
            )
        else:
            await interaction.response.edit_message(
                content=f"✅ **구매 완료!**\n아래 내용을 확인하세요.\n```{result['content']}```",
                embed=None, view=None,
            )

    @discord.ui.button(label="취소", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _button):
        await interaction.response.edit_message(content="❌ 구매가 취소되었습니다.", embed=None, view=None)

# ── 충전 ─────────────────────────────────────────
class ChargeView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="계좌이체", emoji="🏦", style=discord.ButtonStyle.primary)
    async def bank_transfer(self, interaction: discord.Interaction, _button):
        await interaction.response.send_modal(BankChargeModal())

class BankChargeModal(discord.ui.Modal, title="계좌이체 충전 요청"):
    amount = discord.ui.TextInput(
        label="충전 금액",
        placeholder="충전할 금액을 입력하세요 (최소 1,000원, 숫자만)",
        required=True,
    )
    depositor_name = discord.ui.TextInput(
        label="입금자명",
        placeholder="입금자명을 입력하세요",
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount_val = int(str(self.amount).replace(",", ""))
        except ValueError:
            return await interaction.response.send_message("❌ 숫자만 입력해주세요.", ephemeral=True)

        if amount_val < 1000:
            return await interaction.response.send_message("❌ 최소 1,000원 이상 입력해주세요.", ephemeral=True)

        db.get_user(str(interaction.user.id), interaction.user.display_name)
        charge_id = db.add_pending_charge(
            str(interaction.user.id), amount_val, str(self.depositor_name)
        )

        bank_name    = os.getenv("BANK_NAME", "은행명")
        bank_account = os.getenv("BANK_ACCOUNT", "계좌번호")
        bank_holder  = os.getenv("BANK_HOLDER", "예금주")

        await interaction.response.send_message(
            f"✅ 충전 요청이 접수되었습니다!\n\n"
            f"💰 **충전 금액:** {amount_val:,}원\n"
            f"👤 **입금자명:** {self.depositor_name}\n"
            f"🆔 **요청 ID:** `{charge_id}`\n\n"
            f"아래 계좌로 입금해주세요:\n"
            f"> **{bank_name} {bank_account} (예금주: {bank_holder})**\n\n"
            f"입금 확인 후 자동으로 충전됩니다.",
            ephemeral=True,
        )

        # 관리자 채널 알림
        admin_ch_id = os.getenv("ADMIN_CHANNEL_ID")
        if admin_ch_id:
            try:
                channel = interaction.client.get_channel(int(admin_ch_id))
                if channel:
                    await channel.send(
                        f"💰 **새 충전 요청**\n"
                        f"유저: <@{interaction.user.id}> (`{interaction.user.display_name}`)\n"
                        f"금액: **{amount_val:,}원**\n"
                        f"입금자명: **{self.depositor_name}**\n"
                        f"ID: `{charge_id}`\n\n"
                        f"승인: `/충전승인 {charge_id}`\n"
                        f"거절: `/충전거절 {charge_id}`"
                    )
            except Exception as e:
                print(f"관리자 알림 실패: {e}")

# ── 구매 내역 ─────────────────────────────────────
class PurchaseHistoryView(discord.ui.View):
    def __init__(self, user: dict):
        super().__init__(timeout=60)
        self.user = user
        self.add_item(PurchaseHistorySelect(user))

class PurchaseHistorySelect(discord.ui.Select):
    def __init__(self, user: dict):
        purchases = user.get("purchases", [])[:10]
        from datetime import datetime
        options = (
            [
                discord.SelectOption(
                    label=f"{p['product_name']} - {p['price']:,}원",
                    description=datetime.fromisoformat(p["purchased_at"]).strftime("%Y-%m-%d %H:%M"),
                    value=str(i),
                )
                for i, p in enumerate(purchases)
            ]
            if purchases else
            [discord.SelectOption(label="구매 내역 없음", value="none")]
        )
        super().__init__(placeholder="최근 구매 내역을 선택하세요", options=options)
        self.purchases = purchases

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            return await interaction.response.send_message("구매 내역이 없습니다.", ephemeral=True)
        idx = int(self.values[0])
        record = self.purchases[idx]
        from datetime import datetime
        date_str = datetime.fromisoformat(record["purchased_at"]).strftime("%Y-%m-%d %H:%M")
        await interaction.response.send_message(
            f"📦 **{record['product_name']}** ({record['price']:,}원)\n"
            f"구매일: {date_str}\n\n내용:\n```{record['content']}```",
            ephemeral=True,
        )

# ── 헬퍼 함수 ─────────────────────────────────────
def category_embed(title: str) -> discord.Embed:
    return discord.Embed(
        title=title,
        description="카테고리를 선택하시면 제품 목록이 활성화됩니다.",
        color=0x5865F2,
    )

def user_info_embed(user: dict) -> discord.Embed:
    embed = discord.Embed(title="💎 유저 정보", color=0x5865F2)
    embed.add_field(name="닉네임", value=user["username"], inline=False)
    embed.add_field(name="보유 잔액", value=f"{user['balance']:,}원", inline=False)
    embed.add_field(name="누적 구매 금액", value=f"{user['total_purchase']:,}원", inline=False)
    embed.add_field(name="회원 등급", value=user.get("grade", "일반"), inline=False)
    return embed

# ── Cog ──────────────────────────────────────────
class Vending(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.guild = discord.Object(id=GUILD_ID)

    # 자판기 패널
    @app_commands.command(name="자판기", description="자판기 메인 패널을 엽니다")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def vending(self, interaction: discord.Interaction):
        embed = discord.Embed(title="🛒 자동 자판기", color=0x5865F2)
        embed.description = (
            "**📦 제품** —> 제품 목록 보기\n\n"
            "**🛍️ 구매** —> 제품 구매하기\n\n"
            "**💰 충전** —> 구매할 돈 충전\n\n"
            "**💎 정보** —> 잔액, 구매내역 보기"
        )
        await interaction.response.send_message(embed=embed, view=MainView())

    # 상품 추가
    @app_commands.command(name="상품추가", description="[관리자] 상품을 추가합니다")
    @app_commands.describe(카테고리="카테고리 이름", 이름="상품 이름", 가격="가격(원)", 내용="구매 후 전달할 내용", 재고="재고 수량 (-1=무제한)")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def add_product(self, interaction: discord.Interaction, 카테고리: str, 이름: str, 가격: int, 내용: str, 재고: int = -1):
        if not is_admin(interaction):
            return await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
        import time
        pid = f"P-{int(time.time() * 1000)}"
        db.save_product(pid, {"category": 카테고리, "name": 이름, "price": 가격, "content": 내용, "stock": 재고})
        await interaction.response.send_message(f"✅ 상품 `{이름}` 추가 완료! (ID: `{pid}`)", ephemeral=True)

    # 상품 삭제
    @app_commands.command(name="상품삭제", description="[관리자] 상품을 삭제합니다")
    @app_commands.describe(상품id="삭제할 상품 ID")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def del_product(self, interaction: discord.Interaction, 상품id: str):
        if not is_admin(interaction):
            return await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
        db.delete_product(상품id)
        await interaction.response.send_message(f"🗑️ 상품 `{상품id}` 삭제 완료.", ephemeral=True)

    # 충전 목록
    @app_commands.command(name="충전목록", description="[관리자] 대기 중인 충전 요청 목록")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def charge_list(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            return await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
        pending = db.get_pending_charges()
        embed = discord.Embed(title="💰 충전 대기 목록", color=0xFEE75C)
        if not pending:
            embed.description = "대기 중인 충전 요청이 없습니다."
        else:
            embed.description = "\n\n".join(
                f"**ID:** `{c['id']}`\n유저: <@{c['user_id']}> | 금액: **{c['amount']:,}원** | 입금자: **{c['depositor_name']}**"
                for c in pending
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # 충전 승인
    @app_commands.command(name="충전승인", description="[관리자] 충전 요청을 승인합니다")
    @app_commands.describe(충전id="충전 요청 ID")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def approve(self, interaction: discord.Interaction, 충전id: str):
        if not is_admin(interaction):
            return await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
        charge = db.approve_charge(충전id)
        if not charge:
            return await interaction.response.send_message("❌ 요청을 찾을 수 없습니다.", ephemeral=True)
        await interaction.response.send_message(
            f"✅ 충전 승인 완료! <@{charge['user_id']}>님께 **{charge['amount']:,}원** 지급됨."
        )
        try:
            user = await self.bot.fetch_user(int(charge["user_id"]))
            await user.send(f"✅ **충전 완료!**\n금액: **{charge['amount']:,}원**이 충전되었습니다.")
        except Exception:
            pass

    # 충전 거절
    @app_commands.command(name="충전거절", description="[관리자] 충전 요청을 거절합니다")
    @app_commands.describe(충전id="충전 요청 ID")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def reject(self, interaction: discord.Interaction, 충전id: str):
        if not is_admin(interaction):
            return await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
        charge = db.reject_charge(충전id)
        if not charge:
            return await interaction.response.send_message("❌ 요청을 찾을 수 없습니다.", ephemeral=True)
        await interaction.response.send_message(f"❌ 충전 거절 완료.")
        try:
            user = await self.bot.fetch_user(int(charge["user_id"]))
            await user.send(f"❌ **충전 거절**\n입금자명 **{charge['depositor_name']}**의 충전 요청이 거절되었습니다.")
        except Exception:
            pass

    # 잔액 지급
    @app_commands.command(name="잔액지급", description="[관리자] 유저에게 잔액을 지급합니다")
    @app_commands.describe(유저="지급할 유저", 금액="지급 금액")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def give_balance(self, interaction: discord.Interaction, 유저: discord.Member, 금액: int):
        if not is_admin(interaction):
            return await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
        user = db.get_user(str(유저.id), 유저.display_name)
        user["balance"] += 금액
        db.save_user(str(유저.id), user)
        await interaction.response.send_message(f"✅ <@{유저.id}>님께 **{금액:,}원** 지급 완료!")

async def setup(bot):
    await bot.add_cog(Vending(bot))
