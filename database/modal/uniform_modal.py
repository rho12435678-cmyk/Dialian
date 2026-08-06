import discord
from database.modal.gfx_modal import PurchaseModal

class UniformModal(PurchaseModal):
    COMMISSION_NAME = "Roblox 복장"

    def __init__(self, bundle_type: str = "단품 (1개)", selected_designer: int = None):
        # 부모 클래스의 init을 호출하지 않고, 복장에 맞는 필드로 덮어씁니다.
        discord.ui.Modal.__init__(self, title=f"👕 복장 커미션 신청서 [{bundle_type}]")
        self.bundle_type = bundle_type
        self.selected_designer = selected_designer

        self.roblox_nickname = discord.ui.TextInput(
            label="🎮 Roblox 닉네임",
            placeholder="작품에 반영될 로블록스 닉네임을 작성해주세요.",
            required=True, max_length=30
        )
        self.add_item(self.roblox_nickname)

        self.gfx_type = discord.ui.TextInput(
            label="👕 원하는 복장 종류",
            placeholder="예: 군복, 캐주얼, 판타지 갑옷 등",
            required=True, max_length=50
        )
        self.add_item(self.gfx_type)

        if self.bundle_type == "3+1 묶음":
            self.gfx_style = discord.ui.TextInput(
                label="📝 1~3번째 복장 상세 요구사항",
                placeholder="1, 2, 3번째 의상에 대한 요구사항을 적어주세요.",
                required=True, style=discord.TextStyle.paragraph, max_length=500
            )
            self.add_item(self.gfx_style)
            self.fourth_style = discord.ui.TextInput(
                label="🎁 4번째 복장 요구사항 (3+1 보너스)",
                placeholder="4번째 의상에 대한 요구사항을 적어주세요.",
                required=True, style=discord.TextStyle.paragraph, max_length=500
            )
            self.add_item(self.fourth_style)
        elif self.bundle_type == "2+1 묶음":
            self.gfx_style = discord.ui.TextInput(
                label="📝 제작 순서별 상세 요구사항 (총 3개)",
                placeholder="1번, 2번, 3번 의상에 대한 요구사항을 작성해주세요.",
                required=True, style=discord.TextStyle.paragraph, max_length=1000
            )
            self.add_item(self.gfx_style)
            self.fourth_style = None
        else:
            self.gfx_style = discord.ui.TextInput(
                label="📝 원하는 스타일 및 설명",
                placeholder="원하시는 콘셉트, 색감, 디테일 등을 적어주세요.",
                required=True, style=discord.TextStyle.paragraph, max_length=500
            )
            self.add_item(self.gfx_style)
            self.fourth_style = None
