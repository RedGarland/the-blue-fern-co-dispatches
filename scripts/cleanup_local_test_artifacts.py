from __future__ import annotations

import argparse
import shutil
from dataclasses import dataclass
from pathlib import Path


ALLOWLIST_GLOBS = (
    ".tmp/pytest-*",
    ".tmp_pytest_*",
    ".pytest_tmp_*",
    "output/tmp/pytest-*",
    "output/tmp/dispatches_pytest_*",
    "output/tmp/pytest-of-*",
)

PROTECTED_PATHS = (
    ".env",
    "bluefern-dispatches-pages",
    "output/site",
    "output/dispatches",
    "data",
    "src",
    "tests",
    ".git",
)


@dataclass(frozen=True)
class CleanupAction:
    path: Path
    status: str
    detail: str


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _protected_reason(path: Path, root: Path) -> str | None:
    resolved = path.resolve()
    root_resolved = root.resolve()
    for rel in PROTECTED_PATHS:
        protected = (root_resolved / rel).resolve()
        if resolved == protected or _is_within(resolved, protected):
            return f"protected path: {protected}"
    return None


def discover_candidates(root: Path) -> list[Path]:
    root = root.resolve()
    found: dict[str, Path] = {}
    for pattern in ALLOWLIST_GLOBS:
        for candidate in root.glob(pattern):
            if not candidate.exists():
                continue
            found[str(candidate.resolve())] = candidate
    return sorted(found.values(), key=lambda p: str(p).lower())


def cleanup_local_test_artifacts(root: Path, apply: bool = False) -> list[CleanupAction]:
    root = root.resolve()
    actions: list[CleanupAction] = []
    for candidate in discover_candidates(root):
        reason = _protected_reason(candidate, root)
        if reason:
            actions.append(CleanupAction(path=candidate, status="skip", detail=reason))
            continue
        if not _is_within(candidate, root):
            actions.append(CleanupAction(path=candidate, status="skip", detail="outside project root"))
            continue
        if not candidate.is_dir():
            actions.append(CleanupAction(path=candidate, status="skip", detail="not a directory"))
            continue
        if not apply:
            actions.append(CleanupAction(path=candidate, status="would-remove", detail="dry-run"))
            continue
        try:
            shutil.rmtree(candidate)
            actions.append(CleanupAction(path=candidate, status="removed", detail="deleted"))
        except PermissionError as exc:
            actions.append(CleanupAction(path=candidate, status="error", detail=f"permission denied: {exc}"))
        except OSError as exc:
            actions.append(CleanupAction(path=candidate, status="error", detail=f"os error: {exc}"))
    return actions


def _format_action(action: CleanupAction, root: Path) -> str:
    try:
        rel = action.path.resolve().relative_to(root.resolve())
        display = str(rel)
    except ValueError:
        display = str(action.path)
    return f"{action.status:12} {display}  ({action.detail})"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Clean local pytest/temp runtime directories safely.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="Project root.")
    parser.add_argument("--apply", action="store_true", help="Actually delete allowlisted directories.")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    actions = cleanup_local_test_artifacts(root, apply=args.apply)
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] cleanup_local_test_artifacts root={root}")
    print("allowlist:")
    for pattern in ALLOWLIST_GLOBS:
        print(f"  - {pattern}")
    if not actions:
        print("no candidates matched allowlist")
        return 0
    for action in actions:
        print(_format_action(action, root))
    errors = [a for a in actions if a.status == "error"]
    summary = {
        "would-remove": sum(1 for a in actions if a.status == "would-remove"),
        "removed": sum(1 for a in actions if a.status == "removed"),
        "skip": sum(1 for a in actions if a.status == "skip"),
        "error": len(errors),
    }
    print(
        "summary: "
        f"would-remove={summary['would-remove']} removed={summary['removed']} skip={summary['skip']} error={summary['error']}"
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

