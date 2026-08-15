import discord
from config import DESIGNER_ROLE_IDS
from database.modal.gfx_modal import PurchaseModal
from database.modal.uniform_modal import UniformModal

# 모달 매핑
MODALS = {
    "gfx": PurchaseModal,
    "uniform": UniformModal,
}

# 카테고리 라벨
CATEGORY_LABELS = {
    "gfx": "GFX",
    "uniform": "Roblox 복장",
}


async def get_role_designers(guild: discord.Guild, category: str):
    """해당 카테고리의 역할(Role)을 가진 봇이 아닌 멤버 리스트 반환"""
    role_id = DESIGNER_ROLE_IDS.get(category)
    if not role_id:
        return []

    role = guild.get_role(role_id)
    if role is None:
        return []

    members = role.members if role else []
    return [member for member in members if not member.bot]


async def get_designer_options(guild: discord.Guild, category: str):
    """디자이너 드롭다운 선택 옵션 생성"""
    options = [
        discord.SelectOption(
            label="미지정 (추후 배정)",
            value="none",
            description="담당자를 나중에 배정받습니다.",
            emoji="👤"
        )
    ]

    designers = await get_role_designers(guild, category)
    for member in designers[:24]:  # 디스코드 옵션 제한(25개) 고려
        clean_name = member.display_name.strip("{}") 
        options.append(
            discord.SelectOption(
                label=clean_name if clean_name else member.name,
                value=str(member.id),
                description=f"{CATEGORY_LABELS.get(category, '')} 담당 디자이너"
            )
        )

    return options


# 1. 디자이너 선택 드롭다운 & 뷰 (1단계: 디자이너 고르고 -> 수량 선택으로)
class DesignerSelect(discord.ui.Select):
    def __init__(self, category: str, options: list):
        self.category = category
        label = CATEGORY_LABELS.get(category, "디자이너")

        super().__init__(
            placeholder=f"담당 {label} 디자이너를 선택하세요.",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        selected_val = self.values[0]

        if selected_val == "none":
            selected_designer_id = None
        else:
            selected_designer_id = int(selected_val)
            valid_designers = await get_role_designers(interaction.guild, self.category)
            valid_designer_ids = {member.id for member in valid_designers}

            if selected_designer_id not in valid_designer_ids:
                return await interaction.response.send_message(
                    "❌ 선택한 디자이너 권한이 변경되었습니다. 다시 선택해주세요.",
                    ephemeral=True
                )

        # 디자이너 선택 후 -> '수량 선택 뷰(QuantitySelectView)'로 전환!
        view = QuantitySelectView(self.category, selected_designer_id)
        embed = discord.Embed(
            title="🛒 제작 수량 선택",
            description="원하시는 제작 수량(상품 유형)을 선택해 주세요.",
            color=0x5865F2
        )
        await interaction.response.edit_message(content=None, embed=embed, view=view)


class DesignerView(discord.ui.View):
    def __init__(self, category: str, options: list):
        super().__init__(timeout=None)
        self.add_item(DesignerSelect(category, options))

    @classmethod
    async def create(cls, guild: discord.Guild, category: str):
        options = await get_designer_options(guild, category)
        return cls(category, options)


# 2. 수량/상품유형 선택 드롭다운 & 뷰 (2단계: 수량 고르고 -> Modal 팝업)
class QuantitySelect(discord.ui.Select):
    def __init__(self, category: str, selected_designer: int = None):
        self.category = category
        self.selected_designer = selected_designer
        super().__init__(
            placeholder="제작하실 수량(상품 유형)을 선택해주세요.",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="단품 (1개)", value="단품 (1개)", description="기본 단품 제작"),
                discord.SelectOption(label="2+1 묶음", value="2+1 묶음", description="2개 구매 시 1개 추가 제공"),
                discord.SelectOption(label="3+1 묶음", value="3+1 묶음", description="3개 구매 시 1개 추가 제공"),
            ]
        )

    async def callback(self, interaction: discord.Interaction):
        bundle_type = self.values[0]
        modal_class = MODALS.get(self.category)

        if not modal_class:
            return await interaction.response.send_message("❌ 올바르지 않은 카테고리입니다.", ephemeral=True)

        # 수량까지 완벽히 선택했으므로 신청서 Modal 띄우기!
        modal = modal_class(bundle_type=bundle_type, selected_designer=self.selected_designer)
        await interaction.response.send_modal(modal)


class QuantitySelectView(discord.ui.View):
    def __init__(self, category: str, selected_designer: int = None):
        super().__init__(timeout=None)
        self.add_item(QuantitySelect(category, selected_designer))
