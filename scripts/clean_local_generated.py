from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NESTED_PAGES_REPO = Path("bluefern-dispatches-pages")
PROTECTED_TOP_LEVEL = {"assets", "docs", "ops", "scripts", "tests"}
NEVER_TOUCH = {Path(".env"), NESTED_PAGES_REPO}
TRACKED_GENERATED_PREFIXES = (
    Path("data/dispatches/cascadia/raw"),
    Path("data/dispatches/cascadia/normalized"),
    Path("data/dispatches/cascadia/curated"),
    Path("data/records"),
    Path("output/detail"),
    Path("output/dispatches"),
)
OPTIONAL_SITE_PREFIX = Path("output/site")
UNTRACKED_GENERATED_PREFIXES = (
    Path(".pytest_tmp"),
    Path("src/bluefern_dispatches.egg-info"),
    Path("data/dispatches/cascadia/cache"),
)
UNTRACKED_GENERATED_FILE_PREFIXES = (
    Path("output/detail"),
    Path("output/dispatches"),
)
GENERATED_SOURCE_MARKERS = {
    "historical_search_report.json",
    "historical_sources.json",
    "registry_source_report.json",
    "weekly_quality_report.json",
}


@dataclass(frozen=True)
class Action:
    kind: str
    path: Path


@dataclass(frozen=True)
class CleanupResult:
    warnings: list[str]
    critical_failures: list[str]


def _relative(path: Path, root: Path) -> Path:
    return path.resolve().relative_to(root.resolve())


