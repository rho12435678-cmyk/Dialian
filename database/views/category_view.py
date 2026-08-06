import discord
from discord import ui
import aiosqlite
from datetime import datetime
from database.database import DATABASE


async def send_reference_guide(channel: discord.TextChannel, user: discord.User):
    """티켓 생성 시 참고자료 첨부 안내 전송"""
    embed = discord.Embed(
        title="🖼️ 참고 자료(이미지/파일) 첨부 안내",
        description=(
            f"{user.mention}님, 디자이너가 원하시는 스타일을 명확히 파악할 수 있도록\n"
            "**원하시는 구도, 분위기, 색감, 참고용 이미지/파일**을 이 채널에 올려주세요!"
        ),
        color=0x5865F2
    )
    embed.set_footer(text="참고 자료가 상세할수록 높은 완성도의 결과물이 나옵니다 ✨")
    await channel.send(embed=embed)


class CustomCommissionModal(ui.Modal):
    def __init__(self, category: str, bundle_type: str):
        super().__init__(title=f"🎨 {category} [{bundle_type}] 신청서")
        self.category = category
        self.bundle_type = bundle_type

        # Roblox 닉네임 필드
        self.roblox_name = ui.TextInput(
            label="🎮 Roblox 닉네임",
            placeholder="작품에 반영될 로블록스 닉네임을 작성해주세요.",
            required=True
        )
        self.add_item(self.roblox_name)

        # 묶음/단품별 양식
        if bundle_type == "단품 (1개)":
            self.details = ui.TextInput(
                label="🖌️ 원하는 스타일 및 설명",
                style=discord.TextStyle.paragraph,
                placeholder="원하시는 콘셉트, 구도, 색감, 의상 등을 적어주세요.",
                required=True,
                max_length=1000
            )
        else:
            count = 3 if "2+1" in bundle_type else 4
            self.details = ui.TextInput(
                label=f"📝 제작 순서별 상세 요구사항 (총 {count}개)",
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

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        channel_name = f"티켓-{interaction.user.name}"
        ticket_channel = await guild.create_text_channel(
            name=channel_name,
            topic=str(interaction.user.id)
        )

        embed = discord.Embed(
            title=f"📋 {self.category} 커미션 신청서 ({self.bundle_type})",
            color=0x57F287
        )
        embed.add_field(name="🎮 Roblox 닉네임", value=self.roblox_name.value, inline=False)
        embed.add_field(name="📌 요청 상세 내용", value=self.details.value, inline=False)

        await ticket_channel.send(content=interaction.user.mention, embed=embed)
        await send_reference_guide(ticket_channel, interaction.user)

        now = datetime.now().isoformat()
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute("""
                INSERT INTO commissions (ticket_channel, customer_id, category, status, progress, created_at, updated_at)
                VALUES (?, ?, ?, 'in_progress', 0, ?, ?)
            """, (ticket_channel.id, interaction.user.id, self.category, now, now))
            await db.commit()

        await interaction.followup.send(
            f"✅ 티켓 채널이 생성되었습니다: {ticket_channel.mention}",
            ephemeral=True
        )


class BundleSelectView(ui.View):
    def __init__(self, category: str):
        super().__init__(timeout=120)
        self.category = category

    @ui.button(label="1개 (단품)", style=discord.ButtonStyle.secondary, custom_id="bundle_single_btn")
    async def select_single(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(CustomCommissionModal(self.category, "단품 (1개)"))

    @ui.button(label="🎁 2+1 묶음 (총 3개)", style=discord.ButtonStyle.primary, custom_id="bundle_21_btn")
    async def select_21(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(CustomCommissionModal(self.category, "2+1 묶음"))

    @ui.button(label="🎁 3+1 묶음 (총 4개)", style=discord.ButtonStyle.success, custom_id="bundle_31_btn")
    async def select_31(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(CustomCommissionModal(self.category, "3+1 묶음"))


class CustomCategoryView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="🎨 GFX", style=discord.ButtonStyle.primary, custom_id="cat_gfx_btn")
    async def click_gfx(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message(
            "🎨 **GFX 구매 수량을 선택해주세요.**\n*(묶음 선택 시 우선순위 순서대로 제작이 진행됩니다)*",
            view=BundleSelectView("GFX"),
            ephemeral=True
        )

    @ui.button(label="👕 Roblox 복장", style=discord.ButtonStyle.primary, custom_id="cat_clothes_btn")
    async def click_clothes(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message(
            "👕 **Roblox 복장 구매 수량을 선택해주세요.**\n*(묶음 선택 시 우선순위 순서대로 제작이 진행됩니다)*",
            view=BundleSelectView("Roblox 복장"),
            ephemeral=True
        )

class CustomCategoryView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="🎨 GFX", style=discord.ButtonStyle.primary, custom_id="cat_gfx_btn")
    async def click_gfx(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message(
            "🎨 **GFX 구매 수량을 선택해주세요.**\n*(묶음 선택 시 우선순위 순서대로 제작이 진행됩니다)*",
            view=BundleSelectView("GFX"),
            ephemeral=True
        )

    @ui.button(label="👕 Roblox 복장", style=discord.ButtonStyle.primary, custom_id="cat_clothes_btn")
    async def click_clothes(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message(
            "👕 **Roblox 복장 구매 수량을 선택해주세요.**\n*(묶음 선택 시 우선순위 순서대로 제작이 진행됩니다)*",
            view=BundleSelectView("Roblox 복장"),
            ephemeral=True
        )

    @ui.button(label="💻 개발자 지원", style=discord.ButtonStyle.secondary, custom_id="cat_dev_btn")
    async def click_developer(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message(
            "💻 **개발자 지원 문의입니다.**\n담당자가 확인 후 답변드립니다.",
            ephemeral=True
        )


# ImportError 방지를 위한 클래스 별칭 추가
CategoryView = CustomCategoryView
