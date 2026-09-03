from __future__ import annotations

import hashlib
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from email.utils import format_datetime
from pathlib import Path, PurePosixPath
from typing import Any, Sequence
from urllib.parse import urlsplit

from bluefern_dispatches.generator import (
    BASE_URL,
    footer,
    header,
    page,
)


APPROVAL_REQUEST_SCHEMA = "gaza_historical_catchup_approval_request_v2"
APPROVAL_SCHEMA = "gaza_historical_catchup_approval_v2"
PLAN_SCHEMA = "gaza_historical_catchup_plan_v2"
PREVIEW_SCHEMA = "gaza_historical_catchup_private_preview_v2"
RELEASE_SCHEMA = "gaza_historical_catchup_release_manifest_v2"
PUBLICATION_STATE_SCHEMA = "gaza_historical_catchup_publication_state_v2"

REVIEW_PREFIX = "data/agent-history/gaza/reviews/"
DECISION_PREFIX = "data/agent-history/gaza/reviews/decisions/"
NORMALIZED_PREFIX = "data/agent-history/gaza/normalized/"
APPROVAL_PREFIX = "approvals/gaza/"
STATE_PREFIX = "data/dispatches/gaza/historical-catchup-publication-state/"

CATCHUP_RE = re.compile(r"gaza-historical-catchup-[a-z0-9][a-z0-9-]{2,80}")
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
MAX_ITEMS = 15
MOJIBAKE_MARKERS = ("Ã", "Â", "â€", "â€™", "â€œ", "â€�", "ðŸ", "ï»¿", "�")

PUBLIC_ENTRY_NAMES = (
    "gaza/index.html",
    "gaza/archive.html",
    "gaza/rss.xml",
)
NON_MUTATED_DEPENDENCIES = (
    "index.html",
    "gaza/podcast.xml",
    "gaza/audio/podcast.xml",
    "gaza/flash-briefing.json",
)
PROTECTED_OWNER_PATHS = (
    "src/bluefern_dispatches/gaza_historical_catchup.py",
    "src/bluefern_dispatches/generator.py",
    "src/bluefern_dispatches/story_dedupe.py",
    "scripts/manage_gaza_historical_catchup.py",
    "scripts/validate_publish_scope.py",
)


class GazaHistoricalCatchupError(ValueError):
    pass


@dataclass(frozen=True)
class CommittedJson:
    commit: str
    path: str
    blob_sha1: str
    sha256: str
    raw: bytes
    payload: dict[str, Any]


@dataclass(frozen=True)
class CatchupBundle:
    approval: CommittedJson
    catchup_id: str
    publication_date: str
    publication_timestamp: str
    title: str
    introduction: str
    disclosure: str
    source_head: str
    pages_head: str
    approval_pages_head: str
    public_path: str
    public_url: str
    items: tuple[dict[str, Any], ...]
    pages_root: Path
    expected_pages_paths: tuple[str, ...]


def public_path_for(catchup_id: str) -> str:
    value = str(catchup_id or "").strip()
    if not CATCHUP_RE.fullmatch(value):
        raise GazaHistoricalCatchupError("catch-up ID is malformed")
    return f"gaza/catchups/{value}/"


def public_url_for(catchup_id: str) -> str:
    return f"{BASE_URL}/{public_path_for(catchup_id)}"


