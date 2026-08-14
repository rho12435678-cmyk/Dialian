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

    # 캐시된 멤버가 없거나 부족할 경우 안전하게 fetch 수행
    members = role.members
    if not members:
        try:
            members = [m async for m in guild.fetch_members(limit=None) if role in m.roles]
        except Exception:
            members = []

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
        # 1. 3초 타임아웃 방지를 위해 가장 먼저 응답 지연(defer) 처리
        await interaction.response.defer(ephemeral=True)

        if await block_if_ticket_exists(interaction):
            return

        selected_val = self.values[0]
        
        # 2. 모달 생성 시 인자 누락 방지를 위해 클래스 초기화 시 인자 전달
        modal_class = MODALS[self.category]

        if selected_val == "none":
            modal = modal_class(bundle_type="단품 (1개)", selected_designer=None)
        else:
            selected_designer_id = int(selected_val)
            valid_designers = await get_role_designers(interaction.guild, self.category)
            valid_designer_ids = {member.id for member in valid_designers}

            if selected_designer_id not in valid_designer_ids:
                return await interaction.followup.send(
                    "❌ 선택한 디자이너 권한이 변경되었습니다. 다시 선택해주세요.",
                    ephemeral=True
                )

            modal = modal_class(bundle_type="단품 (1개)", selected_designer=selected_designer_id)

        # 3. 모달은 interaction.response.send_modal로 띄워야 하므로, 
        # 이미 defer를 쓴 경우 send_modal 대신 다른 방식으로 모달을 열 수 없으므로 구조를 아래와 같이 조정합니다.
