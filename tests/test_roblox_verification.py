import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import discord

from database.services import roblox_verification as service
from database.views import verify_view as views


class VerificationStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = service.VerificationStore(str(Path(self.temp.name) / "test.db"))
        async with aiosqlite.connect(self.store.path) as db:
            await service.create_verification_tables(db)
            await db.commit()
        self.clock = patch.object(service.time, "time", return_value=100000).start()
        self.addCleanup(patch.stopall)
        self.profile = service.RobloxProfile(123, "TestUser")
        self.fetch = patch.object(service, "fetch_profile", new_callable=AsyncMock).start()
        self.eligibility = patch.object(service, "check_eligibility", new_callable=AsyncMock).start()

    async def issue(self, guild=1, user=2):
        code = await self.store.issue(guild, user, self.profile)
        self.fetch.return_value = service.RobloxProfile(123, "TestUser", f"About me\n{code}\n")
        return code

    async def test_success_persists_across_instances_and_consumes_code(self):
        await self.issue()
        restarted = service.VerificationStore(self.store.path)
        profile = await restarted.verify(1, 2)
        self.assertEqual(profile.name, "TestUser")
        self.assertEqual(await self.store.get_link(1, 2), (123, "TestUser"))
        with self.assertRaises(service.VerificationError):
            await self.store.verify(1, 2)

    async def test_missing_proof_does_not_link_and_can_retry(self):
        code = await self.issue()
        self.fetch.return_value = self.profile
        with self.assertRaises(service.VerificationError):
            await self.store.verify(1, 2)
        self.assertIsNone(await self.store.get_link(1, 2))
        self.clock.return_value += 11
        self.fetch.return_value = service.RobloxProfile(123, "TestUser", code)
        await self.store.verify(1, 2)

    async def test_expired_code_is_rejected_without_api_request(self):
        await self.issue()
        self.clock.return_value += service.CHALLENGE_TTL
        with self.assertRaises(service.VerificationError):
            await self.store.verify(1, 2)
        self.fetch.assert_not_awaited()

    async def test_code_is_bound_to_discord_user_and_guild(self):
        await self.issue()
        for guild, user in ((1, 3), (9, 2)):
            with self.assertRaises(service.VerificationError):
                await self.store.verify(guild, user)
        self.fetch.assert_not_awaited()

    async def test_another_users_public_code_cannot_be_copied(self):
        first = await self.issue()
        await self.store.issue(1, 3, self.profile)
        self.fetch.return_value = service.RobloxProfile(123, "TestUser", first)
        with self.assertRaises(service.VerificationError):
            await self.store.verify(1, 3)
        self.assertIsNone(await self.store.get_link(1, 3))

    async def test_reissue_invalidates_previous_code(self):
        old = await self.issue()
        self.clock.return_value += 11
        new = await self.issue()
        self.assertNotEqual(old, new)
        self.fetch.return_value = service.RobloxProfile(123, "TestUser", old)
        with self.assertRaises(service.VerificationError):
            await self.store.verify(1, 2)

    async def test_replacement_during_api_request_cannot_commit_old_proof(self):
        old = await self.issue()

        async def replaced(_):
            self.clock.return_value += 11
            await self.store.issue(1, 2, self.profile)
            return service.RobloxProfile(123, "TestUser", old)

        self.fetch.side_effect = replaced
        with self.assertRaises(service.VerificationError):
            await self.store.verify(1, 2)
        self.assertIsNone(await self.store.get_link(1, 2))

    async def test_expiry_during_api_request_is_rejected(self):
        code = await self.issue()

        async def expired(_):
            self.clock.return_value += service.CHALLENGE_TTL
            return service.RobloxProfile(123, "TestUser", code)

        self.fetch.side_effect = expired
        with self.assertRaises(service.VerificationError):
            await self.store.verify(1, 2)

    async def test_one_roblox_account_per_guild_even_with_pending_challenges(self):
        first = await self.issue()
        second = await self.issue(user=3)
        self.fetch.return_value = service.RobloxProfile(123, "TestUser", f"{first}\n{second}")
        results = await asyncio.gather(
            self.store.verify(1, 2), self.store.verify(1, 3), return_exceptions=True,
        )
        self.assertEqual(sum(isinstance(r, service.RobloxProfile) for r in results), 1)
        self.assertEqual(sum(isinstance(r, service.VerificationError) for r in results), 1)
        with self.assertRaises(service.VerificationError):
            await self.store.issue(1, 4, self.profile)

    async def test_separate_guilds_can_link_same_roblox_account(self):
        await self.issue()
        await self.store.verify(1, 2)
        await self.issue(guild=5, user=6)
        await self.store.verify(5, 6)
        self.assertEqual(await self.store.get_link(5, 6), (123, "TestUser"))

    async def test_issue_and_verify_cooldowns(self):
        await self.issue()
        with self.assertRaises(service.VerificationError):
            await self.issue()
        self.fetch.return_value = self.profile
        with self.assertRaises(service.VerificationError):
            await self.store.verify(1, 2)
        with self.assertRaises(service.VerificationError):
            await self.store.verify(1, 2)
        self.assertEqual(self.fetch.await_count, 1)

    async def test_failed_account_change_preserves_existing_link(self):
        await self.issue()
        await self.store.verify(1, 2)
        await self.store.issue(1, 2, service.RobloxProfile(456, "OtherUser"))
        self.fetch.return_value = service.RobloxProfile(456, "OtherUser", "")
        with self.assertRaises(service.VerificationError):
            await self.store.verify(1, 2)
        self.assertEqual(await self.store.get_link(1, 2), (123, "TestUser"))

    async def test_ineligible_account_cannot_commit_even_with_valid_proof(self):
        await self.issue()
        self.eligibility.side_effect = service.VerificationError("ineligible")
        with self.assertRaises(service.VerificationError):
            await self.store.verify(1, 2)
        self.assertIsNone(await self.store.get_link(1, 2))

    async def test_refresh_rechecks_policy_and_updates_saved_username(self):
        await self.issue()
        await self.store.verify(1, 2)
        self.fetch.return_value = service.RobloxProfile(123, "NewName")
        self.eligibility.reset_mock()
        await self.store.refresh(1, 2)
        self.eligibility.assert_awaited_once_with(self.fetch.return_value)
        self.assertEqual(await self.store.get_link(1, 2), (123, "NewName"))

    async def test_failed_refresh_preserves_existing_record(self):
        await self.issue()
        await self.store.verify(1, 2)
        self.fetch.return_value = service.RobloxProfile(123, "NewName")
        self.eligibility.side_effect = service.VerificationError("ineligible")
        with self.assertRaises(service.VerificationError):
            await self.store.refresh(1, 2)
        self.assertEqual(await self.store.get_link(1, 2), (123, "TestUser"))


class RobloxApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_username_lookup_and_normalization(self):
        with patch.object(service, "api_request", new_callable=AsyncMock) as api:
            api.return_value = {"data": [{"id": 123, "name": "TestUser"}]}
            self.assertEqual((await service.lookup_username(" @TestUser ")).id, 123)
            self.assertEqual(api.call_args.kwargs["json"]["usernames"], ["TestUser"])

    async def test_invalid_username_makes_no_request(self):
        with patch.object(service, "api_request", new_callable=AsyncMock) as api:
            for username in ("", "Display Name", "https://roblox.com", "a" * 21):
                with self.assertRaises(service.VerificationError):
                    await service.lookup_username(username)
            api.assert_not_awaited()

    async def test_missing_banned_or_malformed_accounts(self):
        with patch.object(service, "api_request", new_callable=AsyncMock) as api:
            for data in ({"data": []}, [], {"data": [{"id": "123", "name": "Name"}]},
                         {"data": [{"id": 123, "name": "Name", "isBanned": True}]}):
                api.return_value = data
                with self.assertRaises(service.VerificationError):
                    await service.lookup_username("TestUser")

    async def test_http_errors_become_user_facing_errors(self):
        with patch.object(service.aiohttp, "ClientSession") as client:
            session = client.return_value.__aenter__.return_value
            session.request = MagicMock()
            response = session.request.return_value.__aenter__.return_value
            for status in (429, 404, 500):
                response.status = status
                with self.assertRaises(service.VerificationError):
                    await service.api_request("GET", "/users/123")

    async def test_timeout_is_retryable(self):
        with patch.object(service.aiohttp, "ClientSession") as client:
            client.return_value.__aenter__.side_effect = asyncio.TimeoutError()
            with self.assertRaises(service.VerificationError):
                await service.api_request("GET", "/users/123")

    async def test_catalog_csrf_retry_is_bounded_and_anonymous(self):
        with patch.object(service.aiohttp, "ClientSession") as client:
            session = client.return_value.__aenter__.return_value
            session.request = MagicMock()
            denied = MagicMock(status=403, headers={"x-csrf-token": "test-csrf"})
            success = MagicMock(status=200)
            success.json = AsyncMock(return_value={"data": []})
            session.request.return_value.__aenter__.side_effect = [denied, success]
            await service.api_request("POST", "/catalog/items/details", base=service.CATALOG_API_BASE)
            self.assertEqual(session.request.call_count, 2)
            self.assertEqual(session.request.call_args.kwargs["headers"], {"x-csrf-token": "test-csrf"})
            session.request.reset_mock()
            session.request.return_value.__aenter__.side_effect = [denied, denied]
            with self.assertRaises(service.VerificationError):
                await service.api_request("POST", "/catalog/items/details", base=service.CATALOG_API_BASE)
            self.assertEqual(session.request.call_count, 2)