def canonical_json(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def fingerprint(payload: Any) -> str:
    return "sha256:" + sha256_bytes(canonical_json(payload))


def _git(root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if check and result.returncode != 0:
        raise GazaHistoricalCatchupError(
            result.stderr.strip() or result.stdout.strip() or f"git {' '.join(args)} failed"
        )
    return result.stdout.strip()


def _root(root: Path) -> Path:
    resolved = root.absolute().resolve(strict=True)
    top = Path(_git(resolved, "rev-parse", "--show-toplevel")).resolve(strict=True)
    if not os.path.samefile(resolved, top):
        raise GazaHistoricalCatchupError("source root must be the exact Git worktree root")
    return resolved


def _safe_relative(value: Any, *, prefix: str) -> str:
    text = str(value or "").strip()
    pure = PurePosixPath(text)
    if (
        "\\" in text
        or ":" in text
        or text.startswith(("/", "./"))
        or re.match(r"^[A-Za-z]:", text)
        or not text.startswith(prefix)
        or not text.endswith(".json")
        or pure.is_absolute()
        or ".." in pure.parts
        or text != pure.as_posix()
        or any(part in {"", "."} for part in pure.parts)
    ):
        raise GazaHistoricalCatchupError(f"path must be a canonical repository-relative {prefix}*.json path")
    return text


def _require_commit(root: Path, commit: Any, *, strict_ancestor: bool = False) -> str:
    value = str(commit or "").strip().lower()
    if not COMMIT_RE.fullmatch(value):
        raise GazaHistoricalCatchupError("commit must be a full lowercase Git object ID")
    result = subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", value, "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise GazaHistoricalCatchupError("committed authority is not an ancestor of the current source HEAD")
    if strict_ancestor and value == _git(root, "rev-parse", "HEAD"):
        raise GazaHistoricalCatchupError("approval authority must be consumed only after normal protected merge")
    return value


def load_committed_json(
    root: Path,
    *,
    commit: str,
    path: str,
    prefix: str,
    expected_blob_sha1: str | None = None,
    expected_sha256: str | None = None,
) -> CommittedJson:
    root = _root(root)
    commit = _require_commit(root, commit)
    path = _safe_relative(path, prefix=prefix)
    blob = _git(root, "rev-parse", f"{commit}:{path}").lower()
    if not COMMIT_RE.fullmatch(blob):
        raise GazaHistoricalCatchupError("committed JSON blob identity is malformed")
    if expected_blob_sha1 and blob != str(expected_blob_sha1).strip().lower():
        raise GazaHistoricalCatchupError("committed JSON blob SHA-1 does not match")
    result = subprocess.run(
        ["git", "-C", str(root), "cat-file", "blob", blob],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        raise GazaHistoricalCatchupError("unable to load committed JSON bytes")
    raw = result.stdout
    digest = sha256_bytes(raw)
    wanted = str(expected_sha256 or "").removeprefix("sha256:").strip().lower()
    if wanted and digest != wanted:
        raise GazaHistoricalCatchupError("committed JSON raw SHA-256 does not match")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GazaHistoricalCatchupError("committed artifact is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise GazaHistoricalCatchupError("committed artifact must contain a JSON object")
    return CommittedJson(commit, path, blob, digest, raw, payload)


def _load_binding(root: Path, row: Any, *, prefix: str) -> CommittedJson:
    if not isinstance(row, dict) or set(row) != {"commit", "path", "blob_sha1", "sha256"}:
        raise GazaHistoricalCatchupError("committed binding fields are invalid")
    return load_committed_json(
        root,
        commit=row["commit"],
        path=row["path"],
        prefix=prefix,
        expected_blob_sha1=row["blob_sha1"],
        expected_sha256=row["sha256"],
    )


def _binding(artifact: CommittedJson) -> dict[str, str]:
    return {
        "commit": artifact.commit,
        "path": artifact.path,
        "blob_sha1": artifact.blob_sha1,
        "sha256": artifact.sha256,
    }


def _timestamp(value: Any, label: str) -> tuple[str, datetime]:
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GazaHistoricalCatchupError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise GazaHistoricalCatchupError(f"{label} must include a timezone")
    return text, parsed.astimezone(timezone.utc)


def _public_text(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise GazaHistoricalCatchupError(f"{label} is required")
    if any(marker in text for marker in MOJIBAKE_MARKERS):
        raise GazaHistoricalCatchupError(f"{label} contains mojibake")
    if re.search(r"(?:GZ-(?:GAP-)?[A-Z0-9-]+|sha256:|\|)", text, re.I):
        raise GazaHistoricalCatchupError(f"{label} exposes internal identity or machine metadata")
    if any(ord(char) < 32 and char not in "\n\t" for char in text):
        raise GazaHistoricalCatchupError(f"{label} contains a control character")
    return text


def _safe_url(value: Any, label: str) -> str:
    text = str(value or "").strip()
    parsed = urlsplit(text)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise GazaHistoricalCatchupError(f"{label} must be a credential-free HTTPS URL")
    return text


def _clean_pages(pages_root: Path, expected_head: str) -> tuple[Path, str]:
    pages = pages_root.absolute().resolve(strict=True)
    top = Path(_git(pages, "rev-parse", "--show-toplevel")).resolve(strict=True)
    if not os.path.samefile(pages, top):
        raise GazaHistoricalCatchupError("Pages root must be the exact checkout root")
    if _git(pages, "branch", "--show-current") != "gh-pages":
        raise GazaHistoricalCatchupError("Pages checkout must be on gh-pages")
    if _git(pages, "status", "--porcelain", "--untracked-files=all"):
        raise GazaHistoricalCatchupError("Pages checkout must be clean")
    head = _git(pages, "rev-parse", "HEAD")
    if head != expected_head:
        raise GazaHistoricalCatchupError("Pages checkout drifted from the approval binding")
    return pages, head


def _git_blob_bytes(root: Path, revision: str, path: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(root), "show", f"{revision}:{path}"],
        check=False,
        capture_output=True,
    )
    if result.returncode:
        raise GazaHistoricalCatchupError(f"required Pages history surface is missing at {revision}: {path}")
    return result.stdout


def _git_path_exists(root: Path, revision: str, path: str) -> bool:
    return subprocess.run(
        ["git", "-C", str(root), "cat-file", "-e", f"{revision}:{path}"],
        check=False,
        capture_output=True,
    ).returncode == 0


def _public_history_identities(raw: bytes) -> set[str]:
    text = raw.decode("utf-8")
    paths = re.findall(
        r"(?:https://[^/]+)?(?:/gaza/)?((?:editions/\d{4}-\d{2}-\d{2}|catchups/gaza-historical-catchup-[a-z0-9-]+)/)",
        text,
    )
    return {f"/gaza/{path}" for path in paths}


def _validate_added_publications(pages: Path, baseline: str, head: str) -> set[str]:
    changed = {
        value for value in _git(pages, "diff", "--name-only", baseline, head, "--", "gaza/editions", "gaza/catchups").splitlines()
        if value
    }
    prior = {
        value for value in _git(pages, "ls-tree", "-r", "--name-only", baseline, "gaza/editions", "gaza/catchups").splitlines()
        if value
    }
    for relative in prior & changed:
        raise GazaHistoricalCatchupError(
            f"Pages descendant modified a relevant prior Gaza publication surface: {relative}"
        )
    added = changed - prior
    roots: set[str] = set()
    for relative in added:
        parts = PurePosixPath(relative).parts
        if len(parts) < 4 or parts[0] != "gaza" or parts[1] not in {"editions", "catchups"}:
            raise GazaHistoricalCatchupError(f"Pages descendant added a malformed Gaza publication path: {relative}")
        roots.add("/".join(parts[:3]))
    for public_root in roots:
        required = {
            f"{public_root}/index.html",
            f"{public_root}/edition_manifest.json",
            f"{public_root}/sources_manifest.json",
            f"{public_root}/curation_manifest.json",
        }
        if not required <= {value for value in added if value.startswith(public_root + "/")}:
            raise GazaHistoricalCatchupError(f"Pages descendant contains an incomplete Gaza publication: {public_root}")
        for relative in required - {f"{public_root}/index.html"}:
            try:
                json.loads((pages / relative).read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise GazaHistoricalCatchupError(f"Pages descendant contains an invalid Gaza manifest: {relative}") from exc
    return {f"/{public_root}/" for public_root in roots}


def _clean_pages_for_plan(
    pages_root: Path,
    approved_head: str,
    *,
    public_path: str,
) -> tuple[Path, str]:
    pages, head = _clean_pages_checkout(pages_root)
    if not COMMIT_RE.fullmatch(approved_head):
        raise GazaHistoricalCatchupError("Pages approval binding is malformed")
    if head == approved_head:
        return pages, head
    ancestor = subprocess.run(
        ["git", "-C", str(pages), "merge-base", "--is-ancestor", approved_head, head],
        check=False,
        capture_output=True,
    )
    if ancestor.returncode:
        raise GazaHistoricalCatchupError("Pages checkout is not a strict descendant of the approval binding")
    target = pages / public_path.rstrip("/")
    if target.exists():
        raise GazaHistoricalCatchupError("approved catch-up public path is already occupied")
    added_publications = _validate_added_publications(pages, approved_head, head)
    for relative in ("gaza/archive.html", "gaza/rss.xml"):
        prior = _public_history_identities(_git_blob_bytes(pages, approved_head, relative))
        current_path = pages / relative
        if not current_path.is_file():
            raise GazaHistoricalCatchupError(f"required Pages history surface is missing: {relative}")
        current = _public_history_identities(current_path.read_bytes())
        if not (prior | added_publications) <= current:
            raise GazaHistoricalCatchupError(f"Pages descendant dropped Gaza history from {relative}")
    index_path = pages / "gaza/index.html"
    if not index_path.is_file():
        raise GazaHistoricalCatchupError("required Pages homepage is missing")
    for identity in _public_history_identities(index_path.read_bytes()):
        relative = identity.split("/gaza/", 1)[1]
        if not (pages / "gaza" / relative).is_dir():
            raise GazaHistoricalCatchupError("Pages homepage references a missing Gaza publication")
    for relative in NON_MUTATED_DEPENDENCIES:
        if _git_path_exists(pages, approved_head, relative) and not (pages / relative).is_file():
            raise GazaHistoricalCatchupError(f"Pages descendant dropped a required non-mutated dependency: {relative}")
    flash = pages / "gaza/flash-briefing.json"
    if flash.is_file():
        try:
            json.loads(flash.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GazaHistoricalCatchupError("Pages descendant contains an invalid flash-briefing dependency") from exc
    return pages, head


def _clean_pages_checkout(pages_root: Path) -> tuple[Path, str]:
    pages = pages_root.absolute().resolve(strict=True)
    top = Path(_git(pages, "rev-parse", "--show-toplevel")).resolve(strict=True)
    if not os.path.samefile(pages, top):
        raise GazaHistoricalCatchupError("Pages root must be the exact checkout root")
    if _git(pages, "branch", "--show-current") != "gh-pages":
        raise GazaHistoricalCatchupError("Pages checkout must be on gh-pages")
    if _git(pages, "status", "--porcelain", "--untracked-files=all"):
        raise GazaHistoricalCatchupError("Pages checkout must be clean")
    return pages, _git(pages, "rev-parse", "HEAD")


def _source_status_for_approval(root: Path, approval_path: str) -> list[str]:
    lines = [line for line in _git(root, "status", "--porcelain", "--untracked-files=all").splitlines() if line]
    return [
        line
        for line in lines
        if line != f"?? {approval_path}"
    ]


def _normalized_finding(root: Path, decision: CommittedJson, candidate_id: str) -> tuple[CommittedJson, dict[str, Any]]:
    row = decision.payload
    path = _safe_relative(row.get("normalized_artifact_path"), prefix=NORMALIZED_PREFIX)
    normalized = load_committed_json(
        root,
        commit=decision.commit,
        path=path,
        prefix=NORMALIZED_PREFIX,
        expected_sha256=str(row.get("normalized_artifact_sha256") or ""),
    )
    findings = normalized.payload.get("findings")
    if not isinstance(findings, list):
        raise GazaHistoricalCatchupError("normalized historical artifact has no findings list")
    matches = [item for item in findings if isinstance(item, dict) and item.get("audit_candidate_id") == candidate_id]
    if len(matches) != 1:
        raise GazaHistoricalCatchupError("normalized historical artifact does not contain exactly one bound candidate")
    return normalized, matches[0]


def _authority_is_false(row: dict[str, Any], keys: Sequence[str], label: str) -> None:
    if any(row.get(key) is not False for key in keys):
        raise GazaHistoricalCatchupError(f"{label} unexpectedly carries prior mutation or publication authority")


def _derive_uncertainty(finding: dict[str, Any], assessment: dict[str, Any]) -> str:
    explicit = str(finding.get("uncertainty_note") or finding.get("qualification_note") or "").strip()
    if explicit:
        return _public_text(explicit, "historical uncertainty")
    attributed = _public_text(assessment.get("attributed_to"), "historical attribution")
    mode = str(assessment.get("mode") or "").strip().lower()
    if mode == "allegation":
        return f"The filing and allegation remain attributed to {attributed}; the allegation is not an adjudicated finding."
    if mode == "single_source_report":
        return f"This remains a single-source report attributed to {attributed}."
    return f"The account and any quantities remain attributed to {attributed}."


def _derive_public_copy(
    root: Path,
    review: CommittedJson,
    decision: CommittedJson,
) -> dict[str, Any]:
    r = review.payload
    d = decision.payload
    candidate_id = str(d.get("audit_candidate_id") or "")
    if not candidate_id or candidate_id != r.get("audit_candidate_id"):
        raise GazaHistoricalCatchupError("review and decision candidate identities do not match")
    if r.get("domain") != "gaza" or d.get("domain") != "gaza":
        raise GazaHistoricalCatchupError("historical authority accepts only Gaza artifacts")
    if r.get("decision") != "confirmed" or d.get("decision") != "confirmed":
        raise GazaHistoricalCatchupError("true-miss catch-up accepts only confirmed, non-correction decisions")
    if r.get("resulting_review_state") != "substantively_reviewed" or d.get("resulting_review_state") != "substantively_reviewed":
        raise GazaHistoricalCatchupError("candidate has not completed substantive historical review")
    if not str(r.get("schema_version") or "").startswith("gaza_historical_editorial_review_"):
        raise GazaHistoricalCatchupError("historical review schema is not supported")
    if not str(d.get("schema_version") or "").startswith("gaza_historical_editorial_decision_"):
        raise GazaHistoricalCatchupError("historical decision schema is not supported")
    _authority_is_false(
        r,
        ("current_publication_approval", "archive_mutation_authorized", "edition_authorized", "publication_authorized", "queue_authorized", "source_record_authorized", "cluster_authorized", "audio_authorized"),
        "historical review",
    )
    _authority_is_false(
        d,
        ("publication_approval", "archive_content_change_authorized", "edition_authorized", "publication_authorized", "queue_authorized", "source_record_authorized", "cluster_authorized", "audio_authorized"),
        "historical decision",
    )
    if d.get("review_artifact_path") != review.path or str(d.get("review_artifact_sha256") or "").removeprefix("sha256:") != review.sha256:
        raise GazaHistoricalCatchupError("decision does not bind the exact committed historical review")
    if d.get("candidate_event_fingerprint") != r.get("candidate_event_fingerprint"):
        raise GazaHistoricalCatchupError("review and decision event fingerprints do not match")
    duplicate = d.get("duplicate_and_authoritative_match_check")
    if not isinstance(duplicate, dict) or duplicate.get("candidate_remains_distinct") is not True:
        raise GazaHistoricalCatchupError("candidate is not confirmed as distinct from public history")
    normalized, finding = _normalized_finding(root, decision, candidate_id)
    if finding.get("domain") != "gaza" or finding.get("historical_backfill") is not True:
        raise GazaHistoricalCatchupError("normalized candidate is not a Gaza historical finding")
    assessment = d.get("attribution_assessment")
    if not isinstance(assessment, dict):
        raise GazaHistoricalCatchupError("historical attribution assessment is missing")
    if any(assessment.get(key) is not True for key in ("attribution_preserved", "uncertainty_preserved")):
        raise GazaHistoricalCatchupError("historical review did not preserve attribution and uncertainty")
    if assessment.get("unsupported_certainty_escalation") is not False:
        raise GazaHistoricalCatchupError("historical review contains an unsupported certainty escalation")

    date_assessment = d.get("date_assessment")
    if not isinstance(date_assessment, dict):
        raise GazaHistoricalCatchupError("historical date assessment is missing")
    assessed_period = date_assessment.get("event_period")
    if not isinstance(assessed_period, dict):
        assessed_period = {}
    event_date = str(finding.get("event_date") or date_assessment.get("event_date") or "").strip()
    period_start = str(
        finding.get("event_period_start")
        or date_assessment.get("event_period_start")
        or assessed_period.get("start")
        or ""
    ).strip()
    period_end = str(
        finding.get("event_period_end")
        or date_assessment.get("event_period_end")
        or assessed_period.get("end")
        or ""
    ).strip()
    if event_date:
        date.fromisoformat(event_date)
        period_start = ""
        period_end = ""
    elif period_start and period_end:
        start = date.fromisoformat(period_start)
        end = date.fromisoformat(period_end)
        if start > end:
            raise GazaHistoricalCatchupError("historical event period is reversed")
    else:
        raise GazaHistoricalCatchupError("historical item lacks an exact event date or bounded event period; a date cannot be invented")

    source_published_at = str(finding.get("source_published_at") or date_assessment.get("source_published_at") or "").strip()
    if not source_published_at:
        raise GazaHistoricalCatchupError("historical item has no source publication date")
    try:
        datetime.fromisoformat(source_published_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GazaHistoricalCatchupError("historical source publication date is invalid") from exc

    evidence = d.get("evidence_references")
    if not isinstance(evidence, list) or not evidence:
        raise GazaHistoricalCatchupError("historical decision has no evidence references")
    source_links: list[str] = []
    for index, item in enumerate(evidence):
        if not isinstance(item, dict):
            raise GazaHistoricalCatchupError("historical evidence reference is malformed")
        url = _safe_url(item.get("url"), f"historical source URL {index + 1}")
        if url not in source_links:
            source_links.append(url)
    principal = next((item for item in evidence if item.get("role") == "principal"), evidence[0])
    principal_url = _safe_url(principal.get("url"), "principal historical source URL")
    canonical = _safe_url(finding.get("canonical_source_url") or finding.get("source_url") or principal_url, "canonical historical source URL")
    if principal_url != source_links[0]:
        source_links.remove(principal_url)
        source_links.insert(0, principal_url)

    title = _public_text(finding.get("title"), "historical title")
    summary = _public_text(assessment.get("safe_future_wording"), "historical approved summary")
    attribution = _public_text(assessment.get("attributed_to"), "historical attribution")
    uncertainty = _derive_uncertainty(finding, assessment)
    category = _public_text(d.get("taxonomy_review", {}).get("category") or finding.get("category"), "historical category")
    publisher = _public_text(finding.get("publisher") or attribution, "historical publisher")
    mode = _public_text(assessment.get("mode"), "historical attribution mode")
    copy = {
        "candidate_id": candidate_id,
        "event_fingerprint": str(d.get("candidate_event_fingerprint") or ""),
        "title": title,
        "summary": summary,
        "category": category,
        "event_date": event_date or None,
        "event_period": {"start": period_start, "end": period_end} if period_start else None,
        "source_published_at": source_published_at,
        "publisher": publisher,
        "attribution_mode": mode,
        "attribution": attribution,
        "uncertainty": uncertainty,
        "principal_source_url": principal_url,
        "canonical_source_url": canonical,
        "source_links": source_links,
        "historical_classification": "confirmed_true_miss",
    }
    copy["public_copy_sha256"] = fingerprint(copy)
    return {
        "candidate_id": candidate_id,
        "review_binding": _binding(review),
        "decision_binding": _binding(decision),
        "normalized_binding": _binding(normalized),
        "public_copy": copy,
    }


def legacy_v1_approval_path_for(catchup_id: str) -> str:
    value = str(catchup_id or "").strip()
    if not CATCHUP_RE.fullmatch(value):
        raise GazaHistoricalCatchupError("catch-up ID is malformed")
    return f"{APPROVAL_PREFIX}{value}-approval.json"


def approval_path_for(catchup_id: str, *, schema_version: str = APPROVAL_SCHEMA) -> str:
    value = str(catchup_id or "").strip()
    if not CATCHUP_RE.fullmatch(value):
        raise GazaHistoricalCatchupError("catch-up ID is malformed")
    if schema_version != APPROVAL_SCHEMA:
        raise GazaHistoricalCatchupError("new catch-up approval path requires the current V2 approval schema")
    return f"{APPROVAL_PREFIX}{value}-approval-v2.json"


def _approval_request(path: Path, root: Path, pages: Path) -> tuple[dict[str, Any], bytes]:
    absolute = path.absolute().resolve(strict=True)
    if root in absolute.parents or pages in absolute.parents:
        raise GazaHistoricalCatchupError("approval request must remain private and outside the source and Pages repositories")
    raw = absolute.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GazaHistoricalCatchupError("approval request is not valid UTF-8 JSON") from exc
    expected = {
        "schema_version", "catchup_id", "publication_date", "title", "introduction",
        "retrospective_disclosure", "approved_by", "approved_at", "source_base_commit",
        "pages_head", "public_path", "public_url", "review_bindings", "decision_bindings", "item_order",
        "publication_authorized", "audio_authorized", "social_authorized",
    }
    if not isinstance(payload, dict) or set(payload) != expected or payload.get("schema_version") != APPROVAL_REQUEST_SCHEMA:
        raise GazaHistoricalCatchupError("approval request fields are invalid")
    return payload, raw


def create_approval(root: Path, pages_root: Path, request_path: Path) -> dict[str, Any]:
    root = _root(root)
    pages = pages_root.absolute().resolve(strict=True)
    request, request_raw = _approval_request(request_path, root, pages)
    catchup_id = str(request.get("catchup_id") or "")
    approval_path = approval_path_for(catchup_id)
    public_path = public_path_for(catchup_id)
    public_url = public_url_for(catchup_id)
    if request.get("public_path") != public_path or request.get("public_url") != public_url:
        raise GazaHistoricalCatchupError("approval request does not bind the canonical catch-up path and URL")
    if _source_status_for_approval(root, approval_path):
        raise GazaHistoricalCatchupError("approval creation requires a clean source worktree")
    source_base = _require_commit(root, request.get("source_base_commit"))
    if source_base != _git(root, "rev-parse", "HEAD"):
        raise GazaHistoricalCatchupError("approval must bind the exact clean protected source head")
    pages_head = str(request.get("pages_head") or "").strip().lower()
    if not COMMIT_RE.fullmatch(pages_head):
        raise GazaHistoricalCatchupError("Pages head binding is malformed")
    _clean_pages(pages, pages_head)
    publication_date = date.fromisoformat(str(request.get("publication_date") or ""))
    approved_at, approved_datetime = _timestamp(request.get("approved_at"), "approved_at")
    if publication_date < approved_datetime.date():
        raise GazaHistoricalCatchupError("catch-up publication date must be the real approval date or later")
    if request.get("publication_authorized") is not True:
        raise GazaHistoricalCatchupError("approval must explicitly authorize this exact catch-up publication")
    if request.get("audio_authorized") is not False or request.get("social_authorized") is not False:
        raise GazaHistoricalCatchupError("historical catch-up audio and social publication are not supported")

    reviews_raw = request.get("review_bindings")
    decisions_raw = request.get("decision_bindings")
    item_order = request.get("item_order")
    if not isinstance(reviews_raw, list) or not isinstance(decisions_raw, list) or not isinstance(item_order, list):
        raise GazaHistoricalCatchupError("review, decision, and order inventories must be lists")
    if not 1 <= len(item_order) <= MAX_ITEMS or len(reviews_raw) != len(item_order) or len(decisions_raw) != len(item_order):
        raise GazaHistoricalCatchupError(f"catch-up approval must contain one through {MAX_ITEMS} exact items")
    if len(set(str(value) for value in item_order)) != len(item_order):
        raise GazaHistoricalCatchupError("catch-up item order contains duplicates")

    reviews = [_load_binding(root, item, prefix=REVIEW_PREFIX) for item in reviews_raw]
    decisions = [_load_binding(root, item, prefix=DECISION_PREFIX) for item in decisions_raw]
    review_by_id = {str(item.payload.get("audit_candidate_id") or ""): item for item in reviews}
    decision_by_id = {str(item.payload.get("audit_candidate_id") or ""): item for item in decisions}
    if len(review_by_id) != len(reviews) or len(decision_by_id) != len(decisions):
        raise GazaHistoricalCatchupError("catch-up contains duplicate review or decision identities")
    if set(review_by_id) != set(item_order) or set(decision_by_id) != set(item_order):
        raise GazaHistoricalCatchupError("review and decision bindings do not match the exact ordered candidate set")

    items: list[dict[str, Any]] = []
    reviewer_names: set[str] = set()
    for order, candidate_id in enumerate(item_order, start=1):
        candidate = str(candidate_id)
        derived = _derive_public_copy(root, review_by_id[candidate], decision_by_id[candidate])
        derived["order"] = order
        items.append(derived)
        for artifact in (review_by_id[candidate].payload, decision_by_id[candidate].payload):
            for key in ("operator", "reviewed_by", "reviewer"):
                name = str(artifact.get(key) or "").strip().casefold()
                if name:
                    reviewer_names.add(name)

    approved_by = str(request.get("approved_by") or "").strip()
    lowered_approver = approved_by.casefold()
    if not approved_by or re.search(r"\b(?:codex|bot|automation|automated)\b", lowered_approver):
        raise GazaHistoricalCatchupError("approval requires a real independent human approver")
    if lowered_approver in reviewer_names:
        raise GazaHistoricalCatchupError("approval authority must be independent from every bound editorial reviewer")
    title = _public_text(request.get("title"), "catch-up title")
    introduction = _public_text(request.get("introduction"), "catch-up introduction")
    disclosure = _public_text(request.get("retrospective_disclosure"), "retrospective disclosure")
    lowered_disclosure = disclosure.lower()
    if not all(term in lowered_disclosure for term in ("historical review", "previously missed", "published")):
        raise GazaHistoricalCatchupError("retrospective disclosure must explain that historical review recovered previously missed reporting published later")

    public_copy_set = [
        {"order": item["order"], "candidate_id": item["candidate_id"], "public_copy": item["public_copy"]}
        for item in items
    ]
    approval = {
        "schema_version": APPROVAL_SCHEMA,
        "approval_type": "gaza_historical_true_miss_catchup",
        "catchup_id": catchup_id,
        "publication_date": publication_date.isoformat(),
        "public_path": public_path,
        "public_url": public_url,
        "title": title,
        "introduction": introduction,
        "retrospective_disclosure": disclosure,
        "approved_by": approved_by,
        "approved_at": approved_at,
        "source_base_commit": source_base,
        "pages_head": pages_head,
        "request_raw_sha256": sha256_bytes(request_raw),
        "request_fingerprint": fingerprint(request),
        "item_count": len(items),
        "item_order": list(item_order),
        "approved_items": items,
        "ordered_public_copy_sha256": fingerprint(public_copy_set),
        "publication_authorized": True,
        "pages_authorized": True,
        "private_preview_authorized": True,
        "private_stage_authorized": True,
        "audio_authorized": False,
        "social_authorized": False,
        "scheduler_authorized": False,
        "source_configuration_authorized": False,
        "daily_collection_authorized": False,
        "existing_edition_rewrite_authorized": False,
        "executed": False,
        "published": False,
    }
    approval["approval_fingerprint"] = fingerprint(approval)
    target = root / approval_path
    raw = canonical_json(approval)
    if target.exists():
        if target.read_bytes() != raw:
            raise GazaHistoricalCatchupError("refusing a conflicting historical catch-up approval replay")
        status = "idempotent_noop"
    else:
        _atomic_write(target, raw)
        status = "approval_created"
    return {
        "status": status,
        "ok": True,
        "approval_path": approval_path,
        "approval_fingerprint": approval["approval_fingerprint"],
        "item_count": len(items),
        "persistent_mutation": status == "approval_created",
        "publication_authorized": True,
        "pages_authorized": True,
        "audio_authorized": False,
        "social_authorized": False,
    }


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _approval_only_commit(root: Path, approval_commit: str, approval_path: str) -> None:
    changed = [
        value
        for value in _git(root, "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", approval_commit).splitlines()
        if value
    ]
    if changed != [approval_path]:
        raise GazaHistoricalCatchupError("approval authority must come from an exact one-file approval-only commit")


def _assert_vacant(root: Path, pages: Path, catchup_id: str) -> None:
    public_path = public_path_for(catchup_id).rstrip("/")
    occupied = [
        path
        for path in (
            pages / public_path,
            root / "output" / "site" / public_path,
            root / "output" / "dispatches" / "gaza" / "catchups" / catchup_id,
        )
        if path.exists()
    ]
    state_root = root / STATE_PREFIX
    if state_root.is_dir():
        for path in sorted(state_root.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise GazaHistoricalCatchupError(f"publication-state artifact is unreadable: {path}") from exc
            if not isinstance(payload, dict):
                raise GazaHistoricalCatchupError(f"publication-state artifact is malformed: {path}")
            if payload.get("catchup_id") == catchup_id or payload.get("public_path") == public_path_for(catchup_id):
                occupied.append(path)
    if occupied:
        raise GazaHistoricalCatchupError("approved catch-up public path is already occupied: " + ", ".join(map(str, occupied)))


def _assert_no_public_collision(root: Path, pages: Path, items: Sequence[dict[str, Any]]) -> None:
    candidate_ids = {str(item["candidate_id"]) for item in items}
    event_fingerprints = {str(item["public_copy"]["event_fingerprint"]) for item in items}
    story_ids = {_story_id(str(item["candidate_id"])) for item in items}
    collisions: list[str] = []
    memory_path = root / "data" / "records" / "story_memory.json"
    if memory_path.is_file():
        payload = json.loads(memory_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise GazaHistoricalCatchupError("story memory must be a JSON list")
        for index, row in enumerate(payload):
            if not isinstance(row, dict):
                raise GazaHistoricalCatchupError("story memory contains a malformed row")
            if (
                row.get("historical_candidate_id") in candidate_ids
                or row.get("event_fingerprint") in event_fingerprints
                or row.get("story_id") in story_ids
            ):
                collisions.append(f"{memory_path}#{index}")
    manifests = list((pages / "gaza" / "editions").glob("*/curation_manifest.json"))
    manifests.extend((pages / "gaza" / "catchups").glob("*/curation_manifest.json"))
    for manifest in manifests:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        rows = payload if isinstance(payload, list) else payload.get("stories") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            continue
        if any(
            isinstance(row, dict)
            and (
                row.get("historical_candidate_id") in candidate_ids
                or row.get("event_fingerprint") in event_fingerprints
                or row.get("story_id") in story_ids
            )
            for row in rows
        ):
            collisions.append(str(manifest))
    if collisions:
        raise GazaHistoricalCatchupError("historical finding is already represented publicly: " + ", ".join(sorted(collisions)))


def load_plan(
    root: Path,
    pages_root: Path,
    *,
    approval_commit: str,
    approval_path: str,
    publication_timestamp: str,
) -> CatchupBundle:
    root = _root(root)
    if _git(root, "status", "--porcelain", "--untracked-files=all"):
        raise GazaHistoricalCatchupError("catch-up planning requires a clean source worktree")
    approval_commit = _require_commit(root, approval_commit, strict_ancestor=True)
    approval_path = _safe_relative(approval_path, prefix=APPROVAL_PREFIX)
    _approval_only_commit(root, approval_commit, approval_path)
    approval = load_committed_json(root, commit=approval_commit, path=approval_path, prefix=APPROVAL_PREFIX)
    row = approval.payload
    if row.get("schema_version") == "gaza_historical_catchup_approval_v1":
        raise GazaHistoricalCatchupError(
            "obsolete date-keyed historical catch-up approval; renewed human approval for the canonical catch-up path is required"
        )
    if row.get("schema_version") != APPROVAL_SCHEMA or row.get("approval_type") != "gaza_historical_true_miss_catchup":
        raise GazaHistoricalCatchupError("historical catch-up approval schema is invalid")
    catchup_id = str(row.get("catchup_id") or "")
    if approval_path != approval_path_for(catchup_id, schema_version=str(row.get("schema_version") or "")):
        raise GazaHistoricalCatchupError("V2 approval must use the exact owner-derived versioned approval path")
    identity_source = dict(row)
    stored = identity_source.pop("approval_fingerprint", None)
    if stored != fingerprint(identity_source):
        raise GazaHistoricalCatchupError("historical catch-up approval fingerprint drifted")
    expected_authority = {
        "publication_authorized": True,
        "pages_authorized": True,
        "private_preview_authorized": True,
        "private_stage_authorized": True,
        "audio_authorized": False,
        "social_authorized": False,
        "scheduler_authorized": False,
        "source_configuration_authorized": False,
        "daily_collection_authorized": False,
        "existing_edition_rewrite_authorized": False,
        "executed": False,
        "published": False,
    }
    if any(row.get(key) != value for key, value in expected_authority.items()):
        raise GazaHistoricalCatchupError("historical catch-up approval authority flags are invalid")
    source_base = _require_commit(root, row.get("source_base_commit"))
    source_head = _git(root, "rev-parse", "HEAD")
    if subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", source_base, approval_commit],
        check=False,
        capture_output=True,
    ).returncode != 0:
        raise GazaHistoricalCatchupError("approval commit does not descend from its bound source base")
    incoming = {
        path for path in _git(root, "diff", "--name-only", source_base, source_head).splitlines() if path
    }
    if incoming != {approval_path}:
        raise GazaHistoricalCatchupError("protected source changed beyond the exact approval-only artifact; a new approval is required")
    public_path = public_path_for(catchup_id)
    public_url = public_url_for(catchup_id)
    if row.get("public_path") != public_path or row.get("public_url") != public_url:
        raise GazaHistoricalCatchupError("approval does not bind the canonical catch-up path and URL")
    approval_pages_head = str(row.get("pages_head") or "")
    pages, pages_head = _clean_pages_for_plan(
        pages_root,
        approval_pages_head,
        public_path=public_path,
    )
    publication_date = date.fromisoformat(str(row.get("publication_date") or ""))
    timestamp_text, timestamp = _timestamp(publication_timestamp, "publication timestamp")
    _, approved_at = _timestamp(row.get("approved_at"), "approved_at")
    if timestamp.date() != publication_date:
        raise GazaHistoricalCatchupError("publication timestamp must fall on the approved real publication date")
    if timestamp < approved_at:
        raise GazaHistoricalCatchupError("publication timestamp predates approval")
    _assert_vacant(root, pages, catchup_id)
    stored_items = row.get("approved_items")
    if not isinstance(stored_items, list) or not 1 <= len(stored_items) <= MAX_ITEMS or row.get("item_count") != len(stored_items):
        raise GazaHistoricalCatchupError("approval item inventory is invalid")
    checked: list[dict[str, Any]] = []
    for order, expected in enumerate(stored_items, start=1):
        if not isinstance(expected, dict) or expected.get("order") != order:
            raise GazaHistoricalCatchupError("approval item order is invalid")
        review = _load_binding(root, expected.get("review_binding"), prefix=REVIEW_PREFIX)
        decision = _load_binding(root, expected.get("decision_binding"), prefix=DECISION_PREFIX)
        actual = _derive_public_copy(root, review, decision)
        actual["order"] = order
        if actual != expected:
            raise GazaHistoricalCatchupError("approved review, decision, normalized evidence, or public copy drifted")
        checked.append(actual)
    if [item["candidate_id"] for item in checked] != row.get("item_order"):
        raise GazaHistoricalCatchupError("approved candidate order drifted")
    public_copy_set = [
        {"order": item["order"], "candidate_id": item["candidate_id"], "public_copy": item["public_copy"]}
        for item in checked
    ]
    if fingerprint(public_copy_set) != row.get("ordered_public_copy_sha256"):
        raise GazaHistoricalCatchupError("ordered approved public-copy identity drifted")
    _assert_no_public_collision(root, pages, checked)
    expected = _expected_pages_paths(catchup_id)
    bundle = CatchupBundle(
        approval=approval,
        catchup_id=catchup_id,
        publication_date=publication_date.isoformat(),
        publication_timestamp=timestamp_text,
        title=_public_text(row.get("title"), "catch-up title"),
        introduction=_public_text(row.get("introduction"), "catch-up introduction"),
        disclosure=_public_text(row.get("retrospective_disclosure"), "retrospective disclosure"),
        source_head=source_head,
        pages_head=pages_head,
        approval_pages_head=approval_pages_head,
        public_path=public_path,
        public_url=public_url,
        items=tuple(checked),
        pages_root=pages,
        expected_pages_paths=expected,
    )
    _validate_plan_surfaces(bundle)
    return bundle


def plan_result(bundle: CatchupBundle) -> dict[str, Any]:
    return {
        "schema_version": PLAN_SCHEMA,
        "status": "validated_plan",
        "ok": True,
        "persistent_mutation": False,
        "pages_mutation": False,
        "catchup_id": bundle.catchup_id,
        "publication_date": bundle.publication_date,
        "publication_timestamp": bundle.publication_timestamp,
        "public_path": bundle.public_path,
        "public_url": bundle.public_url,
        "approval_commit": bundle.approval.commit,
        "approval_path": bundle.approval.path,
        "source_head": bundle.source_head,
        "pages_head": bundle.pages_head,
        "approval_pages_head": bundle.approval_pages_head,
        "item_count": len(bundle.items),
        "all_review_decision_bindings_validated": True,
        "all_public_copy_reproduced": True,
        "retrospective_disclosure_validated": True,
        "edition_identity_vacant": True,
        "publication_authorized": True,
        "pages_authorized": True,
        "audio_authorized": False,
        "social_authorized": False,
        "expected_pages_paths": list(bundle.expected_pages_paths),
    }


def _preview_payload(bundle: CatchupBundle) -> dict[str, Any]:
    payload = {
        "schema_version": PREVIEW_SCHEMA,
        "catchup_id": bundle.catchup_id,
        "publication_date": bundle.publication_date,
        "publication_timestamp": bundle.publication_timestamp,
        "public_path": bundle.public_path,
        "public_url": bundle.public_url,
        "title": bundle.title,
        "introduction": bundle.introduction,
        "retrospective_disclosure": bundle.disclosure,
        "approval_commit": bundle.approval.commit,
        "approval_path": bundle.approval.path,
        "approval_sha256": bundle.approval.sha256,
        "source_head": bundle.source_head,
        "pages_head": bundle.pages_head,
        "approval_pages_head": bundle.approval_pages_head,
        "items": [item["public_copy"] for item in bundle.items],
        "publication_executed": False,
        "pages_mutation": False,
        "audio_authorized": False,
        "social_authorized": False,
    }
    payload["preview_fingerprint"] = fingerprint(payload)
    return payload


def _story_date(copy: dict[str, Any]) -> str:
    if copy.get("event_date"):
        return f"Event date: {copy['event_date']}"
    period = copy["event_period"]
    return f"Event period: {period['start']} through {period['end']}"


def _render_edition(bundle: CatchupBundle) -> bytes:
    chunks = [
        "<h1>Dispatches From Gaza</h1>",
        f"<h2>{html.escape(bundle.title)}</h2>",
        f"<p><strong>Historical catch-up:</strong> {html.escape(bundle.disclosure)}</p>",
        f"<p>{html.escape(bundle.introduction)}</p>",
        "<h2>At A Glance</h2><ul>",
    ]
    chunks.extend(f"<li>{html.escape(item['public_copy']['title'])}</li>" for item in bundle.items)
    chunks.append("</ul><h2>Recovered Developments</h2>")
    for item in bundle.items:
        copy = item["public_copy"]
        chunks.extend(
            [
                f"<article><h3>{html.escape(copy['title'])}</h3>",
                f"<p><em>{html.escape(_story_date(copy))}; source published: {html.escape(copy['source_published_at'])}</em></p>",
                f"<p>{html.escape(copy['summary'])}</p>",
                f"<p><strong>Attribution:</strong> {html.escape(copy['attribution'])}</p>",
                f"<p><strong>Uncertainty:</strong> {html.escape(copy['uncertainty'])}</p>",
                "<p><strong>Sources</strong></p><ul>",
            ]
        )
        chunks.extend(
            f'<li><a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">{html.escape(copy["publisher"])}</a></li>'
            for url in copy["source_links"]
        )
        chunks.append("</ul></article>")
    chunks.extend(
        [
            "<h2>Source Note</h2>",
            "<p>Each item was recovered through protected historical review and remains tied to its original source and event date or reporting period.</p>",
            '<p><a href="/gaza/archive.html">Gaza archive</a> | <a href="/">Dispatches home</a></p>',
        ]
    )
    body = f"""{header("Dispatches From Gaza", "../../", "../../archive.html", "/gaza/")}
  <main class="briefing">
    <section class="hero"><img class="hero-logo" src="../../assets/gaza-logo.png" alt="Dispatches From Gaza"></section>
    <p class="eyebrow">Historical catch-up / {html.escape(bundle.publication_date)}</p>
    {' '.join(chunks)}
  </main>
{footer("../../")}"""
    return page(
        f"Dispatches From Gaza - {bundle.publication_date}",
        bundle.public_url,
        "../../assets/site.css",
        body,
        "Dispatches From Gaza",
    ).encode("utf-8")


def _story_id(candidate_id: str) -> str:
    return "gaza-catchup-" + hashlib.sha256(candidate_id.encode("utf-8")).hexdigest()[:20]


def _manifests(bundle: CatchupBundle) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    stories: list[dict[str, Any]] = []
    for item in bundle.items:
        copy = item["public_copy"]
        source_id = "gaza-historical-" + hashlib.sha256(
            (item["candidate_id"] + "\0" + copy["canonical_source_url"]).encode("utf-8")
        ).hexdigest()[:18]
        sources.append(
            {
                "source_record_id": source_id,
                "historical_candidate_id": item["candidate_id"],
                "title": copy["title"],
                "url": copy["principal_source_url"],
                "canonical_url": copy["canonical_source_url"],
                "source_urls": copy["source_links"],
                "publisher": copy["publisher"],
                "published_at": copy["source_published_at"],
                "summary_or_snippet": copy["summary"],
                "category_hint": copy["category"],
                "event_date": copy["event_date"],
                "event_period": copy["event_period"],
                "attribution": copy["attribution"],
                "uncertainty": copy["uncertainty"],
                "event_fingerprint": copy["event_fingerprint"],
                "historical_classification": copy["historical_classification"],
                "public_copy_sha256": copy["public_copy_sha256"],
            }
        )
        stories.append(
            {
                "story_id": _story_id(item["candidate_id"]),
                "historical_candidate_id": item["candidate_id"],
                "event_fingerprint": copy["event_fingerprint"],
                "title": copy["title"],
                "summary": copy["summary"],
                "category": copy["category"],
                "source_record_ids": [source_id],
                "source_urls": copy["source_links"],
                "event_date": copy["event_date"],
                "event_period": copy["event_period"],
                "attribution": copy["attribution"],
                "uncertainty": copy["uncertainty"],
                "included_in_public_summary": True,
                "historical_catchup": True,
                "historical_catchup_id": bundle.catchup_id,
                "public_url": bundle.public_url,
                "approval_sha256": bundle.approval.sha256,
            }
        )
    edition = {
        "schema_version": "gaza_historical_catchup_edition_v2",
        "dispatch_slug": "gaza",
        "briefing_type": "historical_catchup",
        "edition_date": bundle.publication_date,
        "published_at": bundle.publication_timestamp,
        "catchup_id": bundle.catchup_id,
        "public_path": bundle.public_path,
        "public_url": bundle.public_url,
        "edition_title": bundle.title,
        "edition_introduction": bundle.introduction,
        "retrospective_disclosure": bundle.disclosure,
        "source_count": len(sources),
        "story_count": len(stories),
        "source_head": bundle.source_head,
        "pages_pre_publish_head": bundle.pages_head,
        "approval_commit": bundle.approval.commit,
        "approval_path": bundle.approval.path,
        "approval_sha256": bundle.approval.sha256,
        "public_exposed": True,
        "audio_authorized": False,
        "social_authorized": False,
    }
    dedupe = {
        "schema_version": "gaza_historical_catchup_dedupe_v1",
        "catchup_id": bundle.catchup_id,
        "publication_date": bundle.publication_date,
        "input_candidate_count": len(stories),
        "kept_candidate_count": len(stories),
        "suppressed_candidate_count": 0,
        "event_fingerprints": [story["event_fingerprint"] for story in stories],
        "candidate_ids": [story["historical_candidate_id"] for story in stories],
    }
    return edition, sources, stories, dedupe


def _expected_pages_paths(catchup_id: str) -> tuple[str, ...]:
    base = public_path_for(catchup_id).rstrip("/")
    return (
        f"{base}/index.html",
        f"{base}/edition_manifest.json",
        f"{base}/sources_manifest.json",
        f"{base}/curation_manifest.json",
        f"{base}/dedupe_report.json",
        *PUBLIC_ENTRY_NAMES,
    )


def _insert_archive_entry(raw: bytes, bundle: CatchupBundle) -> bytes:
    text = raw.decode("utf-8")
    relative_url = f"catchups/{bundle.catchup_id}/"
    if bundle.public_path in text or relative_url in text:
        raise GazaHistoricalCatchupError("catch-up navigation entry already exists")
    start = text.find('<ul class="edition-list">')
    if start < 0:
        raise GazaHistoricalCatchupError("Gaza navigation has no edition list")
    insertion = text.find("\n", start)
    if insertion < 0:
        raise GazaHistoricalCatchupError("Gaza navigation edition list is malformed")
    label = f"Historical catch-up / {bundle.publication_date} — {bundle.title}"
    row = (
        f'      <li class="historical-catchup"><span class="edition-date">{html.escape(bundle.publication_date)}</span>'
        f'<a href="{html.escape(relative_url, quote=True)}">{html.escape(label)}</a></li>\n'
    )
    return (text[: insertion + 1] + row + text[insertion + 1 :]).encode("utf-8")


def _insert_rss_entry(raw: bytes, bundle: CatchupBundle) -> bytes:
    text = raw.decode("utf-8")
    if bundle.public_url in text:
        raise GazaHistoricalCatchupError("catch-up RSS entry already exists")
    marker = "  <item>"
    offset = text.find(marker)
    if offset < 0:
        closing = "</channel>"
        offset = text.find(closing)
        if offset < 0:
            raise GazaHistoricalCatchupError("Gaza RSS channel is malformed")
    pub_date = format_datetime(
        datetime.fromisoformat(bundle.publication_timestamp.replace("Z", "+00:00")).astimezone(timezone.utc)
    )
    item = (
        "  <item>\n"
        f"    <title>{html.escape(bundle.title)}</title>\n"
        f"    <link>{html.escape(bundle.public_url)}</link>\n"
        f"    <guid>{html.escape(bundle.public_url)}</guid>\n"
        f"    <pubDate>{html.escape(pub_date)}</pubDate>\n"
        f"    <description>{html.escape(bundle.disclosure)}</description>\n"
        "  </item>\n"
    )
    return (text[:offset] + item + text[offset:]).encode("utf-8")


def _render_navigation(bundle: CatchupBundle, edition_manifest: dict[str, Any]) -> tuple[bytes, bytes, bytes]:
    del edition_manifest
    index_path = bundle.pages_root / "gaza/index.html"
    archive_path = bundle.pages_root / "gaza/archive.html"
    rss_path = bundle.pages_root / "gaza/rss.xml"
    if not all(path.is_file() for path in (index_path, archive_path, rss_path)):
        raise GazaHistoricalCatchupError("required Gaza navigation or RSS surface is missing")
    return (
        _insert_archive_entry(index_path.read_bytes(), bundle),
        _insert_archive_entry(archive_path.read_bytes(), bundle),
        _insert_rss_entry(rss_path.read_bytes(), bundle),
    )


def _stage_payload(bundle: CatchupBundle) -> tuple[dict[str, bytes], dict[str, Any]]:
    edition, sources, curation, dedupe = _manifests(bundle)
    index, archive, rss = _render_navigation(bundle, edition)
    base = bundle.public_path.rstrip("/")
    files = {
        f"{base}/index.html": _render_edition(bundle),
        f"{base}/edition_manifest.json": canonical_json(edition),
        f"{base}/sources_manifest.json": canonical_json(sources),
        f"{base}/curation_manifest.json": canonical_json(curation),
        f"{base}/dedupe_report.json": canonical_json(dedupe),
        "gaza/index.html": index,
        "gaza/archive.html": archive,
        "gaza/rss.xml": rss,
    }
    dependencies = []
    for relative in NON_MUTATED_DEPENDENCIES:
        path = bundle.pages_root / relative
        if path.is_file():
            raw = path.read_bytes()
            dependencies.append({"pages_path": relative, "sha256": sha256_bytes(raw), "length": len(raw)})
    entries = [
        {"pages_path": path, "sha256": sha256_bytes(raw), "length": len(raw)}
        for path, raw in sorted(files.items())
    ]
    release = {
        "schema_version": RELEASE_SCHEMA,
        "catchup_id": bundle.catchup_id,
        "publication_date": bundle.publication_date,
        "publication_timestamp": bundle.publication_timestamp,
        "public_path": bundle.public_path,
        "public_url": bundle.public_url,
        "approval_commit": bundle.approval.commit,
        "approval_path": bundle.approval.path,
        "approval_blob_sha1": bundle.approval.blob_sha1,
        "approval_sha256": bundle.approval.sha256,
        "source_head": bundle.source_head,
        "pages_head": bundle.pages_head,
        "approval_pages_head": bundle.approval_pages_head,
        "item_count": len(bundle.items),
        "ordered_public_copy_sha256": bundle.approval.payload["ordered_public_copy_sha256"],
        "entries": entries,
        "non_mutated_dependencies": dependencies,
        "audio_authorized": False,
        "social_authorized": False,
        "podcast_or_flash_mutation": False,
        "publication_executed": False,
    }
    release["release_fingerprint"] = fingerprint(release)
    return files, release


def _external_root(root: Path, pages: Path, destination: Path, label: str) -> Path:
    value = destination.absolute().resolve(strict=False)
    if value == root or root in value.parents or value == pages or pages in value.parents:
        raise GazaHistoricalCatchupError(f"{label} root must remain outside source and Pages repositories")
    return value


def create_private_preview(bundle: CatchupBundle, root: Path, preview_root: Path) -> dict[str, Any]:
    source = _root(root)
    destination = _external_root(source, bundle.pages_root, preview_root, "private preview")
    payload = _preview_payload(bundle)
    identity = str(payload["preview_fingerprint"]).removeprefix("sha256:")[:24]
    target = destination / f"gaza-historical-catchup-preview-{identity}"
    expected = {
        target / "preview.json": canonical_json(payload),
        target / "preview.html": _render_edition(bundle),
    }
    return _create_external_artifact(target, expected, "preview")


def create_stage(bundle: CatchupBundle, root: Path, stage_root: Path) -> dict[str, Any]:
    source = _root(root)
    destination = _external_root(source, bundle.pages_root, stage_root, "private stage")
    files, release = _stage_payload(bundle)
    identity = str(release["release_fingerprint"]).removeprefix("sha256:")[:24]
    target = destination / f"gaza-historical-catchup-stage-{identity}"
    expected = {target / "site" / path: raw for path, raw in files.items()}
    expected[target / "release_manifest.json"] = canonical_json(release)
    result = _create_external_artifact(target, expected, "stage")
    result.update(
        {
            "release_fingerprint": release["release_fingerprint"],
            "release_manifest": str(target / "release_manifest.json"),
            "pages_entry_count": len(files),
        }
    )
    return result


def _create_external_artifact(target: Path, expected: dict[Path, bytes], kind: str) -> dict[str, Any]:
    if target.exists():
        actual = {path for path in target.rglob("*") if path.is_file()}
        if actual != set(expected) or any(path.read_bytes() != raw for path, raw in expected.items()):
            raise GazaHistoricalCatchupError(f"refusing a conflicting or partial private {kind} replay")
        status = "idempotent_noop"
    else:
        target.mkdir(parents=True, exist_ok=False)
        for path, raw in expected.items():
            _atomic_write(path, raw)
        status = f"{kind}_created"
    return {
        "status": status,
        "ok": True,
        f"{kind}_root": str(target),
        "file_count": len(expected),
        "persistent_public_mutation": False,
        "pages_mutation": False,
        "publication_executed": False,
    }


def verify_stage(bundle: CatchupBundle, root: Path, stage_root: Path) -> dict[str, Any]:
    source = _root(root)
    destination = _external_root(source, bundle.pages_root, stage_root, "private stage")
    files, release = _stage_payload(bundle)
    identity = str(release["release_fingerprint"]).removeprefix("sha256:")[:24]
    target = destination / f"gaza-historical-catchup-stage-{identity}"
    expected = {target / "site" / path: raw for path, raw in files.items()}
    expected[target / "release_manifest.json"] = canonical_json(release)
    if not target.is_dir():
        raise GazaHistoricalCatchupError("approved private stage is missing")
    actual = {path for path in target.rglob("*") if path.is_file()}
    if actual != set(expected) or any(path.read_bytes() != raw for path, raw in expected.items()):
        raise GazaHistoricalCatchupError("staged package bytes or approved prose drifted")
    return {
        "status": "stage_verified",
        "ok": True,
        "stage_root": str(target),
        "release_fingerprint": release["release_fingerprint"],
        "file_count": len(expected),
        "persistent_mutation": False,
        "pages_mutation": False,
    }


def _assert_history_preserved(bundle: CatchupBundle, stage_target: Path) -> None:
    staged = {
        relative: (stage_target / "site" / relative).read_bytes()
        for relative in PUBLIC_ENTRY_NAMES
        if (stage_target / "site" / relative).is_file()
    }
    _assert_history_bytes(bundle, staged)


def _assert_history_bytes(bundle: CatchupBundle, staged: dict[str, bytes]) -> None:
    for relative in PUBLIC_ENTRY_NAMES:
        prior_path = bundle.pages_root / relative
        if not prior_path.is_file() or relative not in staged:
            raise GazaHistoricalCatchupError(f"required history surface is missing: {relative}")
        prior_identities = _public_history_identities(prior_path.read_bytes())
        staged_identities = _public_history_identities(staged[relative])
        canonical_identity = f"/gaza/catchups/{bundle.catchup_id}/"
        if not prior_identities <= staged_identities or canonical_identity not in staged_identities:
            raise GazaHistoricalCatchupError(f"history-shrink validation failed for {relative}")


def _validate_plan_surfaces(bundle: CatchupBundle) -> None:
    files, release = _stage_payload(bundle)
    if set(files) != set(bundle.expected_pages_paths):
        raise GazaHistoricalCatchupError("planned Pages inventory does not match the sanctioned catch-up surfaces")
    _assert_history_bytes(bundle, {key: files[key] for key in PUBLIC_ENTRY_NAMES})
    target = bundle.public_path + "index.html"
    if target not in files or bundle.public_url.encode("utf-8") not in files[target]:
        raise GazaHistoricalCatchupError("planned catch-up does not satisfy canonical public listability")
    if release.get("audio_authorized") is not False or release.get("social_authorized") is not False:
        raise GazaHistoricalCatchupError("planned catch-up release gained unsupported side authority")


def _publication_state_path(root: Path, catchup_id: str) -> Path:
    return root / STATE_PREFIX / f"{catchup_id}.json"


def _memory_rows(bundle: CatchupBundle) -> list[dict[str, Any]]:
    rows = []
    for item in bundle.items:
        copy = item["public_copy"]
        rows.append(
            {
                "dispatch_slug": "gaza",
                "edition_date": bundle.publication_date,
                "published_at": bundle.publication_timestamp,
                "story_id": _story_id(item["candidate_id"]),
                "historical_candidate_id": item["candidate_id"],
                "title": copy["title"],
                "normalized_title": " ".join(re.sub(r"[^a-z0-9]+", " ", copy["title"].lower()).split()),
                "summary": copy["summary"],
                "source_urls": list(copy["source_links"]),
                "canonical_urls": [copy["canonical_source_url"]],
                "publisher_names": [copy["publisher"]],
                "source_dates": [copy["source_published_at"]],
                "event_fingerprint": copy["event_fingerprint"],
                "first_seen_date": bundle.publication_date,
                "last_seen_date": bundle.publication_date,
                "update_count": 0,
                "latest_classification": "new",
                "historical_catchup": True,
                "approval_sha256": bundle.approval.sha256,
            }
        )
    return rows


def _record_publication(root: Path, bundle: CatchupBundle, pages_commit: str) -> dict[str, Any]:
    state_path = _publication_state_path(root, bundle.catchup_id)
    memory_path = root / "data" / "records" / "story_memory.json"
    rows = _memory_rows(bundle)
    state = {
        "schema_version": PUBLICATION_STATE_SCHEMA,
        "catchup_id": bundle.catchup_id,
        "publication_date": bundle.publication_date,
        "published_at": bundle.publication_timestamp,
        "public_path": bundle.public_path,
        "public_url": bundle.public_url,
        "approval_commit": bundle.approval.commit,
        "approval_path": bundle.approval.path,
        "approval_sha256": bundle.approval.sha256,
        "source_head": bundle.source_head,
        "pages_pre_publish_head": bundle.pages_head,
        "pages_publish_commit": pages_commit,
        "candidate_ids": [item["candidate_id"] for item in bundle.items],
        "event_fingerprints": [item["public_copy"]["event_fingerprint"] for item in bundle.items],
        "dedupe_recorded": True,
        "published": True,
    }
    state_raw = canonical_json(state)
    memory: list[dict[str, Any]] = []
    if memory_path.is_file():
        payload = json.loads(memory_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
            raise GazaHistoricalCatchupError("story memory must contain a JSON list of objects")
        memory = list(payload)
    existing = [item for item in memory if item.get("approval_sha256") == bundle.approval.sha256]
    if state_path.exists():
        if state_path.read_bytes() != state_raw or existing != rows:
            raise GazaHistoricalCatchupError("conflicting historical catch-up publication-state replay")
        return {"status": "idempotent_noop", "story_memory_rows": len(rows)}
    if existing:
        raise GazaHistoricalCatchupError("story memory contains a partial catch-up publication record")
    memory.extend(rows)
    memory.sort(key=lambda item: (str(item.get("dispatch_slug") or ""), str(item.get("edition_date") or ""), str(item.get("story_id") or "")))
    prior_memory = memory_path.read_bytes() if memory_path.is_file() else None
    try:
        _atomic_write(memory_path, (json.dumps(memory, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))
        _atomic_write(state_path, state_raw)
    except Exception:
        if prior_memory is None:
            if memory_path.exists():
                memory_path.unlink()
        else:
            _atomic_write(memory_path, prior_memory)
        if state_path.exists():
            state_path.unlink()
        raise
    return {"status": "publication_recorded", "story_memory_rows": len(rows)}


def _remote_head(pages: Path) -> str:
    output = _git(pages, "ls-remote", "--heads", "origin", "refs/heads/gh-pages")
    return output.split()[0] if output else ""


def _run_publish_scope_preflight(root: Path, pages: Path, publication_date: str) -> None:
    validator = root / "scripts" / "validate_publish_scope.py"
    if not validator.is_file():
        raise GazaHistoricalCatchupError("repository publish-scope validator is missing")
    result = subprocess.run(
        [
            sys.executable,
            str(validator),
            "--dispatch",
            "gaza",
            "--date",
            publication_date,
            "--source-repo-root",
            str(root),
            "--pages-repo-root",
            str(pages),
            "--allow-pages",
            "--strict",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown validation failure"
        raise GazaHistoricalCatchupError(f"publish-scope validation failed: {detail}")


def published_replay_result(
    root: Path,
    pages_root: Path,
    *,
    approval_commit: str,
    approval_path: str,
) -> dict[str, Any] | None:
    """Return a verified no-op for an already-published approval, otherwise None."""
    root = _root(root)
    approval_commit = _require_commit(root, approval_commit, strict_ancestor=True)
    approval_path = _safe_relative(approval_path, prefix=APPROVAL_PREFIX)
    _approval_only_commit(root, approval_commit, approval_path)
    approval = load_committed_json(root, commit=approval_commit, path=approval_path, prefix=APPROVAL_PREFIX)
    if approval.payload.get("schema_version") != APPROVAL_SCHEMA:
        raise GazaHistoricalCatchupError(
            "obsolete date-keyed historical catch-up approval; renewed human approval for the canonical catch-up path is required"
        )
    catchup_id = str(approval.payload.get("catchup_id") or "")
    if approval_path != approval_path_for(
        catchup_id, schema_version=str(approval.payload.get("schema_version") or "")
    ):
        raise GazaHistoricalCatchupError("V2 approval must use the exact owner-derived versioned approval path")
    public_path = public_path_for(catchup_id)
    public_url = public_url_for(catchup_id)
    if approval.payload.get("public_path") != public_path or approval.payload.get("public_url") != public_url:
        raise GazaHistoricalCatchupError("approval does not bind the canonical catch-up path and URL")
    state_path = _publication_state_path(root, catchup_id)
    if not state_path.is_file():
        return None
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        memory = json.loads((root / "data/records/story_memory.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GazaHistoricalCatchupError("published catch-up replay state is unreadable") from exc
    if not isinstance(state, dict) or not isinstance(memory, list):
        raise GazaHistoricalCatchupError("published catch-up replay state is malformed")
    pages_commit = str(state.get("pages_publish_commit") or "")
    expected_candidates = list(approval.payload.get("item_order") or [])
    expected_fingerprints = [
        item.get("public_copy", {}).get("event_fingerprint")
        for item in approval.payload.get("approved_items") or []
        if isinstance(item, dict)
    ]
    if (
        state.get("schema_version") != PUBLICATION_STATE_SCHEMA
        or state.get("approval_commit") != approval.commit
        or state.get("approval_path") != approval.path
        or state.get("approval_sha256") != approval.sha256
        or state.get("catchup_id") != catchup_id
        or state.get("public_path") != public_path
        or state.get("public_url") != public_url
        or state.get("candidate_ids") != expected_candidates
        or state.get("event_fingerprints") != expected_fingerprints
        or state.get("published") is not True
        or state.get("dedupe_recorded") is not True
        or not COMMIT_RE.fullmatch(pages_commit)
    ):
        raise GazaHistoricalCatchupError("published catch-up replay state conflicts with committed approval")
    matching_memory = [
        row for row in memory
        if isinstance(row, dict) and row.get("approval_sha256") == approval.sha256
    ]
    if len(matching_memory) != len(expected_candidates) or {
        row.get("historical_candidate_id") for row in matching_memory
    } != set(expected_candidates):
        raise GazaHistoricalCatchupError("published catch-up story-memory replay is incomplete or conflicting")
    pages = pages_root.absolute().resolve(strict=True)
    if Path(_git(pages, "rev-parse", "--show-toplevel")).resolve(strict=True) != pages:
        raise GazaHistoricalCatchupError("Pages root must be the exact checkout root")
    if _git(pages, "branch", "--show-current") != "gh-pages":
        raise GazaHistoricalCatchupError("Pages checkout must be on gh-pages")
    if _git(pages, "status", "--porcelain", "--untracked-files=all"):
        raise GazaHistoricalCatchupError("Pages checkout must be clean")
    if _git(pages, "rev-parse", "HEAD") != pages_commit or _remote_head(pages) != pages_commit:
        raise GazaHistoricalCatchupError("published catch-up replay does not match local and remote Pages heads")
    return {
        "status": "idempotent_noop",
        "ok": True,
        "pages_commit": pages_commit,
        "published": True,
        "dedupe_recorded": True,
        "story_memory_rows": len(matching_memory),
        "audio_changed": False,
        "social_changed": False,
    }


def publish_stage(
    root: Path,
    bundle: CatchupBundle,
    stage_root: Path,
    *,
    push: bool,
    live_base_url: str | None = None,
) -> dict[str, Any]:
    root = _root(root)
    if not push:
        raise GazaHistoricalCatchupError("live catch-up publication requires an explicit push authorization flag")
    state_path = _publication_state_path(root, bundle.catchup_id)
    if state_path.is_file():
        replay = published_replay_result(
            root,
            bundle.pages_root,
            approval_commit=bundle.approval.commit,
            approval_path=bundle.approval.path,
        )
        if replay is None:  # pragma: no cover - state existence is checked above
            raise GazaHistoricalCatchupError("existing catch-up publication state could not be verified")
        return replay
    if _git(root, "status", "--porcelain", "--untracked-files=all"):
        raise GazaHistoricalCatchupError("publication requires a clean source worktree")
    _clean_pages(bundle.pages_root, bundle.pages_head)
    verification = verify_stage(bundle, root, stage_root)
    stage_target = Path(verification["stage_root"])
    _assert_history_preserved(bundle, stage_target)
    _run_publish_scope_preflight(root, bundle.pages_root, bundle.publication_date)
    files, release = _stage_payload(bundle)
    backups: dict[Path, bytes | None] = {}
    targets = [bundle.pages_root / relative for relative in files]
    try:
        for relative, raw in files.items():
            target = bundle.pages_root / relative
            backups[target] = target.read_bytes() if target.is_file() else None
            _atomic_write(target, raw)
        _git(bundle.pages_root, "add", "--", *sorted(files))
        staged = set(_git(bundle.pages_root, "diff", "--cached", "--name-only").splitlines())
        if staged != set(files):
            raise GazaHistoricalCatchupError("Pages staged inventory escaped the approved catch-up package")
        _git(bundle.pages_root, "commit", "-m", f"Publish Gaza historical catch-up {bundle.catchup_id}")
    except Exception:
        for target, prior in backups.items():
            if prior is None:
                if target.exists():
                    target.unlink()
                parent = target.parent
                while parent != bundle.pages_root:
                    try:
                        parent.rmdir()
                    except OSError:
                        break
                    parent = parent.parent
            else:
                _atomic_write(target, prior)
        _git(bundle.pages_root, "reset", "--mixed", "HEAD", check=False)
        raise
    pages_commit = _git(bundle.pages_root, "rev-parse", "HEAD")
    push_result = subprocess.run(
        ["git", "-C", str(bundle.pages_root), "push", "origin", "gh-pages"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if push_result.returncode != 0:
        return {
            "status": "push_failed",
            "ok": False,
            "pages_commit": pages_commit,
            "pages_mutated_locally": True,
            "remote_updated": False,
            "error": push_result.stderr.strip() or push_result.stdout.strip(),
        }
    remote = _remote_head(bundle.pages_root)
    if remote != pages_commit:
        raise GazaHistoricalCatchupError("remote gh-pages head does not match the published catch-up commit")
    recorded = _record_publication(root, bundle, pages_commit)
    live_verified = False
    if live_base_url:
        url = live_base_url.rstrip("/") + f"/{bundle.public_path}?v={pages_commit[:12]}"
        try:
            with urllib.request.urlopen(url, timeout=20) as response:
                body = response.read().decode("utf-8")
            if bundle.disclosure not in body:
                raise GazaHistoricalCatchupError("live catch-up verification did not find the approved disclosure")
            live_verified = True
        except Exception as exc:
            return {
                "status": "published_live_verification_failed",
                "ok": False,
                "pages_commit": pages_commit,
                "remote_head": remote,
                "publication_state": recorded["status"],
                "story_memory_rows": recorded["story_memory_rows"],
                "published": True,
                "live_verified": False,
                "error": str(exc),
                "audio_changed": False,
                "social_changed": False,
            }
    return {
        "status": "published",
        "ok": True,
        "pages_commit": pages_commit,
        "remote_head": remote,
        "release_fingerprint": release["release_fingerprint"],
        "pages_entry_count": len(files),
        "live_verified": live_verified,
        "publication_state": recorded["status"],
        "story_memory_rows": recorded["story_memory_rows"],
        "audio_changed": False,
        "social_changed": False,
    }
