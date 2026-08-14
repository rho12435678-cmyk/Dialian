import discord
from config import DESIGNER_ROLE_IDS
from database.modal.gfx_modal import PurchaseModal
from database.modal.uniform_modal import UniformModal
from database.views.close_ticket import TicketCloseView  # 티켓 관리 버튼(티켓닫기 등) Import

# ==========================================
# 1. 개발자 지원 모달 (제출 시 티켓 채널 생성)
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
        required=False,
        max_length=1000
    )

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        user = interaction.user

        # 개인 전용 지원 티켓 채널 권한 설정
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True, embed_links=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
        }

        # 지원 티켓 채널 생성
        clean_username = user.name.lower().replace(" ", "-")
        channel_name = f"지원-{clean_username}"
        channel = await guild.create_text_channel(
            name=channel_name,
            topic=f"💻 개발자 지원 티켓 | 신청자: {user.name} ({user.id})",
            overwrites=overwrites
        )

        # 지원 신청서 임베드 작성
        embed = discord.Embed(
            title="📩 새로운 개발자 지원서 접수",
            color=discord.Color.blue(),
            timestamp=interaction.created_at
        )
        embed.add_field(name="지원자", value=f"{user.mention} (`{user.id}`)", inline=False)
        embed.add_field(name="포트폴리오 / 경력", value=self.portfolio.value, inline=False)
        embed.add_field(
            name="자기소개 및 동기", 
            value=self.introduction.value if self.introduction.value else "작성 안 함", 
            inline=False
        )
        embed.set_footer(text="담당 관리자가 확인 후 답변드릴 예정입니다.")

        # 생성된 채널에 임베드 및 티켓 닫기 버튼 전송
        await channel.send(content=f"{user.mention} 님의 개발자 지원 티켓이 생성되었습니다.", embed=embed, view=TicketCloseView())
        
        # 유저에게 응답 (ephemeral)
        await interaction.response.send_message(f"✅ 개발자 지원서가 접수되었습니다! 생성된 채널: {channel.mention}", ephemeral=True)


# ==========================================
# 2. 파트너 문의 모달 (제출 시 티켓 채널 생성)
# ==========================================
class PartnerApplyModal(discord.ui.Modal, title="🤝 파트너 문의 신청서"):
    partner_name = discord.ui.TextInput(
        label="파트너(서버/브랜드) 이름",
        placeholder="서버 이름 또는 브랜드명을 입력해주세요.",
        required=True,
        max_length=100
    )
    details = discord.ui.TextInput(
        label="제안 내용 및 조건",
        style=discord.TextStyle.paragraph,
        placeholder="협업 제안 내용이나 상세 조건을 적어주세요.",
        required=True,
        max_length=1000
    )

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        user = interaction.user

        # 개인 전용 파트너 티켓 채널 권한 설정
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True, embed_links=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
        }

        # 파트너 티켓 채널 생성
        clean_username = user.name.lower().replace(" ", "-")
        channel_name = f"파트너-{clean_username}"
        channel = await guild.create_text_channel(
            name=channel_name,
            topic=f"🤝 파트너 문의 티켓 | 신청자: {user.name} ({user.id})",
            overwrites=overwrites
        )

        # 파트너 문의 임베드 작성
        embed = discord.Embed(
            title="🤝 새로운 파트너 문의 접수",
            color=discord.Color.purple(),
            timestamp=interaction.created_at
        )
        embed.add_field(name="신청자", value=f"{user.mention} (`{user.id}`)", inline=False)
        embed.add_field(name="파트너/브랜드명", value=self.partner_name.value, inline=False)
        embed.add_field(name="제안 및 협업 내용", value=self.details.value, inline=False)
        embed.set_footer(text="담당자가 확인 후 답변드릴 예정입니다.")

        # 생성된 채널에 임베드 및 티켓 닫기 버튼 전송
        await channel.send(content=f"{user.mention} 님의 파트너 문의 티켓이 생성되었습니다.", embed=embed, view=TicketCloseView())

        # 유저에게 응답 (ephemeral)
        await interaction.response.send_message(f"✅ 파트너 문의가 접수되었습니다! 생성된 채널: {channel.mention}", ephemeral=True)


# ==========================================
# 3. 담당 디자이너 선택 드롭다운 (Dynamic UI)
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
# 4. 묶음(수량) 선택 뷰
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
# 5. 최종 메인 카테고리 선택 뷰 (4개 버튼)
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

    @discord.ui.button(label="🤝 파트너 문의", style=discord.ButtonStyle.secondary, custom_id="cat_partner_btn")
    async def select_partner(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PartnerApplyModal())
