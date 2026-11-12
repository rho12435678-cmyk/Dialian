"""In-memory Discord REST boundary using real discord.py model/permission code."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
from discord.utils import SnowflakeList


def missing():
    return discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "Missing")


class Role(discord.Role):
    async def edit(self, **kwargs):
        for key, value in kwargs.items():
            if key == "permissions":
                self._permissions = value.value
            elif key == "colour":
                self._colour = value.value
            elif key != "reason":
                setattr(self, key, value)
        return self


def model_data(guild, name, kind, **kwargs):
    category = kwargs.get("category")
    if kind == 0 and kwargs.get("news"):
        kind = 5
    data = dict(id=guild.next_id(), name=name, type=kind, position=kwargs.get("position", 0),
                parent_id=category.id if category else None, topic=kwargs.get("topic"),
                nsfw=False, rate_limit_per_user=kwargs.get("slowmode_delay", 0),
                bitrate=64000, user_limit=0, permission_overwrites=[])
    if kind == 15:
        data["available_tags"] = [
            {
                "id": guild.next_id(), "name":tag.name,
                "moderated": tag.moderated, "emoji_id": None, "emoji_name": None,
            }
            for tag in kwargs.get("available_tags", ())
        ]
        data["default_forum_layout"] = getattr(kwargs.get("default_layout"), "value", 0)
        data["default_sort_order"] = getattr(kwargs.get("default_sort_order"), "value", 0)
    for target, overwrite in kwargs.get("overwrites", {}).items():
        allow, deny = overwrite.pair()
        data["permission_overwrites"].append(dict(id=target.id, type=0 if isinstance(target, discord.Role) else 1,
                                                  allow=str(allow.value), deny=str(deny.value)))
    return data


class Message:
    def __init__(self, channel, card, content="", view=None):
        self.id = channel.guild.next_id()
        self.author = channel.guild.me
        self.embeds = [card] if card else []
        self.content = content
        self.view = view

    async def edit(self, **kwargs):
        self.embeds = [kwargs["embed"]] if kwargs.get("embed") else []
        self.view = kwargs.get("view")
        return self


class ChannelMethods:
    async def edit(self, **kwargs):
        if "name" in kwargs:
            self.name = kwargs["name"]
        if "category" in kwargs:
            self.category_id = kwargs["category"].id
        if "overwrites" in kwargs:
            self._fill_overwrites(model_data(self.guild, self.name, 0, overwrites=kwargs["overwrites"]))
        if "topic" in kwargs:
            self.topic = kwargs["topic"]
        if "type" in kwargs:
            self._type = kwargs["type"].value
        return self


class Text(ChannelMethods, discord.TextChannel):
    def __init__(self, guild, name, **kwargs):
        super().__init__(state=Mock(), guild=guild, data=model_data(guild, name, 0, **kwargs))
        self.messages = []
        self.fail_send = False
        self.fail_edit = False

    async def send(self, content=None, *, embed=None, view=None, **kwargs):
        if self.fail_send:
            raise RuntimeError("simulated send failure")
        msg = Message(self, embed, content, view)
        self.messages.append(msg)
        return msg

    async def edit(self, **kwargs):
        if self.fail_edit:
            raise RuntimeError("simulated edit failure")
        return await super().edit(**kwargs)

    async def fetch_message(self, message_id):
        for message in self.messages:
            if message.id == message_id:
                return message
        raise missing()

    async def history(self, **kwargs):
        for message in reversed(self.messages):
            yield message


class Forum(ChannelMethods, discord.ForumChannel):
    def __init__(self, guild, name, **kwargs):
        super().__init__(
            state=Mock(), guild=guild, data=model_data(guild, name, 15, **kwargs),
        )


class Category(ChannelMethods, discord.CategoryChannel):
    def __init__(self, guild, name, **kwargs):
        super().__init__(state=Mock(), guild=guild, data=model_data(guild, name, 4, **kwargs))

    async def delete(self, **kwargs):
        self.guild.channels.remove(self)


class Voice(ChannelMethods, discord.VoiceChannel):
    def __init__(self, guild, name, **kwargs):
        super().__init__(state=Mock(), guild=guild, data=model_data(guild, name, 2, **kwargs))


class Guild:
    def __init__(self, guild_id=123456789012345678):
        self.id = guild_id
        self.owner_id = 1
        self.counter = 1000
        self.roles = []
        self.channels = []
        self.members = {}
        self.features = ["COMMUNITY", "NEWS"]
        self.default_role = self.make_role("@everyone", role_id=guild_id, position=0)
        bot_role = self.make_role("Dialian", position=200, permissions=discord.Permissions.all())
        self.me = self.member(2, [bot_role])
        self.me.guild_permissions = discord.Permissions.all()
        self.me.top_role = bot_role

    def next_id(self):
        self.counter += 1
        return self.counter

    def make_role(self, name, role_id=None, position=1, permissions=None, **kwargs):
        data = dict(id=role_id or self.next_id(), name=name, position=position,
                    permissions=str((permissions or discord.Permissions.none()).value),
                    managed=False, color=0, hoist=False, mentionable=False)
        role = Role(guild=self, state=Mock(), data=data)
        self.roles.append(role)
        return role

    def member(self, member_id, roles=(), admin=False):
        member = Mock(spec=discord.Member)
        member.id = member_id
        member.guild = self
        member.roles = [self.default_role, *roles]
        member._roles = SnowflakeList(r.id for r in roles)
        member.guild_permissions = discord.Permissions(administrator=admin)
        member.is_timed_out.return_value = False
        async def add(*new_roles, **kwargs):
            for role in new_roles:
                if role not in member.roles:
                    member.roles.append(role)
                    member._roles.add(role.id)
        member.add_roles = AsyncMock(side_effect=add)
        async def remove(*removed_roles, **kwargs):
            for role in removed_roles:
                if role in member.roles:
                    member.roles.remove(role)
                    member._roles.remove(role.id)
        member.remove_roles = AsyncMock(side_effect=remove)
        self.members[member_id] = member
        return member

    def get_role(self, role_id):
        return next((r for r in self.roles if r.id == role_id), None)

    def get_channel(self, channel_id):
        return next((c for c in self.channels if c.id == channel_id), None)

    async def fetch_channels(self):
        return list(self.channels)

    async def fetch_channel(self, channel_id):
        channel = self.get_channel(channel_id)
        if not channel:
            raise missing()
        return channel

    async def fetch_member(self, member_id):
        if member_id not in self.members:
            raise missing()
        return self.members[member_id]

    async def create_role(self, **kwargs):
        return self.make_role(**kwargs)

    async def edit_role_positions(self, positions, **kwargs):
        for role, position in positions.items():
            role.position = position
        return self.roles

    async def create_category(self, name, **kwargs):
        obj = Category(self, name, **kwargs)
        self.channels.append(obj)
        return obj

    async def create_text_channel(self, name, **kwargs):
        obj = Text(self, name, **kwargs)
        self.channels.append(obj)
        return obj

    async def create_voice_channel(self, name, **kwargs):
        obj = Voice(self, name, **kwargs)
        self.channels.append(obj)
        return obj

    async def create_forum(self, name, **kwargs):
        obj = Forum(self, name, **kwargs)
        self.channels.append(obj)
        return obj
