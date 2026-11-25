import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ddh.invite_status import inspect


class StatusTests(unittest.TestCase):
    def test_read_only_missing_and_legacy_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'db'
            with self.assertRaises(sqlite3.OperationalError):
                inspect(path, 1)
            self.assertFalse(path.exists())
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE settings (guild_id INTEGER, key TEXT, value TEXT)')
                db.execute('INSERT INTO settings VALUES (1, ?, ?)',
                           ('event:invite:2026-11-22', json.dumps(dict(scores={'3': 3}, links={'secret': '3'}))))
            before = path.read_bytes()
            result = inspect(path, 1)
            self.assertEqual(result['awarded_total'], 3)
            self.assertEqual(result['tracking_status'], 'legacy_no_diagnostics')
            self.assertEqual(inspect(path, 2), {'event': 'not_initialized'})
            self.assertEqual(before, path.read_bytes())
            self.assertNotIn('secret', json.dumps(result))
