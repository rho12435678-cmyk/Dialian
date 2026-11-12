"""Entrypoint retained for the existing VPS: python dial.py."""
import argparse
import logging
import os
from dataclasses import replace

import discord

from config import Settings
from ddh.bot import Dialian


def main():
    parser = argparse.ArgumentParser(description="Dialian · Dial Dev Hub")
    parser.add_argument("--check-config", action="store_true", help="설정만 검증하고 종료 (로그인하지 않음)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if os.name == "posix":
        os.umask(0o077)
    try:
        settings = Settings.from_env()
    except ValueError as error:
        parser.error(str(error))
    if args.check_config:
        print(f"DDH configuration OK · guild {settings.guild_id} · database {settings.database}")
        return
    try:
        Dialian(settings).run(settings.token, log_handler=None)
    except discord.PrivilegedIntentsRequired:
        if not settings.member_events:
            raise
        logging.getLogger(__name__).warning(
            "Enable Server Members Intent in the Discord Developer Portal and restart to receive joins/leaves."
        )
        Dialian(replace(settings, member_events=False)).run(settings.token, log_handler=None)


if __name__ == "__main__":
    main()
