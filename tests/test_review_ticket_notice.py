"""Regressions for same-ticket review confirmation and safe close UI."""
import unittest
from types import SimpleNamespace

import discord

from database.views.review_view import build_review_ticket_notice


class ReviewTicketNoticeTests(unittest.TestCase):
    def test_all_commission_categories_show_stars_and_safe_close(self):
        customer = SimpleNamespace(mention="<@111>")
        for category in ("GFX", "Roblox 복장", "UI"):
            with self.subTest(category=category):
                embed, view = build_review_ticket_notice(
                    4, category, "2+1 묶음", customer, 222,
                    "https://discord.com/channels/10/20/30",
                )
                self.assertIn("등록 완료", embed.title)
                self.assertIn(category, str(embed.fields[3].value))
                self.assertIn("⭐⭐⭐⭐", str(embed.fields[2].value))
                self.assertEqual(embed.fields[1].value, "<@222>")
                ids = [getattr(item, "custom_id", None) for item in view.children]
                self.assertIn("close_ticket", ids)
                self.assertNotIn("delete_ticket", ids)
                self.assertTrue(any(
                    isinstance(item, discord.ui.Button)
                    and item.url == "https://discord.com/channels/10/20/30"
                    for item in view.children
                ))

    def test_missing_designer_does_not_mention_someone_else(self):
        embed, view = build_review_ticket_notice(
            5, "GFX", "단품 (1개)", SimpleNamespace(mention="<@111>"),
            None, "https://discord.com/channels/10/20/30",
        )
        self.assertEqual(embed.fields[1].value, "미지정")
        self.assertNotIn("delete_ticket", [
            getattr(item, "custom_id", None) for item in view.children
        ])


if __name__ == "__main__":
    unittest.main()
