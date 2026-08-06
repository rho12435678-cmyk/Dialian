import discord
from config import DESIGNER_ROLE_IDS  # config.py에서 역할 ID 불러오기
from database.modal.gfx_modal import PurchaseModal
from database.modal.uniform_modal import UniformModal

class DesignerSelect(discord.ui.Select):
    def __init__(self, category: str, bundle_type: str, guild: discord.Guild):
        self.category = category
        self.bundle_type = bundle_type

        # 기본 옵션: 미지정
        options = [
            discord.SelectOption(
                label="미지정 (추후 배정)", 
                value="none", 
                description="담당자를 나중에 배정받습니다."
            )
        ]

        # 카테고리에 맞는 역할 ID 추출 ("GFX" -> "gfx", "복장" -> "uniform")
        role_key = "gfx" if category == "GFX" else "uniform"
        role_id = DESIGNER_ROLE_IDS.get(role_key)

        # 서버에서 해당 역할을 가진 멤버들을 찾아서 드롭다운 옵션에 동적 추가
        if guild and role_id:
            role = guild.get_role(role_id)
            if role:
                for member in role.members:
                    options.append(
                        discord.SelectOption(
                            label=member.display_name,  # 서버 닉네임
                            value=str(member.id),       # 유저 ID
                            description=f"{category} 담당 디자이너"
                        )
                    )

        # Discord UI Select는 최대 25개까지 지원
        super().__init__(
            placeholder="👨‍💻 담당 디자이너를 선택해주세요", 
            options=options[:25], 
            min_values=1, 
            max_values=1
        )

    async def callback(self, interaction: discord.Interaction):
        designer_id = None if self.values[0] == "none" else int(self.values[0])
        
        if self.category == "GFX":
            modal = PurchaseModal(bundle_type=self.bundle_type, selected_designer=designer_id)
        else:
            modal = UniformModal(bundle_type=self.bundle_type, selected_designer=designer_id)
            
        await interaction.response.send_modal(modal)


class DesignerSelectView(discord.ui.View):
    def __init__(self, category: str, bundle_type: str, guild: discord.Guild):
        super().__init__(timeout=120)
        # guild 정보 전달
        self.add_item(DesignerSelect(category, bundle_type, guild))


class BundleSelectView(discord.ui.View):
    def __init__(self, category: str):
        super().__init__(timeout=120)
        self.category = category

    async def prompt_designer(self, interaction: discord.Interaction, bundle_type: str):
        # interaction.guild를 전달하여 서버 멤버 데이터를 실시간으로 가져옵니다.
        view = DesignerSelectView(self.category, bundle_type, interaction.guild)
        await interaction.response.edit_message(
            content=f"👨‍💻 **{self.category} [{bundle_type}]** - 작업을 진행할 담당 디자이너를 선택해주세요.",
            view=view
        )

    @discord.ui.button(label="1개 (단품)", style=discord.ButtonStyle.secondary, custom_id="bundle_single_btn")
    async def select_single(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.prompt_designer(interaction, "단품 (1개)")

    @discord.ui.button(label="🎁 2+1 묶음 (총 3개)", style=discord.ButtonStyle.primary, custom_id="bundle_21_btn")
    async def select_21(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.prompt_designer(interaction, "2+1 묶음")

    @discord.ui.button(label="🎁 3+1 묶음 (총 4개)", style=discord.ButtonStyle.success, custom_id="bundle_31_btn")
    async def select_31(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.prompt_designer(interaction, "3+1 묶음")
