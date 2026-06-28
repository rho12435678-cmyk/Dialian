import discord

from database.modal.gfx_modal import PurchaseModal


class LogoModal(PurchaseModal, title="🖼️ 로고 커미션 신청서"):

    server_name = discord.ui.TextInput(
        label="서버 / 브랜드 이름",
        placeholder="예: Dialian Studio",
        required=True,
        max_length=50
    )

    logo_style = discord.ui.TextInput(
        label="원하는 로고 스타일",
        placeholder="예: 미니멀, 3D, 심플, 네온",
        required=True,
        max_length=100
    )

    logo_color = discord.ui.TextInput(
        label="원하는 색상",
        placeholder="예: 검정 + 금색",
        required=True,
        max_length=100
    )

    async def on_submit(self, interaction: discord.Interaction):

        # 기존 PurchaseModal의 기능 그대로 실행
        await super().on_submit(interaction)