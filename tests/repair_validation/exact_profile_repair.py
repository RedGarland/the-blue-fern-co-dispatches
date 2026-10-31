"""Private exact seven-file repair library. Importing executes no filesystem work.
Not a generation, Git, scheduler or publication tool. No CLI and no autorun.
External writer quiescence is mandatory: Windows ReplaceFileW is not an atomic
compare-and-swap primitive. No claim that path checks exclude hostile races.
"""
from __future__ import annotations
import hashlib
import json
from dataclasses import dataclass
ROOT = 'CI_SYNTHETIC_ROOT_MUST_BE_BOUND'
RESERVE_BYTES = 2 * 1024 ** 3
ROWS = []

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def require(condition, message):
    if not condition:
        raise Refused(message)

class Refused(RuntimeError):
    pass

@dataclass(frozen=True)
class Snapshot:
    raw: bytes
    identity: tuple
    security: bytes
    attributes: int

def same(a, b):
    return a.raw == b.raw and a.identity == b.identity and (a.security == b.security) and (a.attributes == b.attributes)

def validate(rows=ROWS):
    require(len(rows) == 8 and len({r['path'] for r in rows}) == 8, 'fixed eight-file manifest required')
    require(sum((r['action'] == 'proposed_exact_blob_restore' for r in rows)) == 7, 'exactly seven writes required')
    return rows

def preflight(io):
    """Read-only target proof; no backup claim or mutation."""
    originals = {}
    candidates = {}
    for r in validate():
        snap = io.snapshot(r['path'])
        require(len(snap.raw) == r['expected_observed_working_size'] and sha(snap.raw) == r['expected_observed_working_sha256'], 'preimage mismatch: ' + r['path'])
        originals[r['path']] = snap
        if r['action'] == 'no_write':
            require(sha(snap.raw) == r['canonical_sha256'], 'unchanged leaf mismatch')
        else:
            raw = snap.raw.replace(b'\r\n', b'\n')
            require(len(raw) == r['canonical_size'] and sha(raw) == r['canonical_sha256'], 'canonical derivation mismatch: ' + r['path'])
            candidates[r['path']] = raw
    return (originals, candidates)

def execute(io, *, writer_quiescence_confirmed=False, active_write_authorized=False):
    """One attempt, backups-first; io must be independently reviewed backend.
    No automatic rollback. Every exception stops further replacements.
    A failure can leave a partial transaction, including an uncertain last swap.
    """
    require(writer_quiescence_confirmed is True and active_write_authorized is True, 'explicit authorization and external writer quiescence required')
    original, candidates = preflight(io)
    io.admit_storage(sum((len(s.raw) for s in original.values())) + sum(map(len, candidates.values())) + 1024 * 1024)
    io.claim()
    complete = []
    try:
        io.journal({'event': 'begin', 'paths': list(candidates), 'atomic_multi_file': False, 'external_writer_quiescence_required': True})
        for path, raw in candidates.items():
            io.create_backup(path, original[path])
            require(io.read_backup(path) == original[path].raw, 'backup verification failed')
            io.prepare_candidate(path, raw, original[path])
        for path in candidates:
            require(io.read_backup(path) == original[path].raw, 'backup changed before first replacement')
            require(same(io.snapshot(path), original[path]), 'target changed during backup')
        io.journal({'event': 'backups_verified', 'records': [{'path': p, 'pre_sha256': sha(original[p].raw), 'pre_identity': original[p].identity, 'pre_security_sha256': sha(original[p].security), 'pre_security_hex': original[p].security.hex(), 'pre_attributes': original[p].attributes, 'post_sha256': sha(candidates[p])} for p in candidates]})
        for path, raw in candidates.items():
            for r in ROWS:
                p = r['path']
                now = io.snapshot(p)
                expected = next((entry['post'] for entry in complete if entry['path'] == p), original[p])
                require(same(now, expected), 'transaction input drift: ' + p)
            require(io.read_backup(path) == original[path].raw, 'backup drift before replacement')
            io.journal({'event': 'replace_intent', 'path': path, 'pre_sha256': sha(original[path].raw), 'post_sha256': sha(raw)})
            candidate_identity = io.atomic_replace(path, raw, original[path])
            post = io.snapshot(path)
            require(post.identity == candidate_identity, 'replacement identity differs from prepared candidate')
            require(post.raw == raw, 'postimage bytes differ')
            require(post.security == original[path].security and post.attributes == original[path].attributes, 'permissions/attributes not preserved')
            complete.append({'path': path, 'post': post})
            io.journal({'event': 'replaced', 'path': path, 'post_identity': post.identity, 'post_sha256': sha(post.raw)})
        for r in ROWS:
            snap = io.snapshot(r['path'])
            require(len(snap.raw) == r['canonical_size'] and sha(snap.raw) == r['canonical_sha256'], 'final eight-file verification failed')
            expected = next((e['post'] for e in complete if e['path'] == r['path']), original[r['path']])
            require(same(snap, expected), 'final identity/security drift')
        io.journal({'event': 'success', 'replaced': len(complete), 'verified': 8, 'generation_started': False})
        return {'ok': True, 'replaced': 7, 'verified': 8, 'rollback_automatic': False}
    except Exception as error:
        try:
            io.journal({'event': 'stopped', 'error_type': type(error).__name__, 'verified_replacements': [e['path'] for e in complete], 'last_replacement_may_be_uncertain': True})
        except Exception:
            pass
        raise

def rollback_eligibility(io, *, path, expected_post_identity, expected_security, expected_attributes):
    """Read-only proposal. Never rolls back automatically or accepts byte-only CAS.
    Caller must obtain identity from a flushed/read-back journal record of a successful replacement.
    No power-loss durability guarantee. An uncertain/unjournaled replacement
    requires separate investigation.
    """
    r = next((r for r in ROWS if r['path'] == path and r['action'] == 'proposed_exact_blob_restore'), None)
    require(r is not None, 'rollback path not one of seven targets')
    current = io.snapshot(path)
    backup = io.read_backup(path)
    require(current.identity == tuple(expected_post_identity), 'rollback identity changed')
    require(current.security == expected_security and current.attributes == expected_attributes, 'rollback permissions changed')
    require(sha(current.raw) == r['canonical_sha256'] and len(current.raw) == r['canonical_size'], 'rollback postimage changed')
    require(sha(backup) == r['expected_observed_working_sha256'] and len(backup) == r['expected_observed_working_size'], 'rollback backup mismatch')
    return {'eligible': True, 'path': path, 'restore_sha256': sha(backup), 'requires_separate_authorization': True}
