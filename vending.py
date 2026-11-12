import discord
from discord import app_commands
from discord.ext import commands
import os
import database as db

def is_admin(interaction: discord.Interaction) -> bool:
    if interaction.user.guild_permissions.administrator:
        return True
    ginfo = db.get_guild_info(str(interaction.guild_id))
    if ginfo and ginfo.get("admin_role_id"):
        return any(r.id == int(ginfo["admin_role_id"]) for r in interaction.user.roles)
    return False

def check_active(interaction: discord.Interaction) -> bool:
    return db.is_guild_active(str(interaction.guild_id))

# ── 메인 패널 ─────────────────────────────────────
class MainView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="제품", emoji="📦", style=discord.ButtonStyle.success, custom_id="v_products")
    async def products(self, interaction: discord.Interaction, _):
        if not check_active(interaction):
            return await interaction.response.send_message("❌ 이 서버는 미등록 상태입니다.", ephemeral=True)
        cats = db.get_categories(str(interaction.guild_id))
        await interaction.response.send_message(
            embed=cat_embed("🛒 제품 조회"),
            view=CategoryView(cats, "view"),
            ephemeral=True,
        )

    @discord.ui.button(label="구매", emoji="🛒", style=discord.ButtonStyle.primary, custom_id="v_buy")
    async def buy(self, interaction: discord.Interaction, _):
        if not check_active(interaction):
            return await interaction.response.send_message("❌ 이 서버는 미등록 상태입니다.", ephemeral=True)
        cats = db.get_categories(str(interaction.guild_id))
        await interaction.response.send_message(
            embed=cat_embed("🛍️ 제품 구매"),
            view=CategoryView(cats, "buy"),
            ephemeral=True,
        )

    @discord.ui.button(label="충전", emoji="💰", style=discord.ButtonStyle.danger, custom_id="v_charge")
    async def charge(self, interaction: discord.Interaction, _):
        if not check_active(interaction):
            return await interaction.response.send_message("❌ 이 서버는 미등록 상태입니다.", ephemeral=True)
        bank = db.get_bank_info(str(interaction.guild_id))
        embed = discord.Embed(title="💰 충전", description="충전 수단을 선택하세요", color=0xFEE75C)
        if bank["name"] != "은행명 미설정":
            embed.add_field(name="입금 계좌", value=f"**{bank['name']}** {bank['account']}\n예금주: {bank['holder']}", inline=False)
        ev = event_lines(str(interaction.guild_id), interaction.user.id)
        if ev:
            embed.add_field(name="🎁 진행 중인 이벤트", value=ev, inline=False)
        await interaction.response.send_message(embed=embed, view=ChargeView(), ephemeral=True)

    @discord.ui.button(label="정보", emoji="💎", style=discord.ButtonStyle.secondary, custom_id="v_info")
    async def info(self, interaction: discord.Interaction, _):
        if not check_active(interaction):
            return await interaction.response.send_message("❌ 이 서버는 미등록 상태입니다.", ephemeral=True)
        user = db.get_user(str(interaction.guild_id), str(interaction.user.id), interaction.user.display_name)
        await interaction.response.send_message(
            embed=user_embed(user),
            view=HistoryView(user),
            ephemeral=True,
        )

# ── 카테고리 ──────────────────────────────────────
class CategoryView(discord.ui.View):
    def __init__(self, cats, mode):
        super().__init__(timeout=60)
        self.add_item(CategorySelect(cats, mode))

class CategorySelect(discord.ui.Select):
    def __init__(self, cats, mode):
        self.mode = mode
        opts = [discord.SelectOption(label=c, value=c) for c in cats] if cats else [discord.SelectOption(label="상품 없음", value="none")]
        super().__init__(placeholder="카테고리 선택...", options=opts)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            return await interaction.response.send_message("상품이 없습니다.", ephemeral=True)
        products = db.get_products_by_category(str(interaction.guild_id), self.values[0])
        await interaction.response.send_message(
            embed=discord.Embed(title="상품 선택", color=0x5865F2),
            view=ProductView(products, self.mode),
            ephemeral=True,
        )

