from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any, Sequence

from bluefern_dispatches.bluesky_post import post_bluesky_external_card
from bluefern_dispatches.food_line_signal_wire import (
    BASE_URL,
    CARD_DIR_NAME,
    CARD_SIZE,
    CURRENT_AS_OF,
    PUBLIC_PATH_PREFIX,
    _render_card,
)

DISPATCH_SLUG = "food-line"
SIGNAL_WIRE_STATE_RELATIVE_PATH = Path("data/dispatches/food-line/signal-wire/publication-state.json")
PUBLIC_SIGNAL_ROOT = Path("output/site/food-line/wire")
PAGES_SIGNAL_ROOT = Path("food-line/wire")
PUBLIC_PERMALINK_BASE = f"{BASE_URL}{PUBLIC_PATH_PREFIX}"
PUBLIC_STATE_SCHEMA_VERSION = "food_line_signal_wire_publication_state_v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def _repo_clean(repo: Path) -> bool:
    result = _run_git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"git status failed in {repo}")
    return not any(line.strip() for line in result.stdout.splitlines())


def _repo_branch(repo: Path) -> str:
    result = _run_git(repo, "branch", "--show-current")
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"git branch failed in {repo}")
    return result.stdout.strip()


def _repo_head(repo: Path) -> str:
    result = _run_git(repo, "rev-parse", "HEAD")
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"git rev-parse failed in {repo}")
    return result.stdout.strip()


def _atomic_write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=str(path.parent))
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
    return path


def _signal_wire_state_path(project_root: Path) -> Path:
    return project_root / SIGNAL_WIRE_STATE_RELATIVE_PATH


def load_signal_wire_publication_state(project_root: Path) -> dict[str, Any]:
    path = _signal_wire_state_path(project_root)
    if not path.exists():
        return {"schema_version": PUBLIC_STATE_SCHEMA_VERSION, "signals": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("signal wire publication state must be a JSON object")
    payload.setdefault("schema_version", PUBLIC_STATE_SCHEMA_VERSION)
    payload.setdefault("signals", {})
    if not isinstance(payload["signals"], dict):
        payload["signals"] = {}
    return payload


def write_signal_wire_publication_state(project_root: Path, payload: dict[str, Any]) -> Path:
    payload = dict(payload)
    payload["schema_version"] = PUBLIC_STATE_SCHEMA_VERSION
    payload.setdefault("signals", {})
    return _atomic_write_json(_signal_wire_state_path(project_root), payload)


def _public_dir(project_root: Path, signal_id: str) -> Path:
    return project_root / PUBLIC_SIGNAL_ROOT / signal_id


def _pages_event_dir(pages_repo: Path, signal_id: str) -> Path:
    return pages_repo / PAGES_SIGNAL_ROOT / signal_id


def _event_public_url(signal_id: str) -> str:
    return f"{PUBLIC_PERMALINK_BASE}{signal_id}/"


def _render_public_page(event: dict[str, Any], published_at: str) -> str:
    source_url = str(event.get("canonical_source_url") or "").strip()
    publisher = str(event.get("publisher") or "").strip()
    headline = str(event.get("headline") or "").strip()
    geography = str(event.get("geography_scope") or event.get("state") or "").strip()
    category = str(event.get("pressure_category") or "").strip()
    summary = str(event.get("public_summary") or "").strip()
    why = str(event.get("why_it_matters") or "").strip()
    note = "Source-backed and traceable. This page is limited to a single signal record and its approved card."
    canonical_href = escape(_event_public_url(str(event.get("signal_id") or "")))
    lines = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '  <meta charset="utf-8">',
        '  <meta name="viewport" content="width=device-width, initial-scale=1">',
        f"  <title>{escape(headline or 'Food Line Signal Wire')}</title>",
        f'  <meta name="description" content="{escape(summary[:240] or headline[:240])}">',
        f'  <link rel="canonical" href="{canonical_href}">',
        "  <style>",
        "    body{margin:0;font-family:system-ui,Segoe UI,Arial,sans-serif;background:#08131a;color:#f4f6f1;line-height:1.5}",
        "    main{max-width:920px;margin:0 auto;padding:48px 28px 64px}",
        "    .eyebrow{letter-spacing:.18em;text-transform:uppercase;color:#b9d0c0;font-size:.78rem}",
        "    h1{font-family:Georgia,serif;font-size:clamp(2.1rem,5vw,4rem);line-height:1.02;margin:.4rem 0 1rem}",
        "    .meta{color:#bfd0c4;margin:0 0 1.4rem}",
        "    .summary,.why,.note{max-width:68ch;font-size:1.03rem}",
        "    .card{margin:2rem 0;border:1px solid rgba(173,200,183,.25);padding:18px;background:rgba(13,25,21,.78)}",
        "    .card img{display:block;width:100%;height:auto;border:0}",
        "    a{color:#d9f0dd}",
        "  </style>",
        "</head>",
        "<body>",
        "  <main>",
        "    <div class=\"eyebrow\">THE BLUE FERN CO.</div>",
        "    <div class=\"eyebrow\">FOOD LINE</div>",
        "    <div class=\"eyebrow\">SIGNAL WIRE</div>",
        f"    <h1>{escape(headline)}</h1>",
        f"    <p class=\"meta\">Published {escape(published_at)} | {escape(geography)} | {escape(category)}</p>",
        f"    <p class=\"summary\">{escape(summary)}</p>",
        "    <h2>Why it matters</h2>",
        f"    <p class=\"why\">{escape(why)}</p>",
        f"    <p><strong>Source:</strong> {escape(publisher)}",
    ]
    if source_url:
        lines[-1] += f' - <a href="{escape(source_url)}">{escape(source_url)}</a>'
    lines[-1] += "</p>"
    lines.extend(
        [
            f"    <p class=\"note\">{escape(note)}</p>",
            f'    <p><a href="/food-line/">Back to Food Line</a></p>',
            "    <div class=\"card\">",
            f'      <img src="social.png" width="{CARD_SIZE[0]}" height="{CARD_SIZE[1]}" alt="{escape(str(event.get("card_description") or "Food Line Signal Wire card"))}">',
            "    </div>",
            "  </main>",
            "</body>",
            "</html>",
        ]
    )
    return "\n".join(lines)


