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
        # 1. 중복 티켓 검사
        if await block_if_ticket_exists(interaction):
            return

        selected_val = self.values[0]
        
        # 2. 선택된 디자이너 ID 매핑 (미지정인 경우 None)
        selected_designer_id = None if selected_val == "none" else int(selected_val)

        # 3. 유효성 검사 (선택된 디자이너가 실제 해당 역할을 가지고 있는지 확인)
        if selected_designer_id is not None:
            valid_designers = await get_role_designers(interaction.guild, self.category)
            valid_designer_ids = {member.id for member in valid_designers}

            if selected_designer_id not in valid_designer_ids:
                return await interaction.response.send_message(
                    "❌ 선택한 디자이너 권한이 변경되었습니다. 다시 선택해주세요.",
                    ephemeral=True
                )

        # 4. 모달 클래스를 가져와서 bundle_type과 selected_designer를 인자로 전달하여 생성
        modal_class = MODALS[self.category]
        modal = modal_class(bundle_type="단품 (1개)", selected_designer=selected_designer_id)

        # 5. 타임아웃이 발생하기 전에 즉시 모달 호출
        await interaction.response.send_modal(modal)


class DesignerView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

    @classmethod
    async def create(cls, guild: discord.Guild, category: str):
        view = cls()
        options = await get_designer_options(guild, category)
        view.add_item(DesignerSelect(category, options))
        return view
