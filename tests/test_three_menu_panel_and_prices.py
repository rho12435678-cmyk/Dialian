"""Three-button panel, old-panel compatibility and uniform variation price tests."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from config import REGULAR_CUSTOMER_ROLE_ID
from database.services.price_board import (
    PRICE_PROFILES, build_price_embed, variation_price,
)
from database.services.commission_pricing import UNIFORM_VARIATION_BASE
from database.views import commission_panel


class PriceBoardTests(unittest.TestCase):
    def test_four_profiles_have_consistent_discounts(self):
        self.assertEqual(
            list(PRICE_PROFILES), ["standard", "regular", "family", "combined"]
        )
        self.assertEqual(UNIFORM_VARIATION_BASE, 500)
        self.assertEqual(
            [variation_price(profile) for profile in PRICE_PROFILES],
            [500, 400, 400, 350],
        )

    def test_readable_mobile_layout_and_exact_amounts(self):
        for profile, mid_price in [
            ("standard", 6500),
            ("regular", 5200),
            ("family", 5200),
            ("combined", 4550),
        ]:
            with self.subTest(profile=profile):
                embed = build_price_embed(profile)
                self.assertLessEqual(len(embed.fields), 25)
                self.assertIn("단품: 1개", embed.description)
                mid_field = next(f for f in embed.fields if "중급 GFX" in f.name)
                self.assertIn(f"{mid_price:,}원", mid_field.value)
                self.assertIn("2+1 묶음", mid_field.value)
                self.assertIn("3+1 묶음", mid_field.value)
                variation = next(f for f in embed.fields if "바리에이션" in f.name)
                self.assertIn(f"{variation_price(profile):,}원", variation.value)
                self.assertIn("별도", variation.value)
                ui_field = next(f for f in embed.fields if "UI" in f.name)
                self.assertEqual("사전 커미션" in ui_field.name,
                                 profile in ("family", "combined"))
                self.assertIn("회원", embed.footer.text)

    def test_combined_not_40_percent_and_ui_exact_30_percent(self):
        combined = build_price_embed("combined")
        self.assertIn("30%", combined.title)
        self.assertIn("40%", combined.description)
        ui_field = next(f for f in combined.fields if "UI" in f.name)
        self.assertIn("3,500원", ui_field.value)
        self.assertIn("7,000원", ui_field.value)
        self.assertIn("10,500원", ui_field.value)

    def test_regular_not_mistaken_for_unrestricted_discount(self):
        normal = build_price_embed("standard")
        regular = build_price_embed("regular")
        self.assertIn("기본", normal.description)
        self.assertIn("단골 역할", regular.description)


class PanelViewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        class DummyDevModal:
            pass

        class DummyPartnerModal:
            pass

        self.MainView, self.LegacyView = commission_panel.build_panel_views(
            DummyDevModal, DummyPartnerModal,
        )

    @staticmethod
    def click(view, label):
        return next(b for b in view.children if b.label == label)

    @staticmethod
    def interaction(roles=()):
        return SimpleNamespace(
            guild=SimpleNamespace(),
            user=SimpleNamespace(
                roles=[SimpleNamespace(id=role_id) for role_id in roles]
            ),
            response=SimpleNamespace(
                send_message=AsyncMock(),
                defer=AsyncMock(),
                send_modal=AsyncMock(),
            ),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def test_only_three_public_buttons_and_legacy_ids_still_registered(self):
        root = self.MainView()
        self.assertEqual(
            [b.label for b in root.children],
            ["🎨 커미션 문의", "🤝 지원 & 제휴 문의", "📋 가격표"],
        )
        self.assertIsNone(root.timeout)
        old = self.LegacyView()
        self.assertEqual(
            {b.custom_id for b in old.children},
            {
                "ticket_gfx", "ticket_uniform", "ticket_ui_preview",
                "ticket_dev_apply", "ticket_partner_apply",
                "price_standard", "price_vip", "price_family",
            },
        )
        self.assertIsNone(old.timeout)
        main_ids = {b.custom_id for b in root.children}
        self.assertFalse(main_ids & {b.custom_id for b in old.children})
        source = (Path(__file__).resolve().parents[1] / "dial.py").read_text("utf8")
        self.assertIn("self.add_view(LegacyCategorySelectView())", source)

    async def test_group_menus_have_three_two_and_four_options(self):
        root = self.MainView()
        expected = [
            ("🎨 커미션 문의", ["🎨 GFX 커미션", "👕 Roblox 복장",
                                   "🖥️ UI · FAMILY 사전 체험"]),
            ("🤝 지원 & 제휴 문의", ["💻 개발자 지원", "🤝 파트너 문의"]),
            ("📋 가격표", ["📋 일반 가격표", "⭐ 단골 · 20%",
                                "💎 FAMILY · 20%", "✨ FAMILY + 단골 · 30%"]),
        ]
        for main_label, submenu_labels in expected:
            with self.subTest(main_label=main_label):
                interaction = self.interaction()
                await self.click(root, main_label).callback(interaction)
                interaction.response.send_message.assert_awaited_once()
                kwargs = interaction.response.send_message.await_args.kwargs
                self.assertTrue(kwargs["ephemeral"])
                sub = kwargs["view"]
                self.assertEqual([b.label for b in sub.children], submenu_labels)
                self.assertEqual(sub.timeout, 300)

    async def test_prices_are_presentation_only_until_eligibility_is_checked(self):
        interaction = self.interaction()
        root = self.MainView()
        await self.click(root, "📋 가격표").callback(interaction)
        sub = interaction.response.send_message.await_args.kwargs["view"]
        await self.click(sub, "📋 일반 가격표").callback(interaction)
        self.assertIn("일반", interaction.followup.send.await_args.kwargs["embed"].title)

    async def test_family_30_requires_both_eligible_memberships(self):
        root = self.MainView()
        interaction = self.interaction()
        await self.click(root, "📋 가격표").callback(interaction)
        sub = interaction.response.send_message.await_args.kwargs["view"]

        with patch.object(
            commission_panel, "is_family_active", new=AsyncMock(return_value=False)
        ):
            blocked = self.interaction(roles=[REGULAR_CUSTOMER_ROLE_ID])
            await self.click(sub, "✨ FAMILY + 단골 · 30%").callback(blocked)
            self.assertIn("FAMILY", blocked.followup.send.await_args.args[0])
            self.assertNotIn("embed", blocked.followup.send.await_args.kwargs)

        with patch.object(
            commission_panel, "is_family_active", new=AsyncMock(return_value=True)
        ):
            no_regular = self.interaction()
            await self.click(sub, "✨ FAMILY + 단골 · 30%").callback(no_regular)
            self.assertIn("단골", no_regular.followup.send.await_args.args[0])

            allowed = self.interaction(roles=[REGULAR_CUSTOMER_ROLE_ID])
            await self.click(sub, "✨ FAMILY + 단골 · 30%").callback(allowed)
            self.assertIn("30%", allowed.followup.send.await_args.kwargs["embed"].title)

    async def test_ui_menu_still_checks_family(self):
        root = self.MainView()
        interaction = self.interaction()
        await self.click(root, "🎨 커미션 문의").callback(interaction)
        sub = interaction.response.send_message.await_args.kwargs["view"]
        with patch.object(
            commission_panel, "is_family_active", new=AsyncMock(return_value=False)
        ):
            blocked = self.interaction()
            await self.click(sub, "🖥️ UI · FAMILY 사전 체험").callback(blocked)
            self.assertIn("FAMILY", blocked.followup.send.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