def _write_public_artifacts(project_root: Path, event: dict[str, Any], *, published_at: str) -> dict[str, Path]:
    signal_id = str(event.get("signal_id") or "").strip()
    public_dir = _public_dir(project_root, signal_id)
    public_dir.mkdir(parents=True, exist_ok=True)
    page_path = public_dir / "index.html"
    card_path = public_dir / "social.png"
    page_path.write_text(_render_public_page(event, published_at), encoding="utf-8")
    _render_card(event, card_path)
    return {"page_path": page_path, "card_path": card_path}


def _load_state_record(state: dict[str, Any], signal_id: str) -> dict[str, Any] | None:
    signals = state.get("signals") if isinstance(state, dict) else None
    if not isinstance(signals, dict):
        return None
    record = signals.get(signal_id)
    return record if isinstance(record, dict) else None


def _write_state_record(project_root: Path, record: dict[str, Any]) -> Path:
    state = load_signal_wire_publication_state(project_root)
    signals = state.setdefault("signals", {})
    signals[str(record["signal_id"])] = record
    state["updated_at"] = _utc_now()
    return write_signal_wire_publication_state(project_root, state)


def _page_repo_signal_dir(pages_repo: Path, signal_id: str) -> Path:
    return pages_repo / PAGES_SIGNAL_ROOT / signal_id


def _pages_changed_paths(pages_repo: Path) -> list[str]:
    result = _run_git(pages_repo, "status", "--porcelain=v1", "--untracked-files=all")
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git status failed")
    paths: list[str] = []
    for raw_line in result.stdout.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        path = line[3:] if len(line) > 3 else line
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.append(path.replace("\\", "/").lstrip("./"))
    return paths


def _pages_scope_ok(signal_id: str, changed_paths: Sequence[str]) -> tuple[bool, list[str]]:
    allowed_prefix = f"food-line/wire/{signal_id}/"
    unexpected = [path for path in changed_paths if not (path == allowed_prefix.rstrip("/") or path.startswith(allowed_prefix))]
    return not unexpected, unexpected


def _copy_signal_wire_page(source_root: Path, pages_repo: Path, signal_id: str) -> None:
    source_dir = _public_dir(source_root, signal_id)
    if not source_dir.exists():
        raise FileNotFoundError(f"missing rendered signal wire page directory: {source_dir}")
    target_dir = _page_repo_signal_dir(pages_repo, signal_id)
    if target_dir.exists():
        shutil.rmtree(target_dir)
    shutil.copytree(source_dir, target_dir)


