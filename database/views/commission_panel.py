"""Three-button DDS enquiry panel and ephemeral submenus.

Legacy persistent callbacks keep already-posted eight-button panels responsive
until admins repost the panel with !티켓생성 and remove old messages.
"""
import discord
from discord import ui

from database.services.family import is_family_active
from database.services.price_board import build_price_embed
from database.services.ui_designer_role import ensure_ui_designer_role
from database.views.designer_select import DesignerView


def build_panel_views(dev_modal_class, partner_modal_class):
    """Inject existing dial.py application modals without circular imports."""

    async def open_designer_menu(interaction, category, label):
        await interaction.response.defer(ephemeral=True)
        view = await DesignerView.create(interaction.guild, category)
        embed = discord.Embed(
            title=f"{label} 디자이너 선택",
            description="담당 디자이너를 선택하거나 추후 배정을 요청하세요.",
            color=discord.Color.blurple(),
        )
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    async def open_ui_preview(interaction):
        # Defer before the DB and guild-role checks to avoid interaction timeout.
        await interaction.response.defer(ephemeral=True)
        if not await is_family_active(interaction.user):
            return await interaction.followup.send(
                "🔒 FAMILY 활성 회원만 UI 사전 체험을 신청할 수 있습니다.",
                ephemeral=True,
            )
        role = await ensure_ui_designer_role(interaction.guild)
        if role is None:
            return await interaction.followup.send(
                "⚠️ UI 디자이너 역할이 설정되지 않았습니다. 관리자에게 문의해 주세요.",
                ephemeral=True,
            )
        view = await DesignerView.create(interaction.guild, "ui")
        embed = discord.Embed(
            title="🖥️ UI 디자이너 선택 · FAMILY 전용",
            description="담당 UI 디자이너를 선택하거나 추후 배정을 요청하세요.",
            color=discord.Color.blurple(),
        )
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    async def show_price(interaction, profile):
        """Price boards are public reference material; eligibility is enforced at checkout.

        This lets new members compare all four tiers without granting any role,
        membership, or discount. The real commission quote still derives the
        applicable rate from the member's current FAMILY/regular eligibility.
        """
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(
            embed=build_price_embed(profile),
            ephemeral=True,
        )

    class CommissionMenuView(ui.View):
        def __init__(self):
            super().__init__(timeout=300)

        @ui.button(label="🎨 GFX 커미션", style=discord.ButtonStyle.primary, row=0)
        async def gfx(self, interaction: discord.Interaction, button: ui.Button):
            await open_designer_menu(interaction, "gfx", "🎨 GFX")

        @ui.button(label="👕 Roblox 복장", style=discord.ButtonStyle.success, row=0)
        async def uniform(self, interaction: discord.Interaction, button: ui.Button):
            await open_designer_menu(interaction, "uniform", "👕 Roblox 복장")

        @ui.button(label="🖥️ UI · FAMILY 사전 체험", style=discord.ButtonStyle.primary, row=1)
        async def ui_preview(self, interaction: discord.Interaction, button: ui.Button):
            await open_ui_preview(interaction)

    class SupportMenuView(ui.View):
        def __init__(self):
            super().__init__(timeout=300)

        @ui.button(label="💻 개발자 지원", style=discord.ButtonStyle.secondary)
        async def dev(self, interaction: discord.Interaction, button: ui.Button):
            await interaction.response.send_modal(dev_modal_class())

        @ui.button(label="🤝 파트너 문의", style=discord.ButtonStyle.success)
        async def partner(self, interaction: discord.Interaction, button: ui.Button):
            await interaction.response.send_modal(partner_modal_class())

    class PriceMenuView(ui.View):
        def __init__(self):
            super().__init__(timeout=300)

        @ui.button(label="📋 일반 가격표", style=discord.ButtonStyle.secondary, row=0)
        async def standard(self, interaction: discord.Interaction, button: ui.Button):
            await show_price(interaction, "standard")

        @ui.button(label="⭐ 단골 · 20%", style=discord.ButtonStyle.success, row=0)
        async def regular(self, interaction: discord.Interaction, button: ui.Button):
            await show_price(interaction, "regular")

        @ui.button(label="💎 FAMILY · 20%", style=discord.ButtonStyle.primary, row=1)
        async def family(self, interaction: discord.Interaction, button: ui.Button):
            await show_price(interaction, "family")

        @ui.button(label="✨ FAMILY + 단골 · 30%", style=discord.ButtonStyle.primary, row=1)
        async def combined(self, interaction: discord.Interaction, button: ui.Button):
            await show_price(interaction, "combined")

    class CategorySelectView(ui.View):
        """Only three buttons on the newly posted public panel."""
        def __init__(self):
            super().__init__(timeout=None)

        @ui.button(label="🎨 커미션 문의", style=discord.ButtonStyle.primary,
                   custom_id="dds_main_commissions_v2", row=0)
        async def commissions(self, interaction: discord.Interaction, button: ui.Button):
            embed = discord.Embed(
                title="🎨 디자인 커미션 선택",
                description=(
                    "원하시는 커미션 분야를 선택하세요.\n"
                    "🖥️ UI 사전 체험은 FAMILY 활성 회원 전용입니다."
                ),
                color=discord.Color.blurple(),
            )
            await interaction.response.send_message(
                embed=embed, view=CommissionMenuView(), ephemeral=True,
            )

        @ui.button(label="🤝 지원 & 제휴 문의", style=discord.ButtonStyle.success,
                   custom_id="dds_main_support_v2", row=0)
        async def support(self, interaction: discord.Interaction, button: ui.Button):
            embed = discord.Embed(
                title="🤝 지원 & 제휴 문의",
                description="개발자 지원 또는 파트너 문의를 선택하세요.",
                color=discord.Color.green(),
            )
            await interaction.response.send_message(
                embed=embed, view=SupportMenuView(), ephemeral=True,
            )

        @ui.button(label="📋 가격표", style=discord.ButtonStyle.secondary,
                   custom_id="dds_main_prices_v2", row=1)
        async def prices(self, interaction: discord.Interaction, button: ui.Button):
            embed = discord.Embed(
                title="📋 DDS 가격표 선택",
                description=(
                    "일반 · 단골 · FAMILY · FAMILY+단골 가격을 비교해 보세요.\n"
                    "※ 전용 할인은 실제 회원 역할과 이용 자격에 따라 적용됩니다."
                ),
                color=discord.Color.gold(),
            )
            await interaction.response.send_message(
                embed=embed, view=PriceMenuView(), ephemeral=True,
            )

    class LegacyCategorySelectView(ui.View):
        """Keep old 8-button public messages working after a restart/merge."""
        def __init__(self):
            super().__init__(timeout=None)

        @ui.button(label="🎨 GFX 커미션", style=discord.ButtonStyle.primary,
                   custom_id="ticket_gfx", row=0)
        async def gfx(self, interaction: discord.Interaction, button: ui.Button):
            await open_designer_menu(interaction, "gfx", "🎨 GFX")

        @ui.button(label="👔 Roblox 복장 커미션", style=discord.ButtonStyle.success,
                   custom_id="ticket_uniform", row=0)
        async def uniform(self, interaction: discord.Interaction, button: ui.Button):
            await open_designer_menu(interaction, "uniform", "👕 Roblox 복장")

        @ui.button(label="🖥️ UI 커미션 (FAMILY 사전 체험)",
                   style=discord.ButtonStyle.primary, custom_id="ticket_ui_preview", row=0)
        async def ui_preview(self, interaction: discord.Interaction, button: ui.Button):
            await open_ui_preview(interaction)

        @ui.button(label="💻 개발자 지원", style=discord.ButtonStyle.secondary,
                   custom_id="ticket_dev_apply", row=1)
        async def dev(self, interaction: discord.Interaction, button: ui.Button):
            await interaction.response.send_modal(dev_modal_class())

        @ui.button(label="🤝 파트너 문의", style=discord.ButtonStyle.danger,
                   custom_id="ticket_partner_apply", row=1)
        async def partner(self, interaction: discord.Interaction, button: ui.Button):
            await interaction.response.send_modal(partner_modal_class())

        @ui.button(label="📋 일반 / 묶음 가격표", style=discord.ButtonStyle.secondary,
                   custom_id="price_standard", row=2)
        async def standard(self, interaction: discord.Interaction, button: ui.Button):
            await show_price(interaction, "standard")

        @ui.button(label="⭐ 단골 전용 (20% 할인) 가격표", style=discord.ButtonStyle.success,
                   custom_id="price_vip", row=2)
        async def regular(self, interaction: discord.Interaction, button: ui.Button):
            await show_price(interaction, "regular")

        @ui.button(label="💎 FAMILY 전용 (20% 할인) 가격표",
                   style=discord.ButtonStyle.primary, custom_id="price_family", row=3)
        async def family(self, interaction: discord.Interaction, button: ui.Button):
            if any(
                role.id == REGULAR_CUSTOMER_ROLE_ID for role in interaction.user.roles
            ):
                # Previous FAMILY button dynamically showed the combined rate.
                return await show_price(interaction, "combined")
            await show_price(interaction, "family")

    return CategorySelectView, LegacyCategorySelectView
