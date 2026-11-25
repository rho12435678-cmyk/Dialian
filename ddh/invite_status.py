"""Read-only local campaign diagnostics: python -m ddh.invite_status."""
import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv


KEY = 'event:invite:2026-11-22'


def open_readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)


def read_state(db, guild_id):
    row = db.execute('SELECT value FROM settings WHERE guild_id=? AND key=?',
                     (guild_id, KEY)).fetchone()
    return json.loads(row[0]) if row else None


def inspect(path, guild_id, inviter=None, *, link=None):
    with open_readonly(path) as db:
        s = read_state(db, guild_id)
    if s is None:
        return {'event': 'not_initialized'}
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
    if link is not None:
        inviter = s.get('links', {}).get(link)
        if inviter is None:
            result['link'] = 'not_registered'
        else:
            result['link'] = dict(last_observed_uses=s.get('uses', {}).get(link, [None])[0])
    if inviter is not None:
        uid = str(inviter)
        links = s.get('links', {})
        counters = [count for code, (count, owner) in s.get('uses', {}).items()
                    if (links.get(code) or owner) == uid]
        result['inviter'] = dict(confirmed=s.get('scores', {}).get(uid, 0),
                                 observed_links=len(counters), lifetime_uses=sum(counters))
        observations = []
        for backup in sorted(Path(path).parent.joinpath('ddh-backups').glob('ddh-*.db'))[-32:]:
            try:
                with open_readonly(backup) as db:
                    saved = read_state(db, guild_id)
            except (sqlite3.Error, ValueError):
                continue
            if saved and saved['start'] == s['start']:
                used = sum(saved['uses'].get(code, [0])[0] for code, owner in saved['links'].items()
                           if owner == uid)
                observations.append(dict(backup_utc=backup.stem[4:19],
                                         personal_link_uses=used, confirmed=saved['scores'].get(uid, 0)))
        result['backup_observations'] = observations
        result['unresolved_windows_with_inviter'] = [
            dict(at_utc=datetime.fromtimestamp(row['at'], timezone.utc).isoformat(),
                 eligible=len(row['eligible']), uses=row['changes'][uid], unresolved=row['unresolved'])
            for row in s.get('recent_observations', []) if row['unresolved'] and uid in row['changes']]
    return result


def main():
    load_dotenv(Path.cwd() / '.env')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', default=os.getenv('DDH_DATABASE', 'data/ddh.db'))
    parser.add_argument('--guild', type=int, default=int(os.getenv('DDH_GUILD_ID', '1570276882669240383')))
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--inviter', type=int, help='Discord user ID for a private score check')
    selection.add_argument('--link', help='Personal invite code without the URL')
    args = parser.parse_args()
    try:
        print(json.dumps(inspect(args.database, args.guild, args.inviter, link=args.link),
                         ensure_ascii=False, indent=2))
    except (sqlite3.Error, ValueError) as error:
        parser.exit(1, f'Cannot read event state: {error}\n')


if __name__ == '__main__':
    main()