def _commit_pages_repo(pages_repo: Path, signal_id: str) -> str | None:
    _run_git(pages_repo, "add", "-A", "--", str(PAGES_SIGNAL_ROOT / signal_id))
    if _run_git(pages_repo, "diff", "--cached", "--quiet").returncode == 0:
        return None
    commit_result = _run_git(pages_repo, "commit", "-m", f"Publish Food Line Signal Wire {signal_id}")
    if commit_result.returncode != 0:
        raise RuntimeError(commit_result.stderr.strip() or commit_result.stdout.strip() or "git commit failed")
    return _repo_head(pages_repo)


def publish_signal_wire_event(
    project_root: Path,
    event: dict[str, Any],
    *,
    pages_repo: Path | None = None,
    source_branch: str | None = None,
    pages_branch: str = "gh-pages",
    push: bool = False,
    post_bluesky: bool = True,
    dry_run: bool = False,
    trace: list[str] | None = None,
) -> dict[str, Any]:
    root = project_root.resolve()
    signal_id = str(event.get("signal_id") or "").strip()
    if not signal_id:
        return {"ok": False, "status": "blocked", "reason": "missing_signal_id", "signal_id": None, "trace": trace or []}
    if not bool(event.get("wire_auto_publish_eligible")):
        return {"ok": False, "status": "blocked", "reason": "signal_not_eligible", "signal_id": signal_id, "trace": trace or []}

    trace = trace if trace is not None else []
    state = load_signal_wire_publication_state(root)
    existing = _load_state_record(state, signal_id)
    content_sha256 = str(event.get("content_sha256") or "").strip()
    record_fingerprint = str(event.get("record_fingerprint") or "").strip()
    canonical_source_url = str(event.get("canonical_source_url") or "").strip()
    public_permalink = str(event.get("public_permalink") or _event_public_url(signal_id)).strip()
    published_at = _utc_now()

    if existing:
        if existing.get("content_sha256") and existing.get("content_sha256") != content_sha256:
            existing["revision_status"] = "material_update_requires_review"
            existing["last_error"] = "material_update_requires_review"
            existing["last_seen_at"] = published_at
            _write_state_record(root, existing)
            return {
                "ok": False,
                "status": "blocked",
                "reason": "material_update_requires_review",
                "signal_id": signal_id,
                "trace": trace,
            }
        if (
            str(existing.get("publication_status") or "") == "published"
            and str(existing.get("content_sha256") or "") == content_sha256
            and str(existing.get("bluesky_status") or "") == "posted"
            and str(existing.get("post_uri") or "").strip()
            and str(existing.get("public_permalink") or "").strip() == public_permalink
        ):
            existing["last_seen_at"] = published_at
            _write_state_record(root, existing)
            return {
                "ok": True,
                "status": "skipped",
                "reason": "already_posted",
                "signal_id": signal_id,
                "trace": trace + ["duplicate_detected", "no_pages_write", "no_bluesky_call"],
                "state_path": str(_signal_wire_state_path(root)),
                "public_permalink": public_permalink,
            }
    page_publish_needed = True
    bluesky_retry_only = False
    if existing and str(existing.get("content_sha256") or "") == content_sha256:
        if str(existing.get("publication_status") or "") == "published" and str(existing.get("bluesky_status") or "") == "failed":
            page_publish_needed = False
            bluesky_retry_only = True
        elif str(existing.get("publication_status") or "") == "published" and not str(existing.get("post_uri") or "").strip():
            page_publish_needed = False
            bluesky_retry_only = True

    record = {
        "signal_id": signal_id,
        "record_fingerprint": record_fingerprint,
        "content_sha256": content_sha256,
        "canonical_source_url": canonical_source_url,
        "public_permalink": public_permalink,
        "first_seen_at": existing.get("first_seen_at") if existing else published_at,
        "last_seen_at": published_at,
        "publication_status": "pending",
        "pages_commit": existing.get("pages_commit") if existing else None,
        "published_at": existing.get("published_at") if existing else None,
        "bluesky_status": existing.get("bluesky_status") if existing else "not_posted",
        "post_uri": existing.get("post_uri") if existing else None,
        "post_cid": existing.get("post_cid") if existing else None,
        "posted_at": existing.get("posted_at") if existing else None,
        "last_error": None,
        "revision_status": existing.get("revision_status") if existing else None,
        "publisher": str(event.get("publisher") or "").strip(),
        "headline": str(event.get("headline") or "").strip(),
        "geography_scope": str(event.get("geography_scope") or event.get("state") or "").strip(),
        "pressure_category": str(event.get("pressure_category") or "").strip(),
        "public_summary": str(event.get("public_summary") or "").strip(),
        "why_it_matters": str(event.get("why_it_matters") or "").strip(),
        "wire_auto_publish_eligible": bool(event.get("wire_auto_publish_eligible")),
    }
    _write_state_record(root, record)

    if dry_run:
        record["last_seen_at"] = _utc_now()
        record["last_error"] = "dry_run"
        _write_state_record(root, record)
        return {
            "ok": True,
            "status": "dry_run",
            "reason": "dry_run",
            "signal_id": signal_id,
            "trace": trace + ["render", "pages_validate", "pages_commit", "pages_push_mock", "state_page_published", "bluesky_session_mock", "bluesky_blob_mock", "bluesky_post_mock", "state_social_posted"],
            "state_path": str(_signal_wire_state_path(root)),
            "public_permalink": public_permalink,
        }

    trace.append("render")
    artifacts = None
    if page_publish_needed:
        artifacts = _write_public_artifacts(root, event, published_at=published_at)
    else:
        existing_artifact_dir = _public_dir(root, signal_id)
        if existing_artifact_dir.exists():
            artifacts = {"page_path": existing_artifact_dir / "index.html", "card_path": existing_artifact_dir / "social.png"}
        else:
            artifacts = _write_public_artifacts(root, event, published_at=published_at)
    pages_result: dict[str, Any] = {
        "status": "skipped",
        "commit_hash": None,
        "push_performed": False,
        "changed_paths": [],
    }
    pages_root = pages_repo.resolve() if pages_repo else None
    if page_publish_needed:
        if pages_root is not None:
            if not pages_root.exists():
                raise FileNotFoundError(f"pages repo does not exist: {pages_root}")
            if _repo_branch(pages_root) != pages_branch:
                raise RuntimeError(f"pages repo branch mismatch: expected {pages_branch}, found {_repo_branch(pages_root) or '<detached>'}")
            if not _repo_clean(pages_root):
                raise RuntimeError("pages repo must be clean before signal wire publication")
            trace.append("pages_validate")
            _copy_signal_wire_page(root, pages_root, signal_id)
            changed_paths = _pages_changed_paths(pages_root)
            ok, unexpected = _pages_scope_ok(signal_id, changed_paths)
            if not ok:
                raise RuntimeError("unexpected Pages repo changes outside the allowed signal wire scope: " + ", ".join(sorted(unexpected)))
            trace.append("pages_commit")
            commit_hash = _commit_pages_repo(pages_root, signal_id)
            pages_result["commit_hash"] = commit_hash
            pages_result["changed_paths"] = changed_paths
            pages_result["status"] = "committed" if commit_hash else "skipped-no-changes"
            if push and commit_hash:
                if _repo_branch(pages_root) != pages_branch:
                    raise RuntimeError(f"push target branch mismatch: expected {pages_branch}")
                push_result = _run_git(pages_root, "push", "origin", pages_branch)
                if push_result.returncode != 0:
                    raise RuntimeError(push_result.stderr.strip() or push_result.stdout.strip() or "git push failed")
                pages_result["push_performed"] = True
                trace.append("pages_push_mock")
        else:
            _write_public_artifacts(root, event, published_at=published_at)
    else:
        pages_result["status"] = "skipped"
        pages_result["commit_hash"] = str(existing.get("pages_commit") or "") if existing else None

    record["publication_status"] = "published"
    record["pages_commit"] = pages_result.get("commit_hash")
    record["published_at"] = published_at
    record["last_seen_at"] = _utc_now()
    _write_state_record(root, record)
    trace.append("state_page_published")

    bluesky_result = {
        "requested": bool(post_bluesky),
        "status": "skipped" if not post_bluesky else "blocked",
        "reason": "not_requested" if not post_bluesky else "publication_not_run",
        "post_uri": None,
        "post_cid": None,
    }
    if post_bluesky:
        trace.append("bluesky_session_mock")
        trace.append("bluesky_blob_mock")
        trace.append("bluesky_post_mock")
        bluesky_result = post_bluesky_external_card(
            project_root=root,
            public_url=public_permalink,
            post_text=str(event.get("bluesky_post_text") or "").strip(),
            card_title=str(event.get("headline") or "").strip() or "Food Line Signal Wire",
            card_description=str(event.get("card_description") or "").strip() or f"{event.get('geography_scope') or event.get('state') or ''} - {event.get('pressure_category') or ''}",
            image_candidates=[artifacts["card_path"]],
            image_alt=str(event.get("card_description") or "Food Line Signal Wire card"),
            receipt_path=None,
            allow_publish=True,
            dry_run=False,
            force_post=False,
        )
        record["bluesky_status"] = "posted" if bluesky_result.get("status") == "success" else "failed"
        record["post_uri"] = bluesky_result.get("post_uri")
        record["post_cid"] = bluesky_result.get("post_cid")
        record["posted_at"] = _utc_now() if bluesky_result.get("status") == "success" else record.get("posted_at")
        record["last_error"] = bluesky_result.get("reason") if bluesky_result.get("status") != "success" else None
        _write_state_record(root, record)
        trace.append("state_social_posted")
    elif bluesky_retry_only:
        record["last_seen_at"] = _utc_now()
        _write_state_record(root, record)

    return {
        "ok": pages_result.get("status") in {"committed", "skipped-no-changes", "skipped"} and (not post_bluesky or bluesky_result.get("status") == "success"),
        "status": "success" if pages_result.get("status") in {"committed", "skipped-no-changes", "skipped"} else "failed",
        "reason": None,
        "signal_id": signal_id,
        "state_path": str(_signal_wire_state_path(root)),
        "public_permalink": public_permalink,
        "pages_result": pages_result,
        "bluesky_result": bluesky_result,
        "trace": trace,
        "artifacts": {k: str(v) for k, v in artifacts.items()},
    }


