import discord
from discord import ui
import aiosqlite
from database.database import DATABASE

# ==================== [참고자료 안내 헬퍼 함수] ====================
async def send_reference_guide(channel: discord.TextChannel, user: discord.User):
    embed = discord.Embed(
        title="🖼️ 참고 자료(이미지/파일) 첨부 안내",
        description=(
            f"{user.mention}님, 디자이너가 원하시는 스타일을 명확히 파악할 수 있도록\n"
            "**원하시는 구도, 분위기, 색감, 참고용 이미지/파일**을 이 채널에 구체적으로 올려주세요!"
        ),
        color=0x5865F2
    )
    embed.set_footer(text="참고 자료가 상세할수록 높은 완성도의 결과물이 나옵니다 ✨")
    await channel.send(embed=embed)


# ==================== [커미션 신청 모달] ====================
class CustomCommissionModal(ui.Modal):
    def __init__(self, category: str, bundle_type: str, designer_id: int = None):
        title_prefix = f"🎨 {category}" if category == "GFX" else f"👕 {category}"
        super().__init__(title=f"{title_prefix} [{bundle_type}] 신청서")
        self.category = category
        self.bundle_type = bundle_type
        self.designer_id = designer_id

        self.roblox_name = ui.TextInput(
            label="🎮 Roblox 닉네임",
            placeholder="작품에 반영될 로블록스 닉네임을 작성해주세요.",
            required=True
        )
        self.add_item(self.roblox_name)

        # GFX 카테고리일 경우에만 장르(종류) 입력 칸 추가
        self.gfx_genre = None
        if category == "GFX":
            self.gfx_genre = ui.TextInput(
                label="🏷️ GFX 장르 / 종류",
                placeholder="예: 초급, 중급, 상급 등 원하시는 장르나 스타일을 적어주세요.",
                required=True,
                max_length=100
            )
            self.add_item(self.gfx_genre)

        if bundle_type == "단품 (1개)":
            self.details = ui.TextInput(
                label="🖌️ 원하는 스타일 및 설명",
                style=discord.TextStyle.paragraph,
                placeholder="원하시는 콘셉트, 구도, 색감, 의상 등을 적어주세요.",
                required=True,
                max_length=1000
            )
        else:
            self.details = ui.TextInput(
                label="📝 제작 순서별 상세 요구사항",
                style=discord.TextStyle.paragraph,
                placeholder=(
                    "우선적으로 제작되길 원하는 순서대로 작성해주세요:\n"
                    "1번째 작품: (의상/구도/콘셉트)\n"
                    "2번째 작품: (의상/구도/콘셉트)"
                ),
                required=True,
                max_length=1000
            )
        self.add_item(self.details)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        channel_name = f"티켓-{interaction.user.name}"
        
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        }
        
        if self.designer_id:
            designer_member = guild.get_member(self.designer_id)
            if designer_member:
                overwrites[designer_member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

        ticket_channel = await guild.create_text_channel(
            name=channel_name,
            topic=str(interaction.user.id),
            overwrites=overwrites
        )

        embed = discord.Embed(
            title=f"📋 {self.category} 커미션 신청서 ({self.bundle_type})",
            color=0x57F287
        )
        embed.add_field(name="🎮 Roblox 닉네임", value=self.roblox_name.value, inline=False)
        
        if self.gfx_genre:
            embed.add_field(name="🏷️ GFX 장르", value=self.gfx_genre.value, inline=False)
            
        if self.designer_id:
            embed.add_field(name="👨‍💻 담당 디자이너", value=f"<@{self.designer_id}>", inline=False)
            
        embed.add_field(name="📌 요청 상세 내용", value=self.details.value, inline=False)

        await ticket_channel.send(content=interaction.user.mention, embed=embed)
        await send_reference_guide(ticket_channel, interaction.user)

        now_iso = discord.utils.utcnow().isoformat()
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute("""
                INSERT INTO commissions (ticket_channel, customer_id, designer_id, category, status, progress, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'in_progress', 0, ?, ?)
            """, (ticket_channel.id, interaction.user.id, self.designer_id, self.category, now_iso, now_iso))
            await db.commit()

        await interaction.followup.send(
            f"✅ 티켓 채널이 생성되었습니다: {ticket_channel.mention}",
            ephemeral=True
        )


# ==================== [수량/묶음 선택 뷰] ====================
class BundleSelectView(ui.View):
    def __init__(self, category: str, designer_id: int = None):
        super().__init__(timeout=120)
        self.category = category
        self.designer_id = designer_id

    @ui.button(label="1개 (단품)", style=discord.ButtonStyle.secondary, custom_id="bundle_single_btn")
    async def select_single(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(CustomCommissionModal(self.category, "단품 (1개)", self.designer_id))

    @ui.button(label="🎁 2+1 묶음 (총 3개)", style=discord.ButtonStyle.primary, custom_id="bundle_21_btn")
    async def select_21(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(CustomCommissionModal(self.category, "2+1 묶음", self.designer_id))

    @ui.button(label="🎁 3+1 묶음 (총 4개)", style=discord.ButtonStyle.success, custom_id="bundle_31_btn")
    async def select_31(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(CustomCommissionModal(self.category, "3+1 묶음", self.designer_id))


# ==================== [카테고리 선택 뷰] ====================
class CategorySelectView(ui.View):
    def __init__(self, designer_id: int = None):
        super().__init__(timeout=120)
        self.designer_id = designer_id

    @ui.button(label="🎨 GFX", style=discord.ButtonStyle.primary, custom_id="cat_gfx_btn")
    async def click_gfx(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message(
            "🎨 **GFX 구매 수량을 선택해주세요.**",
            view=BundleSelectView("GFX", self.designer_id),
            ephemeral=True
        )

    @ui.button(label="👕 Roblox 복장", style=discord.ButtonStyle.primary, custom_id="cat_clothes_btn")
    async def click_clothes(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message(
            "👕 **Roblox 복장 구매 수량을 선택해주세요.**",
            view=BundleSelectView("Roblox 복장", self.designer_id),
            ephemeral=True
        )


# ==================== [디자이너 선택 드롭다운 (메인)] ====================
class DesignerSelect(ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="랜덤 / 지정 안 함", value="random", description="가장 빠른 디자이너에게 배정됩니다."),
        ]
        super().__init__(placeholder="👨‍💻 원하는 담당 디자이너를 선택해주세요!", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        designer_id = None if self.values[0] == "random" else int(self.values[0])
        
        await interaction.response.send_message(
            "✨ 원하시는 **커미션 카테고리**를 선택해주세요.",
            view=CategorySelectView(designer_id),
            ephemeral=True
        )


class CategoryView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(DesignerSelect())
