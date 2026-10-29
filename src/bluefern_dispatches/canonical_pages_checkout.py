"""Explicit, local-only fresh Pages staging; never repair or publish a checkout.

The caller must authorize both the source read and the new destination. The
approved parent is an ordinary, quiescent private directory, not a security
sandbox. Concurrent hostile filesystem/configuration changes are unsupported.
No hooks, permissions, protocol restrictions, or system/global config are changed.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import unicodedata

RESERVE_BYTES = 2 * 1024**3
_OID = re.compile(r"[0-9a-f]{40}\Z")
_DEVICE = re.compile(r"(?:con|conin\$|conout\$|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\..*)?\Z", re.I)


class CheckoutRefused(RuntimeError):
    """Failed proof; any newly created directory is retained for inspection."""


def _ordinary(path: Path, *, directory: bool) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise CheckoutRefused(f"missing/unreadable path: {path}") from exc
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise CheckoutRefused(f"link/reparse point refused: {path}")
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise CheckoutRefused(f"unexpected path type: {path}")
    return info


def _directory(path: Path) -> Path:
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise CheckoutRefused("absolute lexical path without '..' required")
    for part in [*reversed(path.parents), path]:
        _ordinary(part, directory=True)
    return path


def _identity(path: Path) -> tuple[int, int]:
    info = _ordinary(path, directory=True)
    return info.st_dev, info.st_ino


def _git(root: Path, *args: str) -> bytes:
    # Do not inherit redirection/config-injection or object-store overrides.
    # Refuse them, rather than silently clearing a caller's security settings.
    if any(key.startswith("GIT_") and not (key == "GIT_PAGER" and value == "cat")
           for key, value in os.environ.items()):
        raise CheckoutRefused("GIT_* environment overrides are unsupported")
    try:
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                                check=False, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CheckoutRefused(f"Git {args[0]} could not complete; inspect retained destination") from exc
    if result.returncode:
        # Do not serialize Git's potentially secret-bearing config/remote text.
        raise CheckoutRefused(f"Git {args[0]} refused (exit {result.returncode})")
    return result.stdout


def _config_guard(root: Path, *, allow_remote_metadata: bool = True) -> None:
    keys = _git(root, "config", "--name-only", "--list").decode("utf-8").splitlines()
    for raw in keys:
        key = raw.lower()
        if (key.startswith(("filter.", "uploadpack.", "include.", "includeif."))
                or key in {"core.hookspath", "init.templatedir", "core.fsmonitor",
                           "extensions.partialclone", "extensions.worktreeconfig",
                           "core.worktree", "core.alternaterefscommand", "core.sshcommand",
                           "core.gitproxy", "fetch.bundleuri", "clone.defaultremotename"}
                or (key.startswith("url.") and key.endswith((".insteadof", ".pushinsteadof")))
                or (key.startswith("remote.") and (not allow_remote_metadata
                    or key.rsplit(".", 1)[-1] not in {"url", "fetch", "pushurl"}))):
            # Configuration *keys* can themselves contain credentials (URL
            # rewrite sections), so do not echo them in errors or receipts.
            raise CheckoutRefused("unsupported Git execution/redirect configuration")


def _walk(root: Path):
    def failed(exc):
        raise CheckoutRefused("directory inventory could not be read") from exc
    return os.walk(root, followlinks=False, onerror=failed)


def _template_proof(template: Path) -> str:
    """Bind explicitly selected template bytes BEFORE clone can create refs.

    This is an explicit new-clone template selection, NOT a claim to preserve
    an undiscoverable compiled Git default. Known hooksPath/template policy is
    refused by _config_guard, never silently overridden or disabled.
    """
    template = _directory(template)
    inventory = []
    total = 0
    for folder, dirs, files in _walk(template):
        for name in dirs:
            path = Path(folder) / name
            _ordinary(path, directory=True)
            if path.relative_to(template).as_posix() not in {"hooks", "info"}:
                raise CheckoutRefused("unsupported template directory")
            inventory.append((path.relative_to(template).as_posix() + "/", "directory"))
        for name in files:
            path = Path(folder) / name
            info = _ordinary(path, directory=False)
            relative = path.relative_to(template).as_posix()
            _safe_tree_path(relative)
            if relative not in {"description", "info/exclude"} and not (
                    path.parent == template / "hooks" and name.endswith(".sample")):
                raise CheckoutRefused("active hook or unsupported template file requires separate review")
            total += info.st_size
            if info.st_nlink != 1 or total > 1024**2 or len(inventory) >= 100:
                raise CheckoutRefused("template must be small regular independent files")
            inventory.append((relative, hashlib.sha256(path.read_bytes()).hexdigest()))
    import json
    return hashlib.sha256(json.dumps(sorted(inventory), separators=(",", ":")).encode()).hexdigest()


def _repository(root: Path) -> None:
    _directory(root)
    gitdir = root / ".git"
    _ordinary(gitdir, directory=True)  # no linked worktree/gitfile redirection
    # Shallow histories are accepted: selected commit/tree/blob proofs suffice.
    # Redirected or potentially network-backed object stores are not supported.
    for relative in ("objects/info/alternates", "objects/info/http-alternates", "info/grafts"):
        if os.path.lexists(gitdir / relative):
            raise CheckoutRefused(f"redirected/history-rewritten object store: {relative}")
    for folder, dirs, files in _walk(gitdir):
        for name in dirs:
            _ordinary(Path(folder) / name, directory=True)
        for name in files:
            _ordinary(Path(folder) / name, directory=False)
            if name.endswith(".promisor"):
                raise CheckoutRefused("promisor objects refused")
    # Reject redirected repository metadata before Git can read config/objects.
    _config_guard(root)
    if Path(os.fsdecode(_git(root, "rev-parse", "--show-toplevel")).strip()) != root:
        raise CheckoutRefused("repository root differs")
    if _git(root, "rev-parse", "--is-bare-repository").strip() != b"false":
        raise CheckoutRefused("ordinary working repository required")
    if _git(root, "rev-parse", "--show-object-format").strip() != b"sha1":
        raise CheckoutRefused("only exact 40-hex SHA-1 object format is supported")
    if _git(root, "for-each-ref", "--format=%(refname)", "refs/replace/").strip():
        raise CheckoutRefused("replacement objects refused")


def _safe_tree_path(value: str) -> None:
    if not value or unicodedata.normalize("NFC", value) != value:
        raise CheckoutRefused("non-canonical tree path")
    for component in value.split("/"):
        if (component in {"", ".", ".."} or component.lower() == ".git"
                or re.fullmatch(r"git~[0-9]+", component, re.I)
                or component[-1:] in {".", " "} or _DEVICE.fullmatch(component)
                or any(ord(c) < 32 or ord(c) == 127 or c in '\\:<>"|?*' for c in component)):
            raise CheckoutRefused(f"unsafe tree path: {value!r}")


def _object(root: Path, kind: str, oid: str) -> bytes:
    raw = _git(root, "cat-file", kind, oid)
    if hashlib.sha1(kind.encode() + b" " + str(len(raw)).encode() + b"\0" + raw).hexdigest() != oid:
        raise CheckoutRefused(f"raw {kind} object hash differs")
    return raw


def _tree(root: Path, expected_commit: str) -> tuple[str, dict[str, tuple[str, str, int]], set[str]]:
    if not isinstance(expected_commit, str) or not _OID.fullmatch(expected_commit):
        raise CheckoutRefused("expected commit must be exactly 40 lowercase hexadecimal characters")
    commit = _object(root, "commit", expected_commit)
    first = commit.split(b"\n", 1)[0]
    if not first.startswith(b"tree ") or not _OID.fullmatch(first[5:].decode("ascii")):
        raise CheckoutRefused("commit tree header invalid")
    tree = first[5:].decode("ascii")
    _object(root, "tree", tree)
    leaves: dict[str, tuple[str, str, int]] = {}
    directories: set[str] = set()
    folded: set[str] = set()
    rows = _git(root, "ls-tree", "-r", "-t", "-z", expected_commit)
    for row in rows.split(b"\0"):
        if not row:
            continue
        try:
            metadata, raw_path = row.split(b"\t", 1)
            mode, kind, oid = metadata.decode("ascii").split(" ")
            path = raw_path.decode("utf-8", errors="strict")
        except (ValueError, UnicodeError) as exc:
            raise CheckoutRefused("malformed tree entry") from exc
        _safe_tree_path(path)
        key = path.casefold()
        if key in folded or not _OID.fullmatch(oid):
            raise CheckoutRefused("case-colliding/duplicate path or invalid object ID")
        folded.add(key)
        if mode == "040000" and kind == "tree":
            _object(root, kind, oid)
            directories.add(path)
        elif mode in {"100644", "100755"} and kind == "blob":
            size = int(_git(root, "cat-file", "-s", oid))
            leaves[path] = mode, oid, size
        else:
            raise CheckoutRefused(f"unsupported tree mode/type: {path} {mode} {kind}")
    if not leaves:
        raise CheckoutRefused("empty Pages tree refused")
    return tree, leaves, directories


def verify_canonical_checkout(repo: Path, expected_commit: str) -> dict:
    """Read-only proof of every committed leaf and exact working inventory.

    This proves bytes/modes at the observation boundary, not publication or a
    future checkout. Windows lacks POSIX executable mode; require Git index mode
    there and report that native execute-bit enforcement was not available.
    """
    repo = Path(repo)
    _repository(repo)
    head = _git(repo, "rev-parse", "HEAD").decode().strip()
    if head != expected_commit:
        raise CheckoutRefused("HEAD differs from pinned commit")
    tree, leaves, directories = _tree(repo, expected_commit)
    index = _git(repo, "ls-files", "--stage", "-z")
    expected_index = {f"{mode} {oid} 0\t{path}".encode() for path, (mode, oid, _) in leaves.items()}
    index_rows = index.split(b"\0")[:-1] if index.endswith(b"\0") else []
    if len(index_rows) != len(expected_index) or set(index_rows) != expected_index:
        raise CheckoutRefused("index modes/objects/inventory differ")
    seen_files, seen_dirs = set(), set()
    for folder, dirs, files in _walk(repo):
        if Path(folder) == repo:
            dirs.remove(".git")
        for name in dirs:
            path = Path(folder) / name
            _ordinary(path, directory=True)
            seen_dirs.add(path.relative_to(repo).as_posix())
        for name in files:
            path = Path(folder) / name
            _ordinary(path, directory=False)
            seen_files.add(path.relative_to(repo).as_posix())
    if seen_files != set(leaves) or seen_dirs != directories:
        raise CheckoutRefused("working inventory differs (including untracked/ignored paths)")
    total = 0
    for relative, (mode, oid, size) in leaves.items():
        path = repo / relative
        before = _ordinary(path, directory=False)
        if before.st_nlink != 1:
            raise CheckoutRefused(f"hard-linked working file refused: {relative}")
        # Git's POSIX executable classification uses the owner's execute bit,
        # not whether any group/other execute bit happens to be set.
        if os.name != "nt" and bool(before.st_mode & stat.S_IXUSR) != (mode == "100755"):
            raise CheckoutRefused(f"working mode differs: {relative}")
        raw = _object(repo, "blob", oid)
        # Binary read: NEVER universal-newline decoding or clean/smudge filtering.
        with path.open("rb") as stream:
            working = stream.read()
            after = os.fstat(stream.fileno())
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise CheckoutRefused(f"working file changed during verification: {relative}")
        if len(raw) != size or working != raw:
            raise CheckoutRefused(f"raw working bytes differ: {relative}")
        total += size
    if _git(repo, "rev-parse", "HEAD").decode().strip() != head:
        raise CheckoutRefused("HEAD changed during verification")
    return {"ok": True, "scope": "private_canonical_checkout_only", "head": head,
            "tree": tree, "leaf_count": len(leaves), "tree_count": len(directories) + 1,
            "working_bytes": total, "raw_blobs_verified": True,
            "native_execute_mode_verified": os.name != "nt", "publication_authorized": False}


def create_canonical_pages_clone(source_repo: Path, destination: Path, *,
                                 approved_parent: Path, expected_commit: str,
                                 reviewed_template: Path) -> dict:
    """Create one new private clone. Never overwrite, repair, clean, or retry it."""
    source_repo = _directory(Path(source_repo))
    parent = _directory(Path(approved_parent))
    destination = Path(destination)
    if destination.parent != parent or not destination.is_absolute():
        raise CheckoutRefused("destination must be a direct child of the approved private parent")
    _safe_tree_path(destination.name)
    if source_repo == parent or source_repo in parent.parents or parent in source_repo.parents:
        raise CheckoutRefused("source and approved parent must not contain one another")
    if os.path.lexists(destination):
        raise CheckoutRefused("destination already exists; retain it and choose a newly approved path")
    _repository(source_repo)
    # An inherited remote setting can select git-remote-* even with a local
    # clone URL. The fresh clone must not compete with ANY such parent policy.
    _config_guard(parent, allow_remote_metadata=False)
    template = _directory(Path(reviewed_template))
    template_sha256 = _template_proof(template)
    tree, leaves, _ = _tree(source_repo, expected_commit)
    object_bytes = sum(p.stat().st_size for p in (source_repo / ".git").rglob("*") if p.is_file())
    projected = 2 * object_bytes + sum(row[2] for row in leaves.values()) + 16 * 1024**2
    if shutil.disk_usage(parent).free < RESERVE_BYTES + projected:
        raise CheckoutRefused("fresh clone storage admission refused: retain 2 GiB plus projected bytes")
    parent_id = _identity(parent)
    destination.mkdir()  # atomic claim; even an empty existing directory is refused
    destination_id = _identity(destination)
    if _template_proof(template) != template_sha256:
        raise CheckoutRefused("reviewed template changed before clone")
    # Clone only from the validated absolute local repository; no fetch command,
    # shared object store, hardlinks, recursive submodules, or remote URL input.
    _git(parent, "clone", "--no-checkout", "--no-local", "--config", "core.autocrlf=false",
         "--template", str(template), "--", str(source_repo), str(destination))
    if _identity(parent) != parent_id or _identity(destination) != destination_id:
        raise CheckoutRefused("new destination/parent identity changed")
    if _template_proof(template) != template_sha256:
        raise CheckoutRefused("reviewed template changed during clone")
    _repository(destination)
    hooks = destination / ".git/hooks"
    if hooks.exists() and any(p.is_file() and not p.name.endswith(".sample") for p in hooks.iterdir()):
        raise CheckoutRefused("active template hooks require separate review; hooks were not disabled")
    _git(destination, "remote", "remove", "origin")
    if _git(destination, "remote").strip():
        raise CheckoutRefused("fresh clone still has remotes")
    if _git(destination, "config", "--local", "--get", "core.autocrlf").strip() != b"false":
        raise CheckoutRefused("fresh-clone autocrlf policy differs")
    if _tree(destination, expected_commit)[0] != tree:
        raise CheckoutRefused("clone tree differs from pinned input")
    _git(destination, "checkout", "--detach", expected_commit)
    proof = verify_canonical_checkout(destination, expected_commit)
    if _identity(parent) != parent_id or _identity(destination) != destination_id:
        raise CheckoutRefused("destination/parent identity changed")
    proof.update({"source_repo": str(source_repo), "destination": str(destination),
                  "local_autocrlf": "false", "remotes": [],
                  "reviewed_template": str(template), "template_inventory_sha256": template_sha256,
                  "existing_worktree_writes_requested": False})
    return proof
