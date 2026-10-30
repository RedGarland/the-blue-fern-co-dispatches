"""Independent Windows CI test only. Never invokes PowerShell or production paths.
Explicit --run-disposable required. Unchanged production modules loaded; only their
fixed input manifest/destination bindings are replaced with synthetic CI fixtures.
No automatic deletion: artifacts remain for the job's upload step.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import exact_profile_repair as core
import windows_backend as native


def digest(b):
    return hashlib.sha256(b).hexdigest()


class WindowsDisposable(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.name != 'nt':
            raise unittest.SkipTest('Real Windows required; no platform emulation')
        if os.environ.get('GITHUB_ACTIONS') != 'true':
            raise unittest.SkipTest('Independent hosted CI only, not the denied local host')
        cls.base = Path(tempfile.mkdtemp(prefix='gaza-synthetic-adapter-'))
        print('PRESERVE_ARTIFACT_DIRECTORY=' + str(cls.base), flush=True)

    def setUp(self):
        self.case = self.base / self._testMethodName
        self.case.mkdir()
        self.root = self.case / 'synthetic-pages'
        self.root.mkdir()
        rows = []
        for n in range(8):
            name = f'fixture-{n}.txt'
            before = f'synthetic-only-{n}\r\n'.encode() if n < 7 else b'no-write'
            after = before.replace(b'\r\n', b'\n')
            (self.root / name).write_bytes(before)
            rows.append({'path': name, 'expected_observed_working_sha256': digest(before),
                         'expected_observed_working_size': len(before),
                         'canonical_sha256': digest(after), 'canonical_size': len(after),
                         'action': 'proposed_exact_blob_restore' if n < 7 else 'no_write'})
        # validate has a definition-time default: replace it with equivalent
        # fixed-cardinality validation for this synthetic manifest only.
        original_validate = core.validate
        self.patches = [patch.object(core, 'ROWS', rows),
                        patch.object(core, 'validate', lambda: original_validate(rows)),
                        patch.object(native, 'ROOT', str(self.root)),
                        patch.object(native, 'ROWS', rows),
                        patch.object(native, 'TX_PARENT', self.case),
                        patch.object(native, 'TX_NAME', 'transaction')]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in reversed(self.patches)])
        self.rows = rows
        self.io = native.WindowsBackend()

    def run_repair(self):
        return core.execute(self.io, writer_quiescence_confirmed=True,
                            active_write_authorized=True)

    def test_actual_adapter_success_and_preserved_backups(self):
        before = {r['path']: self.io.snapshot(r['path']) for r in self.rows}
        result = self.run_repair()
        self.assertEqual(result['replaced'], 7)
        for r in self.rows:
            after = self.io.snapshot(r['path'])
            self.assertEqual(digest(after.raw), r['canonical_sha256'])
            self.assertEqual(after.security, before[r['path']].security)
            self.assertEqual(after.attributes, before[r['path']].attributes)
            if r['action'] == 'no_write':
                self.assertEqual(after.identity, before[r['path']].identity)
            else:
                self.assertEqual(self.io.read_backup(r['path']), before[r['path']].raw)
                self.assertNotEqual(after.identity, before[r['path']].identity)
        events = [json.loads(line)['event'] for line in (self.io.tx/'journal.jsonl').read_text().splitlines()]
        self.assertLess(events.index('backups_verified'), events.index('replace_intent'))
        self.assertEqual(events[-1], 'success')

    def test_drift_refuses_before_transaction_creation(self):
        (self.root / self.rows[0]['path']).write_bytes(b'unexpected')
        with self.assertRaises(core.Refused):
            self.run_repair()
        self.assertFalse(self.io.tx.exists())

    def test_collision_preserved_without_target_replacement(self):
        self.io.tx.mkdir()
        (self.io.tx / 'sentinel').write_bytes(b'preserve')
        with self.assertRaises(core.Refused):
            self.run_repair()
        self.assertEqual((self.io.tx/'sentinel').read_bytes(), b'preserve')
        for r in self.rows:
            self.assertEqual(digest((self.root/r['path']).read_bytes()), r['expected_observed_working_sha256'])

    def test_ads_refused_before_transaction_creation(self):
        Path(str(self.root/self.rows[0]['path']) + ':synthetic').write_bytes(b'preserve')
        with self.assertRaises(core.Refused):
            self.run_repair()
        self.assertFalse(self.io.tx.exists())

    def test_security_mismatch_refuses_before_payload(self):
        # Native descriptor compared against deliberately unequal expected bytes.
        # No owner/DACL is changed and no claim of reproducing production ACLs.
        destination = self.case / 'mismatched-backup'
        with self.assertRaises(core.Refused):
            self.io.write_new(destination, b'must-not-be-written', b'not-a-matching-descriptor')
        self.assertTrue(destination.exists())
        self.assertEqual(destination.read_bytes(), b'')
        for r in self.rows:
            self.assertEqual(digest((self.root/r['path']).read_bytes()), r['expected_observed_working_sha256'])

    def test_native_replace_missing_candidate_no_retry(self):
        # Direct disposable Win32 error behavior, not a production failure simulation.
        target = self.root / self.rows[0]['path']
        before = target.read_bytes()
        self.assertFalse(self.io.replace(str(target), str(self.case/'absent'), None, 0, None, None))
        self.assertEqual(target.read_bytes(), before)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-disposable', action='store_true')
    args = parser.parse_args()
    if not args.run_disposable:
        parser.error('Explicit --run-disposable required')
    unittest.main(argv=[__file__], verbosity=2)
