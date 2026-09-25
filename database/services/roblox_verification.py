import asyncio
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import aiohttp
import aiosqlite

from database.database import DATABASE


CHALLENGE_TTL = 600
REQUEST_COOLDOWN = 10
API_BASE = "https://users.roblox.com/v1"
MIN_ACCOUNT_AGE_DAYS = 30


class VerificationError(Exception):
    pass


@dataclass(frozen=True)
class RobloxProfile:
    id: int
    name: str
    description: str = ""
    created_at: datetime | None = None


async def create_verification_tables(db):
    await db.execute("""
        CREATE TABLE IF NOT EXISTS roblox_links (
            guild_id INTEGER NOT NULL,
            discord_id INTEGER NOT NULL,
            roblox_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            verified_at INTEGER NOT NULL,
            PRIMARY KEY (guild_id, discord_id),
            UNIQUE (guild_id, roblox_id)
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS roblox_challenges (
            guild_id INTEGER NOT NULL,
            discord_id INTEGER NOT NULL,
            roblox_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            expires_at INTEGER NOT NULL,
            last_attempt INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (guild_id, discord_id)
        )
    """)


async def api_request(method, path, *, base=API_BASE, **kwargs):
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.request(method, base + path, **kwargs) as response:
                if response.status == 429:
                    raise VerificationError("⏳ Roblox 요청이 많아요. 잠시 후 다시 눌러주세요.")
                if response.status == 404:
                    raise VerificationError("로블록스 계정을 찾을 수 없습니다.")
                if response.status != 200:
                    raise VerificationError("⚠️ Roblox 서버에서 계정 정보를 받지 못했어요. 잠시 후 다시 시도해 주세요.")
                return await response.json()
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        raise VerificationError("⚠️ Roblox 연결이 불안정해요. 잠시 후 다시 시도하고, 반복되면 운영진에게 알려주세요.") from exc


def parse_profile(data):
    if not isinstance(data, dict):
        raise VerificationError("로블록스 계정 정보가 올바르지 않습니다.")
    if data.get("isBanned"):
        raise VerificationError("정지된 로블록스 계정은 인증할 수 없습니다.")
    if (type(data.get("id")) is not int or data["id"] <= 0
            or not isinstance(data.get("name"), str)
            or not re.fullmatch(r"[A-Za-z0-9_]{1,20}", data["name"])
            or not isinstance(data.get("description", ""), str)):
        raise VerificationError("로블록스 계정 정보가 올바르지 않습니다.")
    created_at = None
    if "created" in data:
        try:
            created_at = datetime.fromisoformat(data["created"].replace("Z", "+00:00"))
            if created_at.tzinfo is None:
                raise ValueError("Missing timezone")
            created_at = created_at.astimezone(timezone.utc)
        except (ValueError, TypeError, AttributeError) as exc:
            raise VerificationError("계정 생성일을 확인할 수 없습니다. 잠시 뒤 다시 시도해주세요.") from exc
    return RobloxProfile(data["id"], data["name"], data.get("description", ""), created_at)


async def lookup_username(username):
    username = username.strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,20}", username):
        raise VerificationError("❌ 표시 이름이 아닌 Roblox @사용자이름을 입력해 주세요. 예: @Roblox")
    data = await api_request("POST", "/usernames/users", json={
        "usernames": [username], "excludeBannedUsers": True,
    })
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise VerificationError("로블록스 계정 정보가 올바르지 않습니다.")
    if not data["data"]:
        raise VerificationError("❌ 계정을 찾지 못했어요. Roblox 프로필의 @사용자이름을 확인해 주세요.")
    return parse_profile(data["data"][0])


async def fetch_profile(roblox_id):
    profile = parse_profile(await api_request("GET", f"/users/{int(roblox_id)}"))
    if profile.id != roblox_id:
        raise VerificationError("로블록스 계정 정보가 일치하지 않습니다.")
    return profile


def check_account_age(profile):
    if profile.created_at is None or profile.created_at.tzinfo is None:
        raise VerificationError("계정 생성일을 확인할 수 없어 인증할 수 없습니다. 잠시 뒤 다시 시도해주세요.")
    eligible_at = profile.created_at.timestamp() + MIN_ACCOUNT_AGE_DAYS * 86400
    if time.time() < eligible_at:
        raise VerificationError(
            f"로블록스 계정 생성 후 {MIN_ACCOUNT_AGE_DAYS}일이 지나야 인증할 수 있습니다.\n"
            f"인증 가능 시각: <t:{int(eligible_at)}:F>"
        )



async def check_eligibility(profile):
    """DDS verification policy: account must be at least 30 days old.

    Avatar/catalog Robux value is intentionally not part of verification.
    """
    check_account_age(profile)


async def fetch_eligible_profile(roblox_id):
    profile = await fetch_profile(roblox_id)
    await check_eligibility(profile)
    return profile


