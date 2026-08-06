import discord
from database.modal.gfx_modal import PurchaseModal
from database.modal.uniform_modal import UniformModal

class DesignerSelect(discord.ui.Select):
    def __init__(self, category: str, bundle_type: str):
        self.category = category
        self.bundle_type = bundle_type
        
        # 실제 서버의 디자이너 목록이나 역할에 맞춰 수정해주세요.
        options = [
            discord.SelectOption(label="미지정 (추후 배정)", value="none", description="담당자를 나중에 배정받습니다."),
            discord.SelectOption(label="ParkSun", value="123456789012345678", description="GFX / 3D 전문"), 
            discord.SelectOption(label="Dial", value="987654321098765432", description="복장 / 2D 전문")
        ]
        super().__init__(placeholder="👨‍💻 담당 디자이너를 선택해주세요", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        designer_id = None if self.values[0] == "none" else int(self.values[0])
        
        # 선택된 카테고리에 맞춰 올바른 모달을 호출합니다. (Discord API 제약상 모달은 여기서 띄워야 합니다)
        if self.category == "GFX":
            modal = PurchaseModal(bundle_type=self.bundle_type, selected_designer=designer_id)
        else:
            modal = UniformModal(bundle_type=self.bundle_type, selected_designer=designer_id)
            
        await interaction.response.send_modal(modal)

class DesignerSelectView(discord.ui.View):
    def __init__(self, category: str, bundle_type: str):
        super().__init__(timeout=120)
        self.add_item(DesignerSelect(category, bundle_type))

class BundleSelectView(discord.ui.View):
    def __init__(self, category: str):
        super().__init__(timeout=120)
        self.category = category

    async def prompt_designer(self, interaction: discord.Interaction, bundle_type: str):
        # 묶음을 선택하면, 디자이너 선택 드롭다운이 포함된 View로 메시지를 업데이트합니다.
        view = DesignerSelectView(self.category, bundle_type)
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
