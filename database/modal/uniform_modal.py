import discord
from database.modal.gfx_modal import PurchaseModal


class UniformModal(PurchaseModal):
    COMMISSION_NAME = "Roblox 복장"

    def __init__(self, bundle_type: str = "단품 (1개)", selected_designer: int = None):
        discord.ui.Modal.__init__(self, title=f"👕 복장 커미션 신청서 [{bundle_type}]")
        self.bundle_type = bundle_type
        self.selected_designer = selected_designer

        # 1. [2+1 묶음] - 1~2번째 본품 + 3번째 보너스 분리
        if self.bundle_type == "2+1 묶음":
            self.gfx_style = discord.ui.TextInput(
                label="📝 1~2번째 복장 상세 요구사항",
                placeholder="1, 2번째 의상에 대한 요구사항을 적어주세요.",
                required=True, 
                style=discord.TextStyle.paragraph, 
                max_length=1000
            )
            self.add_item(self.gfx_style)

            self.fourth_style = discord.ui.TextInput(
                label="🎁 3번째 복장 요구사항 (2+1 보너스)",
                placeholder="3번째(보너스) 의상에 대한 요구사항을 적어주세요.",
                required=True, 
                style=discord.TextStyle.paragraph, 
                max_length=1000
            )
            self.add_item(self.fourth_style)

        # 2. [3+1 묶음] - 1~3번째 본품 + 4번째 보너스 분리
        elif self.bundle_type == "3+1 묶음":
            self.gfx_style = discord.ui.TextInput(
                label="📝 1~3번째 복장 상세 요구사항",
                placeholder="1, 2, 3번째 의상에 대한 요구사항을 적어주세요.",
                required=True, 
                style=discord.TextStyle.paragraph, 
                max_length=1000
            )
            self.add_item(self.gfx_style)

            self.fourth_style = discord.ui.TextInput(
                label="🎁 4번째 복장 요구사항 (3+1 보너스)",
                placeholder="4번째(보너스) 의상에 대한 요구사항을 적어주세요.",
                required=True, 
                style=discord.TextStyle.paragraph, 
                max_length=1000
            )
            self.add_item(self.fourth_style)

        # 3. [단품 (1개)] - 단일 입력칸
        else:
            self.gfx_style = discord.ui.TextInput(
                label="📝 원하는 스타일 및 설명",
                placeholder="원하시는 콘셉트, 색감, 디테일 등을 적어주세요.",
                required=True, 
                style=discord.TextStyle.paragraph, 
                max_length=1000
            )
            self.add_item(self.gfx_style)
            
            # 단품은 보너스 칸이 없으므로 None 처리
            self.fourth_style = None

        # 부모 클래스(PurchaseModal) 참조 에러 방지용 속성 초기화
        self.roblox_nickname = None
        self.gfx_genre = None
