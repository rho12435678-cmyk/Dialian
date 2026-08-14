import discord

from config import DESIGNER_ROLE_IDS
from database.modal.gfx_modal import PurchaseModal
from database.modal.uniform_modal import UniformModal
from database.views.ticket_guard import block_if_ticket_exists


MODALS = {
    "gfx": PurchaseModal,
    "uniform": UniformModal,
}


CATEGORY_LABELS = {
    "gfx": "GFX",
    "uniform": "Roblox 복장",
}


async def get_role_designers(guild: discord.Guild, category: str):
    role_id = DESIGNER_ROLE_IDS.get(category)
    if not role_id:
        return []

    role = guild.get_role(role_id)
    if role is None:
        return []

    members = role.members if role else []

    return [
        member
        for member in members
        if not member.bot
    ]


async def get_designer_options(guild: discord.Guild, category: str):
    options = [
        discord.SelectOption(
            label="미지정 (추후 배정)",
            value="none",
            description="담당자를 나중에 배정받습니다.",
            emoji="👤"
        )
    ]

    designers = await get_role_designers(guild, category)
    for member in designers[:24]:
        options.append(
            discord.SelectOption(
                label=member.display_name,
                value=str(member.id),
                description=f"{CATEGORY_LABELS.get(category, '')} 담당 디자이너"
            )
        )

    return options


class DesignerSelect(discord.ui.Select):
    def __init__(self, category: str, bundle_type: str, options: list):
        self.category = category
        self.bundle_type = bundle_type
        label = CATEGORY_LABELS[category]

        super().__init__(
            placeholder=f"담당 {label} 디자이너를 선택하세요.",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        selected_val = self.values[0]
        modal_class = MODALS[self.category]

        if selected_val == "none":
            modal = modal_class(bundle_type=self.bundle_type, selected_designer=None)
        else:
            selected_designer_id = int(selected_val)
            valid_designers = await get_role_designers(interaction.guild, self.category)
            valid_designer_ids = {member.id for member in valid_designers}

            if selected_designer_id not in valid_designer_ids:
                return await interaction.response.send_message(
                    "❌ 선택한 디자이너 권한이 변경되었습니다. 다시 선택해주세요.",
                    ephemeral=True
                )

            modal = modal_class(bundle_type=self.bundle_type, selected_designer=selected_designer_id)

        await interaction.response.send_modal(modal)


class DesignerView(discord.ui.View):
    def __init__(self, category: str, bundle_type: str, options: list):
        super().__init__(timeout=None)
        self.add_item(DesignerSelect(category, bundle_type, options))

    @classmethod
    async def create(cls, guild: discord.Guild, category: str, bundle_type: str = "단품 (1개)"):
        """메인 파일(dial.py)에서 호출할 수 있도록 비동기 생성 메서드 추가"""
        options = await get_designer_options(guild, category)
        return cls(category, bundle_type, options)


class QuantitySelect(discord.ui.Select):
    def __init__(self, category: str):
        self.category = category
        super().__init__(
            placeholder="제작하실 수량(상품 유형)을 선택해주세요.",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="단품 (1개)", value="단품 (1개)", description="기본 단품 제작"),
                discord.SelectOption(label="세트 / 패키지", value="세트 / 패키지", description="할인 패키지 상품"),
            ]
        )

    async def callback(self, interaction: discord.Interaction):
        # ⚠️ 3초 타임아웃 방지를 위한 응답 지연 처리
        await interaction.response.defer(ephemeral=True)

        bundle_type = self.values[0]
        options = await get_designer_options(interaction.guild, self.category)
        
        view = DesignerView(self.category, bundle_type, options)
        
        # followup을 이용해 안전하게 메시지 수정
        await interaction.followup.edit_message(
            message_id=interaction.message.id,
            content=f"선택하신 상품 유형: **{bundle_type}**\n담당 디자이너를 선택해주세요.",
            view=view
        )


class QuantitySelectView(discord.ui.View):
    def __init__(self, category: str):
        super().__init__(timeout=None)
        self.add_item(QuantitySelect(category))