class VerificationStore:
    def __init__(self, path=DATABASE):
        self.path = path

    async def get_link(self, guild_id, discord_id):
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT roblox_id, username FROM roblox_links WHERE guild_id=? AND discord_id=?",
                (guild_id, discord_id),
            ) as cursor:
                return await cursor.fetchone()

    async def issue(self, guild_id, discord_id, profile):
        now = int(time.time())
        code = "DIALIAN-" + secrets.token_hex(12).upper()
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute("DELETE FROM roblox_challenges WHERE expires_at<=?", (now,))
            async with db.execute(
                "SELECT 1 FROM roblox_links WHERE guild_id=? AND roblox_id=? AND discord_id<>?",
                (guild_id, profile.id, discord_id),
            ) as cursor:
                if await cursor.fetchone():
                    raise VerificationError("이미 이 서버의 다른 디스코드 계정에 연결된 로블록스 계정입니다.")
            async with db.execute(
                "SELECT expires_at FROM roblox_challenges WHERE guild_id=? AND discord_id=?",
                (guild_id, discord_id),
            ) as cursor:
                row = await cursor.fetchone()
                if row and row[0] - CHALLENGE_TTL + REQUEST_COOLDOWN > now:
                    raise VerificationError("인증 코드를 방금 발급했습니다. 10초 뒤 다시 시도해주세요.")
            await db.execute("""
                INSERT INTO roblox_challenges VALUES (?, ?, ?, ?, ?, 0)
                ON CONFLICT(guild_id, discord_id) DO UPDATE SET
                    roblox_id=excluded.roblox_id, code=excluded.code,
                    expires_at=excluded.expires_at, last_attempt=0
            """, (guild_id, discord_id, profile.id, code, now + CHALLENGE_TTL))
            await db.commit()
        return code

    async def refresh(self, guild_id, discord_id):
        link = await self.get_link(guild_id, discord_id)
        if not link:
            raise VerificationError("로블록스 계정을 먼저 연결해주세요.")
        profile = await fetch_eligible_profile(link[0])
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute("""
                UPDATE roblox_links SET username=?
                WHERE guild_id=? AND discord_id=? AND roblox_id=?
            """, (profile.name, guild_id, discord_id, profile.id))
            if cursor.rowcount != 1:
                raise VerificationError("연결 정보가 변경되었습니다. 다시 시도해주세요.")
            await db.commit()
        return profile

    async def verify(self, guild_id, discord_id):
        now = int(time.time())
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute("""
                SELECT roblox_id, code, expires_at, last_attempt FROM roblox_challenges
                WHERE guild_id=? AND discord_id=?
            """, (guild_id, discord_id)) as cursor:
                row = await cursor.fetchone()
            if not row or row[2] <= now:
                raise VerificationError("인증 코드가 없거나 만료되었습니다. 인증하기 버튼에서 다시 시작해주세요.")
            roblox_id, code, expires_at, last_attempt = row
            if last_attempt + REQUEST_COOLDOWN > now:
                raise VerificationError("10초 뒤에 다시 확인해주세요.")
            await db.execute(
                "UPDATE roblox_challenges SET last_attempt=? WHERE guild_id=? AND discord_id=?",
                (now, guild_id, discord_id),
            )
            await db.commit()

        profile = await fetch_profile(roblox_id)
        if not re.search(r"(?<![A-Za-z0-9-])" + re.escape(code) + r"(?![A-Za-z0-9-])", profile.description):
            raise VerificationError("❌ 프로필 소개(About)에 코드가 없어요. 코드를 붙여넣고 저장한 뒤 다시 눌러주세요.")
        await check_eligibility(profile)

        # Recheck after the network request, so replaced or consumed codes cannot be reused.
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute("""
                SELECT 1 FROM roblox_challenges
                WHERE guild_id=? AND discord_id=? AND code=? AND expires_at>?
            """, (guild_id, discord_id, code, int(time.time()))) as cursor:
                if not await cursor.fetchone():
                    raise VerificationError("이미 사용했거나 변경·만료된 코드입니다. 인증을 다시 시작해주세요.")
            try:
                await db.execute("""
                    INSERT INTO roblox_links VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(guild_id, discord_id) DO UPDATE SET
                        roblox_id=excluded.roblox_id, username=excluded.username,
                        verified_at=excluded.verified_at
                """, (guild_id, discord_id, profile.id, profile.name, int(time.time())))
            except aiosqlite.IntegrityError as exc:
                raise VerificationError("이미 이 서버의 다른 디스코드 계정에 연결된 로블록스 계정입니다.") from exc
            await db.execute(
                "DELETE FROM roblox_challenges WHERE guild_id=? AND discord_id=?",
                (guild_id, discord_id),
            )
            await db.commit()
        return profile
