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

    # 3초 타임아웃 방지를 위해 캐시된 멤버만 안전하게 가져옴 (fetch 제거)
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
    def __init__(self, category: str, options: list):
        self.category = category
        label = CATEGORY_LABELS[category]

        super().__init__(
            placeholder=f"담당 {label} 디자이너를 선택하세요.",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        # ⚠️ defer()를 사용하면 모달을 띄울 수 없으므로 제거했습니다.
        
        # 티켓 존재 여부 체크는 모달 내부나 가벼운 로직으로 처리해야 타임아웃을 피할 수 있습니다.
        selected_val = self.values[0]
        modal_class = MODALS[self.category]

        if selected_val == "none":
            modal = modal_class(bundle_type="단품 (1개)", selected_designer=None)
        else:
            selected_designer_id = int(selected_val)
            valid_designers = await get_role_designers(interaction.guild, self.category)
            valid_designer_ids = {member.id for member in valid_designers}

            if selected_designer_id not in valid_designer_ids:
                return await interaction.response.send_message(
                    "❌ 선택한 디자이너 권한이 변경되었습니다. 다시 선택해주세요.",
                    ephemeral=True
                )

            modal = modal_class(bundle_type="단품 (1개)", selected_designer=selected_designer_id)

        # ✅ 지연 없이 즉시 모달을 호출하여 "Didn't respond in time" 오류를 해결합니다.
        await interaction.response.send_modal(modal)


class DesignerView(discord.ui.View):
    def __init__(self, category: str, options: list):
        super().__init__(timeout=None)
        self.add_item(DesignerSelect(category, options))

    @classmethod
    async def create(cls, guild: discord.Guild, category: str):
        options = await get_designer_options(guild, category)
        return cls(category, options)
