#!/usr/bin/env python3
"""Recovery comparison rejects stale snapshots and preserves existing targets."""
import importlib.util
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'docker/verify_recovery.py'
spec = importlib.util.spec_from_file_location('verify_recovery', HELPER)
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


class RecoveryTests(unittest.TestCase):
    def test_late_commit_and_safe_auxiliary_copy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source, restored, auxiliary = [root / name for name in ('source.db', 'restored.db', 'auxiliary.db')]
            db = sqlite3.connect(source)
            try:
                db.execute('CREATE TABLE evidence (id INTEGER PRIMARY KEY, value BLOB)')
                db.execute('INSERT INTO evidence VALUES (1, ?)', (b'original\x00\xff',))
                db.commit()
                target = sqlite3.connect(restored)
                try:
                    db.backup(target)
                    verify.verify_equivalent(source, restored)
                    self.assertEqual(set(root.iterdir()), {source, restored})
                    db.execute('INSERT INTO evidence VALUES (2, ?)', (b'late commit',))
                    db.commit()
                    with self.assertRaises(RuntimeError):
                        verify.verify_equivalent(source, restored)
                    db.backup(target)
                finally:
                    target.close()
            finally:
                db.close()
            command = [sys.executable, str(HELPER), str(source), str(restored), str(source), str(auxiliary)]
            result = subprocess.run(command, capture_output=True)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(stat.S_IMODE(auxiliary.stat().st_mode), 0o600)
            verify.verify_equivalent(source, auxiliary)
            before = auxiliary.read_bytes()
            result = subprocess.run(command, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(auxiliary.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