# ── 상품 선택 ─────────────────────────────────────
class ProductView(discord.ui.View):
    def __init__(self, products, mode):
        super().__init__(timeout=60)
        self.add_item(ProductSelect(products, mode))

class ProductSelect(discord.ui.Select):
    def __init__(self, products, mode):
        self.mode = mode
        opts = (
            [discord.SelectOption(
                label=f"{p['name']} - {p['price']:,}원",
                description="재고: " + ("무제한" if p.get("stock", -1) == -1 else f"{p.get('stock', '?')}개"),
                value=p["id"],
            ) for p in products]
            if products else [discord.SelectOption(label="상품 없음", value="none")]
        )
        super().__init__(placeholder="제품 선택...", options=opts)

    async def callback(self, interaction: discord.Interaction):
        pid = self.values[0]
        if pid == "none":
            return await interaction.response.send_message("상품이 없습니다.", ephemeral=True)
        product = db.get_product(str(interaction.guild_id), pid)
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
            embed = discord.Embed(title="🛍️ 구매 확인", description=f"**{product['name']}** 을(를) 구매하시겠습니까?", color=0xFEE75C)
            embed.add_field(name="가격", value=f"{product['price']:,}원")
            await interaction.response.send_message(embed=embed, view=BuyConfirmView(pid, product), ephemeral=True)

# ── 구매 확인 ─────────────────────────────────────
class BuyConfirmView(discord.ui.View):
    def __init__(self, pid, product):
        super().__init__(timeout=120)
        self.pid = pid
        self.product = product
        self.coupon_code = None
        self.discount = 0

    def build_embed(self):
        price = self.product["price"]
        embed = discord.Embed(title="🛍️ 구매 확인", description=f"**{self.product['name']}** 을(를) 구매하시겠습니까?", color=0xFEE75C)
        if self.discount:
            embed.add_field(name="가격", value=f"~~{price:,}원~~ → **{price - self.discount:,}원**")
            embed.add_field(name="🎟️ 쿠폰", value=f"`{self.coupon_code}` (-{self.discount:,}원)")
        else:
            embed.add_field(name="가격", value=f"{price:,}원")
        return embed

    @discord.ui.button(label="구매 확인", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, _):
        result = db.purchase_product(str(interaction.guild_id), str(interaction.user.id), self.pid, self.coupon_code)
        if not result["success"]:
            msgs = {"잔액 부족": "잔액이 부족합니다. 충전 후 다시 시도해주세요.", "재고 없음": "재고가 없습니다."}
            return await interaction.response.edit_message(content=f"❌ {msgs.get(result['reason'], result['reason'])}", embed=None, view=None)
        extra = f"\n🎟️ 쿠폰 `{result['coupon']}` 적용: -{result['discount']:,}원 (결제 {result['paid']:,}원)" if result.get("coupon") else ""
        await interaction.response.edit_message(
            content=f"✅ **구매 완료!**{extra}\n아래 내용을 확인하세요.\n```{result['content']}```",
            embed=None, view=None,
        )

    @discord.ui.button(label="쿠폰 사용", emoji="🎟️", style=discord.ButtonStyle.primary)
    async def coupon(self, interaction: discord.Interaction, _):
        await interaction.response.send_modal(CouponModal(self))

    @discord.ui.button(label="취소", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _):
        await interaction.response.edit_message(content="❌ 취소되었습니다.", embed=None, view=None)

class CouponModal(discord.ui.Modal, title="쿠폰 사용"):
    code = discord.ui.TextInput(label="쿠폰 코드", placeholder="쿠폰 코드를 입력하세요", required=True, max_length=32)

    def __init__(self, parent: "BuyConfirmView"):
        super().__init__()
        self.parent = parent

    async def on_submit(self, interaction: discord.Interaction):
        chk = db.check_coupon(str(interaction.guild_id), str(self.code), str(interaction.user.id), self.parent.product["price"])
        if not chk["ok"]:
            return await interaction.response.send_message(f"❌ {chk['reason']}", ephemeral=True)
        self.parent.coupon_code = chk["code"]
        self.parent.discount = chk["discount"]
        await interaction.response.edit_message(embed=self.parent.build_embed(), view=self.parent)

