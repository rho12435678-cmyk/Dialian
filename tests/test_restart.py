import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord.ext import commands

import dial


class RestartCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = MagicMock(restart_requested=False)
        self.client.close = AsyncMock()
        self.ctx = MagicMock()
        self.ctx.send = AsyncMock()
        self.ctx.guild = MagicMock()
        self.ctx.permissions = SimpleNamespace(administrator=True)
        self.patcher = patch.object(dial, "bot", self.client)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    async def test_admin_can_restart_after_notice(self):
        for check in dial.restart_bot.checks:
            self.assertTrue(await discord.utils.maybe_coroutine(check, self.ctx))
        events = []
        self.ctx.send.side_effect = lambda *a, **kw: events.append("notice")
        self.client.close.side_effect = lambda: events.append("close")
        await dial.restart_bot.callback(self.ctx)
        self.assertEqual(events, ["notice", "close"])
        self.assertTrue(self.client.restart_requested)

    async def test_regular_member_cannot_restart(self):
        self.ctx.permissions.administrator = False
        with self.assertRaises(commands.MissingPermissions):
            for check in dial.restart_bot.checks:
                await discord.utils.maybe_coroutine(check, self.ctx)
        self.client.close.assert_not_awaited()

    async def test_dm_cannot_restart(self):
        self.ctx.guild = None
        with self.assertRaises(commands.NoPrivateMessage):
            for check in dial.restart_bot.checks:
                await discord.utils.maybe_coroutine(check, self.ctx)
        self.client.close.assert_not_awaited()

    async def test_duplicate_request_does_not_close_twice(self):
        self.client.restart_requested = True
        await dial.restart_bot.callback(self.ctx)
        self.client.close.assert_not_awaited()

    async def test_notice_failure_keeps_bot_running(self):
        self.ctx.send.side_effect = RuntimeError("send failed")
        with self.assertRaises(RuntimeError):
            await dial.restart_bot.callback(self.ctx)
        self.assertFalse(self.client.restart_requested)
        self.client.close.assert_not_awaited()

    async def test_permission_error_is_not_silenced(self):
        await dial.on_command_error(self.ctx, commands.MissingPermissions(["administrator"]))
        self.ctx.send.assert_awaited_once()


class RestartRunnerTests(unittest.TestCase):
    def test_requested_restart_happens_after_run_returns_and_preserves_arguments(self):
        client = MagicMock(restart_requested=True)
        events = []
        client.run.side_effect = lambda *a: events.append("run returned")
        with patch.object(dial, "bot", client), patch.object(dial, "TOKEN", "test-token"), \
                patch.object(dial.sys, "executable", "C:/Python Env/python.exe"), \
                patch.object(dial.sys, "orig_argv", ["python", "-u", "C:/Bot Project/dial.py"]), \
                patch.object(dial.os, "execv") as execute:
            execute.side_effect = lambda *a: events.append("exec")
            dial.run_bot()
            execute.assert_called_once_with(
                "C:/Python Env/python.exe",
                ["C:/Python Env/python.exe", "-u", "C:/Bot Project/dial.py"],
            )
        self.assertEqual(events, ["run returned", "exec"])

    def test_normal_shutdown_does_not_restart(self):
        with patch.object(dial, "bot", MagicMock(restart_requested=False)), \
                patch.object(dial, "TOKEN", "test-token"), patch.object(dial.os, "execv") as execute:
            dial.run_bot()
            execute.assert_not_called()

    def test_login_failure_does_not_restart_loop(self):
        client = MagicMock(restart_requested=False)
        client.run.side_effect = RuntimeError("login failed")
        with patch.object(dial, "bot", client), patch.object(dial, "TOKEN", "test-token"), \
                patch.object(dial.os, "execv") as execute:
            with self.assertRaises(RuntimeError):
                dial.run_bot()
            execute.assert_not_called()

    def test_restart_aliases_are_registered(self):
        for name in ("재시작", "봇재시작", "restart"):
            self.assertIs(dial.bot.get_command(name), dial.restart_bot)


if __name__ == "__main__":
    unittest.main()
