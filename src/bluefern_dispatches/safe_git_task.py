from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


DEFAULT_BASE_BRANCH = "add/pages-repo-default"
DEFAULT_PAGES_REPO_NAME = "bluefern-dispatches-pages"
FORBIDDEN_BRANCHES = {"add/pages-repo-default", "main", "master", "gh-pages"}
FORBIDDEN_PATH_PREFIXES = (
    "bluefern-dispatches-pages/",
    "output/site/",
    "logs/",
    "output/tmp-backups-pages/",
)
REVIEW_OUTPUT_PREFIX = "output/review/"
FORBIDDEN_EXACT_PATHS = {".", "*"}


@dataclass(frozen=True)
class RepoSnapshot:
    branch: str
    head: str
    status_lines: tuple[str, ...]


def _normalize_path(path: str | Path) -> str:
    text = str(path).replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


def _run_git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _git_stdout(repo: Path, *args: str) -> str:
    result = _run_git(repo, *args, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def _is_git_repo(repo: Path) -> bool:
    if not repo.exists() or not repo.is_dir():
        return False
    result = _run_git(repo, "rev-parse", "--is-inside-work-tree", check=False)
    return result.returncode == 0 and result.stdout.strip().lower() == "true"


def _repo_root_from_cwd(cwd: Path | None = None) -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=str(cwd or Path.cwd()),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Unable to determine repository root.")
    return Path(result.stdout.strip()).resolve()


def _repo_snapshot(repo: Path) -> RepoSnapshot:
    branch = _git_stdout(repo, "branch", "--show-current")
    head = _git_stdout(repo, "rev-parse", "HEAD")
    status_lines = tuple(line for line in _git_stdout(repo, "status", "--short").splitlines() if line.strip())
    return RepoSnapshot(branch=branch, head=head, status_lines=status_lines)


def _repo_clean(repo: Path) -> bool:
    return not _repo_snapshot(repo).status_lines


def _repo_status_text(repo: Path) -> str:
    snapshot = _repo_snapshot(repo)
    status = "\n".join(snapshot.status_lines) if snapshot.status_lines else "<clean>"
    return f"branch: {snapshot.branch or '<detached>'}\nhead: {snapshot.head}\nstatus:\n{status}"


def _branch_commit(repo: Path, ref: str) -> str:
    return _git_stdout(repo, "rev-parse", ref)


def _current_branch(repo: Path) -> str:
    return _git_stdout(repo, "branch", "--show-current")


def _pages_repo_from_source(source_repo: Path, pages_repo_text: str | None) -> Path:
    if pages_repo_text:
        return Path(pages_repo_text).resolve()
    return (source_repo / DEFAULT_PAGES_REPO_NAME).resolve()


def _validate_pages_repo(pages_repo: Path, *, require_clean: bool = True, require_branch: str = "gh-pages") -> None:
    if not pages_repo.exists() or not pages_repo.is_dir():
        raise RuntimeError(f"Pages repo does not exist: {pages_repo}")
    if not _is_git_repo(pages_repo):
        raise RuntimeError(f"Pages repo is not a git repository: {pages_repo}")
    branch = _current_branch(pages_repo)
    if branch != require_branch:
        raise RuntimeError(f"Pages repo must be on {require_branch}; found {branch or '<detached>'}.")
    if require_clean and not _repo_clean(pages_repo):
        raise RuntimeError(f"Pages repo must be clean before running this helper:\n{_repo_status_text(pages_repo)}")


def _path_is_forbidden(raw_path: str) -> tuple[bool, str]:
    normalized = _normalize_path(raw_path)
    if normalized in FORBIDDEN_EXACT_PATHS:
        return True, normalized
    if normalized.startswith(".env"):
        return True, normalized
    for prefix in FORBIDDEN_PATH_PREFIXES:
        if normalized == prefix.rstrip("/") or normalized.startswith(prefix):
            return True, normalized
    return False, normalized


def _path_is_review_output(path: str) -> bool:
    normalized = _normalize_path(path)
    return normalized == REVIEW_OUTPUT_PREFIX.rstrip("/") or normalized.startswith(REVIEW_OUTPUT_PREFIX)


def _validate_task_paths(paths: Sequence[str], *, allow_directory: bool, allow_review_output: bool) -> tuple[list[str], list[str], list[str]]:
    selected: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []
    for raw_path in paths:
        normalized = _normalize_path(raw_path)
        if not normalized:
            errors.append("Empty file path provided.")
            continue
        if Path(normalized).is_absolute() or any(part == ".." for part in Path(normalized).parts):
            errors.append(f"Refusing path outside the repository: {normalized}")
            continue
        if normalized in FORBIDDEN_EXACT_PATHS:
            errors.append(f"Refusing forbidden path: {normalized}")
            continue
        path_obj = Path(normalized)
        if path_obj.exists() and path_obj.is_dir() and not allow_directory:
            errors.append(f"Directory path requires --allow-directory: {normalized}")
            continue
        if normalized.endswith("/") and not allow_directory:
            errors.append(f"Directory path requires --allow-directory: {normalized}")
            continue
        if normalized == "*" or "*" in normalized:
            errors.append(f"Refusing glob path: {normalized}")
            continue
        forbidden, forbidden_text = _path_is_forbidden(normalized)
        if forbidden:
            errors.append(f"Refusing forbidden path: {forbidden_text}")
            continue
        if _path_is_review_output(normalized):
            if not allow_review_output:
                errors.append(f"Review output requires --allow-review-output: {normalized}")
                continue
            warnings.append(f"Review output allowed: {normalized}")
        selected.append(normalized)
    return selected, warnings, errors


def _existing_path_kind(path: Path) -> str:
    if not path.exists():
        return "missing"
    if path.is_dir():
        return "directory"
    return "file"


def _staged_files(repo: Path) -> tuple[str, ...]:
    return tuple(line for line in _git_stdout(repo, "diff", "--cached", "--name-only").splitlines() if line.strip())


def _diff_stat(repo: Path) -> str:
    return _git_stdout(repo, "diff", "--cached", "--stat") or "<no staged diff>"


def _print_lines(label: str, lines: Sequence[str]) -> None:
    print(label)
    if lines:
        for line in lines:
            print(f"- {line}")
    else:
        print("- <none>")


def _print_repo_state(label: str, repo: Path) -> None:
    print(label)
    print(_repo_status_text(repo))


def _current_dirty_summary(repo: Path, *, exclude: Sequence[str] = ()) -> list[str]:
    excluded = tuple(_normalize_path(item) for item in exclude)
    result: list[str] = []
    for line in _repo_snapshot(repo).status_lines:
        path = _normalize_path(line[3:] if len(line) > 3 else line)
        if not any(path == item or path.startswith(f"{item}/") for item in excluded):
            result.append(line)
    return result


def _branch_ref_exists(repo: Path, branch: str) -> bool:
    return _run_git(repo, "show-ref", "--verify", f"refs/heads/{branch}", check=False).returncode == 0


def _branch_based_on(repo: Path, branch: str, base_ref: str) -> bool:
    result = _run_git(repo, "merge-base", "--is-ancestor", base_ref, branch, check=False)
    return result.returncode == 0


def start_safe_task(*, branch: str, base: str = DEFAULT_BASE_BRANCH, pages_repo: str | None = None, dry_run: bool = False) -> int:
    source_repo = _repo_root_from_cwd()
    pages_root = _pages_repo_from_source(source_repo, pages_repo)

    if source_repo.resolve() == pages_root.resolve():
        print(f"Refusing to run from the Pages repo checkout: {pages_root}", file=sys.stderr)
        return 1

    try:
        _validate_pages_repo(pages_root, require_clean=True, require_branch="gh-pages")
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1

    current_branch = _current_branch(source_repo)
    if dry_run:
        print(f"Source repo: {source_repo}")
        print(f"Pages repo: {pages_root}")
        print(f"Current branch: {current_branch or '<detached>'}")
        print(f"Base branch: {base}")
        print(f"Feature branch: {branch}")
        print("Planned actions:")
        print(f"- fetch {base} from origin")
        print(f"- switch to {base}")
        print(f"- pull --ff-only origin {base}")
        if _branch_ref_exists(source_repo, branch):
            print(f"- switch to existing branch {branch}")
            print(f"- check whether {branch} is based on current origin/{base}")
        else:
            print(f"- create branch {branch} from origin/{base}")
        print("Source repo dirty summary:")
        dirty = _current_dirty_summary(source_repo)
        if dirty:
            for line in dirty:
                print(f"- {line}")
        else:
            print("- <clean>")
        print("Pages repo status:")
        _print_repo_state("Pages repo snapshot:", pages_root)
        return 0

    try:
        _run_git(source_repo, "fetch", "origin", base)
        if current_branch != base:
            if _branch_ref_exists(source_repo, base):
                _run_git(source_repo, "switch", base)
            else:
                _run_git(source_repo, "switch", "-c", base, "--track", f"origin/{base}")
        _run_git(source_repo, "pull", "--ff-only", "origin", base)

        if _branch_ref_exists(source_repo, branch):
            _run_git(source_repo, "switch", branch)
        else:
            _run_git(source_repo, "switch", "-c", branch, "--track", f"origin/{base}")

        base_commit = _branch_commit(source_repo, f"origin/{base}")
        feature_commit = _branch_commit(source_repo, "HEAD")
        warning = None
        if not _branch_based_on(source_repo, branch, f"origin/{base}"):
            warning = f"WARNING: {branch} is not based on current origin/{base}."

        print(f"Source repo: {source_repo}")
        print(f"Pages repo: {pages_root}")
        print(f"Current branch: {_current_branch(source_repo) or '<detached>'}")
        print(f"Base branch commit: {base_commit}")
        print(f"Feature branch commit: {feature_commit}")
        if warning:
            print(warning)
        print("Source repo dirty summary:")
        dirty = _current_dirty_summary(source_repo)
        if dirty:
            for line in dirty:
                print(f"- {line}")
        else:
            print("- <clean>")
        print("Pages repo status:")
        _print_repo_state("Pages repo snapshot:", pages_root)
        return 0
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1


def _stage_exact_files(repo: Path, paths: Sequence[str]) -> None:
    _run_git(repo, "add", "--", *paths)


def stage_safe_task(
    *,
    files: Sequence[str],
    allow_review_output: bool = False,
    allow_directory: bool = False,
    allow_base_branch: bool = False,
    pages_repo: str | None = None,
    dry_run: bool = False,
) -> int:
    source_repo = _repo_root_from_cwd()
    pages_root = _pages_repo_from_source(source_repo, pages_repo)

    try:
        _validate_pages_repo(pages_root, require_clean=True, require_branch="gh-pages")
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1

    current_branch = _current_branch(source_repo)
    if current_branch in FORBIDDEN_BRANCHES and not allow_base_branch:
        print(
            f"Refusing to stage on protected branch {current_branch or '<detached>'}. Re-run with --allow-base-branch only if this is intentional.",
            file=sys.stderr,
        )
        return 1

    selected, warnings, errors = _validate_task_paths(files, allow_directory=allow_directory, allow_review_output=allow_review_output)
    if errors:
        print("Refusing to stage unsafe paths.", file=sys.stderr)
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    if dry_run:
        print(f"Source repo: {source_repo}")
        print(f"Current branch: {current_branch or '<detached>'}")
        _print_lines("Requested files:", selected)
        if warnings:
            _print_lines("Warnings:", warnings)
        print("Planned command:")
        print("git add -- " + " ".join(selected))
        print("Pages repo status:")
        _print_repo_state("Pages repo snapshot:", pages_root)
        print("Unrelated dirty files:")
        unrelated = _current_dirty_summary(source_repo, exclude=selected)
        if unrelated:
            for line in unrelated:
                print(f"- {line}")
        else:
            print("- <none>")
        return 0

    try:
        if not selected:
            print("No files selected for staging.", file=sys.stderr)
            return 1
        for item in selected:
            path = source_repo / item
            kind = _existing_path_kind(path)
            if kind == "missing":
                print(f"Missing path: {item}", file=sys.stderr)
                return 1
            if kind == "directory" and not allow_directory:
                print(f"Directory path requires --allow-directory: {item}", file=sys.stderr)
                return 1

        _stage_exact_files(source_repo, selected)

        staged = _staged_files(source_repo)
        print("Staged files:")
        for item in staged:
            print(f"- {item}")
        print("Staged diff stat:")
        print(_diff_stat(source_repo))
        if warnings:
            _print_lines("Warnings:", warnings)
        print("Unrelated dirty files:")
        unrelated = _current_dirty_summary(source_repo, exclude=selected)
        if unrelated:
            for line in unrelated:
                print(f"- {line}")
        else:
            print("- <none>")
        print("Pages repo status:")
        _print_repo_state("Pages repo snapshot:", pages_root)
        return 0
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1


def _suggest_title(message: str) -> str:
    text = message.strip()
    if not text:
        return ""
    return text[0].upper() + text[1:]


def commit_safe_task(
    *,
    message: str,
    push: bool = False,
    base: str = DEFAULT_BASE_BRANCH,
    allow_review_output: bool = False,
    pages_repo: str | None = None,
    dry_run: bool = False,
) -> int:
    source_repo = _repo_root_from_cwd()
    pages_root = _pages_repo_from_source(source_repo, pages_repo)

    if pages_root.exists():
        try:
            _validate_pages_repo(pages_root, require_clean=False, require_branch="gh-pages")
        except Exception as exc:  # noqa: BLE001
            print(str(exc), file=sys.stderr)
            return 1

    current_branch = _current_branch(source_repo)
    if current_branch in FORBIDDEN_BRANCHES:
        print(f"Refusing to commit on protected branch {current_branch or '<detached>'}.", file=sys.stderr)
        return 1
    if push and not current_branch:
        print("Refusing to push from a detached HEAD.", file=sys.stderr)
        return 1

    staged = _staged_files(source_repo)
    if not staged:
        print("Refusing to commit because no files are staged.", file=sys.stderr)
        return 1

    staged_errors: list[str] = []
    review_staged = [path for path in staged if _path_is_review_output(path)]
    if review_staged and not allow_review_output:
        staged_errors.append("Refusing staged output/review paths without --allow-review-output.")
    for path in staged:
        forbidden, forbidden_text = _path_is_forbidden(path)
        if forbidden:
            staged_errors.append(f"Refusing staged forbidden path: {forbidden_text}")
    if staged_errors:
        for error in staged_errors:
            print(error, file=sys.stderr)
        return 1

    print("Staged files:")
    for item in staged:
        print(f"- {item}")
    print("Staged diff stat:")
    print(_diff_stat(source_repo))
    if review_staged:
        print("Review output included in staged files and explicitly allowed.")
    print(f"Base branch: {base}")
    print(f"Compare branch: {current_branch or '<detached>'}")
    print(f"Suggested PR title: {_suggest_title(message)}")

    if dry_run:
        print("Dry run: no commit created.")
        if push:
            print(f"Dry run: would push -u origin {current_branch or '<detached>'}.")
        return 0

    try:
        _run_git(source_repo, "commit", "-m", message)
        print(f"Created commit on {current_branch or '<detached>'}.")
        if push:
            if not current_branch:
                print("Refusing to push from a detached HEAD.", file=sys.stderr)
                return 1
            if current_branch in FORBIDDEN_BRANCHES:
                print(f"Refusing to push from protected branch {current_branch or '<detached>'}.", file=sys.stderr)
                return 1
            _run_git(source_repo, "push", "-u", "origin", current_branch)
            print(f"Pushed branch {current_branch} to origin.")
        print("Next PR info:")
        print(f"- Base branch: {base}")
        print(f"- Compare branch: {current_branch or '<detached>'}")
        print(f"- Title suggestion: {_suggest_title(message)}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1


def _build_start_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safely start a feature branch for a Codex task.")
    parser.add_argument("--branch", required=True, help="Feature branch to create or switch to.")
    parser.add_argument("--base", default=DEFAULT_BASE_BRANCH, help="Base branch to fast-forward from origin.")
    parser.add_argument("--pages-repo", default=DEFAULT_PAGES_REPO_NAME, help="Local Pages repo path.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned actions without changing git state.")
    return parser


def _build_stage_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stage a safe task scope without touching generated or public artifacts.")
    parser.add_argument("--files", nargs="+", required=True, help="Exact file or directory paths to stage.")
    parser.add_argument("--allow-review-output", action="store_true", help="Allow staging output/review paths.")
    parser.add_argument("--allow-directory", action="store_true", help="Allow explicit directory paths.")
    parser.add_argument("--allow-base-branch", action="store_true", help="Allow staging while on a protected base branch.")
    parser.add_argument("--pages-repo", default=DEFAULT_PAGES_REPO_NAME, help="Local Pages repo path.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print actions without staging files.")
    return parser


def _build_commit_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Commit a safe task scope and optionally push the feature branch.")
    parser.add_argument("--message", required=True, help="Commit message to use.")
    parser.add_argument("--push", action="store_true", help="Push the current feature branch with tracking.")
    parser.add_argument("--base", default=DEFAULT_BASE_BRANCH, help="Base branch used for PR guidance.")
    parser.add_argument("--allow-review-output", action="store_true", help="Allow committing output/review paths.")
    parser.add_argument("--pages-repo", default=DEFAULT_PAGES_REPO_NAME, help="Local Pages repo path.")
    parser.add_argument("--dry-run", action="store_true", help="Print the commit plan without creating a commit.")
    return parser


def main_start(argv: Sequence[str] | None = None) -> int:
    parser = _build_start_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    return start_safe_task(branch=args.branch, base=args.base, pages_repo=args.pages_repo, dry_run=bool(args.dry_run))


def main_stage(argv: Sequence[str] | None = None) -> int:
    parser = _build_stage_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    return stage_safe_task(
        files=args.files,
        allow_review_output=bool(args.allow_review_output),
        allow_directory=bool(args.allow_directory),
        allow_base_branch=bool(args.allow_base_branch),
        pages_repo=args.pages_repo,
        dry_run=bool(args.dry_run),
    )


def main_commit(argv: Sequence[str] | None = None) -> int:
    parser = _build_commit_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    return commit_safe_task(
        message=args.message,
        push=bool(args.push),
        base=args.base,
        allow_review_output=bool(args.allow_review_output),
        pages_repo=args.pages_repo,
        dry_run=bool(args.dry_run),
    )