# ── 충전 ─────────────────────────────────────────
class ChargeView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="계좌이체", emoji="🏦", style=discord.ButtonStyle.primary)
    async def bank(self, interaction: discord.Interaction, _):
        await interaction.response.send_modal(BankModal())

class BankModal(discord.ui.Modal, title="계좌이체 충전 요청"):
    amount = discord.ui.TextInput(label="충전 금액", placeholder="최소 1,000원, 숫자만", required=True)
    name   = discord.ui.TextInput(label="입금자명",   placeholder="입금자명을 입력하세요",    required=True)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            val = int(str(self.amount).replace(",", ""))
        except:
            return await interaction.response.send_message("❌ 숫자만 입력해주세요.", ephemeral=True)
        gid = str(interaction.guild_id)
        settings = db.get_settings(gid)
        min_c = settings.get("min_charge", 1000)
        max_c = settings.get("max_charge", 0)
        if val < min_c:
            return await interaction.response.send_message(f"❌ 최소 {min_c:,}원 이상 입력해주세요.", ephemeral=True)
        if max_c > 0 and val > max_c:
            return await interaction.response.send_message(f"❌ 한 번에 최대 {max_c:,}원까지 충전할 수 있습니다.", ephemeral=True)
        db.get_user(gid, str(interaction.user.id), interaction.user.display_name)
        charge_id = db.add_pending_charge(gid, str(interaction.user.id), val, str(self.name))
        bank = db.get_bank_info(gid)

        await interaction.response.send_message(
            f"✅ **충전 요청 접수!**\n\n"
            f"💰 금액: **{val:,}원**\n"
            f"👤 입금자명: **{self.name}**\n"
            f"🆔 요청 ID: `{charge_id}`\n\n"
            f"아래 계좌로 입금해주세요:\n"
            f"> **{bank['name']} {bank['account']}** (예금주: {bank['holder']})\n\n"
            + ("입금 확인 후 자동으로 충전됩니다." if settings.get("auto_charge", True) else "관리자가 입금을 확인한 뒤 충전해드립니다.")
            + (f"\n\n{event_lines(gid, interaction.user.id)}" if event_lines(gid, interaction.user.id) else ""),
            ephemeral=True,
        )

        # 관리자 채널 알림
        settings = db.get_settings(gid)
        ch_id = settings.get("admin_channel_id")
        if ch_id:
            try:
                ch = interaction.client.get_channel(int(ch_id)) or await interaction.client.fetch_channel(int(ch_id))
                await ch.send(
                    embed=charge_request_embed(gid, charge_id, interaction.user.id, val, str(self.name)),
                    view=ChargeReviewView(),
                )
            except Exception as e:
                print(f"관리자 채널 알림 실패: {e}")

# ── 충전 요청 승인/거절 버튼 (관리자 채널) ─────────
def charge_request_embed(gid, charge_id, user_id, amount, depositor):
    e = discord.Embed(title="💰 새 충전 요청", color=0xFEE75C)
    e.add_field(name="유저", value=f"<@{user_id}>", inline=True)
    e.add_field(name="금액", value=f"**{amount:,}원**", inline=True)
    e.add_field(name="입금자", value=depositor, inline=True)
    e.set_footer(text=f"{charge_id}|{gid}")  # 버튼이 어떤 요청인지 알아내는 데 사용
    return e

def read_charge_footer(message):
    try:
        charge_id, gid = message.embeds[0].footer.text.split("|")
        return gid, charge_id
    except Exception:
        return None, None