def run_signal_wire_live_publication(
    project_root: Path,
    *,
    pages_repo: Path,
    source_branch: str,
    pages_branch: str,
    dry_run: bool = False,
    post_bluesky: bool = True,
) -> dict[str, Any]:
    from bluefern_dispatches.food_line_discovery_expansion import run_food_line_discovery_expansion
    from bluefern_dispatches.food_line_signal_wire import build_signal_wire_event_from_candidate

    root = project_root.resolve()
    if not root.exists():
        raise FileNotFoundError(f"source repo does not exist: {root}")
    if _repo_branch(root) != source_branch:
        raise RuntimeError(f"source repo branch mismatch: expected {source_branch}, found {_repo_branch(root) or '<detached>'}")
    if not _repo_clean(root):
        raise RuntimeError("source repo must be clean before signal wire publication")
    discovery = run_food_line_discovery_expansion(
        root,
        CURRENT_AS_OF,
        edition_mode="current_update",
        max_results_per_query=5,
        max_queries=4,
        query_lookback_days=1,
        query_lookahead_days=0,
        public_claim_lookback_days=0,
        public_claim_lookahead_days=0,
        dry_run=True,
    )
    candidates = [
        row
        for row in (discovery.get("candidates") or discovery.get("_candidate_records") or [])
        if isinstance(row, dict) and bool(row.get("public_claim_eligible"))
    ]
    events = [build_signal_wire_event_from_candidate(candidate, as_of=CURRENT_AS_OF) for candidate in candidates]
    events = sorted(events, key=lambda item: str(item.get("signal_id") or ""))
    results: list[dict[str, Any]] = []
    for event in events:
        result = publish_signal_wire_event(
            root,
            event,
            pages_repo=pages_repo,
            source_branch=source_branch,
            pages_branch=pages_branch,
            push=False,
            post_bluesky=post_bluesky,
            dry_run=dry_run,
        )
        results.append(result)
    eligible_count = sum(1 for event in events if bool(event.get("wire_auto_publish_eligible")))
    return {
        "ok": all(bool(result.get("ok")) for result in results) if results else True,
        "status": "success",
        "source_count": int(discovery.get("source_count") or len(discovery.get("query_rows") or [])),
        "candidate_count": len(candidates),
        "eligible_count": eligible_count,
        "results": results,
        "discovery": discovery,
        "trace": [step for result in results for step in list(result.get("trace") or [])],
        "pages_repo": str(pages_repo),
        "source_branch": source_branch,
        "pages_branch": pages_branch,
    }
