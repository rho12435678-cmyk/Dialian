"""Compact and consistent ticket introduction regression coverage."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from database.ticket_notice import build_ticket_notice_embed
from database.services import commission_pricing
from database.modal.ui_modal import UI_BASE


class CompactTicketGuideTests(unittest.TestCase):
    def test_single_mobile_guide_for_all_three_categories(self):
        quote = {
            "base": 8500, "rate": 20, "total": 6800,
            "family": True, "regular": False,
        }
        cases = (
            ("GFX", "상급 3,000원", "구도"),
            ("Roblox 복장", "1,500원", "의상"),
            ("Roblox UI 사전 체험", "사전 확인", "UI 화면"),
        )
        for category, cancellation, reference in cases:
            with self.subTest(category=category):
                embed = build_ticket_notice_embed(category, quote)
                self.assertEqual(len(embed.fields), 3)
                self.assertIn("진행 순서", embed.description)
                self.assertIn("6,800원", embed.fields[0].value)
                self.assertIn("최종 확인", embed.fields[0].value)
                self.assertIn(reference, embed.fields[1].value)
                self.assertIn(cancellation, embed.fields[2].value)
                self.assertNotIn("@", embed.description)

    def test_unassigned_and_uniform_variation(self):
        unassigned = build_ticket_notice_embed("GFX")
        self.assertIn("배정", unassigned.fields[0].value)
        uniform = build_ticket_notice_embed(
            "Roblox 복장", {"base": 5000, "rate": 30, "total": 3500},
            variation_unit=350,
        )
        self.assertIn("3,500원", uniform.fields[0].value)
        self.assertIn("350원", uniform.fields[0].value)

    def test_modal_ticket_creation_uses_same_guide_and_no_ui_double_quote(self):
        root = Path(__file__).resolve().parents[1] / "database" / "modal"
        for name in ("gfx_modal.py", "uniform_modal.py"):
            source = (root / name).read_text("utf-8")
            self.assertIn("embed=build_ticket_notice_embed(", source)
            self.assertEqual(source.count("await ticket_channel.send(\n                embed=build_ticket_notice_embed("), 1)
            self.assertNotIn("ref_embed =", source)
        ui = (root / "ui_modal.py").read_text("utf-8")
        self.assertNotIn("await channel.send(", ui)
        self.assertIn("return await super().create_ticket(interaction)", ui)


class UIQuoteRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_ui_quote_respects_membership_and_combined_discount(self):
        self.assertEqual(list(UI_BASE.values()), [5000, 10000, 15000])
        member = SimpleNamespace(roles=[
            SimpleNamespace(id=commission_pricing.REGULAR_CUSTOMER_ROLE_ID)
        ])
        with patch.object(
            commission_pricing, "is_family_active",
            new=AsyncMock(return_value=True),
        ):
            quote = await commission_pricing.quote_for(
                "Roblox UI 사전 체험", "단품 (1개)", member
            )
            self.assertEqual(quote["rate"], 30)
            self.assertEqual(quote["total"], 3500)
        with patch.object(
            commission_pricing, "is_family_active",
            new=AsyncMock(return_value=False),
        ):
            self.assertIsNone(await commission_pricing.quote_for(
                "Roblox UI 사전 체험", "단품 (1개)", member
            ))


if __name__ == "__main__":
    unittest.main()