class ChargeReviewView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)  # 봇을 재시작해도 버튼이 계속 동작하도록 영구 뷰

    async def _handle(self, interaction: discord.Interaction, approve: bool):
        if not is_admin(interaction):
            return await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
        gid, cid = read_charge_footer(interaction.message)
        if not cid or gid != str(interaction.guild_id):
            return await interaction.response.send_message("❌ 요청 정보를 읽을 수 없습니다.", ephemeral=True)

        charge = db.approve_charge(gid, cid) if approve else db.reject_charge(gid, cid)
        embed = interaction.message.embeds[0]
        if not charge:  # 이미 처리됨 (SMS 자동충전, 다른 관리자 등)
            embed.color = 0x99AAB5
            embed.add_field(name="상태", value="⚪ 이미 처리된 요청입니다", inline=False)
            return await interaction.response.edit_message(embed=embed, view=None)

        embed.color = 0x57F287 if approve else 0xED4245
        embed.add_field(
            name="처리 결과",
            value=f"{'✅ 승인' if approve else '❌ 거절'} · {interaction.user.mention}",
            inline=False,
        )
        if approve and charge.get("bonus"):
            embed.add_field(name="🎉 이벤트 보너스", value=f"+{charge['bonus']:,}원 ({charge['event_name']})", inline=False)
        await interaction.response.edit_message(embed=embed, view=None)

        try:
            user = await interaction.client.fetch_user(int(charge["user_id"]))
            if approve:
                bonus = f"\n🎉 이벤트 **{charge['event_name']}** 보너스 **+{charge['bonus']:,}원** 지급!" if charge.get("bonus") else ""
                await user.send(f"✅ **충전 완료!** {charge['amount']:,}원이 충전되었습니다.{bonus}")
            else:
                await user.send(f"❌ **충전 거절** 입금자명 **{charge['depositor_name']}**의 요청이 거절되었습니다.")
        except Exception:
            pass

    @discord.ui.button(label="승인", emoji="✅", style=discord.ButtonStyle.success, custom_id="charge_approve")
    async def approve_btn(self, interaction: discord.Interaction, _):
        await self._handle(interaction, True)

    @discord.ui.button(label="거절", emoji="❌", style=discord.ButtonStyle.danger, custom_id="charge_reject")
    async def reject_btn(self, interaction: discord.Interaction, _):
        await self._handle(interaction, False)

# ── 구매 내역 ─────────────────────────────────────
class HistoryView(discord.ui.View):
    def __init__(self, user):
        super().__init__(timeout=60)
        self.add_item(HistorySelect(user))

class HistorySelect(discord.ui.Select):
    def __init__(self, user):
        from datetime import datetime
        purchases = user.get("purchases", [])[:10]
        opts = (
            [discord.SelectOption(
                label=f"{p['product_name']} - {p['price']:,}원",
                description=datetime.fromisoformat(p["purchased_at"]).strftime("%Y-%m-%d %H:%M"),
                value=str(i),
            ) for i, p in enumerate(purchases)]
            if purchases else [discord.SelectOption(label="구매 내역 없음", value="none")]
        )
        super().__init__(placeholder="최근 구매 내역 선택", options=opts)
        self.purchases = purchases

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            return await interaction.response.send_message("구매 내역이 없습니다.", ephemeral=True)
        from datetime import datetime
        r = self.purchases[int(self.values[0])]
        await interaction.response.send_message(
            f"📦 **{r['product_name']}** ({r['price']:,}원)\n"
            f"구매일: {datetime.fromisoformat(r['purchased_at']).strftime('%Y-%m-%d %H:%M')}\n\n"
            f"내용:\n```{r['content']}```",
            ephemeral=True,
        )

# ── 헬퍼 ──────────────────────────────────────────
def cat_embed(title):
    return discord.Embed(title=title, description="카테고리를 선택하시면 제품 목록이 활성화됩니다.", color=0x5865F2)

