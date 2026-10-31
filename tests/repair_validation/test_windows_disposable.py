"""Independent Windows CI test only. Never invokes PowerShell or production paths.
Explicit --run-disposable required. Unchanged production modules loaded; only their
fixed input manifest/destination bindings are replaced with synthetic CI fixtures.
No automatic deletion: artifacts remain for the job's upload step.
"""
import argparse
import hashlib
import ctypes
from ctypes import wintypes
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
        self._fixture_cleanup_errors = []
        self._fixture_capability_tokens = set()
        capability = self._fixture_create_case(self.case)
        self._fixture_security = {'case': self._initialize_empty_fixture_security(self.case, capability)}
        self.root = self.case / 'synthetic-pages'
        self.root.mkdir()
        self._fixture_security['synthetic_pages'] = self._fixture_read_security_path(self.root)
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

    _FIXTURE_SDDL = 'D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;OW)'
    _ERROR_SUCCESS = 0
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    _OPEN_EXISTING = 3
    _FILE_SHARE_READ = 1
    _FILE_SHARE_WRITE = 2
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _FILE_ATTRIBUTE_DIRECTORY = 0x00000010
    _FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
    _READ_CONTROL = 0x00020000
    _WRITE_DAC = 0x00040000
    _FILE_READ_ATTRIBUTES = 0x00000080
    _SE_FILE_OBJECT = 1
    _OWNER_SECURITY_INFORMATION = 0x00000001
    _GROUP_SECURITY_INFORMATION = 0x00000002
    _DACL_SECURITY_INFORMATION = 0x00000004
    _PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
    _SE_DACL_AUTO_INHERIT_REQ = 0x0100
    _SE_DACL_AUTO_INHERITED = 0x0400
    _SE_DACL_PROTECTED = 0x1000
    _DACL_CONTROL_MASK = (_SE_DACL_AUTO_INHERIT_REQ | _SE_DACL_AUTO_INHERITED |
                          _SE_DACL_PROTECTED)
    _ACL_REVISION_INFORMATION = 2

    class _ACL_SIZE_INFORMATION(ctypes.Structure):
        _fields_ = [('AceCount', wintypes.DWORD),
                    ('AclBytesInUse', wintypes.DWORD),
                    ('AclBytesFree', wintypes.DWORD)]

    class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = [('FileAttributes', wintypes.DWORD),
                    ('CreationTime', wintypes.FILETIME),
                    ('LastAccessTime', wintypes.FILETIME),
                    ('LastWriteTime', wintypes.FILETIME),
                    ('VolumeSerialNumber', wintypes.DWORD),
                    ('FileSizeHigh', wintypes.DWORD),
                    ('FileSizeLow', wintypes.DWORD),
                    ('NumberOfLinks', wintypes.DWORD),
                    ('FileIndexHigh', wintypes.DWORD),
                    ('FileIndexLow', wintypes.DWORD)]

    class _FixtureCreationCapability:
        __slots__ = ('owner', 'token', 'path', 'handle', 'identity', 'base_identity', 'kernel', 'advapi', 'consumed')
        def __init__(self, owner, path, handle, identity, base_identity, kernel, advapi):
            self.owner = owner
            self.token = object()
            self.path = path
            self.handle = handle
            self.identity = identity
            self.base_identity = base_identity
            self.kernel = kernel
            self.advapi = advapi
            self.consumed = False

    def _fixture_api(self):
        advapi = ctypes.WinDLL('advapi32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.ULONG)]
        advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
        advapi.GetSecurityInfo.argtypes = [
            wintypes.HANDLE, ctypes.c_int, wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p)]
        advapi.GetSecurityInfo.restype = wintypes.DWORD
        advapi.SetSecurityInfo.argtypes = [
            wintypes.HANDLE, ctypes.c_int, wintypes.DWORD,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        advapi.SetSecurityInfo.restype = wintypes.DWORD
        advapi.GetSecurityDescriptorDacl.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.BOOL)]
        advapi.GetSecurityDescriptorDacl.restype = wintypes.BOOL
        advapi.IsValidSecurityDescriptor.argtypes = [ctypes.c_void_p]
        advapi.IsValidSecurityDescriptor.restype = wintypes.BOOL
        advapi.GetSecurityDescriptorControl.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD)]
        advapi.GetSecurityDescriptorControl.restype = wintypes.BOOL
        advapi.GetAclInformation.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD]
        advapi.GetAclInformation.restype = wintypes.BOOL
        advapi.GetSecurityDescriptorOwner.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
        advapi.GetSecurityDescriptorOwner.restype = wintypes.BOOL
        advapi.GetSecurityDescriptorGroup.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
        advapi.GetSecurityDescriptorGroup.restype = wintypes.BOOL
        advapi.GetLengthSid.argtypes = [ctypes.c_void_p]
        advapi.GetLengthSid.restype = wintypes.DWORD
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        kernel.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CreateDirectoryW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p]
        kernel.CreateDirectoryW.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        kernel.GetFileInformationByHandle.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(self._BY_HANDLE_FILE_INFORMATION)]
        kernel.GetFileInformationByHandle.restype = wintypes.BOOL
        return advapi, kernel

    def _fixture_create_case(self, case):
        base = Path(os.path.abspath(str(self.base)))
        lexical = Path(os.path.abspath(str(case)))
        if lexical.parent != base or os.path.commonpath((str(base), str(lexical))) != str(base):
            raise AssertionError('fixture case outside synthetic base')
        base_stat = base.lstat()
        if not stat.S_ISDIR(base_stat.st_mode) or getattr(base_stat, 'st_file_attributes', 0) & self._FILE_ATTRIBUTE_REPARSE_POINT:
            raise AssertionError('fixture synthetic base must be an ordinary non-reparse directory')
        self._fixture_validate_ancestor_chain(base)
        if case.exists():
            raise AssertionError('fixture creation requires an absent path')
        advapi, kernel = self._fixture_api()
        if not kernel.CreateDirectoryW(str(case), None):
            raise AssertionError(f'fixture exclusive directory creation failed; Win32 error {ctypes.get_last_error()}')
        flags = self._FILE_FLAG_BACKUP_SEMANTICS | self._FILE_FLAG_OPEN_REPARSE_POINT
        handle = kernel.CreateFileW(str(case), self._READ_CONTROL | self._FILE_READ_ATTRIBUTES,
                                    self._FILE_SHARE_READ | self._FILE_SHARE_WRITE, None,
                                    self._OPEN_EXISTING, flags, None)
        if handle in (None, self._INVALID_HANDLE_VALUE):
            raise AssertionError(f'fixture creation handle failed; Win32 error {ctypes.get_last_error()}')
        try:
            identity = self._fixture_handle_identity(kernel, handle)
            if not identity[3] & self._FILE_ATTRIBUTE_DIRECTORY or identity[3] & self._FILE_ATTRIBUTE_REPARSE_POINT:
                raise AssertionError('fixture creation handle is not an ordinary directory')
            capability = self._FixtureCreationCapability(
                self, case, handle, identity, (base_stat.st_dev, base_stat.st_ino), kernel, advapi)
            self._fixture_capability_tokens.add(capability.token)
            self.addCleanup(self._fixture_cleanup_capability, capability)
            return capability
        except BaseException as primary:
            try:
                if not kernel.CloseHandle(handle):
                    self._fixture_record_cleanup_error(AssertionError('CloseHandle failed after factory error'), primary)
            except BaseException as cleanup_error:
                self._fixture_record_cleanup_error(cleanup_error, primary)
            raise

    def _fixture_cleanup_capability(self, capability):
        cleanup_error = self._fixture_release_capability(capability)
        if cleanup_error is not None:
            self._fixture_record_cleanup_error(cleanup_error)
            raise cleanup_error

    def _fixture_validate_ancestor_chain(self, leaf):
        chain = []
        current = Path(leaf)
        while True:
            chain.append(current)
            if current.parent == current:
                break
            current = current.parent
        for ancestor in reversed(chain):
            info = ancestor.lstat()
            if not stat.S_ISDIR(info.st_mode) or getattr(info, 'st_file_attributes', 0) & self._FILE_ATTRIBUTE_REPARSE_POINT:
                raise AssertionError(f'fixture ancestor is not an ordinary non-reparse directory: {ancestor}')

    def _fixture_handle_identity(self, kernel, handle):
        info = self._BY_HANDLE_FILE_INFORMATION()
        self._fixture_require_win32(
            kernel.GetFileInformationByHandle(handle, ctypes.byref(info)),
            'GetFileInformationByHandle failed')
        return (int(info.VolumeSerialNumber), int(info.FileIndexHigh),
                int(info.FileIndexLow), int(info.FileAttributes))

    def _fixture_require_win32(self, ok, message):
        if not ok:
            code = ctypes.get_last_error()
            raise AssertionError(f'{message}; Win32 error {code}')

    def _fixture_record_cleanup_error(self, error, primary=None):
        note = f'{type(error).__name__}: {error}'
        self._fixture_cleanup_errors.append(note)
        if primary is not None and hasattr(primary, 'add_note'):
            primary.add_note('fixture cleanup failure: ' + note)

    def _fixture_security_snapshot(self, advapi, sd, require_three=True):
        self._fixture_require_win32(advapi.IsValidSecurityDescriptor(sd), 'invalid security descriptor')
        present = wintypes.BOOL()
        dacl = ctypes.c_void_p()
        defaulted = wintypes.BOOL()
        self._fixture_require_win32(
            advapi.GetSecurityDescriptorDacl(sd, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)),
            'GetSecurityDescriptorDacl failed')
        if not present.value or not dacl.value:
            raise AssertionError('fixture requires present non-null DACL')
        info = self._ACL_SIZE_INFORMATION()
        self._fixture_require_win32(
            advapi.GetAclInformation(dacl, ctypes.byref(info), ctypes.sizeof(info), self._ACL_REVISION_INFORMATION),
            'GetAclInformation failed')
        if require_three and info.AceCount != 3:
            raise AssertionError(f'fixture requires exactly three ACEs; got {info.AceCount}')
        owner = ctypes.c_void_p()
        owner_defaulted = wintypes.BOOL()
        group = ctypes.c_void_p()
        group_defaulted = wintypes.BOOL()
        self._fixture_require_win32(
            advapi.GetSecurityDescriptorOwner(sd, ctypes.byref(owner), ctypes.byref(owner_defaulted)),
            'GetSecurityDescriptorOwner failed')
        self._fixture_require_win32(
            advapi.GetSecurityDescriptorGroup(sd, ctypes.byref(group), ctypes.byref(group_defaulted)),
            'GetSecurityDescriptorGroup failed')
        owner_bytes = None if not owner.value else ctypes.string_at(owner, advapi.GetLengthSid(owner))
        group_bytes = None if not group.value else ctypes.string_at(group, advapi.GetLengthSid(group))
        control = wintypes.WORD(); revision = wintypes.DWORD()
        self._fixture_require_win32(
            advapi.GetSecurityDescriptorControl(sd, ctypes.byref(control), ctypes.byref(revision)),
            'GetSecurityDescriptorControl failed')
        return {'dacl': ctypes.string_at(dacl, info.AclBytesInUse),
                'ace_count': info.AceCount, 'owner': owner_bytes, 'group': group_bytes,
                'control': int(control.value), 'revision': int(revision.value),
                'protected': bool(control.value & self._SE_DACL_PROTECTED),
                'unexpected_ace_count': info.AceCount != 3,
                'owner_defaulted': bool(owner_defaulted.value),
                'group_defaulted': bool(group_defaulted.value)}

    def _fixture_read_security_handle(self, advapi, kernel, handle):
        owner = ctypes.c_void_p(); group = ctypes.c_void_p(); dacl = ctypes.c_void_p()
        sacl = ctypes.c_void_p(); sd = ctypes.c_void_p()
        flags = (self._OWNER_SECURITY_INFORMATION | self._GROUP_SECURITY_INFORMATION |
                 self._DACL_SECURITY_INFORMATION)
        rc = advapi.GetSecurityInfo(handle, self._SE_FILE_OBJECT, flags,
                                    ctypes.byref(owner), ctypes.byref(group), ctypes.byref(dacl),
                                    ctypes.byref(sacl), ctypes.byref(sd))
        if rc != self._ERROR_SUCCESS:
            raise AssertionError(f'GetSecurityInfo failed; Win32 error {rc}')
        primary_error = None
        try:
            return self._fixture_security_snapshot(advapi, sd, require_three=False)
        except BaseException as error:
            primary_error = error
            raise
        finally:
            if sd.value:
                try:
                    if kernel.LocalFree(sd):
                        raise AssertionError('LocalFree failed after security read')
                except BaseException as cleanup_error:
                    self._fixture_record_cleanup_error(cleanup_error, primary_error)
                    if primary_error is None:
                        raise

    def _fixture_read_security_path(self, path):
        advapi, kernel = self._fixture_api()
        access = self._READ_CONTROL | self._FILE_READ_ATTRIBUTES
        flags = self._FILE_FLAG_BACKUP_SEMANTICS | self._FILE_FLAG_OPEN_REPARSE_POINT
        handle = kernel.CreateFileW(str(path), access, self._FILE_SHARE_READ | self._FILE_SHARE_WRITE,
                                    None, self._OPEN_EXISTING, flags, None)
        if handle in (None, self._INVALID_HANDLE_VALUE):
            raise AssertionError(f'fixture security open failed; Win32 error {ctypes.get_last_error()}')
        primary_error = None
        try:
            return self._fixture_read_security_handle(advapi, kernel, handle)
        except BaseException as error:
            primary_error = error
            raise
        finally:
            try:
                if not kernel.CloseHandle(handle):
                    raise AssertionError('CloseHandle failed')
            except BaseException as cleanup_error:
                self._fixture_record_cleanup_error(cleanup_error, primary_error)
                if primary_error is None:
                    raise

    def _fixture_set_security(self, advapi, handle, expected_dacl):
        rc = advapi.SetSecurityInfo(handle, self._SE_FILE_OBJECT,
                                    self._DACL_SECURITY_INFORMATION | self._PROTECTED_DACL_SECURITY_INFORMATION,
                                    None, None, expected_dacl, None)
        if rc != self._ERROR_SUCCESS:
            raise AssertionError(f'SetSecurityInfo failed; Win32 error {rc}')

    def _fixture_release_capability(self, capability):
        if not isinstance(capability, self._FixtureCreationCapability) or capability.consumed:
            return None
        handle = capability.handle
        capability.handle = None
        capability.consumed = True
        self._fixture_capability_tokens.discard(capability.token)
        if handle in (None, self._INVALID_HANDLE_VALUE):
            return None
        try:
            if not capability.kernel.CloseHandle(handle):
                return AssertionError('CloseHandle failed for creation handle')
        except BaseException as error:
            return error
        return None

    def _initialize_empty_fixture_security(self, case, creation_capability=None):
        base = Path(os.path.abspath(str(self.base)))
        lexical = Path(os.path.abspath(str(case)))
        if os.path.commonpath((str(base), str(lexical))) != str(base) or lexical.parent != base:
            raise AssertionError('fixture case outside synthetic base')
        if (not isinstance(creation_capability, self._FixtureCreationCapability) or
                creation_capability.owner is not self or creation_capability.consumed or
                creation_capability.token not in self._fixture_capability_tokens or
                creation_capability.path != case):
            raise AssertionError('fixture case was not created by the exclusive fixture factory')
        primary_error = None
        try:
            return self._initialize_empty_fixture_security_owned(case, creation_capability)
        except BaseException as error:
            primary_error = error
            raise
        finally:
            cleanup_error = self._fixture_release_capability(creation_capability)
            if cleanup_error is not None:
                self._fixture_record_cleanup_error(cleanup_error, primary_error)
                if primary_error is None:
                    raise cleanup_error

    def _initialize_empty_fixture_security_owned(self, case, creation_capability):
        base = Path(os.path.abspath(str(self.base)))
        lexical = Path(os.path.abspath(str(case)))
        if os.path.commonpath((str(base), str(lexical))) != str(base) or lexical.parent != base:
            raise AssertionError('fixture case outside synthetic base')
        if creation_capability.handle in (None, self._INVALID_HANDLE_VALUE):
            raise AssertionError('fixture case was not created by the exclusive fixture factory')
        base_stat = base.lstat()
        if not stat.S_ISDIR(base_stat.st_mode) or getattr(base_stat, 'st_file_attributes', 0) & 0x400:
            raise AssertionError('fixture synthetic base must be an ordinary non-reparse directory')
        self._fixture_validate_ancestor_chain(base)
        if (base_stat.st_dev, base_stat.st_ino) != creation_capability.base_identity:
            raise AssertionError('fixture synthetic base identity changed')
        before_stat = case.lstat()
        if not stat.S_ISDIR(before_stat.st_mode) or getattr(before_stat, 'st_file_attributes', 0) & 0x400:
            raise AssertionError('fixture case must be an ordinary non-reparse directory')
        before_id = (before_stat.st_dev, before_stat.st_ino)
        advapi, kernel = creation_capability.advapi, creation_capability.kernel
        expected_sd = ctypes.c_void_p(); expected_len = wintypes.ULONG()
        handle = None
        creation_handle = creation_capability.handle
        primary_error = None
        try:
            self._fixture_require_win32(
                advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                    self._FIXTURE_SDDL, 1, ctypes.byref(expected_sd), ctypes.byref(expected_len)),
                'ConvertStringSecurityDescriptorToSecurityDescriptorW failed')
            expected = self._fixture_security_snapshot(advapi, expected_sd, require_three=True)
            if not expected['protected'] or expected['ace_count'] != 3:
                raise AssertionError('fixture SDDL did not produce the required protected three-ACE DACL')
            access = self._READ_CONTROL | self._WRITE_DAC | self._FILE_READ_ATTRIBUTES
            flags = self._FILE_FLAG_BACKUP_SEMANTICS | self._FILE_FLAG_OPEN_REPARSE_POINT
            creation_identity = creation_capability.identity
            if not creation_identity[3] & self._FILE_ATTRIBUTE_DIRECTORY or creation_identity[3] & self._FILE_ATTRIBUTE_REPARSE_POINT:
                raise AssertionError('fixture creation handle is not an ordinary directory')
            handle = kernel.CreateFileW(str(case), access, self._FILE_SHARE_READ | self._FILE_SHARE_WRITE,
                                        None, self._OPEN_EXISTING, flags, None)
            if handle in (None, self._INVALID_HANDLE_VALUE):
                raise AssertionError(f'fixture case open failed; Win32 error {ctypes.get_last_error()}')
            opened_stat = case.lstat()
            if (opened_stat.st_dev, opened_stat.st_ino) != before_id:
                raise AssertionError('fixture case identity changed before security read')
            if any(case.iterdir()):
                raise AssertionError('fixture case must be empty before inheritance initialization')
            handle_identity = self._fixture_handle_identity(kernel, handle)
            if handle_identity[3] & 0x400:
                raise AssertionError('fixture case handle is a reparse point')
            if handle_identity != creation_identity:
                raise AssertionError('fixture case identity differs from creation handle')
            before = self._fixture_read_security_handle(advapi, kernel, handle)
            if before['ace_count'] != 3:
                raise AssertionError(f'fixture case has unexpected ACE count {before["ace_count"]}')
            if before['dacl'] != expected['dacl']:
                raise AssertionError('fixture case initial DACL is not the exact approved policy')
            dacl_present = wintypes.BOOL(); expected_dacl = ctypes.c_void_p(); dacl_defaulted = wintypes.BOOL()
            self._fixture_require_win32(
                advapi.GetSecurityDescriptorDacl(expected_sd, ctypes.byref(dacl_present),
                                                  ctypes.byref(expected_dacl), ctypes.byref(dacl_defaulted)),
                'GetSecurityDescriptorDacl on expected descriptor failed')
            self._fixture_validate_ancestor_chain(base)
            pre_set_stat = case.lstat()
            if ((pre_set_stat.st_dev, pre_set_stat.st_ino) != before_id or
                    any(case.iterdir()) or
                    self._fixture_handle_identity(kernel, handle) != creation_identity or
                    self._fixture_handle_identity(kernel, creation_handle) != creation_identity or
                    (base.lstat().st_dev, base.lstat().st_ino) != creation_capability.base_identity):
                raise AssertionError('fixture case identity or emptiness changed before setter')
            self._fixture_set_security(advapi, handle, expected_dacl)
            after = self._fixture_read_security_handle(advapi, kernel, handle)
            if after['owner'] != before['owner'] or after['group'] != before['group']:
                raise AssertionError('fixture initializer changed owner or group')
            if (after['dacl'] != expected['dacl'] or
                    (after['control'] & self._SE_DACL_PROTECTED) !=
                    (expected['control'] & self._SE_DACL_PROTECTED) or
                    not after['protected']):
                raise AssertionError('fixture initializer changed exact DACL policy')
            after_stat = case.lstat()
            if ((after_stat.st_dev, after_stat.st_ino) != before_id or
                    any(case.iterdir()) or
                    self._fixture_handle_identity(kernel, handle) != creation_identity or
                    self._fixture_handle_identity(kernel, creation_handle) != creation_identity):
                raise AssertionError('fixture case identity or emptiness changed during initialization')
            return {'before': before, 'after': after, 'identity': list(before_id),
                    'expected_dacl': expected['dacl'].hex()}
        except BaseException as error:
            primary_error = error
            raise
        finally:
            cleanup_errors = []
            if handle not in (None, self._INVALID_HANDLE_VALUE):
                try:
                    if not kernel.CloseHandle(handle):
                        cleanup_errors.append(AssertionError('CloseHandle failed during fixture cleanup'))
                except BaseException as error:
                    cleanup_errors.append(error)
            if expected_sd.value:
                try:
                    if kernel.LocalFree(expected_sd):
                        cleanup_errors.append(AssertionError('LocalFree failed during fixture cleanup'))
                except BaseException as error:
                    cleanup_errors.append(error)
            for cleanup_error in cleanup_errors:
                self._fixture_record_cleanup_error(cleanup_error, primary_error)
            if cleanup_errors and primary_error is None:
                cleanup_error = cleanup_errors[0]
                if primary_error is None:
                    raise cleanup_error

    def test_fixture_initializer_refuses_nonempty_case(self):
        case = self.base / 'nonempty-fixture-case'
        capability = self._fixture_create_case(case)
        (case / 'payload').write_bytes(b'no payload')
        with self.assertRaisesRegex(AssertionError, '^fixture case must be empty'):
            self._initialize_empty_fixture_security(case, capability)

    def test_fixture_initializer_refuses_unregistered_existing_case(self):
        case = self.base / 'unregistered-fixture-case'
        case.mkdir()
        with self.assertRaisesRegex(AssertionError, '^fixture case was not created by the exclusive fixture factory'):
            self._initialize_empty_fixture_security(case)

    def test_factory_capability_cleanup_is_single_use(self):
        case = self.base / 'caller-abort-fixture-case'
        capability = self._fixture_create_case(case)
        self._fixture_cleanup_capability(capability)
        self._fixture_cleanup_capability(capability)
        self.assertTrue(capability.consumed)
        self.assertIsNone(capability.handle)

    def test_fixture_initializer_refuses_out_of_scope_case(self):
        with self.assertRaisesRegex(AssertionError, '^fixture case outside synthetic base'):
            self._initialize_empty_fixture_security(self.base.parent / 'outside-fixture-case')

    def test_fixture_initializer_refuses_reparse_case(self):
        case = self.base / 'reparse-fixture-case'
        capability = self._fixture_create_case(case)
        fake = type('FakeStat', (), {'st_mode': stat.S_IFDIR, 'st_file_attributes': 0x400,
                                     'st_dev': 1, 'st_ino': 1})()
        real_lstat = Path.lstat
        def case_only_lstat(path):
            if Path(path) == case:
                return fake
            return real_lstat(path)
        with patch.object(Path, 'lstat', side_effect=case_only_lstat, autospec=True):
            with patch.object(self, '_fixture_set_security') as setter:
                with self.assertRaisesRegex(AssertionError, '^fixture case must be an ordinary non-reparse directory'):
                    self._initialize_empty_fixture_security(case, capability)
                setter.assert_not_called()

    def test_fixture_initializer_refuses_identity_change(self):
        case = self.base / 'identity-fixture-case'
        capability = self._fixture_create_case(case)
        first = type('FakeStat', (), {'st_mode': stat.S_IFDIR, 'st_file_attributes': 0,
                                      'st_dev': 1, 'st_ino': 1})()
        second = type('FakeStat', (), {'st_mode': stat.S_IFDIR, 'st_file_attributes': 0,
                                       'st_dev': 1, 'st_ino': 2})()
        real_lstat = Path.lstat
        calls = []
        def case_only_lstat(path):
            if Path(path) == case:
                calls.append(len(calls) + 1)
                return first if len(calls) == 1 else second
            return real_lstat(path)
        with patch.object(Path, 'lstat', side_effect=case_only_lstat, autospec=True):
            with patch.object(self, '_fixture_set_security') as setter:
                with self.assertRaisesRegex(AssertionError, '^fixture case identity changed before security read'):
                    self._initialize_empty_fixture_security(case, capability)
                setter.assert_not_called()

    def test_fixture_initializer_refuses_reparse_ancestor_before_setter(self):
        case = self.base / 'reparse-ancestor-fixture-case'
        capability = self._fixture_create_case(case)
        fake = type('FakeStat', (), {'st_mode': stat.S_IFDIR, 'st_file_attributes': 0x400,
                                     'st_dev': 1, 'st_ino': 1})()
        with patch.object(Path, 'lstat', return_value=fake):
            with patch.object(self, '_fixture_set_security') as setter:
                with self.assertRaisesRegex(AssertionError, '^fixture synthetic base must be an ordinary non-reparse directory'):
                    self._initialize_empty_fixture_security(case, capability)
                setter.assert_not_called()

    def test_fixture_initializer_refuses_wrong_opened_handle_before_setter(self):
        case = self.base / 'wrong-handle-fixture-case'
        capability = self._fixture_create_case(case)
        with patch.object(self, '_fixture_handle_identity', return_value=(1, 2, 4, self._FILE_ATTRIBUTE_DIRECTORY)):
            with patch.object(self, '_fixture_set_security') as setter:
                with self.assertRaisesRegex(AssertionError, '^fixture case identity differs from creation handle'):
                    self._initialize_empty_fixture_security(case, capability)
                setter.assert_not_called()

    def test_fixture_initializer_preserves_no_payload_on_setter_failure(self):
        case = self.base / 'setter-failure-fixture-case'
        capability = self._fixture_create_case(case)
        with patch.object(self, '_fixture_set_security', side_effect=RuntimeError('injected setter failure')):
            with self.assertRaisesRegex(RuntimeError, '^injected setter failure$'):
                self._initialize_empty_fixture_security(case, capability)
        self.assertEqual(list(case.iterdir()), [])

    def test_fixture_initializer_preserves_no_payload_on_readback_failure(self):
        case = self.base / 'readback-failure-fixture-case'
        capability = self._fixture_create_case(case)
        original = self._fixture_read_security_handle
        calls = []
        def forwarding(*args):
            calls.append(len(calls) + 1)
            if len(calls) == 1:
                return original(*args)
            raise RuntimeError('injected readback failure')
        with patch.object(self, '_fixture_read_security_handle', side_effect=forwarding):
            with self.assertRaisesRegex(RuntimeError, '^injected readback failure$'):
                self._initialize_empty_fixture_security(case, capability)
        self.assertEqual(calls, [1, 2])
        self.assertEqual(list(case.iterdir()), [])

    def test_set_security_uses_dword_return_code(self):
        class FakeAdvapi:
            def __init__(self, result):
                self.result = result
            def SetSecurityInfo(self, *args):
                return self.result
        self._fixture_set_security(FakeAdvapi(0), object(), object())
        with self.assertRaisesRegex(AssertionError, '^SetSecurityInfo failed; Win32 error 5$'):
            self._fixture_set_security(FakeAdvapi(5), object(), object())

    def test_primary_read_error_survives_close_error(self):
        primary = RuntimeError('primary read failure')
        class FakeKernel:
            def CreateFileW(self, *args):
                return object()
            def CloseHandle(self, handle):
                raise RuntimeError('cleanup close failure')
        with patch.object(self, '_fixture_api', return_value=(object(), FakeKernel())):
            with patch.object(self, '_fixture_read_security_handle', side_effect=primary):
                with self.assertRaisesRegex(RuntimeError, '^primary read failure$') as raised:
                    self._fixture_read_security_path(self.case)
        self.assertIs(raised.exception, primary)

    def test_localfree_failure_is_recorded_and_fails_cleanup_only(self):
        class FakeAdvapi:
            def GetSecurityInfo(self, *args):
                args[-1]._obj.value = 1
                return 0
        class FakeKernel:
            def LocalFree(self, value):
                return 1
        with patch.object(self, '_fixture_security_snapshot', return_value={}):
            with self.assertRaisesRegex(AssertionError, '^LocalFree failed after security read$'):
                self._fixture_read_security_handle(FakeAdvapi(), FakeKernel(), object())
        self.assertTrue(any('LocalFree failed after security read' in item for item in self._fixture_cleanup_errors))
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

    def _fixture_json_projection(self, value):
        if isinstance(value, bytes):
            return {'length': len(value), 'hex': value.hex()}
        if isinstance(value, dict):
            return {key: self._fixture_json_projection(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._fixture_json_projection(item) for item in value]
        return value

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
        try:
            fixture_security = dict(self._fixture_security)
            if self.io.tx.exists():
                fixture_security['transaction'] = self._fixture_read_security_path(self.io.tx)
            report['fixture_security'] = self._fixture_json_projection(fixture_security)
        except Exception as diagnostic_error:
            report['fixture_security_error'] = f'{type(diagnostic_error).__name__}: {diagnostic_error}'
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

    def test_failure_diagnostic_is_json_and_preserves_original_exception(self):
        original_error = core.Refused('original core failure')
        self.io.tx.mkdir()
        (self.io.tx / 'journal.jsonl').write_text('{"event":"stopped"}\n')
        with patch.object(core, 'execute', side_effect=original_error):
            with patch('builtins.print') as printer:
                with self.assertRaises(core.Refused) as raised:
                    self.run_repair()
        self.assertIs(raised.exception, original_error)
        payload = printer.call_args.args[0]
        prefix = 'DISPOSABLE_REPLACE_FAILURE='
        self.assertTrue(payload.startswith(prefix))
        report = json.loads(payload[len(prefix):])
        self.assertIn('fixture_security', report)
        self.assertEqual(report['core_exception'], str(original_error))
        self.assertIn('case', report['fixture_security'])
        self.assertIn('synthetic_pages', report['fixture_security'])
        self.assertIn('transaction', report['fixture_security'])
        self.assertIn('replacements', report)
        self.assertIn('transaction_journal', report)
        self.assertIn('journal_bytes', report['transaction_journal'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-disposable', action='store_true')
    args = parser.parse_args()
    if not args.run_disposable:
        parser.error('Explicit --run-disposable required')
    unittest.main(argv=[__file__], verbosity=2)
