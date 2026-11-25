"""Read-only local campaign diagnostics: python -m ddh.invite_status."""
import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv


def inspect(path, guild_id, inviter=None):
    with sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True) as db:
        row = db.execute('SELECT value FROM settings WHERE guild_id=? AND key=?',
                         (guild_id, 'event:invite:2026-11-22')).fetchone()
    if row is None:
        return {'event': 'not_initialized'}
    s = json.loads(row[0])
    def stamp(key):
        value = s.get(key)
        return datetime.fromtimestamp(value, timezone.utc).isoformat() if value else None
    result = dict(event='ended' if s.get('announced_end') else 'initialized',
                start_utc=stamp('start'), end_utc=stamp('end'),
                last_checked_utc=stamp('last_checked'),
                tracking_status=s.get('tracking_status', 'legacy_no_diagnostics'),
                personal_links=len(s.get('links', {})),
                ranked_inviters=len(s.get('scores', {})),
                awarded_total=sum(s.get('scores', {}).values()),
                pending_joins=len(s.get('pending', [])),
                unresolved_since_fix=s.get('unresolved_total', 0),
                latest_observation=s.get('last_observation'),
                board_channel=s.get('channel'), board_message=s.get('message'))
    if inviter is not None:
        uid = str(inviter)
        links = s.get('links', {})
        counters = [count for code, (count, owner) in s.get('uses', {}).items()
                    if (links.get(code) or owner) == uid]
        result['inviter'] = dict(confirmed=s.get('scores', {}).get(uid, 0),
                                 observed_links=len(counters), lifetime_uses=sum(counters))
    return result


def main():
    load_dotenv(Path.cwd() / '.env')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', default=os.getenv('DDH_DATABASE', 'data/ddh.db'))
    parser.add_argument('--guild', type=int, default=int(os.getenv('DDH_GUILD_ID', '1570276882669240383')))
    parser.add_argument('--inviter', type=int, help='Discord user ID for a private score check')
    args = parser.parse_args()
    try:
        print(json.dumps(inspect(args.database, args.guild, args.inviter), ensure_ascii=False, indent=2))
    except (sqlite3.Error, ValueError) as error:
        parser.exit(1, f'Cannot read event state: {error}\n')


if __name__ == '__main__':
    main()