class EligibilityTests(unittest.IsolatedAsyncioTestCase):
    def profile(self, created_at):
        return service.RobloxProfile(123, "TestUser", created_at=created_at)

    async def test_exact_thirty_day_boundary(self):
        now = datetime(2026, 9, 24, tzinfo=timezone.utc)
        with patch.object(service.time, "time", return_value=now.timestamp()):
            service.check_account_age(self.profile(now - timedelta(days=30)))
            with self.assertRaises(service.VerificationError):
                service.check_account_age(self.profile(now - timedelta(days=30) + timedelta(seconds=1)))
            with self.assertRaises(service.VerificationError):
                service.check_account_age(self.profile(now + timedelta(days=1)))

    async def test_missing_and_invalid_creation_dates_fail_closed(self):
        for value in (None, datetime(2020, 1, 1)):
            with self.assertRaises(service.VerificationError):
                service.check_account_age(self.profile(value))
        for value in (None, "invalid", "2020-01-01", 123):
            with self.assertRaises(service.VerificationError):
                service.parse_profile({"id": 123, "name": "TestUser", "created": value})

    async def test_created_timezone_is_normalized(self):
        p = service.parse_profile({"id": 123, "name": "TestUser", "created": "2020-01-01T09:00:00+09:00"})
        self.assertEqual(p.created_at, datetime(2020, 1, 1, tzinfo=timezone.utc))

    async def test_eligibility_checks_only_account_age(self):
        old = self.profile(datetime(2020, 1, 1, tzinfo=timezone.utc))
        self.assertIsNone(await service.check_eligibility(old))

        young = self.profile(datetime.now(timezone.utc))
        with self.assertRaises(service.VerificationError):
            await service.check_eligibility(young)

    async def test_avatar_price_helpers_are_not_part_of_verification(self):
        self.assertFalse(hasattr(service, "MIN_AVATAR_ROBUX"))
        self.assertFalse(hasattr(service, "avatar_sale_total"))
        self.assertFalse(hasattr(service, "current_item_price"))



