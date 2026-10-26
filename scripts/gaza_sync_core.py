"""Fail-closed, target-scoped Gaza source synchronizer.

This module is private review code.  It is deliberately independent of the
production runner and has no default that can mutate a real checkout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence


DEFAULT_TARGET = Path(r"C:\BlueFernRunner\GazaDispatchesCurrent6")
DEFAULT_BRANCH = "add/pages-repo-default"
DEFAULT_BACKUP_ROOT = Path(r"D:\Dispatches-Archive\GazaInstallBackups\2026-10-26")
DEFAULT_RESERVE_BYTES = 2 * 1024**3


class SyncError(RuntimeError):
    """A deterministic preflight or verification failure."""


class UnknownMutation(SyncError):
    """A write may have started but its final state cannot be proven."""


@dataclass(frozen=True)
class SyncSpec:
    target: Path
    backup_root: Path
    expected_branch: str
    expected_current_head: str
    desired_head: str
    expected_remote: str | None = None
    reserve_bytes: int = DEFAULT_RESERVE_BYTES
    target_allowlist: tuple[Path, ...] = (DEFAULT_TARGET,)


def _norm(path: Path) -> str:
    return os.path.normcase(str(path.absolute())).replace("\\", "/").rstrip("/")


def _inside(child: Path, parent: Path) -> bool:
    c = _norm(child)
    p = _norm(parent)
    return c == p or c.startswith(p + "/")


def _overlap(left: str, right: str) -> bool:
    a = left.replace("\\", "/").strip("/").split("/")
    b = right.replace("\\", "/").strip("/").split("/")
    return a[: len(b)] == b or b[: len(a)] == a


def _is_reparse_or_link(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        stat = path.lstat()
        attrs = getattr(stat, "st_file_attributes", 0)
        return bool(attrs & getattr(__import__("stat"), "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    except FileNotFoundError:
        return False


def _assert_no_reparse_ancestors(path: Path, *, allow_missing_leaf: bool = False) -> None:
    candidate = path.absolute()
    existing = candidate if candidate.exists() else candidate.parent
    if not allow_missing_leaf and not candidate.exists():
        raise SyncError(f"required path does not exist: {candidate}")
    while True:
        if _is_reparse_or_link(existing):
            raise SyncError(f"reparse or symlink path rejected: {existing}")
        if existing.parent == existing:
            break
        existing = existing.parent


def _assert_safe_backup(
    root: Path,
    target: Path,
    reserve_bytes: int,
    *,
    create: bool,
    projected_bytes: int = 0,
) -> None:
    if not root.is_absolute():
        raise SyncError("backup root must be absolute")
    if _inside(root, target) or _inside(target, root):
        raise SyncError("backup root and target must be disjoint")
    _assert_no_reparse_ancestors(root, allow_missing_leaf=True)
    if root.exists() and not root.is_dir():
        raise SyncError("backup root is not a directory")
    if create:
        root.parent.mkdir(parents=True, exist_ok=True)
        if not root.exists():
            root.mkdir()
    elif not root.exists() and not root.parent.exists():
        raise SyncError("backup root parent does not exist for read-only preflight")
    probe_root = root if root.exists() else root.parent
    probe = root / f".gaza-sync-probe-{uuid.uuid4().hex}"
    if create:
        try:
            probe.write_text("probe", encoding="utf-8")
        except OSError as exc:
            raise SyncError(f"backup root is not writable: {exc}") from exc
        finally:
            probe.unlink(missing_ok=True)
    usage = shutil.disk_usage(probe_root)
    if projected_bytes < 0:
        raise SyncError("projected backup bytes cannot be negative")
    required_free = reserve_bytes + projected_bytes
    if usage.free < required_free:
        raise SyncError(
            f"backup volume below reserve plus projected preimages: "
            f"free={usage.free} reserve={reserve_bytes} projected={projected_bytes}"
        )


def _git(target: Path, args: Sequence[str], *, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", "-C", str(target), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and proc.returncode:
        raise SyncError(f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout


def _git_status(target: Path) -> list[dict[str, str]]:
    raw = _git(target, ["status", "--porcelain=v1", "-z", "--untracked-files=all"])
    records: list[dict[str, str]] = []
    chunks = raw.split("\0")
    for chunk in chunks:
        if not chunk:
            continue
        if len(chunk) < 4 or chunk[2] != " ":
            raise SyncError(f"ambiguous git status record: {chunk!r}")
        status = chunk[:2]
        path = chunk[3:].replace("\\", "/")
        if " -> " in path:
            raise SyncError("rename/copy status is not accepted by this synchronizer")
        if not path:
            raise SyncError("empty git status path")
        records.append({"status": status, "path": path})
    return records


def _dirty_snapshot(target: Path, records: list[dict[str, str]]) -> list[dict[str, str]]:
    snapshot: list[dict[str, str]] = []
    for record in records:
        path = target / Path(record["path"])
        if path.is_file():
            digest = _hash_file(path)
        else:
            digest = "missing"
        snapshot.append({**record, "sha256": digest, "index_oid": _index_oid(target, record["path"])})
    return sorted(snapshot, key=lambda item: (item["path"], item["status"], item["sha256"]))


def _index_oid(target: Path, relative: str) -> str:
    """Return the staged blob identity, including an explicit no-index state."""
    proc = subprocess.run(
        ["git", "-C", str(target), "ls-files", "--stage", "--", relative],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode:
        raise SyncError(f"could not inspect index state for {relative}: {proc.stderr.strip()}")
    rows = [line for line in proc.stdout.splitlines() if line.strip()]
    if not rows:
        return "none"
    oids: list[str] = []
    for row in rows:
        fields = row.split(None, 2)
        if len(fields) < 2:
            raise SyncError(f"ambiguous index record for {relative}: {row!r}")
        oids.append(fields[1])
    if len(set(oids)) != 1:
        return "unmerged:" + ",".join(sorted(oids))
    return oids[0]


def _status_key(records: Iterable[dict[str, str]]) -> list[tuple[str, str]]:
    return sorted((str(item["path"]), str(item["status"])) for item in records)


def _snapshot_key(records: Iterable[dict[str, str]]) -> list[tuple[str, str, str, str]]:
    return sorted(
        (str(item["path"]), str(item["status"]), str(item["sha256"]), str(item.get("index_oid", "missing")))
        for item in records
    )


def _dirty_state_matches(
    target: Path,
    records: list[dict[str, str]],
    expected_snapshot: Iterable[dict[str, str]],
) -> bool:
    """Compare both porcelain state and file bytes, not just path names."""
    return _status_key(records) == _status_key(_git_status(target)) and _snapshot_key(
        _dirty_snapshot(target, records)
    ) == _snapshot_key(expected_snapshot)


def _incoming_paths(target: Path, old: str, new: str) -> list[str]:
    output = _git(target, ["diff-tree", "--no-commit-id", "--name-only", "-r", old, new])
    paths = sorted({line.strip().replace("\\", "/") for line in output.splitlines() if line.strip()})
    if not paths:
        raise SyncError("desired head has no incoming paths")
    return paths


def _ignored_collision_paths(target: Path, incoming: Iterable[str]) -> list[str]:
    collisions: list[str] = []
    for relative in incoming:
        candidate = target / Path(relative)
        if not candidate.exists():
            continue
        tracked = subprocess.run(
            ["git", "-C", str(target), "ls-files", "--error-unmatch", "--", relative],
            capture_output=True,
            text=True,
        )
        if tracked.returncode == 0:
            continue
        ignored = subprocess.run(
            ["git", "-C", str(target), "check-ignore", "-q", "--", relative],
            capture_output=True,
        )
        if ignored.returncode == 0:
            collisions.append(relative)
    return sorted(set(collisions))


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _assert_target_allowed(spec: SyncSpec) -> None:
    target = Path(spec.target).absolute()
    if not any(_norm(target) == _norm(allowed) for allowed in spec.target_allowlist):
        raise SyncError(f"target is outside the Gaza allowlist: {target}")
    _assert_no_reparse_ancestors(target)


def _fetch_named_source(spec: SyncSpec) -> None:
    if not spec.expected_remote:
        raise SyncError("named source fetch requires an expected remote identity")
    proc = subprocess.run(
        ["git", "-C", str(spec.target), "fetch", "--no-tags", "origin", spec.expected_branch],
        capture_output=True,
        text=True,
    )
    if proc.returncode:
        raise SyncError(f"named source fetch failed: {proc.stderr.strip()}")
    fetched = _git(spec.target, ["rev-parse", "FETCH_HEAD"]).strip()
    if fetched != spec.desired_head:
        raise SyncError(f"fetched head drift: {fetched} != {spec.desired_head}")


def _source_preimages(target: Path, current: str, incoming: Iterable[str]) -> dict[str, str | None]:
    preimages: dict[str, str | None] = {}
    for relative in incoming:
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise SyncError(f"unsafe incoming path: {relative}")
        proc = subprocess.run(
            ["git", "-C", str(target), "show", f"{current}:{relative}"],
            capture_output=True,
        )
        preimages[relative] = hashlib.sha256(proc.stdout).hexdigest() if proc.returncode == 0 else None
    return preimages


def _source_preimage_bytes(target: Path, current: str, incoming: Iterable[str]) -> int:
    total = 0
    for relative in incoming:
        proc = subprocess.run(
            ["git", "-C", str(target), "cat-file", "-s", f"{current}:{relative}"],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            try:
                total += int(proc.stdout.strip())
            except ValueError as exc:
                raise SyncError(f"invalid source preimage size: {relative}") from exc
    return total


def preflight(spec: SyncSpec, *, fetch: bool = False) -> dict[str, object]:
    _assert_target_allowed(spec)
    _assert_safe_backup(Path(spec.backup_root).absolute(), Path(spec.target).absolute(), spec.reserve_bytes, create=False)
    inside = _git(spec.target, ["rev-parse", "--is-inside-work-tree"]).strip()
    if inside != "true":
        raise SyncError("target is not a Git work tree")
    branch = _git(spec.target, ["branch", "--show-current"]).strip()
    if branch != spec.expected_branch:
        raise SyncError(f"branch drift: {branch!r} != {spec.expected_branch!r}")
    remote = _git(spec.target, ["remote", "get-url", "origin"]).strip()
    if spec.expected_remote and remote != spec.expected_remote:
        raise SyncError(f"remote drift: {remote!r} != {spec.expected_remote!r}")
    if fetch:
        _fetch_named_source(spec)
    current = _git(spec.target, ["rev-parse", "HEAD"]).strip()
    if current != spec.expected_current_head:
        raise SyncError(f"current HEAD drift: {current} != {spec.expected_current_head}")
    proc = subprocess.run(
        ["git", "-C", str(spec.target), "cat-file", "-e", f"{spec.desired_head}^{{commit}}"],
        capture_output=True,
        text=True,
    )
    if proc.returncode:
        raise SyncError(f"desired commit object unavailable: {spec.desired_head}")
    if subprocess.run(
        ["git", "-C", str(spec.target), "merge-base", "--is-ancestor", current, spec.desired_head],
        capture_output=True,
    ).returncode:
        raise SyncError("desired head is not a fast-forward descendant of current HEAD")
    outside = _git(spec.target, ["rev-list", "--count", f"{spec.desired_head}..{current}"]).strip()
    if outside != "0":
        raise SyncError("local commits are not contained by desired head")
    dirty = _git_status(spec.target)
    incoming = _incoming_paths(spec.target, current, spec.desired_head)
    collisions = sorted(
        {item["path"] for item in dirty for incoming_path in incoming if _overlap(item["path"], incoming_path)}
    )
    if collisions:
        raise SyncError(f"dirty paths overlap incoming source paths: {collisions}")
    preimages = _source_preimages(spec.target, current, incoming)
    ignored_collisions = _ignored_collision_paths(Path(spec.target), incoming)
    if ignored_collisions:
        raise SyncError(f"ignored files collide with incoming source paths: {ignored_collisions}")
    projected_preimage_bytes = _source_preimage_bytes(Path(spec.target), current, incoming)
    _assert_safe_backup(
        Path(spec.backup_root).absolute(),
        Path(spec.target).absolute(),
        spec.reserve_bytes,
        create=False,
        projected_bytes=projected_preimage_bytes,
    )
    return {
        "schema": "gaza_single_desk_sync_preflight_v1",
        "target": str(Path(spec.target).absolute()),
        "backup_root": str(Path(spec.backup_root).absolute()),
        "branch": branch,
        "remote": remote,
        "current_head": current,
        "desired_head": spec.desired_head,
        "dirty": dirty,
        "dirty_snapshot": _dirty_snapshot(Path(spec.target), dirty),
        "incoming_paths": incoming,
        "source_preimages": preimages,
        "projected_preimage_bytes": projected_preimage_bytes,
        "reserve_bytes": spec.reserve_bytes,
        "target_allowlist": [str(Path(item).absolute()) for item in spec.target_allowlist],
    }


def backup(preflight_receipt: dict[str, object]) -> Path:
    backup_root = Path(str(preflight_receipt["backup_root"]))
    run_root = backup_root / f"{preflight_receipt['current_head'][:12]}-{uuid.uuid4().hex}"
    _assert_safe_backup(
        run_root,
        Path(str(preflight_receipt["target"])),
        int(preflight_receipt["reserve_bytes"]),
        create=True,
        projected_bytes=int(preflight_receipt.get("projected_preimage_bytes") or 0),
    )
    source_manifest = run_root / "preinstall-manifest.json"
    _write_json(source_manifest, preflight_receipt)
    target = Path(str(preflight_receipt["target"]))
    preimage_root = run_root / "source-preimages"
    for relative in preflight_receipt.get("incoming_paths", []):
        blob = preflight_receipt.get("source_preimages", {}).get(relative)
        if not blob:
            continue
        proc = subprocess.run(
            ["git", "-C", str(target), "show", f"{preflight_receipt['current_head']}:{relative}"],
            capture_output=True,
        )
        if proc.returncode:
            raise SyncError(f"could not back up source preimage: {relative}")
        destination = preimage_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(proc.stdout)
        if _hash_file(destination) != blob:
            raise SyncError(f"source preimage hash mismatch: {relative}")
    return run_root


def apply(spec: SyncSpec, *, allow_mutation: bool = False, fetch: bool = False) -> dict[str, object]:
    if not allow_mutation:
        raise SyncError("apply requires an explicit mutation authorization")
    receipt = preflight(spec, fetch=fetch)
    backup_root = backup(receipt)
    try:
        proc = subprocess.run(
            ["git", "-C", str(spec.target), "merge", "--ff-only", "--no-overwrite-ignore", spec.desired_head],
            capture_output=True,
            text=True,
        )
        if proc.returncode:
            actual = _git(spec.target, ["rev-parse", "HEAD"], check=False).strip()
            after_dirty = _git_status(spec.target)
            after_dirty_snapshot = _dirty_snapshot(Path(spec.target), after_dirty)
            same_state = (
                actual == receipt["current_head"]
                and _status_key(after_dirty) == _status_key(receipt["dirty"])
                and _snapshot_key(after_dirty_snapshot)
                == _snapshot_key(receipt.get("dirty_snapshot", []))
            )
            result = {
                **receipt,
                "status": "failed" if same_state else "unknown",
                "mutation": "not_applied" if same_state else "unknown_after_merge_error",
                "actual_head": actual,
                "after_dirty": after_dirty,
                "after_dirty_snapshot": after_dirty_snapshot,
                "stderr": proc.stderr[-4096:],
            }
            _write_json(backup_root / "apply-result.json", result)
            return result
    except (OSError, subprocess.SubprocessError, SyncError) as exc:
        result = {**receipt, "status": "unknown", "mutation": "unknown", "error": str(exc)}
        _write_json(backup_root / "apply-result.json", result)
        return result
    try:
        actual = _git(spec.target, ["rev-parse", "HEAD"]).strip()
        after_dirty = _git_status(spec.target)
        after_dirty_snapshot = _dirty_snapshot(Path(spec.target), after_dirty)
    except (OSError, subprocess.SubprocessError, SyncError) as exc:
        result = {
            **receipt,
            "status": "unknown",
            "mutation": "unknown_after_apply_verification",
            "error": str(exc),
        }
        _write_json(backup_root / "apply-result.json", result)
        return result
    if (
        actual != spec.desired_head
        or _status_key(after_dirty) != _status_key(receipt["dirty"])
        or _snapshot_key(after_dirty_snapshot)
        != _snapshot_key(receipt.get("dirty_snapshot", []))
    ):
        result = {
            **receipt,
            "status": "unknown",
            "mutation": "applied_but_unverified",
            "actual_head": actual,
            "after_dirty": after_dirty,
            "after_dirty_snapshot": after_dirty_snapshot,
        }
        _write_json(backup_root / "apply-result.json", result)
        return result
    result = {**receipt, "status": "success", "mutation": "applied", "actual_head": actual}
    _write_json(backup_root / "apply-result.json", result)
    return result


def rollback(
    receipt_path: Path,
    *,
    allow_mutation: bool = False,
    target_allowlist: tuple[Path, ...] = (DEFAULT_TARGET,),
) -> dict[str, object]:
    if not allow_mutation:
        raise SyncError("rollback requires an explicit mutation authorization")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    target = Path(str(receipt["target"]))
    if not any(_norm(target) == _norm(allowed) for allowed in target_allowlist):
        raise SyncError(f"rollback target is outside the Gaza allowlist: {target}")
    _assert_no_reparse_ancestors(target)
    branch = _git(target, ["branch", "--show-current"]).strip()
    if branch != receipt.get("branch"):
        raise SyncError("rollback stopped: branch identity changed")
    remote = _git(target, ["remote", "get-url", "origin"]).strip()
    if remote != receipt.get("remote"):
        raise SyncError("rollback stopped: remote identity changed")
    current = _git(target, ["rev-parse", "HEAD"]).strip()
    if current != receipt.get("actual_head") or current != receipt.get("desired_head"):
        raise SyncError("rollback stopped: current head does not match the recorded applied head")
    current_dirty = _git_status(target)
    if "dirty_snapshot" not in receipt:
        raise SyncError("rollback stopped: receipt lacks a dirty-byte snapshot")
    current_dirty_snapshot = _dirty_snapshot(target, current_dirty)
    if _status_key(current_dirty) != _status_key(receipt.get("dirty", [])) or _snapshot_key(
        current_dirty_snapshot
    ) != _snapshot_key(receipt.get("dirty_snapshot", [])):
        raise SyncError("rollback stopped: dirty/untracked state changed after installation")
    if receipt.get("dirty"):
        raise SyncError("rollback stopped: pre-existing dirty files make destructive reset unsafe")
    old_head = str(receipt["current_head"])
    reverse_incoming = _incoming_paths(target, current, old_head)
    reverse_ignored_collisions = _ignored_collision_paths(target, reverse_incoming)
    if reverse_ignored_collisions:
        raise SyncError(
            "rollback stopped: ignored files collide with reverse incoming source paths: "
            f"{reverse_ignored_collisions}"
        )
    proc = subprocess.run(["git", "-C", str(target), "reset", "--hard", old_head], capture_output=True, text=True)
    if proc.returncode:
        result = {**receipt, "rollback_status": "failed", "rollback_mutation": "unknown", "stderr": proc.stderr[-4096:]}
        _write_json(receipt_path.with_name("rollback-result.json"), result)
        return result
    try:
        actual = _git(target, ["rev-parse", "HEAD"]).strip()
        after_dirty = _git_status(target)
        after_dirty_snapshot = _dirty_snapshot(target, after_dirty)
    except (OSError, subprocess.SubprocessError, SyncError) as exc:
        result = {
            **receipt,
            "rollback_status": "unknown",
            "rollback_mutation": "unknown_after_rollback_verification",
            "error": str(exc),
        }
        _write_json(receipt_path.with_name("rollback-result.json"), result)
        return result
    state_restored = (
        actual == old_head
        and _status_key(after_dirty) == _status_key(receipt.get("dirty", []))
        and _snapshot_key(after_dirty_snapshot) == _snapshot_key(receipt.get("dirty_snapshot", []))
    )
    result = {
        **receipt,
        "rollback_status": "success" if state_restored else "unknown",
        "rollback_mutation": "rolled_back" if state_restored else "unknown_after_rollback_verification",
        "rollback_head": actual,
        "rollback_dirty": after_dirty,
        "rollback_dirty_snapshot": after_dirty_snapshot,
    }
    _write_json(receipt_path.with_name("rollback-result.json"), result)
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gaza-only source sync; plan is the default")
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--backup-root", type=Path, default=DEFAULT_BACKUP_ROOT)
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    parser.add_argument("--expected-current-head")
    parser.add_argument("--desired-head")
    parser.add_argument("--expected-remote")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--fetch", action="store_true", help="Fetch only the named protected source branch before preflight.")
    parser.add_argument("--allow-mutation", action="store_true")
    parser.add_argument("--rollback-receipt", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.rollback_receipt:
            result = rollback(args.rollback_receipt, allow_mutation=args.allow_mutation)
        else:
            if not args.expected_current_head or not args.desired_head:
                raise SyncError("--expected-current-head and --desired-head are required unless --rollback-receipt is supplied")
            spec = SyncSpec(
                target=args.target,
                backup_root=args.backup_root,
                expected_branch=args.branch,
                expected_current_head=args.expected_current_head,
                desired_head=args.desired_head,
                expected_remote=args.expected_remote,
            )
            result = apply(spec, allow_mutation=args.apply and args.allow_mutation, fetch=args.fetch) if args.apply else preflight(spec, fetch=args.fetch)
        print(json.dumps(result, indent=2, sort_keys=True))
        final_status = result.get("rollback_status") if args.rollback_receipt else result.get("status", "plan")
        return 0 if final_status in {"plan", "success"} else 1
    except SyncError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