def user_embed(user):
    embed = discord.Embed(title="💎 유저 정보", color=0x5865F2)
    embed.add_field(name="닉네임", value=user["username"], inline=False)
    embed.add_field(name="보유 잔액", value=f"{user['balance']:,}원", inline=False)
    embed.add_field(name="누적 구매", value=f"{user['total_purchase']:,}원", inline=False)
    embed.add_field(name="등급", value=user.get("grade","일반"), inline=False)
    return embed

def event_lines(gid, uid):
    lines = []
    for ev in db.active_charge_events(gid, uid):
        mw = ev.get("max_winners", 0)
        left = f" (선착순 {mw}명 중 **{mw - len(ev.get('claimed_by', []))}자리 남음**)" if mw > 0 else ""
        cond = f" · {ev['min_amount']:,}원 이상 충전 시" if ev.get("min_amount", 0) > 0 else ""
        lines.append(f"🎉 **{ev['name']}** — **{ev['multiplier']:g}배 충전**{left}{cond}")
    return "\n".join(lines)

# ── Cog ──────────────────────────────────────────
class Vending(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="자판기", description="자판기 메인 패널")
    async def vending(self, interaction: discord.Interaction):
        if not check_active(interaction):
            return await interaction.response.send_message("❌ 이 서버는 미등록 상태입니다. 관리자에게 문의하세요.", ephemeral=True)
        embed = discord.Embed(title="🛒 자동 자판기", color=0x5865F2)
        embed.description = (
            "**📦 제품** —> 제품 목록 보기\n\n"
            "**🛍️ 구매** —> 제품 구매하기\n\n"
            "**💰 충전** —> 구매할 돈 충전\n\n"
            "**💎 정보** —> 잔액, 구매내역 보기"
        )
        await interaction.response.send_message(embed=embed, view=MainView())

    @app_commands.command(name="상품추가", description="[관리자] 상품 추가")
    @app_commands.describe(카테고리="카테고리", 이름="상품명", 가격="가격(원)", 내용="구매 후 전달 내용", 재고="재고(-1=무제한)")
    async def add_product(self, interaction: discord.Interaction, 카테고리:str, 이름:str, 가격:int, 내용:str, 재고:int=-1):
        if not is_admin(interaction): return await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
        import time
        pid = f"P-{int(time.time()*1000)}"
        db.save_product(str(interaction.guild_id), pid, {"category":카테고리,"name":이름,"price":가격,"content":내용,"stock":재고})
        await interaction.response.send_message(f"✅ `{이름}` 추가 완료! (ID: `{pid}`)", ephemeral=True)

    @app_commands.command(name="상품삭제", description="[관리자] 상품 삭제")
    @app_commands.describe(상품id="삭제할 상품 ID")
    async def del_product(self, interaction: discord.Interaction, 상품id:str):
        if not is_admin(interaction): return await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
        db.delete_product(str(interaction.guild_id), 상품id)
        await interaction.response.send_message(f"🗑️ `{상품id}` 삭제 완료", ephemeral=True)

    @app_commands.command(name="충전목록", description="[관리자] 대기 충전 목록")
    async def charge_list(self, interaction: discord.Interaction):
        if not is_admin(interaction): return await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
        pending = db.get_pending_charges(str(interaction.guild_id))
        embed = discord.Embed(title="💰 충전 대기 목록", color=0xFEE75C)
        embed.description = (
            "\n\n".join(f"**ID:** `{c['id']}`\n유저: <@{c['user_id']}> | 금액: **{c['amount']:,}원** | 입금자: **{c['depositor_name']}**" for c in pending)
            if pending else "대기 중인 요청 없음"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="충전승인", description="[관리자] 충전 승인")
    @app_commands.describe(충전id="충전 요청 ID")
    async def approve(self, interaction: discord.Interaction, 충전id:str):
        if not is_admin(interaction): return await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
        charge = db.approve_charge(str(interaction.guild_id), 충전id)
        if not charge: return await interaction.response.send_message("❌ 요청 없음", ephemeral=True)
        bonus = f"\n🎉 이벤트 **{charge['event_name']}** 보너스 **+{charge['bonus']:,}원** 지급!" if charge.get("bonus") else ""
        await interaction.response.send_message(f"✅ <@{charge['user_id']}>님 **{charge['amount']:,}원** 충전 완료!{bonus}")
        try:
            user = await self.bot.fetch_user(int(charge["user_id"]))
            await user.send(f"✅ **충전 완료!** {charge['amount']:,}원이 충전되었습니다.{bonus}")
        except: pass

    @app_commands.command(name="충전거절", description="[관리자] 충전 거절")
    @app_commands.describe(충전id="충전 요청 ID")
    async def reject(self, interaction: discord.Interaction, 충전id:str):
        if not is_admin(interaction): return await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
        charge = db.reject_charge(str(interaction.guild_id), 충전id)
        if not charge: return await interaction.response.send_message("❌ 요청 없음", ephemeral=True)
        await interaction.response.send_message(f"❌ `{충전id}` 거절 완료")
        try:
            user = await self.bot.fetch_user(int(charge["user_id"]))
            await user.send(f"❌ **충전 거절** 입금자명 **{charge['depositor_name']}**의 요청이 거절되었습니다.")
        except: pass

    @app_commands.command(name="잔액지급", description="[관리자] 잔액 지급")
    @app_commands.describe(유저="지급할 유저", 금액="금액")
    async def give_balance(self, interaction: discord.Interaction, 유저:discord.Member, 금액:int):
        if not is_admin(interaction): return await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
        gid = str(interaction.guild_id)
        user = db.get_user(gid, str(유저.id), 유저.display_name)
        user["balance"] += 금액
        db.save_user(gid, str(유저.id), user)
        await interaction.response.send_message(f"✅ <@{유저.id}>님 **{금액:,}원** 지급 완료!")

    @app_commands.command(name="계좌설정", description="[관리자] 계좌 정보 설정")
    @app_commands.describe(은행이름="예: 국민은행", 계좌번호="계좌번호", 예금주="예금주")
    async def set_bank(self, interaction: discord.Interaction, 은행이름:str, 계좌번호:str, 예금주:str):
        if not is_admin(interaction): return await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
        gid = str(interaction.guild_id)
        s = db.get_settings(gid)
        s["bank_name"] = 은행이름; s["bank_account"] = 계좌번호; s["bank_holder"] = 예금주
        db.save_settings(gid, s)
        embed = discord.Embed(title="✅ 계좌 설정 완료", color=0x57F287)
        embed.add_field(name="은행", value=은행이름, inline=True)
        embed.add_field(name="계좌", value=계좌번호, inline=True)
        embed.add_field(name="예금주", value=예금주, inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="관리채널설정", description="[관리자] 충전 알림 받을 채널 설정")
    @app_commands.describe(채널="알림 받을 채널")
    async def set_admin_ch(self, interaction: discord.Interaction, 채널:discord.TextChannel):
        if not is_admin(interaction): return await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
        gid = str(interaction.guild_id)
        s = db.get_settings(gid)
        s["admin_channel_id"] = str(채널.id)
        db.save_settings(gid, s)
        await interaction.response.send_message(f"✅ 관리 채널이 {채널.mention}로 설정되었습니다!", ephemeral=True)

    @app_commands.command(name="계좌확인", description="[관리자] 현재 계좌 정보 확인")
    async def check_bank(self, interaction: discord.Interaction):
        if not is_admin(interaction): return await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
        info = db.get_bank_info(str(interaction.guild_id))
        embed = discord.Embed(title="🏦 현재 계좌 정보", color=0x5865F2)
        embed.add_field(name="은행", value=info["name"], inline=True)
        embed.add_field(name="계좌번호", value=info["account"], inline=True)
        embed.add_field(name="예금주", value=info["holder"], inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot):
    await bot.add_cog(Vending(bot))
    bot.add_view(MainView())          # 재시작 후에도 메인 패널 버튼 유지
    bot.add_view(ChargeReviewView())  # 재시작 후에도 승인/거절 버튼 유지
