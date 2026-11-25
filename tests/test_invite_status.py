import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ddh.invite_status import inspect, participant_totals


class StatusTests(unittest.TestCase):
    def test_participant_report_combines_codes_by_owner_without_summing_duplicate_backups(self):
        old = dict(links={'deleted': '3', 'active': '3', 'other': '5'},
                   uses={'deleted': [2, '3'], 'active': [1, '3']})
        live = dict(links={'deleted': '3', 'active': '3', 'new': '3'},
                    uses={'active': [4, '3'], 'new': [1, '3']}, scores={'3': 2})
        self.assertEqual(participant_totals(live, [old, old]), [
            dict(inviter_id='3', confirmed=2, personal_link_uses=7, registered_personal_links=3),
            dict(inviter_id='5', confirmed=0, personal_link_uses=0, registered_personal_links=1),
        ])

    def test_read_only_missing_and_legacy_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'db'
            with self.assertRaises(sqlite3.OperationalError):
                inspect(path, 1)
            self.assertFalse(path.exists())
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE settings (guild_id INTEGER, key TEXT, value TEXT)')
                db.execute('INSERT INTO settings VALUES (1, ?, ?)',
                           ('event:invite:2026-11-22', json.dumps(dict(
                               start=10, scores={'3': 3}, links={'secret': '3'}, uses={'secret': [5, None]}))))
            before = path.read_bytes()
            result = inspect(path, 1)
            self.assertEqual(result['awarded_total'], 3)
            self.assertEqual(result['tracking_status'], 'legacy_no_diagnostics')
            self.assertEqual(inspect(path, 2), {'event': 'not_initialized'})
            linked = inspect(path, 1, link='secret')
            self.assertEqual(linked['link']['last_observed_uses'], 5)
            self.assertEqual(linked['inviter']['confirmed'], 3)
            self.assertNotIn('secret', json.dumps(linked))
            self.assertEqual(before, path.read_bytes())
            self.assertNotIn('secret', json.dumps(result))
