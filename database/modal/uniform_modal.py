import discord
from database.modal.gfx_modal import PurchaseModal


class UniformModal(PurchaseModal):

    MODAL_TITLE = "👕 Roblox 복장 커미션 신청서"
    FORM_TITLE = "📋 Roblox 복장 신청서"

    FIELD1 = "🎮 Roblox 닉네임"
    FIELD2 = "👕 원하는 복장 종류"
    FIELD3 = "📝 원하는 스타일"

    async def on_submit(self, interaction: discord.Interaction):
        await super().on_submit(interaction)