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
import stat
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
        cls.base = Path(tempfile.mkdtemp(prefix='gaza-synthetic-adapter-')).resolve(strict=True)
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

    def _validate_diagnostic_path(self, path, allow_missing_suffix=False):
        case = Path(os.path.abspath(str(self.case)))
        lexical = Path(os.path.abspath(str(path)))
        if os.path.commonpath((str(case), str(lexical))) != str(case):
            raise AssertionError('diagnostic path escaped synthetic case')
        missing = False
        current = Path(path.anchor)
        for index, part in enumerate(path.parts):
            current = Path(part) if index == 0 else current / part
            try:
                info = current.lstat()
            except FileNotFoundError:
                if not allow_missing_suffix:
                    raise AssertionError('diagnostic path is unexpectedly absent')
                missing = True
                continue
            if missing:
                raise AssertionError('diagnostic path materialized during validation')
            attributes = getattr(info, 'st_file_attributes', 0)
            if stat.S_ISLNK(info.st_mode) or attributes & 0x400:
                raise AssertionError('diagnostic reparse path refused')
        return path

    def _fixed_paths(self, name):
        names = {r['path'] for r in self.rows}
        if name not in names or Path(name).name != name:
            raise AssertionError('diagnostic target is not in the fixed synthetic manifest')
        paths = {
            'target': self.root / name,
            'candidate': self.io.tx / (self.io.token(name) + '.candidate'),
            'journal': self.io.tx / 'journal.jsonl',
        }
        self._validate_diagnostic_path(paths['target'])
        self._validate_diagnostic_path(paths['candidate'], allow_missing_suffix=True)
        self._validate_diagnostic_path(paths['journal'], allow_missing_suffix=True)
        return paths

    def _bounded_state(self, label, path):
        state = {'label': label}
        try:
            self._validate_diagnostic_path(path, allow_missing_suffix=True)
            if not path.exists():
                state['exists'] = False
                return state
            before = path.lstat()
            attributes = getattr(before, 'st_file_attributes', 0)
            initial = (before.st_dev, before.st_ino, before.st_mode, before.st_nlink, attributes)
            state.update({
                'exists': True,
                'bytes': before.st_size,
                'identity': [before.st_dev, before.st_ino],
                'attributes': attributes,
                'regular_file': stat.S_ISREG(before.st_mode),
                'link_count': before.st_nlink,
            })
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                state['diagnostic_error'] = 'regular independent file required'
                return state
            with path.open('rb') as stream:
                opened = os.fstat(stream.fileno())
                opened_tuple = (opened.st_dev, opened.st_ino, opened.st_mode, opened.st_nlink, getattr(opened, 'st_file_attributes', 0))
                if opened_tuple != initial:
                    raise RuntimeError('opened file identity differs from lstat')
                raw = stream.read(131073)
                content_truncated = len(raw) > 131072
                self._validate_diagnostic_path(path)
                after_content = os.fstat(stream.fileno())
                current = path.lstat()
                after_content_tuple = (after_content.st_dev, after_content.st_ino, after_content.st_mode, after_content.st_nlink, getattr(after_content, 'st_file_attributes', 0))
                current_tuple = (current.st_dev, current.st_ino, current.st_mode, current.st_nlink, getattr(current, 'st_file_attributes', 0))
                if after_content_tuple != initial or current_tuple != initial:
                    raise RuntimeError('identity or attributes changed after content read')
                descriptor = self.io.security(path)
                self._validate_diagnostic_path(path)
                after_security = os.fstat(stream.fileno())
                current = path.lstat()
                after_security_tuple = (after_security.st_dev, after_security.st_ino, after_security.st_mode, after_security.st_nlink, getattr(after_security, 'st_file_attributes', 0))
                current_tuple = (current.st_dev, current.st_ino, current.st_mode, current.st_nlink, getattr(current, 'st_file_attributes', 0))
                if after_security_tuple != initial or current_tuple != initial:
                    raise RuntimeError('identity or attributes changed after security read')
                state['content_truncated'] = content_truncated
                if not content_truncated:
                    state['sha256'] = digest(raw)
                    if label == 'journal':
                        state['journal_bytes'] = raw.decode('utf-8', errors='replace')
                if len(descriptor) <= 65536:
                    state['security_bytes_length'] = len(descriptor)
                    state['security_bytes_hex'] = descriptor.hex()
                    state['security_sha256'] = digest(descriptor)
                else:
                    state['security_error'] = 'descriptor exceeds diagnostic bound'
            return state
        except Exception as error:
            state['diagnostic_error'] = f'{type(error).__name__}: {error}'
            return state

    def _emit_replace_failure(self, error):
        report = {
            'event': 'replacefilew_failure_diagnostic',
            'core_exception_type': type(error).__name__,
            'core_exception': str(error),
            'replacements': self._replace_diagnostics,
            'retry_count': 0,
            'rollback_attempted': False,
            'success': False,
        }
        try:
            paths = self._fixed_paths(self.rows[0]['path'])
            report['transaction_journal'] = self._bounded_state('journal', paths['journal'])
        except Exception as diagnostic_error:
            report['diagnostic_error'] = f'{type(diagnostic_error).__name__}: {diagnostic_error}'
        self._last_replace_failure = report
        try:
            print('DISPOSABLE_REPLACE_FAILURE=' + json.dumps(report, sort_keys=True), flush=True)
        except Exception:
            pass

    def run_repair(self):
        self._replace_diagnostics = []
        self._last_replace_failure = None
        for row in self.rows:
            self._fixed_paths(row['path'])
        original_atomic_replace = self.io.atomic_replace

        def instrumented_atomic_replace(name, raw, expected):
            paths = self._fixed_paths(name)
            record = {
                'name': name,
                'forward_calls': 1,
                'retry_count': 0,
                'rollback_attempted': False,
                'pre': {key: self._bounded_state(key, path) for key, path in paths.items()},
            }
            try:
                returned = original_atomic_replace(name, raw, expected)
                record['returned'] = True
                record['post'] = {key: self._bounded_state(key, path) for key, path in paths.items()}
                self._replace_diagnostics.append(record)
                return returned
            except BaseException as error:
                record['returned'] = False
                record['exception_type'] = type(error).__name__
                record['exception'] = str(error)
                record['post'] = {key: self._bounded_state(key, path) for key, path in paths.items()}
                self._replace_diagnostics.append(record)
                raise

        self.io.atomic_replace = instrumented_atomic_replace
        try:
            return core.execute(self.io, writer_quiescence_confirmed=True,
                                active_write_authorized=True)
        except BaseException as error:
            try:
                self._emit_replace_failure(error)
            except BaseException:
                pass
            raise
        finally:
            self.io.atomic_replace = original_atomic_replace

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
        with self.assertRaisesRegex(core.Refused, '^preimage mismatch: fixture-0[.]txt$'):
            self.run_repair()
        self.assertFalse(self.io.tx.exists())

    def test_collision_preserved_without_target_replacement(self):
        self.io.tx.mkdir()
        (self.io.tx / 'sentinel').write_bytes(b'preserve')
        with self.assertRaisesRegex(core.Refused, '^transaction collision; never reuse/delete$'):
            self.run_repair()
        self.assertEqual((self.io.tx/'sentinel').read_bytes(), b'preserve')
        for r in self.rows:
            self.assertEqual(digest((self.root/r['path']).read_bytes()), r['expected_observed_working_sha256'])

    def test_ads_refused_before_transaction_creation(self):
        Path(str(self.root/self.rows[0]['path']) + ':synthetic').write_bytes(b'preserve')
        with self.assertRaisesRegex(core.Refused, '^alternate stream refused$'):
            self.run_repair()
        self.assertFalse(self.io.tx.exists())

    def test_security_mismatch_refuses_before_payload(self):
        # Native descriptor compared against deliberately unequal expected bytes.
        # No owner/DACL is changed and no claim of reproducing production ACLs.
        destination = self.case / 'mismatched-backup'
        with self.assertRaisesRegex(core.Refused, '^new empty file security differs; no payload written$'):
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

    def test_failure_after_replace_is_reported_without_retry_or_rollback(self):
        original_atomic_replace = self.io.atomic_replace
        original_snapshot = self.io.snapshot
        first = next(r for r in self.rows if r['action'] == 'proposed_exact_blob_restore')
        replacement_completed = False

        def mark_after_replace(name, raw, expected):
            nonlocal replacement_completed
            result = original_atomic_replace(name, raw, expected)
            replacement_completed = True
            return result

        def fail_on_post_replace_snapshot(name):
            if replacement_completed and name == first['path']:
                raise core.Refused('synthetic post-replace snapshot failure')
            return original_snapshot(name)

        with patch.object(self.io, 'atomic_replace', side_effect=mark_after_replace):
            with patch.object(self.io, 'snapshot', side_effect=fail_on_post_replace_snapshot):
                with self.assertRaisesRegex(core.Refused, '^synthetic post-replace snapshot failure$'):
                    self.run_repair()

        report = self._last_replace_failure
        self.assertIsNotNone(report)
        self.assertFalse(report['success'])
        self.assertEqual(report['retry_count'], 0)
        self.assertFalse(report['rollback_attempted'])
        self.assertEqual(len(report['replacements']), 1)
        replacement = report['replacements'][0]
        self.assertEqual(replacement['forward_calls'], 1)
        self.assertTrue(replacement['returned'])
        self.assertEqual(replacement['post']['target']['sha256'], first['canonical_sha256'])
        for row in self.rows:
            if row is not first:
                self.assertEqual(digest((self.root / row['path']).read_bytes()), row['expected_observed_working_sha256'])
        self.assertIn('stopped', report['transaction_journal']['journal_bytes'])

    def test_diagnostic_failure_preserves_original_exception_identity(self):
        original_error = core.Refused('original core failure')
        original_atomic_replace = self.io.atomic_replace
        with patch.object(core, 'execute', side_effect=original_error):
            with patch.object(self, '_emit_replace_failure', side_effect=RuntimeError('diagnostic failure')):
                with self.assertRaises(core.Refused) as raised:
                    self.run_repair()
        self.assertIs(raised.exception, original_error)
        self.assertIs(self.io.atomic_replace.__self__, original_atomic_replace.__self__)
        self.assertIs(self.io.atomic_replace.__func__, original_atomic_replace.__func__)
        self.assertFalse(self.io.tx.exists())
        self.assertEqual(self._replace_diagnostics, [])


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-disposable', action='store_true')
    args = parser.parse_args()
    if not args.run_disposable:
        parser.error('Explicit --run-disposable required')
    unittest.main(argv=[__file__], verbosity=2)
