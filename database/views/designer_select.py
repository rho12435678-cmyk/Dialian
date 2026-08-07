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


def get_role_designers(guild: discord.Guild, category: str):
    role_id = DESIGNER_ROLE_IDS.get(category)

    if not role_id:
        return []

    role = guild.get_role(role_id)

    if role is None:
        return []

    return [
        member
        for member in role.members
        if not member.bot
    ]


def get_designer_options(guild: discord.Guild, category: str):
    # 맨 첫 번째 옵션으로 미지정 항목 추가
    options = [
        discord.SelectOption(
            label="미지정 (추후 배정)",
            value="none",
            description="담당자를 나중에 배정받습니다.",
            emoji="👤"
        )
    ]

    # 서버 디자이너 목록 추가 (최대 24명까지)
    designers = get_role_designers(guild, category)[:24]
    for member in designers:
        options.append(
            discord.SelectOption(
                label=member.display_name,
                value=str(member.id),
                description=f"{CATEGORY_LABELS.get(category, '')} 담당 디자이너"
            )
        )

    return options


class DesignerSelect(discord.ui.Select):

    def __init__(self, guild: discord.Guild, category: str):
        self.category = category

        label = CATEGORY_LABELS[category]

        super().__init__(
            placeholder=f"담당 {label} 디자이너를 선택하세요.",
            min_values=1,
            max_values=1,
            options=get_designer_options(guild, category)
        )

    async def callback(self, interaction: discord.Interaction):
        if await block_if_ticket_exists(interaction):
            return

        selected_val = self.values[0]

        modal = MODALS[self.category]()

        # '미지정'을 선택한 경우
        if selected_val == "none":
            modal.selected_designer = None
        else:
            selected_designer_id = int(selected_val)
            valid_designer_ids = {
                member.id
                for member in get_role_designers(interaction.guild, self.category)
            }

            if selected_designer_id not in valid_designer_ids:
                return await interaction.response.send_message(
                    "❌ 선택한 디자이너 권한이 변경되었습니다. 다시 선택해주세요.",
                    ephemeral=True
                )

            modal.selected_designer = selected_designer_id

        await interaction.response.send_modal(modal)


class DesignerView(discord.ui.View):

    def __init__(self, guild: discord.Guild, category: str):
        super().__init__(timeout=180)

        self.add_item(DesignerSelect(guild, category))
