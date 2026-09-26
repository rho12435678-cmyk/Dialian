"""DDS UI designer role provisioning and FAMILY-only designer selection tests."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiosqlite

from config import DESIGNER_ROLE_IDS
from database.services import ui_designer_role
from database.views.designer_select import MODALS, get_designer_options
from database.modal.ui_modal import UIPreviewModal


class DesignerRoleIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path=str(Path(temp.name) / "ui-roles.db")
        p=patch.object(ui_designer_role, "DATABASE", self.path)
        p.start()
        self.addCleanup(p.stop)
        mapping=patch.dict(DESIGNER_ROLE_IDS, {}, clear=True)
        mapping.start()
        self.addCleanup(mapping.stop)
        async with aiosqlite.connect(self.path) as db:
            await db.execute("CREATE TABLE bot_settings(key TEXT PRIMARY KEY, value TEXT)")
            await db.commit()
        self.created_role=SimpleNamespace(
            id=123456,name=ui_designer_role.UI_DESIGNER_ROLE_NAME,
            members=[SimpleNamespace(id=345,display_name="DesignerA",name="DesignerA",bot=False)]
        )
        self.guild=SimpleNamespace(
            id=222,
            roles=[],
            me=SimpleNamespace(guild_permissions=SimpleNamespace(manage_roles=True)),
            get_role=lambda role_id: next((r for r in self.guild.roles if r.id == role_id),None),
        )
        async def create_role(**kwargs):
            self.guild.roles.append(self.created_role)
            return self.created_role
        self.guild.create_role=AsyncMock(side_effect=create_role)

    async def test_creates_once_and_persists_stable_role_id(self):
        role=await ui_designer_role.ensure_ui_designer_role(self.guild)
        again=await ui_designer_role.ensure_ui_designer_role(self.guild)
        self.assertEqual(role.id,123456)
        self.assertIs(role,again)
        self.guild.create_role.assert_awaited_once()
        self.assertEqual(DESIGNER_ROLE_IDS["ui"],123456)
        self.assertIs(MODALS["ui"],UIPreviewModal)
        opts=await get_designer_options(self.guild,"ui")
        self.assertEqual([option.value for option in opts],["none","345"])
        self.assertFalse(self.guild.create_role.await_args.kwargs["mentionable"])
        self.assertFalse(self.guild.create_role.await_args.kwargs["hoist"])
        async with aiosqlite.connect(self.path) as db:
            async with db.execute("SELECT value FROM bot_settings WHERE key=?",
                                  ("ui_designer_role_id:222",)) as cursor:
                self.assertEqual((await cursor.fetchone())[0],"123456")

    async def test_existing_named_role_is_reused_without_creation(self):
        self.guild.roles=[self.created_role]
        role=await ui_designer_role.ensure_ui_designer_role(self.guild)
        self.assertIs(role,self.created_role)
        self.guild.create_role.assert_not_awaited()

    async def test_no_manage_roles_returns_none_without_creation(self):
        self.guild.me.guild_permissions.manage_roles=False
        role=await ui_designer_role.ensure_ui_designer_role(self.guild)
        self.assertIsNone(role)
        self.guild.create_role.assert_not_awaited()
        self.assertNotIn("ui",DESIGNER_ROLE_IDS)

    async def test_preview_form_retains_selected_designer(self):
        form=UIPreviewModal("3+1 묶음",selected_designer=345)
        self.assertEqual(form.selected_designer,345)
        self.assertLessEqual(len(form.children),5)


if __name__=="__main__":
    unittest.main()
