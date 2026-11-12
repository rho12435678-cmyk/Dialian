"""DDH-only configuration. No legacy guild/channel IDs or secrets in source."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    token: str
    guild_id: int
    database: Path = Path("data/ddh.db")
    notice_hour: int = 18
    notice_interval_days: int = 2
    notices_enabled: bool = True

    @classmethod
    def from_env(cls):
        load_dotenv()
        token = os.getenv("TOKEN", "").strip()
        raw_id = os.getenv("DDH_GUILD_ID", "").strip()
        if not token:
            raise ValueError("TOKEN을 서버의 .env 또는 환경 변수에 설정하세요.")
        if not raw_id.isdecimal() or not 17 <= len(raw_id) <= 20:
            raise ValueError("DDH_GUILD_ID에 새 DDH 서버 ID를 설정하세요.")
        hour = int(os.getenv("DDH_NOTICE_HOUR", "18"))
        interval = int(os.getenv("DDH_NOTICE_INTERVAL_DAYS", "2"))
        if not 0 <= hour <= 23 or not 1 <= interval <= 30:
            raise ValueError("공지 시각은 0~23시, 주기는 1~30일이어야 합니다.")
        return cls(token, int(raw_id), Path(os.getenv("DDH_DATABASE", "data/ddh.db")),
                   hour, interval, os.getenv("DDH_NOTICES_ENABLED", "true").lower() == "true")
