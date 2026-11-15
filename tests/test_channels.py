import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord

from ddh.channels import fetch_supported_channels
from fakes import Guild, model_data


class ChannelCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    def guild_with_response(self, payload):
        guild = Guild()
        guild.fetch_channels = AsyncMock(side_effect=discord.InvalidData("Unknown channel type 20"))
        guild._state = Mock()
        guild._state.http.get_all_guild_channels = AsyncMock(return_value=payload)
        return guild

    async def test_real_models_survive_unknown_type_without_using_cache(self):
        source = Guild()
        text = model_data(source, "문의", 0, topic="DDH ticket:1 owner:3 kind:developer")
        forum = model_data(source, "새-포럼", 15, topic="recovery marker")
        guild = self.guild_with_response([text, {"id": "1570874679114342479", "type": 20}, forum])
        # Reproduce the actual pinned library failure before exercising fallback.
        with self.assertRaises(discord.InvalidData):
            await discord.Guild.fetch_channels(guild)
        channels = await fetch_supported_channels(guild)
        self.assertEqual([c.id for c in channels], [int(text["id"]), int(forum["id"])])
        self.assertIsInstance(channels[0], discord.TextChannel)
        self.assertIsInstance(channels[1], discord.ForumChannel)
        self.assertEqual(channels[1].topic, "recovery marker")
        self.assertNotIn(channels[1], guild.channels)  # latest REST, absent from gateway cache

    async def test_http_failure_is_not_an_empty_list(self):
        guild = self.guild_with_response([])
        error = discord.HTTPException(SimpleNamespace(status=503, reason="Unavailable"), "retry")
        guild._state.http.get_all_guild_channels.side_effect = error
        with self.assertRaises(discord.HTTPException):
            await fetch_supported_channels(guild)
        guild.fetch_channels.side_effect = error
        guild._state.http.get_all_guild_channels.reset_mock()
        with self.assertRaises(discord.HTTPException):
            await fetch_supported_channels(guild)
        guild._state.http.get_all_guild_channels.assert_not_awaited()

    async def test_malformed_known_model_is_not_silently_skipped(self):
        guild = self.guild_with_response([{"id": "10", "type": 0}])
        with self.assertRaises(KeyError):
            await fetch_supported_channels(guild)
