"""Unexecuted Windows adapter for exact_profile_repair. No CLI/autorun.
Requires external writer quiescence; ReplaceFileW is NOT atomic CAS.
No Git, process query, network, scheduler, provider or browser operations.
"""
from pathlib import Path
import ctypes
from ctypes import wintypes
import json
import os
import shutil
import stat
from exact_profile_repair import ROOT, ROWS, RESERVE_BYTES, Snapshot, require
TX_PARENT = None
TX_NAME = 'CI_SYNTHETIC_TRANSACTION_MUST_BE_BOUND'

class WindowsBackend:

    def __init__(self):
        require(os.name == 'nt', 'Windows-only adapter; never emulate native calls')
        self.root = Path(ROOT)
        self.tx = TX_PARENT / TX_NAME
        self.names = {r['path'] for r in ROWS}
        self.original = {}
        self.candidates = {}
        self.tx_id = None
        self.required_security = None
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.advapi = ctypes.WinDLL('advapi32', use_last_error=True)
        self.replace = self.kernel.ReplaceFileW
        self.replace.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p]
        self.replace.restype = wintypes.BOOL
        self.getsecurity = self.advapi.GetFileSecurityW
        self.getsecurity.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        self.getsecurity.restype = wintypes.BOOL

    def ordinary(self, p, file=False):
        require(p.is_absolute(), 'absolute path required')
        for q in [*reversed(p.parents), p]:
            s = q.lstat()
            require(not stat.S_ISLNK(s.st_mode) and (not getattr(s, 'st_file_attributes', 0) & 1024), 'redirected path refused')
        s = p.lstat()
        if file:
            require(stat.S_ISREG(s.st_mode) and s.st_nlink == 1, 'ordinary independent file required')
        else:
            require(stat.S_ISDIR(s.st_mode), 'ordinary directory required')
        require(os.path.normcase(str(p)) == os.path.normcase(str(p.resolve(strict=True))), 'path identity changed')
        return s

    def security(self, p):
        needed = wintypes.DWORD()
        self.getsecurity(str(p), 7, None, 0, ctypes.byref(needed))
        require(0 < needed.value <= 65536, 'security descriptor unavailable')
        buf = ctypes.create_string_buffer(needed.value)
        if not self.getsecurity(str(p), 7, buf, len(buf), ctypes.byref(needed)):
            raise ctypes.WinError(ctypes.get_last_error())
        return bytes(buf[:needed.value])

    def reject_extra_streams(self, p):

        class StreamData(ctypes.Structure):
            _fields_ = [('size', ctypes.c_longlong), ('name', wintypes.WCHAR * 296)]
        first = self.kernel.FindFirstStreamW
        first.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(StreamData), wintypes.DWORD]
        first.restype = wintypes.HANDLE
        nxt = self.kernel.FindNextStreamW
        nxt.argtypes = [wintypes.HANDLE, ctypes.POINTER(StreamData)]
        nxt.restype = wintypes.BOOL
        close = self.kernel.FindClose
        close.argtypes = [wintypes.HANDLE]
        close.restype = wintypes.BOOL
        data = StreamData()
        handle = first(str(p), 0, ctypes.byref(data), 0)
        require(handle not in (None, ctypes.c_void_p(-1).value), 'stream inventory unavailable')
        try:
            require(data.name == '::$DATA', 'alternate stream refused')
            while nxt(handle, ctypes.byref(data)):
                require(data.name == '::$DATA', 'alternate stream refused')
            require(ctypes.get_last_error() == 38, 'stream enumeration incomplete')
        finally:
            if not close(handle):
                raise ctypes.WinError(ctypes.get_last_error())

    def snapshot(self, name):
        require(name in self.names, 'out-of-scope target')
        p = self.root / name
        s = self.ordinary(p, True)
        identity = (s.st_dev, s.st_ino)
        require(s.st_size <= 100000, 'target too large')
        attrs = getattr(s, 'st_file_attributes', 0)
        require(attrs in (32, 128), 'unsupported file attributes; no attribute changes authorized')
        sec = self.security(p)
        self.reject_extra_streams(p)
        if self.required_security is None:
            self.required_security = sec
        require(sec == self.required_security, 'target security differs; per-target transaction policy requires review')
        with p.open('rb') as f:
            raw = f.read(100001)
        a = self.ordinary(p, True)
        require((a.st_dev, a.st_ino) == identity and a.st_size == len(raw) and (self.security(p) == sec) and (getattr(a, 'st_file_attributes', 0) == attrs), 'input changed while reading')
        return Snapshot(raw, identity, sec, attrs)

    def admit_storage(self, projected):
        self.ordinary(self.root)
        self.ordinary(TX_PARENT)
        require(self.root.stat().st_dev == TX_PARENT.stat().st_dev, 'same volume required for replacement')
        require(shutil.disk_usage(TX_PARENT).free >= RESERVE_BYTES + projected, 'retain unchanged 2GiB reserve plus projected writes')
        self.parent_id = (TX_PARENT.stat().st_dev, TX_PARENT.stat().st_ino)
        self.root_id = (self.root.stat().st_dev, self.root.stat().st_ino)

    def claim(self):
        require(not os.path.lexists(self.tx), 'transaction collision; never reuse/delete')
        self.tx.mkdir()
        s = self.ordinary(self.tx)
        self.tx_id = (s.st_dev, s.st_ino)
        self.write_new(self.tx / 'journal.jsonl', b'', self.required_security)

    def check_tx(self):
        require(self.tx_id is not None, 'transaction not claimed')
        for path, identity in [(self.tx, self.tx_id), (TX_PARENT, self.parent_id), (self.root, self.root_id)]:
            s = self.ordinary(path)
            require((s.st_dev, s.st_ino) == identity, 'directory identity changed')

    def token(self, name):
        require(name in self.names, 'unknown path')
        return str([r['path'] for r in ROWS].index(name))

    def write_new(self, p, raw, expected_security):
        with p.open('xb') as f:
            require(self.security(p) == expected_security, 'new empty file security differs; no payload written')
            n = f.write(raw)
            require(n == len(raw), 'short write')
            f.flush()
            os.fsync(f.fileno())
        s = self.ordinary(p, True)
        require(s.st_size == len(raw), 'persisted length differs')
        require(p.read_bytes() == raw, 'persisted bytes differ')

    def journal(self, record):
        self.check_tx()
        p = self.tx / 'journal.jsonl'
        self.ordinary(p, True)
        require(self.security(p) == self.required_security, 'journal permissions changed')
        raw = (json.dumps(record, sort_keys=True, allow_nan=False) + '\n').encode()
        with p.open('ab') as f:
            n = f.write(raw)
            require(n == len(raw), 'short journal write')
            f.flush()
            os.fsync(f.fileno())
        require(p.read_bytes().endswith(raw), 'journal persistence verification failed')

    def create_backup(self, name, original):
        self.check_tx()
        self.write_new(self.tx / (self.token(name) + '.preimage'), original.raw, original.security)

    def read_backup(self, name):
        self.check_tx()
        p = self.tx / (self.token(name) + '.preimage')
        s = self.ordinary(p, True)
        require(s.st_size <= 100000, 'backup too large')
        require(self.security(p) == self.required_security, 'backup permissions changed')
        self.reject_extra_streams(p)
        return p.read_bytes()

    def prepare_candidate(self, name, raw, original):
        self.check_tx()
        p = self.tx / (self.token(name) + '.candidate')
        self.write_new(p, raw, original.security)
        s = self.ordinary(p, True)
        require(self.security(p) == original.security and getattr(s, 'st_file_attributes', 0) == original.attributes, 'prepared candidate metadata differs; refuse before first swap')
        self.reject_extra_streams(p)
        self.candidates[name] = (p, (s.st_dev, s.st_ino), raw)

    def atomic_replace(self, name, raw, expected):
        from exact_profile_repair import same
        self.check_tx()
        p, identity, saved = self.candidates[name]
        s = self.ordinary(p, True)
        require((s.st_dev, s.st_ino) == identity and saved == raw and (p.read_bytes() == raw), 'candidate changed')
        require(self.security(p) == expected.security and getattr(s, 'st_file_attributes', 0) == expected.attributes, 'candidate ownership/group/DACL/attributes differ')
        self.reject_extra_streams(p)
        require(same(self.snapshot(name), expected), 'last pre-replacement compare failed')
        if not self.replace(str(self.root / name), str(p), None, 0, None, None):
            raise ctypes.WinError(ctypes.get_last_error())
        return identity
