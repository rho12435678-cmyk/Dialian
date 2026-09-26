"""FAMILY-only UI pre-release commission; reuses existing ticket/payment/review flow."""
import discord
from config import REGULAR_CUSTOMER_ROLE_ID
from database.modal.gfx_modal import PurchaseModal
from database.services.family import discounted_price, is_family_active

UI_BASE = {"단품 (1개)": 5000, "2+1 묶음": 10000, "3+1 묶음": 15000}

class UIPreviewModal(PurchaseModal):
    COMMISSION_NAME = "Roblox UI 사전 체험"

    def __init__(self, bundle_type="단품 (1개)"):
        discord.ui.Modal.__init__(self, title=f"🖥️ UI 커미션 사전 체험 [{bundle_type}]")
        self.bundle_type = bundle_type
        self.selected_designer = None
        self.roblox_nickname = discord.ui.TextInput(label="🎮 Roblox 사용자명/프로젝트", required=True, max_length=100)
        self.gfx_genre = discord.ui.TextInput(label="🖥️ UI 종류", placeholder="예: 게임 메뉴, 상점, 설정", max_length=100)
        self.gfx_style = discord.ui.TextInput(label="📝 UI 요구사항", style=discord.TextStyle.paragraph, max_length=1000)
        self.add_item(self.roblox_nickname)
        self.add_item(self.gfx_genre)
        self.add_item(self.gfx_style)
        self.fourth_style = None
        if bundle_type != "단품 (1개)":
            self.fourth_style = discord.ui.TextInput(
                label="🎁 묶음 추가 작품 요구사항", style=discord.TextStyle.paragraph, max_length=1000
            )
            self.add_item(self.fourth_style)

    async def on_submit(self, interaction):
        if not await is_family_active(interaction.user):
            return await interaction.response.send_message("🔒 FAMILY 활성 회원 전용입니다.", ephemeral=True)
        await super().on_submit(interaction)

    async def create_ticket(self, interaction):
        if not await is_family_active(interaction.user):
            return await interaction.followup.send("🔒 FAMILY 자격이 만료되었습니다.", ephemeral=True)
        regular = any(r.id == REGULAR_CUSTOMER_ROLE_ID for r in interaction.user.roles)
        base = UI_BASE[self.bundle_type]
        quote = discounted_price(base, family=True, regular=regular)
        # Quote the exact channel returned by the base ticket flow. Avoid
        # choosing the wrong order if a member opens multiple tickets at once.
        channel = await super().create_ticket(interaction)
        if channel is None:
            return
        try:
            await channel.send(
                f"💎 **DDS FAMILY UI 사전 체험 가격**\n"
                f"기준가 {base:,}원 / 할인 {'30' if regular else '20'}% / "
                f"**결제 예정 금액 {quote:,}원**\n"
                "※ 할인은 주문 접수 시점 기준입니다. 실제 입금은 담당자 안내 후 진행하세요."
            )
        except discord.HTTPException as exc:
            # Ticket already exists; never mislead customers with a generic
            # creation error or create a duplicate just because quote failed.
            print(f"[FAMILY UI quote send failed] ticket={channel.id}: {exc}")

class UIQuantitySelect(discord.ui.Select):
    def __init__(self):
        super().__init__(placeholder="UI 사전 체험 상품 선택", options=[
            discord.SelectOption(label="단품", value="단품 (1개)"),
            discord.SelectOption(label="2+1 묶음", value="2+1 묶음"),
            discord.SelectOption(label="3+1 묶음", value="3+1 묶음"),
        ])

    async def callback(self, interaction):
        if not await is_family_active(interaction.user):
            return await interaction.response.send_message("🔒 FAMILY 활성 회원 전용입니다.", ephemeral=True)
        await interaction.response.send_modal(UIPreviewModal(self.values[0]))

class UIQuantityView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(UIQuantitySelect())
