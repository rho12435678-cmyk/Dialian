"""FAMILY-only UI pre-release commission; reuses existing ticket/payment/review flow."""
import discord
from database.modal.gfx_modal import PurchaseModal
from database.services.family import is_family_active
from database.services.commission_pricing import UI_PRICES

UI_BASE = dict(zip(("단품 (1개)", "2+1 묶음", "3+1 묶음"), UI_PRICES))

class UIPreviewModal(PurchaseModal):
    COMMISSION_NAME = "Roblox UI 사전 체험"

    def __init__(self, bundle_type="단품 (1개)", selected_designer=None):
        discord.ui.Modal.__init__(self, title=f"🖥️ UI 커미션 사전 체험 [{bundle_type}]")
        self.bundle_type = bundle_type
        self.selected_designer = selected_designer
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
        # The parent posts the same unified quote + guide used by GFX and clothing.
        # Do not send a second UI-only price message.
        return await super().create_ticket(interaction)

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
