import discord
from database.modal.gfx_modal import PurchaseModal

class UniformModal(PurchaseModal):
    COMMISSION_NAME = "Roblox 복장"

    def __init__(self, bundle_type: str = "단품 (1개)", selected_designer: int = None):
        discord.ui.Modal.__init__(self, title=f"👕 복장 커미션 신청서 [{bundle_type}]")
        self.bundle_type = bundle_type
        self.selected_designer = selected_designer

        # 원하는 스타일 및 설명 (유일한 입력 필드)
        self.gfx_style = discord.ui.TextInput(
            label="📝 원하는 스타일 및 설명",
            placeholder="원하시는 콘셉트, 색감, 디테일 등을 적어주세요.",
            required=True,
            style=discord.TextStyle.paragraph,
            max_length=1000
        )
        self.add_item(self.gfx_style)

        # 제출 로직(on_submit) 참조 에러 방지용 기본값
        self.roblox_nickname = None
        self.gfx_type = None
        self.fourth_style = None