class VerificationViewTests(unittest.IsolatedAsyncioTestCase):
    def interaction(self):
        interaction = MagicMock()
        interaction.guild_id = 1
        interaction.user = MagicMock(spec=discord.Member)
        interaction.user.id = 2
        interaction.user.top_role = 2
        interaction.user.roles = []
        interaction.guild.owner_id = 99
        interaction.guild.me.top_role = 10
        interaction.guild.me.guild_permissions.manage_nicknames = True
        interaction.guild.me.guild_permissions.manage_roles = True
        role = MagicMock(spec=discord.Role)
        role.is_default.return_value = False
        role.managed = False
        role.__ge__.return_value = False
        interaction.guild.get_role.return_value = role
        interaction.response = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.response.send_modal = AsyncMock()
        interaction.response.send_message = AsyncMock()
        interaction.followup.send = AsyncMock()
        return interaction

    async def test_nickname_and_role_are_applied(self):
        interaction = self.interaction()
        await views.apply_verified_profile(interaction, service.RobloxProfile(123, "TestUser"))
        interaction.user.edit.assert_awaited_once_with(nick="TestUser", reason="Roblox account verified")
        interaction.user.add_roles.assert_awaited_once()
        self.assertTrue(interaction.followup.send.call_args.kwargs["ephemeral"])

    async def test_permission_failures_do_not_mutate_member(self):
        for failure in ("owner", "hierarchy", "nickname_permission", "role_permission", "missing_role", "dm"):
            interaction = self.interaction()
            if failure == "owner":
                interaction.guild.owner_id = 2
            elif failure == "hierarchy":
                interaction.user.top_role = 10
            elif failure == "nickname_permission":
                interaction.guild.me.guild_permissions.manage_nicknames = False
            elif failure == "role_permission":
                interaction.guild.me.guild_permissions.manage_roles = False
            elif failure == "missing_role":
                interaction.guild.get_role.return_value = None
            else:
                interaction.guild = None
            with self.assertRaises(service.VerificationError):
                await views.apply_verified_profile(interaction, service.RobloxProfile(123, "TestUser"))
            interaction.user.edit.assert_not_awaited()
            interaction.user.add_roles.assert_not_awaited()

    async def test_discord_failure_reports_retry_without_granting_role(self):
        interaction = self.interaction()
        interaction.user.edit.side_effect = discord.Forbidden(MagicMock(status=403, reason="Forbidden"), "denied")
        with self.assertRaisesRegex(service.VerificationError, "인증 정보 업데이트"):
            await views.apply_verified_profile(interaction, service.RobloxProfile(123, "TestUser"))
        interaction.user.add_roles.assert_not_awaited()
        interaction.followup.send.assert_not_awaited()

    async def test_original_button_opens_modal_without_granting_role(self):
        interaction = self.interaction()
        view = views.VerifyView()
        await view.verify.callback(interaction)
        interaction.response.send_modal.assert_awaited_once()
        interaction.user.add_roles.assert_not_awaited()
        self.assertTrue(view.is_persistent())
        self.assertTrue(views.RobloxConfirmView().is_persistent())
        self.assertEqual(view.verify.custom_id, "verify_button")

    async def test_confirm_requires_proof_before_discord_changes(self):
        interaction = self.interaction()
        with patch.object(views, "check_cooldown"), patch.object(views, "store") as store:
            store.verify = AsyncMock(side_effect=service.VerificationError("missing proof"))
            with self.assertRaises(service.VerificationError):
                await views.RobloxConfirmView().confirm.callback(interaction)
            interaction.response.defer.assert_awaited_once()
            interaction.user.edit.assert_not_awaited()
            interaction.user.add_roles.assert_not_awaited()

    async def test_sync_uses_saved_id_and_current_username(self):
        interaction = self.interaction()
        with patch.object(views, "check_cooldown"), patch.object(views, "store") as store:
            store.get_link = AsyncMock(return_value=(123, "OldName"))
            store.refresh = AsyncMock(return_value=service.RobloxProfile(123, "NewName"))
            await views.VerifyView().sync.callback(interaction)
            store.refresh.assert_awaited_once_with(1, 2)
            interaction.user.edit.assert_awaited_once_with(nick="NewName", reason="Roblox account verified")

    async def test_unverified_user_cannot_use_sync(self):
        interaction = self.interaction()
        with patch.object(views, "check_cooldown"), patch.object(views, "store") as store:
            store.get_link = AsyncMock(return_value=None)
            await views.VerifyView().sync.callback(interaction)
            interaction.user.edit.assert_not_awaited()
            self.assertTrue(interaction.followup.send.call_args.kwargs["ephemeral"])
            self.assertIsInstance(interaction.followup.send.call_args.kwargs["view"], views.VerifyView)

    async def test_modal_issues_private_code_without_granting_role(self):
        interaction = self.interaction()
        modal = views.RobloxUsernameModal()
        modal.username._value = "TestUser"
        with patch.object(views, "check_cooldown"), patch.object(views, "store") as store, \
                patch.object(views, "lookup_username", new_callable=AsyncMock) as lookup, \
                patch.object(views, "fetch_eligible_profile", new_callable=AsyncMock) as eligible:
            store.get_link = AsyncMock(return_value=None)
            store.issue = AsyncMock(return_value="DIALIAN-TEST")
            lookup.return_value = service.RobloxProfile(123, "TestUser")
            eligible.return_value = lookup.return_value
            await modal.on_submit(interaction)
            store.issue.assert_awaited_once_with(1, 2, lookup.return_value)
            self.assertTrue(interaction.followup.send.call_args.kwargs["ephemeral"])
            interaction.user.edit.assert_not_awaited()
            interaction.user.add_roles.assert_not_awaited()

    async def test_update_cannot_bypass_eligibility(self):
        interaction = self.interaction()
        with patch.object(views, "check_cooldown"), patch.object(views, "store") as store:
            store.get_link = AsyncMock(return_value=(123, "TestUser"))
            store.refresh = AsyncMock(side_effect=service.VerificationError("ineligible"))
            with self.assertRaises(service.VerificationError):
                await views.VerifyView().sync.callback(interaction)
            interaction.user.edit.assert_not_awaited()
            interaction.user.add_roles.assert_not_awaited()

    async def test_modal_for_existing_link_cannot_bypass_eligibility(self):
        interaction = self.interaction()
        modal = views.RobloxUsernameModal()
        modal.username._value = "TestUser"
        with patch.object(views, "check_cooldown"), patch.object(views, "store") as store, \
                patch.object(views, "lookup_username", new_callable=AsyncMock) as lookup:
            lookup.return_value = service.RobloxProfile(123, "TestUser")
            store.get_link = AsyncMock(return_value=(123, "TestUser"))
            store.refresh = AsyncMock(side_effect=service.VerificationError("ineligible"))
            with self.assertRaises(service.VerificationError):
                await modal.on_submit(interaction)
            interaction.user.edit.assert_not_awaited()
            interaction.user.add_roles.assert_not_awaited()

    async def test_cooldown_is_scoped_to_member(self):
        cooldowns = views.commands.CooldownMapping.from_cooldown(
            1, 10, lambda i: (i.guild_id, i.user.id),
        )
        with patch.object(views, "_cooldowns", cooldowns):
            interaction = self.interaction()
            views.check_cooldown(interaction)
            with self.assertRaises(service.VerificationError):
                views.check_cooldown(interaction)
            interaction.user.id = 3
            views.check_cooldown(interaction)


class DatabaseMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_adds_verification_tables_and_is_repeatable(self):
        from database import database

        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "migration.db")
            with patch.object(database, "DATABASE", path), patch.object(database, "secure_database_file"):
                await database.create_tables()
                await database.create_tables()
            async with aiosqlite.connect(path) as db:
                for table in ("commissions", "roblox_links", "roblox_challenges"):
                    async with db.execute("SELECT name FROM sqlite_master WHERE name=?", (table,)) as cursor:
                        self.assertIsNotNone(await cursor.fetchone())


if __name__ == "__main__":
    unittest.main()
