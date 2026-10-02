from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = "bluefern.watch_run.v1"
RECONCILIATION_SCHEMA = "bluefern.watch_reconciliation.v1"
DISPATCHES = {"gaza", "food-line", "care-line"}
STATUSES = {"SUCCESS", "DEGRADED", "FAILED"}
OUTCOMES = {"findings", "no_findings", "failed"}
LEDGER_ROOT = Path("ops/watch-ledger")
LEGACY_LEDGER_ROOT = Path("data/private-agent-handoff/watch-ledger")
RECON_ROOT = Path("data/private-agent-handoff/watch-reconciliation")


class WatchLedgerError(RuntimeError):
    """Raised when authoritative watch-ledger persistence cannot complete."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe(value: Any) -> str:
    text = str(value or "").strip()
    return "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in text)[:180] or "unknown"


def _day(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return ""


def _canonical_finding_url(row: Mapping[str, Any]) -> str:
    for key in ("canonical_source_url", "source_url", "url", "final_trace_url"):
        value = str(row.get(key) or "").strip()
        if value.startswith("https://"):
            return value
    return ""


def _fingerprint(row: Mapping[str, Any]) -> str:
    finding_id = str(row.get("finding_id") or "").strip()
    if finding_id:
        return finding_id
    raw = "|".join(
        [
            _canonical_finding_url(row),
            str(row.get("title") or row.get("headline") or "").strip(),
            str(row.get("source_published_date") or row.get("source_published_at") or "").strip(),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def observation_date(payload: Mapping[str, Any]) -> str:
    window = payload.get("search_window") if isinstance(payload.get("search_window"), Mapping) else {}
    for value in (window.get("edition_date"), window.get("date_to"), payload.get("scheduled_for"), payload.get("started_at")):
        parsed = _day(value)
        if parsed:
            return parsed
    return ""


def validate_watch_run(payload: Any) -> list[str]:
    if not isinstance(payload, Mapping):
        return ["watch run must be an object"]
    errors: list[str] = []
    if payload.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    dispatch = str(payload.get("dispatch") or "")
    if dispatch not in DISPATCHES:
        errors.append("unsupported dispatch")
    for field in ("watch_name", "run_id", "scheduled_for", "started_at", "completed_at", "coverage_notes"):
        if not str(payload.get(field) or "").strip():
            errors.append(f"{field} must be non-empty")
    status = str(payload.get("status") or "")
    outcome = str(payload.get("outcome") or "")
    if status not in STATUSES:
        errors.append("status must be SUCCESS, DEGRADED, or FAILED")
    if outcome not in OUTCOMES:
        errors.append("outcome must be findings, no_findings, or failed")
    findings = payload.get("findings")
    if not isinstance(findings, list) or any(not isinstance(row, Mapping) for row in findings):
        errors.append("findings must be an array of objects")
        findings = []
    if outcome == "findings" and not findings:
        errors.append("findings outcome requires at least one finding")
    if outcome == "no_findings" and findings:
        errors.append("no_findings outcome requires an empty findings array")
    if outcome == "failed" and status != "FAILED":
        errors.append("failed outcome requires FAILED status")
    if status == "FAILED" and outcome != "failed":
        errors.append("FAILED status requires failed outcome")
    if not isinstance(payload.get("search_window"), Mapping):
        errors.append("search_window must be an object")
    if not observation_date(payload):
        errors.append("watch run must resolve to an ISO observation date")
    return errors


def ledger_path(root: Path, payload: Mapping[str, Any]) -> Path:
    return root / LEDGER_ROOT / str(payload["dispatch"]) / observation_date(payload) / f"{_safe(payload.get('run_id'))}.json"


def persist_watch_run(root: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    errors = validate_watch_run(payload)
    if errors:
        raise ValueError("; ".join(errors))
    path = ledger_path(root, payload)
    rendered = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") == rendered:
            return {"status": "SAFE_NO_OP", "path": path.relative_to(root).as_posix()}
        raise FileExistsError(f"watch run idempotency conflict: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered, encoding="utf-8", newline="\n")
    return {"status": "SUCCESS", "path": path.relative_to(root).as_posix()}


def validate_watch_ledger_paths(paths: Iterable[str]) -> None:
    normalized = [path.replace("\\", "/").strip() for path in paths]
    if any(not (path == LEDGER_ROOT.as_posix() or path.startswith(f"{LEDGER_ROOT.as_posix()}/")) for path in normalized):
        raise WatchLedgerError("watch-ledger commit touched a path outside ops/watch-ledger/")


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)


def _git_dirty_paths(root: Path) -> list[str]:
    result = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if result.returncode:
        raise WatchLedgerError(result.stderr.strip() or "cannot inspect watch-ledger checkout")
    paths: list[str] = []
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        status = line[:2]
        path = line[3:].replace("\\", "/").strip()
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[-1].strip()
        if status in {"??", "!!"} and path.endswith("/"):
            directory = root / path
            if directory.is_dir():
                paths.extend(child.relative_to(root).as_posix() for child in sorted(directory.rglob("*")) if child.is_file())
                continue
        paths.append(path)
    return sorted(dict.fromkeys(paths))


def prepare_watch_ledger_checkout(root: Path, *, branch: str = "ops/watch-ledger", remote: str = "origin") -> None:
    current = _git(root, "branch", "--show-current")
    if current.returncode or current.stdout.strip() != branch:
        raise WatchLedgerError(f"watch-ledger checkout branch mismatch: expected {branch}")
    dirty = _git_dirty_paths(root)
    if dirty:
        validate_watch_ledger_paths(dirty)
    fetched = _git(root, "fetch", "--no-tags", remote, f"refs/heads/{branch}")
    if fetched.returncode:
        raise WatchLedgerError(fetched.stderr.strip() or "watch-ledger checkout fetch failed")
    incoming = _git(root, "diff", "--name-only", "HEAD..FETCH_HEAD")
    if incoming.returncode:
        raise WatchLedgerError(incoming.stderr.strip() or "cannot inspect incoming watch-ledger changes")
    overlap = sorted(set(dirty) & {line.strip().replace("\\", "/") for line in incoming.stdout.splitlines() if line.strip()})
    if overlap:
        raise WatchLedgerError(f"incoming watch-ledger changes overlap local ledger changes: {', '.join(overlap)}")
    ancestor = _git(root, "merge-base", "--is-ancestor", "HEAD", "FETCH_HEAD")
    if ancestor.returncode:
        raise WatchLedgerError("watch-ledger checkout cannot fast-forward to its protected branch")
    merged = _git(root, "merge", "--ff-only", "FETCH_HEAD")
    if merged.returncode:
        raise WatchLedgerError(merged.stderr.strip() or "watch-ledger checkout fast-forward failed")


def commit_and_push_watch_ledger(
    root: Path,
    *,
    paths: list[str],
    message: str,
    remote: str = "origin",
    branch: str | None = None,
) -> str | None:
    approved_paths = sorted(dict.fromkeys(path.replace("\\", "/").strip() for path in paths))
    validate_watch_ledger_paths(approved_paths)
    dirty = _git_dirty_paths(root)
    if set(dirty) - set(approved_paths):
        raise WatchLedgerError("watch-ledger checkout contains dirty paths outside the approved ledger append")
    staged = _git(root, "add", "--force", "--", *approved_paths)
    if staged.returncode:
        raise WatchLedgerError(staged.stderr.strip() or "watch-ledger artifact staging failed")
    staged_names = _git(root, "diff", "--cached", "--name-only")
    if staged_names.returncode:
        raise WatchLedgerError(staged_names.stderr.strip() or "cannot inspect staged watch-ledger artifacts")
    staged_paths = [line.strip().replace("\\", "/") for line in staged_names.stdout.splitlines() if line.strip()]
    validate_watch_ledger_paths(staged_paths)
    if set(staged_paths) - set(approved_paths):
        raise WatchLedgerError("watch-ledger staging included an unapproved path")
    if not staged_paths:
        return None
    commit = _git(root, "commit", "-m", message)
    if commit.returncode:
        raise WatchLedgerError(commit.stderr.strip() or "watch-ledger commit failed")
    target_branch = branch or _git(root, "branch", "--show-current").stdout.strip()
    if not target_branch:
        raise WatchLedgerError("watch-ledger checkout is detached")
    pushed = _git(root, "push", remote, target_branch)
    if pushed.returncode:
        raise WatchLedgerError(pushed.stderr.strip() or "watch-ledger final ref update failed")
    head = _git(root, "rev-parse", "HEAD")
    if head.returncode:
        raise WatchLedgerError(head.stderr.strip() or "cannot read watch-ledger commit")
    return head.stdout.strip()


def _production_search_roots(root: Path, dispatch: str) -> tuple[Path, ...]:
    return (
        root / "data" / "dispatches" / dispatch,
        root / "output" / "site" / dispatch,
        root / "output" / "dispatches" / dispatch,
    )


def _production_contains(root: Path, dispatch: str, needles: list[str]) -> tuple[bool, str]:
    usable = [needle for needle in needles if needle]
    if not usable:
        return False, ""
    for search_root in _production_search_roots(root, dispatch):
        if not search_root.exists():
            continue
        for path in search_root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".json", ".md", ".html", ".yml", ".yaml"}:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if any(needle in text for needle in usable):
                return True, path.relative_to(root).as_posix()
    return False, ""


def reconcile_day(root: Path, dispatch: str, observation_day: str, *, write: bool = True) -> dict[str, Any]:
    if dispatch not in DISPATCHES:
        raise ValueError("unsupported dispatch")
    day = _day(observation_day)
    if not day:
        raise ValueError("observation_day must be ISO YYYY-MM-DD")
    run_root = root / LEDGER_ROOT / dispatch / day
    runs: list[dict[str, Any]] = []
    if run_root.exists():
        for path in sorted(run_root.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            errors = validate_watch_run(payload)
            if errors:
                raise ValueError(f"invalid watch ledger entry {path}: {'; '.join(errors)}")
            runs.append(payload)

    rows: list[dict[str, Any]] = []
    for run in runs:
        for finding in run.get("findings") or []:
            finding_id = _fingerprint(finding)
            url = _canonical_finding_url(finding)
            title = str(finding.get("title") or finding.get("headline") or "").strip()
            accounted, evidence_ref = _production_contains(root, dispatch, [url, finding_id, title])
            rows.append(
                {
                    "run_id": run["run_id"],
                    "finding_id": finding_id,
                    "canonical_source_url": url,
                    "title": title,
                    "status": "ACCOUNTED" if accounted else "UNRECONCILED",
                    "evidence_ref": evidence_ref,
                }
            )

    report = {
        "schema_version": RECONCILIATION_SCHEMA,
        "dispatch": dispatch,
        "observation_date": day,
        "generated_at": _utc_now(),
        "run_count": len(runs),
        "successful_run_count": sum(1 for row in runs if row.get("status") in {"SUCCESS", "DEGRADED"}),
        "failed_run_count": sum(1 for row in runs if row.get("status") == "FAILED"),
        "zero_finding_run_count": sum(1 for row in runs if row.get("outcome") == "no_findings"),
        "finding_count": len(rows),
        "accounted_count": sum(1 for row in rows if row["status"] == "ACCOUNTED"),
        "unreconciled_count": sum(1 for row in rows if row["status"] == "UNRECONCILED"),
        "findings": rows,
        "publication_authorized": False,
        "automatic_approval": False,
    }
    if write:
        path = root / RECON_ROOT / dispatch / f"{day}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")
        report["report_ref"] = path.relative_to(root).as_posix()
    return report
