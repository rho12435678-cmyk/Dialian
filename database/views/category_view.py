import discord
from discord import ui
import aiosqlite
from database.database import DATABASE

# ==================== [티켓 오픈 시 안내/진행/참고자료 임베드 동시 전송 함수] ====================
async def send_ticket_guides(channel: discord.TextChannel, user: discord.User):
    # 1. 안내 사항 임베드
    guide_embed = discord.Embed(
        title="📌 커미션 안내 사항",
        description=(
            "**기본 안내**\n"
            "1. 가격 협상(네고) 안됨\n"
            "2. 작업 중 과도한 수정요청 삼가\n"
            "3. 모든 커미션은 선 결제 후 작업을 원칙으로 함\n"
            "4. 커미션 중 철회 시 수수료 부담\n\n"
            "**철회 수수료**\n"
            "작업 전 철회 : 전액 환불\n\n"
            "작업 후 철회 :\n"
            "상급 : 3,000원\n"
            "중급 : 2,000원\n"
            "초급 : 1,500원\n\n"
            "복장 커미션 : 1,500원"
        ),
        color=0xFEE75C
    )

    # 2. 커미션 진행 임베드
    progress_embed = discord.Embed(
        title="📌 커미션 진행",
        description=(
            "👨‍💻 담당 디자이너 : 미배정\n\n"
            "📌 상태 : 🟢 상담중\n"
            "📊 진행률 : 0%\n"
            "⏰ 예상 완료 : 작업 시작 전"
        ),
        color=0x5865F2
    )

    # 3. 참고 자료 안내 임베드
    ref_embed = discord.Embed(
        title="🖼️ 참고 자료(이미지/파일) 첨부 안내",
        description=(
            f"{user.mention}님, 디자이너가 원하시는 스타일을 명확히 파악할 수 있도록\n"
            "**원하시는 구도, 분위기, 색감, 참고용 이미지/파일**을 이 채널에 구체적으로 올려주세요!"
        ),
        color=0x5865F2
    )
    ref_embed.set_footer(text="참고 자료가 상세할수록 높은 완성도의 결과물이 나옵니다 ✨")

    # 세 개의 임베드를 한 번에 전송
    await channel.send(embeds=[guide_embed, progress_embed, ref_embed])


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

        # 묶음 타입별 상세 요구사항 입력란 분기
        self.fourth_details = None
        if bundle_type == "단품 (1개)":
            self.details = ui.TextInput(
                label="🖌️ 원하는 스타일 및 설명",
                style=discord.TextStyle.paragraph,
                placeholder="원하시는 콘셉트, 구도, 색감, 의상 등을 적어주세요.",
                required=True,
                max_length=1000
            )
            self.add_item(self.details)
        elif bundle_type == "2+1 묶음":
            self.details = ui.TextInput(
                label="📝 제작 순서별 상세 요구사항 (총 3개)",
                style=discord.TextStyle.paragraph,
                placeholder=(
                    "우선적으로 제작되길 원하는 순서대로 작성해주세요:\n"
                    "1번째 작품: (의상/구도/콘셉트)\n"
                    "2번째 작품: (의상/구도/콘셉트)\n"
                    "3번째 작품: (의상/구도/콘셉트)"
                ),
                required=True,
                max_length=1000
            )
            self.add_item(self.details)
        else: # 3+1 묶음
            self.details = ui.TextInput(
                label="📝 1~3번째 작품 상세 요구사항",
                style=discord.TextStyle.paragraph,
                placeholder="1, 2, 3번째 작품에 대한 상세 요구사항을 적어주세요.",
                required=True,
                max_length=1000
            )
            self.add_item(self.details)

            # 3+1 묶음일 때 4번째 작품 입력란 분리 추가
            self.fourth_details = ui.TextInput(
                label="🎁 4번째 작품 요구사항 (3+1 보너스)",
                style=discord.TextStyle.paragraph,
                placeholder="4번째 작품에 대한 요구사항이나 추가 설명을 적어주세요.",
                required=True,
                max_length=1000
            )
            self.add_item(self.fourth_details)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        channel_name = f"티켓-{interaction.user.name}"
        
        # 권한 설정: 일반 유저 차단, 유저/봇/디자이너 허용
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

        # 3+1 묶음일 경우 4번째 작품 내용 추가 표시
        if self.fourth_details:
            embed.add_field(name="🎁 4번째 작품 요구사항", value=self.fourth_details.value, inline=False)

        # 티켓 채널에 유저 멘션과 함께 신청서 임베드 전송
        await ticket_channel.send(content=interaction.user.mention, embed=embed)
        
        # 안내/진행/참고자료 임베드 3종 동시 전송 함수 실행
        await send_ticket_guides(ticket_channel, interaction.user)

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
        self.category = category if 'category' in locals() else None # 안전장치
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