def _is_under(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def _is_protected(path: Path) -> bool:
    return path in NEVER_TOUCH or any(_is_under(path, protected) for protected in NEVER_TOUCH) or path.parts[:1] in [(part,) for part in PROTECTED_TOP_LEVEL]


def _is_generated_source_dir(path: Path, root: Path) -> bool:
    full = root / path
    if not full.is_dir() or not _is_under(path, Path("data/dispatches/cascadia/sources")):
        return False
    if (full / "manual_sources.json").exists():
        return False
    return any((full / marker).exists() for marker in GENERATED_SOURCE_MARKERS)


def _generated_source_parent(path: Path, root: Path) -> Path | None:
    sources = Path("data/dispatches/cascadia/sources")
    if path.name not in GENERATED_SOURCE_MARKERS or not _is_under(path, sources):
        return None
    parent = path.parent
    if (root / parent / "manual_sources.json").exists():
        return None
    return parent


def _allowed_tracked(path: Path, include_site_output: bool) -> bool:
    prefixes = TRACKED_GENERATED_PREFIXES + ((OPTIONAL_SITE_PREFIX,) if include_site_output else ())
    return not _is_protected(path) and any(_is_under(path, prefix) for prefix in prefixes)


def _allowed_untracked(path: Path, root: Path, include_site_output: bool) -> bool:
    if _is_protected(path):
        return False
    prefixes = UNTRACKED_GENERATED_PREFIXES + UNTRACKED_GENERATED_FILE_PREFIXES + ((OPTIONAL_SITE_PREFIX,) if include_site_output else ())
    return any(_is_under(path, prefix) for prefix in prefixes) or _is_generated_source_dir(path, root) or _generated_source_parent(path, root) is not None


def _git_status(root: Path) -> list[tuple[str, Path]]:
    completed = subprocess.run(
        ["git", "status", "--short", "--untracked-files=all"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    entries: list[tuple[str, Path]] = []
    for line in completed.stdout.splitlines():
        if not line:
            continue
        status = line[:2]
        raw_path = line[3:]
        if " -> " in raw_path:
            raw_path = raw_path.split(" -> ", 1)[1]
        entries.append((status, Path(raw_path)))
    return entries


def planned_actions(root: Path = ROOT, include_site_output: bool = False) -> list[Action]:
    actions: list[Action] = []
    untracked_roots: set[Path] = set()
    for prefix in UNTRACKED_GENERATED_PREFIXES:
        if (root / prefix).exists():
            untracked_roots.add(prefix)
    for status, path in _git_status(root):
        if _is_under(path, NESTED_PAGES_REPO):
            continue
        if status == "??":
            allowed = _allowed_untracked(path, root, include_site_output)
            if allowed:
                root_path = _generated_source_parent(path, root) or path
                for prefix in UNTRACKED_GENERATED_PREFIXES + ((OPTIONAL_SITE_PREFIX,) if include_site_output else ()):
                    if _is_under(path, prefix):
                        root_path = prefix
                        break
                untracked_roots.add(root_path)
            continue
        if _allowed_tracked(path, include_site_output):
            actions.append(Action("restore", path))
    for path in sorted(untracked_roots, key=lambda item: item.as_posix()):
        actions.append(Action("remove", path))
    return actions


def _make_writable(path: Path) -> None:
    paths = [path]
    if path.is_dir():
        paths.extend(child for child in path.rglob("*"))
    for item in paths:
        try:
            os.chmod(item, stat.S_IWRITE | stat.S_IREAD)
        except OSError:
            continue


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _locked_remove_warning(path: Path, exc: BaseException) -> str:
    return f"Could not remove {path.as_posix()}; close Python/pytest/OneDrive locks and rerun. ({exc})"


def _remove_with_retry(path: Path, root: Path) -> str | None:
    target = root / path
    if not target.exists():
        return None
    resolved = target.resolve()
    root_resolved = root.resolve()
    if resolved == root_resolved or root_resolved not in resolved.parents:
        raise RuntimeError(f"Refusing to remove outside project root: {target}")
    try:
        _remove_path(target)
        return None
    except FileNotFoundError:
        return None
    except (PermissionError, OSError) as first_exc:
        try:
            _make_writable(target)
            _remove_path(target)
            return None
        except FileNotFoundError:
            return None
        except (PermissionError, OSError) as retry_exc:
            return _locked_remove_warning(path, retry_exc or first_exc)


def apply_actions(actions: list[Action], root: Path) -> CleanupResult:
    warnings: list[str] = []
    critical_failures: list[str] = []
    restore_paths = [str(action.path) for action in actions if action.kind == "restore"]
    if restore_paths:
        try:
            subprocess.run(["git", "restore", "--", *restore_paths], cwd=root, check=True)
        except subprocess.CalledProcessError as exc:
            critical_failures.append(f"git restore failed with exit code {exc.returncode}")
    for action in actions:
        if action.kind != "remove":
            continue
        try:
            warning = _remove_with_retry(action.path, root)
        except RuntimeError as exc:
            critical_failures.append(str(exc))
            continue
        if warning:
            warnings.append(warning)
    return CleanupResult(warnings=warnings, critical_failures=critical_failures)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List or clean local generated/runtime artifacts.")
    parser.add_argument("--root", type=Path, default=ROOT, help="Project root to inspect.")
    parser.add_argument("--apply", action="store_true", help="Apply the listed restore/remove operations.")
    parser.add_argument("--include-site-output", action="store_true", help="Allow output/site generated files to be restored or removed.")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    actions = planned_actions(root=root, include_site_output=args.include_site_output)
    if not actions:
        print("No generated/runtime cleanup actions found.")
        return 0

    mode = "Applying" if args.apply else "Dry run"
    print(f"{mode}: {len(actions)} generated/runtime cleanup action(s)")
    for action in actions:
        print(f"{action.kind}: {action.path.as_posix()}")
    if args.apply:
        result = apply_actions(actions, root)
        for warning in result.warnings:
            print(f"WARNING: {warning}")
        for failure in result.critical_failures:
            print(f"ERROR: {failure}")
        if result.critical_failures:
            print("Cleanup finished with critical failures.")
            return 1
        print("Cleanup complete.")
    else:
        print("No files changed. Re-run with --apply to clean listed paths.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
