import discord
from config import DESIGNER_ROLE_IDS
from database.modal.gfx_modal import PurchaseModal
from database.modal.uniform_modal import UniformModal
from database.modal.simple_ticket_modal import SimpleTicketModal  # 파트너 문의 모달 import

# ==========================================
# 개발자 지원 모달 (Modal)
# ==========================================
class DeveloperApplyModal(discord.ui.Modal, title="💻 개발자 지원 신청서"):
    portfolio = discord.ui.TextInput(
        label="포트폴리오 링크 또는 경력",
        style=discord.TextStyle.paragraph,
        placeholder="포트폴리오 링크나 간단한 개발 경력을 작성해 주세요.",
        required=True,
        max_length=1000
    )
    introduction = discord.ui.TextInput(
        label="자기소개 및 지원 동기",
        style=discord.TextStyle.paragraph,
        placeholder="자기소개와 각오를 적어주세요.",
        required=0
    )

    async def on_submit(self, interaction: discord.Interaction):
        # 지원서를 수신할 채널 ID (관리자 채널)
        APPLY_CHANNEL_ID = 1537806140711239760  # 실제 관리자 채널 ID로 변경 필수

        channel = interaction.guild.get_channel(APPLY_CHANNEL_ID)
        
        embed = discord.Embed(
            title="📩 새로운 개발자 지원서 접수",
            color=discord.Color.green(),
            timestamp=interaction.created_at
        )
        embed.add_field(name="지원자", value=f"{interaction.user.mention} (`{interaction.user.id}`)", inline=False)
        embed.add_field(name="포트폴리오 / 경력", value=self.portfolio.value, inline=False)
        embed.add_field(name="자기소개 및 동기", value=self.introduction.value, inline=False)

        if channel:
            await channel.send(embed=embed)
            await interaction.response.send_message("✅ 개발자 지원서가 성공적으로 접수되었습니다!", ephemeral=True)
        else:
            await interaction.response.send_message("✅ 지원서 작성이 완료되었습니다. (관리자 수신 채널 설정 필요)", ephemeral=True)


# ==========================================
# 담당 디자이너 선택 드롭다운 (Dynamic UI)
# ==========================================
class DesignerSelect(discord.ui.Select):
    def __init__(self, category: str, bundle_type: str, guild: discord.Guild):
        self.category = category
        self.bundle_type = bundle_type

        options = [
            discord.SelectOption(
                label="미지정 (추후 배정)", 
                value="none", 
                description="담당자를 나중에 배정받습니다."
            )
        ]

        role_key = "gfx" if category == "GFX" else "uniform"
        role_id = DESIGNER_ROLE_IDS.get(role_key)

        if guild and role_id:
            role = guild.get_role(role_id)
            if role:
                for member in role.members:
                    options.append(
                        discord.SelectOption(
                            label=member.display_name,
                            value=str(member.id),
                            description=f"{category} 담당 디자이너"
                        )
                    )

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
        self.add_item(DesignerSelect(category, bundle_type, guild))


# ==========================================
# 묶음(수량) 선택 뷰
# ==========================================
class BundleSelectView(discord.ui.View):
    def __init__(self, category: str):
        super().__init__(timeout=120)
        self.category = category

    async def prompt_designer(self, interaction: discord.Interaction, bundle_type: str):
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


# ==========================================
# 최종 메인 카테고리 선택 뷰
# ==========================================
class CategoryView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None) # Persistent View

    @discord.ui.button(label="🎨 GFX 커미션", style=discord.ButtonStyle.primary, custom_id="cat_gfx_btn")
    async def select_gfx(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="📦 **GFX 커미션** - 원하시는 수량(묶음)을 선택해주세요.", view=BundleSelectView(category="GFX"))

    @discord.ui.button(label="👕 복장 커미션", style=discord.ButtonStyle.secondary, custom_id="cat_uniform_btn")
    async def select_uniform(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="📦 **복장 커미션** - 원하시는 수량(묶음)을 선택해주세요.", view=BundleSelectView(category="복장"))

    @discord.ui.button(label="💻 개발자 지원", style=discord.ButtonStyle.success, custom_id="cat_dev_apply_btn")
    async def select_dev_apply(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(DeveloperApplyModal())

    # [요구사항 4] 4번째 파트너 문의 버튼 추가
    @discord.ui.button(label="🤝 파트너 문의", style=discord.ButtonStyle.secondary, custom_id="cat_partner_btn")
    async def select_partner(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 파트너 문의 전용 모달 호출 (SimpleTicketModal 사용)
        await interaction.response.send_modal(SimpleTicketModal(ticket_type="파트너 문의"))
