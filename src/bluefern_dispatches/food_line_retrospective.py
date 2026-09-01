from __future__ import annotations

import hashlib
import html
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Sequence
from urllib.parse import urlsplit


CORRECTION_SCHEMA = "food_line_retrospective_public_copy_correction_v1"
APPROVAL_REQUEST_SCHEMA = "food_line_retrospective_approval_request_v1"
APPROVAL_SCHEMA = "food_line_retrospective_approval_v1"
PLAN_SCHEMA = "food_line_retrospective_publication_plan_v1"
PREVIEW_SCHEMA = "food_line_retrospective_private_preview_v1"
PUBLICATION_STATE_SCHEMA = "food_line_retrospective_publication_state_v1"
DECISION_SCHEMA = "food_line_historical_event_editorial_decision_v1"
DECISION_PREFIX = "data/agent-history/food-line/reviews/recovery-decisions/"
SUBMISSION_PREFIX = "data/agent-history/food-line/reviews/recovery-submissions/"
CORRECTION_PREFIX = "data/agent-history/food-line/reviews/public-copy-corrections/"
APPROVAL_PREFIX = "approvals/food-line/"
RETROSPECTIVE_BATCH_RE = re.compile(r"food-line-[a-z0-9-]+-retrospective-[0-9]{2}")
EVENT_RE = re.compile(r"food-line-event-[0-9a-f]{24}")
SHA256_RE = re.compile(r"(?:sha256:)?[0-9a-f]{64}")
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
MOJIBAKE_MARKERS = ("Ã", "Â", "â€", "â€™", "â€œ", "â€�", "ðŸ", "ï»¿", "�")
VISIBLE_COPY_FIELDS = ("headline", "summary")
MAX_STORIES = 6


class FoodLineRetrospectiveError(ValueError):
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
class RetrospectiveBundle:
    approval: CommittedJson
    source_head: str
    pages_head: str
    edition_date: str
    publication_timestamp: str
    batch_id: str
    title: str
    introduction: str
    disclosure: str
    source_rows: tuple[dict[str, Any], ...]
    public_copies: tuple[dict[str, Any], ...]
    decision_bindings: tuple[dict[str, Any], ...]
    correction_bindings: tuple[dict[str, Any], ...]
    audio_authorized: bool
    expected_public_paths: tuple[str, ...]


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
        raise FoodLineRetrospectiveError(result.stderr.strip() or result.stdout.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def _root(root: Path) -> Path:
    resolved = root.absolute().resolve(strict=True)
    top = Path(_git(resolved, "rev-parse", "--show-toplevel")).resolve(strict=True)
    if not os.path.samefile(resolved, top):
        raise FoodLineRetrospectiveError("source root must be the exact Git worktree root")
    return resolved


def _safe_relative(raw: str, *, prefix: str, suffix: str = ".json") -> str:
    value = str(raw or "").strip()
    pure = PurePosixPath(value)
    if (
        "\\" in value
        or value.startswith(("/", "./"))
        or re.match(r"^[A-Za-z]:", value)
        or not value.startswith(prefix)
        or not value.endswith(suffix)
        or pure.is_absolute()
        or ".." in pure.parts
    ):
        raise FoodLineRetrospectiveError(f"path must be a repository-relative {prefix}*{suffix} path")
    if value != pure.as_posix() or any(part in {"", "."} for part in pure.parts):
        raise FoodLineRetrospectiveError("path has a non-canonical spelling")
    return value


def _require_commit(root: Path, commit: str, *, strict_ancestor: bool = False) -> str:
    value = str(commit or "").strip().lower()
    if not COMMIT_RE.fullmatch(value):
        raise FoodLineRetrospectiveError("commit must be a full lowercase Git object ID")
    result = subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", value, "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise FoodLineRetrospectiveError("committed authority is not an ancestor of the current source HEAD")
    if strict_ancestor and value == _git(root, "rev-parse", "HEAD"):
        raise FoodLineRetrospectiveError("authority must be consumed only after its normal protected merge")
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
    if not re.fullmatch(r"[0-9a-f]{40}", blob):
        raise FoodLineRetrospectiveError("committed JSON blob identity is malformed")
    if expected_blob_sha1 and blob != str(expected_blob_sha1).lower():
        raise FoodLineRetrospectiveError("committed JSON blob identity does not match")
    raw_result = subprocess.run(
        ["git", "-C", str(root), "cat-file", "blob", blob],
        check=False,
        capture_output=True,
    )
    if raw_result.returncode != 0:
        raise FoodLineRetrospectiveError("unable to load committed JSON bytes")
    raw = raw_result.stdout
    digest = sha256_bytes(raw)
    wanted = str(expected_sha256 or "").removeprefix("sha256:").lower()
    if wanted and digest != wanted:
        raise FoodLineRetrospectiveError("committed JSON SHA-256 does not match")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FoodLineRetrospectiveError("committed artifact is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise FoodLineRetrospectiveError("committed artifact must contain a JSON object")
    return CommittedJson(commit, path, blob, digest, raw, payload)


def validate_public_text(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise FoodLineRetrospectiveError(f"{label} is required")
    marker = next((item for item in MOJIBAKE_MARKERS if item in text), None)
    if marker:
        raise FoodLineRetrospectiveError(f"{label} contains mojibake marker {marker!r}")
    if re.search(r"food-line-event-|sha256:|\|", text, re.I):
        raise FoodLineRetrospectiveError(f"{label} exposes internal identity or machine metadata")
    if any(ord(char) < 32 and char not in "\n\t" for char in text):
        raise FoodLineRetrospectiveError(f"{label} contains a control character")
    return text


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


def _decision_public_copy(decision: dict[str, Any]) -> dict[str, Any]:
    if decision.get("schema_version") != DECISION_SCHEMA or decision.get("decision") != "confirmed":
        raise FoodLineRetrospectiveError("retrospective authority accepts only confirmed migrated-event decisions")
    if decision.get("publication_approval") is not False or decision.get("publication_authorized") is not False:
        raise FoodLineRetrospectiveError("review decision unexpectedly carries publication authority")
    copy = decision.get("publication_copy")
    if not isinstance(copy, dict) or set(copy) != {"headline", "summary", "source_links"}:
        raise FoodLineRetrospectiveError("decision publication copy is malformed")
    links = copy.get("source_links")
    if not isinstance(links, list) or not links:
        raise FoodLineRetrospectiveError("decision publication copy has no source links")
    for link in links:
        parsed = urlsplit(str(link))
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise FoodLineRetrospectiveError("decision publication copy has an unsafe source link")
    return {"headline": str(copy["headline"]), "summary": str(copy["summary"]), "source_links": list(links)}


def correction_path_for(event_id: str) -> str:
    if not EVENT_RE.fullmatch(str(event_id or "")):
        raise FoodLineRetrospectiveError("event ID is malformed")
    return f"{CORRECTION_PREFIX}{event_id}.json"


def create_public_copy_correction(
    root: Path,
    *,
    decision_commit: str,
    decision_path: str,
    decision_blob_sha1: str,
    decision_sha256: str,
    event_id: str,
    field: str,
    prior_text: str,
    replacement_text: str,
    reason: str,
    corrected_by: str,
    corrected_at: str,
) -> dict[str, Any]:
    root = _root(root)
    if field != "publication_copy.summary":
        raise FoodLineRetrospectiveError("only publication_copy.summary may receive a public-copy correction")
    decision = load_committed_json(
        root,
        commit=decision_commit,
        path=decision_path,
        prefix=DECISION_PREFIX,
        expected_blob_sha1=decision_blob_sha1,
        expected_sha256=decision_sha256,
    )
    payload = decision.payload
    if payload.get("event_id") != event_id:
        raise FoodLineRetrospectiveError("correction event ID does not match the protected decision")
    copy = _decision_public_copy(payload)
    prior = str(prior_text)
    replacement = str(replacement_text)
    if not prior or not replacement or prior == replacement:
        raise FoodLineRetrospectiveError("correction requires distinct prior and replacement text")
    if copy["summary"].count(prior) != 1:
        raise FoodLineRetrospectiveError("prior text must occur exactly once in the protected summary")
    corrected_summary = copy["summary"].replace(prior, replacement, 1)
    if prior in corrected_summary or corrected_summary.count(replacement) != 1:
        raise FoodLineRetrospectiveError("correction was missing, duplicated, or already applied")
    validate_public_text(replacement, "replacement text")
    validate_public_text(corrected_summary, "corrected public summary")
    try:
        parsed_at = datetime.fromisoformat(str(corrected_at).replace("Z", "+00:00"))
    except ValueError as exc:
        raise FoodLineRetrospectiveError("corrected_at must be an ISO-8601 timestamp") from exc
    if parsed_at.tzinfo is None:
        raise FoodLineRetrospectiveError("corrected_at must include a timezone")
    if "encoding" not in str(reason).lower() and "mojibake" not in str(reason).lower():
        raise FoodLineRetrospectiveError("correction reason must identify the encoding/mojibake defect")
    overlay = {
        "schema_version": CORRECTION_SCHEMA,
        "correction_type": "encoding_mojibake_public_copy_overlay",
        "decision_commit": decision.commit,
        "decision_path": decision.path,
        "decision_blob_sha1": decision.blob_sha1,
        "decision_sha256": decision.sha256,
        "event_id": event_id,
        "field": field,
        "prior_text": prior,
        "prior_utf8_hex": prior.encode("utf-8").hex(),
        "prior_sha256": sha256_bytes(prior.encode("utf-8")),
        "replacement_text": replacement,
        "replacement_utf8_hex": replacement.encode("utf-8").hex(),
        "replacement_sha256": sha256_bytes(replacement.encode("utf-8")),
        "corrected_public_copy_sha256": fingerprint({**copy, "summary": corrected_summary}),
        "reason": str(reason).strip(),
        "corrected_by": str(corrected_by).strip(),
        "corrected_at": str(corrected_at).strip(),
        "original_decision_immutable": True,
        "approval_authorized": False,
        "generation_authorized": False,
        "publication_authorized": False,
        "pages_authorized": False,
    }
    if not overlay["corrected_by"]:
        raise FoodLineRetrospectiveError("corrected_by is required")
    overlay["correction_identity"] = fingerprint(overlay)
    relative = correction_path_for(event_id)
    target = root / relative
    raw = canonical_json(overlay)
    if target.exists():
        if target.read_bytes() != raw:
            raise FoodLineRetrospectiveError("refusing a conflicting public-copy correction replay")
        status = "idempotent_noop"
    else:
        _atomic_write(target, raw)
        status = "correction_created"
    return {
        "status": status,
        "correction_path": relative,
        "correction_identity": overlay["correction_identity"],
        "persistent_mutation": status == "correction_created",
        "approval_authorized": False,
        "generation_authorized": False,
        "publication_authorized": False,
        "pages_authorized": False,
    }


def _apply_overlay(decision: CommittedJson, overlay: CommittedJson | None) -> tuple[dict[str, Any], dict[str, Any] | None]:
    copy = _decision_public_copy(decision.payload)
    has_mojibake = any(marker in f"{copy['headline']} {copy['summary']}" for marker in MOJIBAKE_MARKERS)
    if overlay is None:
        if has_mojibake:
            raise FoodLineRetrospectiveError("uncorrected protected decision cannot be approved or generated")
        for key in VISIBLE_COPY_FIELDS:
            copy[key] = validate_public_text(copy[key], f"decision {key}")
        return copy, None
    row = overlay.payload
    expected_fields = {
        "schema_version", "correction_type", "decision_commit", "decision_path", "decision_blob_sha1",
        "decision_sha256", "event_id", "field", "prior_text", "prior_utf8_hex", "prior_sha256",
        "replacement_text", "replacement_utf8_hex", "replacement_sha256", "corrected_public_copy_sha256",
        "reason", "corrected_by", "corrected_at", "original_decision_immutable", "approval_authorized",
        "generation_authorized", "publication_authorized", "pages_authorized", "correction_identity",
    }
    if set(row) != expected_fields or row.get("schema_version") != CORRECTION_SCHEMA:
        raise FoodLineRetrospectiveError("correction overlay fields are invalid")
    exact = {
        "decision_commit": decision.commit,
        "decision_path": decision.path,
        "decision_blob_sha1": decision.blob_sha1,
        "decision_sha256": decision.sha256,
        "event_id": decision.payload.get("event_id"),
        "field": "publication_copy.summary",
    }
    if any(row.get(key) != value for key, value in exact.items()):
        raise FoodLineRetrospectiveError("correction overlay does not bind the exact protected decision")
    if row.get("original_decision_immutable") is not True or any(
        row.get(key) is not False
        for key in ("approval_authorized", "generation_authorized", "publication_authorized", "pages_authorized")
    ):
        raise FoodLineRetrospectiveError("correction overlay carries unauthorized authority")
    identity_source = dict(row)
    stored_identity = identity_source.pop("correction_identity")
    if stored_identity != fingerprint(identity_source):
        raise FoodLineRetrospectiveError("correction overlay identity drifted")
    prior = str(row.get("prior_text") or "")
    replacement = str(row.get("replacement_text") or "")
    if prior.encode("utf-8").hex() != row.get("prior_utf8_hex") or sha256_bytes(prior.encode("utf-8")) != row.get("prior_sha256"):
        raise FoodLineRetrospectiveError("correction prior-byte binding drifted")
    if replacement.encode("utf-8").hex() != row.get("replacement_utf8_hex") or sha256_bytes(replacement.encode("utf-8")) != row.get("replacement_sha256"):
        raise FoodLineRetrospectiveError("correction replacement-byte binding drifted")
    if copy["summary"].count(prior) != 1:
        raise FoodLineRetrospectiveError("protected prior text is missing, duplicated, changed, or already corrected")
    corrected = {**copy, "summary": copy["summary"].replace(prior, replacement, 1)}
    if fingerprint(corrected) != row.get("corrected_public_copy_sha256"):
        raise FoodLineRetrospectiveError("correction changes more than the bound defective substring")
    for key in VISIBLE_COPY_FIELDS:
        corrected[key] = validate_public_text(corrected[key], f"corrected {key}")
    return corrected, {
        "commit": overlay.commit,
        "path": overlay.path,
        "blob_sha1": overlay.blob_sha1,
        "sha256": overlay.sha256,
        "correction_identity": row["correction_identity"],
    }


def approval_path_for(batch_id: str) -> str:
    if not RETROSPECTIVE_BATCH_RE.fullmatch(str(batch_id or "")):
        raise FoodLineRetrospectiveError("retrospective batch ID is malformed")
    return f"{APPROVAL_PREFIX}{batch_id}-approval.json"


def _iso_timestamp(value: Any, label: str) -> tuple[str, datetime]:
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FoodLineRetrospectiveError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise FoodLineRetrospectiveError(f"{label} must include a timezone")
    return text, parsed.astimezone(timezone.utc)


def _load_binding(root: Path, row: Any, *, prefix: str) -> CommittedJson:
    if not isinstance(row, dict) or set(row) != {"commit", "path", "blob_sha1", "sha256"}:
        raise FoodLineRetrospectiveError("committed binding fields are invalid")
    return load_committed_json(
        root,
        commit=row["commit"],
        path=row["path"],
        prefix=prefix,
        expected_blob_sha1=row["blob_sha1"],
        expected_sha256=row["sha256"],
    )


def _source_row(decision: dict[str, Any], copy: dict[str, Any]) -> dict[str, Any]:
    evidence = decision.get("evidence_references")
    if not isinstance(evidence, list) or not evidence:
        raise FoodLineRetrospectiveError("confirmed decision has no evidence references")
    principal = next((row for row in evidence if isinstance(row, dict) and row.get("role") == "principal"), evidence[0])
    url = str(principal.get("canonical_source_url") or copy["source_links"][0]).strip()
    parsed_url = urlsplit(url)
    if parsed_url.scheme != "https" or not parsed_url.netloc or parsed_url.username or parsed_url.password:
        raise FoodLineRetrospectiveError("principal retrospective source URL is unsafe")
    publisher = str(principal.get("publisher") or "Source").strip()
    passages = principal.get("exact_supporting_passages") or []
    passage = str(passages[0] if passages else "").strip()
    assessment = decision.get("event_assessment") if isinstance(decision.get("event_assessment"), dict) else {}
    source_id = "food_line_source_" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    event_id = str(decision.get("event_id") or "")
    story_id = "food-line-retrospective-" + hashlib.sha256(event_id.encode("utf-8")).hexdigest()[:20]
    publisher = validate_public_text(publisher, "source publisher")
    passage = validate_public_text(passage, "supporting passage")
    location = validate_public_text(assessment.get("location") or "United States", "source location")
    affected_groups = [validate_public_text(value, "affected group") for value in assessment.get("affected_population") or []]
    return {
        "story_id": story_id,
        "source_record_id": source_id,
        "title": copy["headline"],
        "summary": copy["summary"],
        "url": url,
        "canonical_source_url": url,
        "publisher": publisher,
        "publisher_names": [publisher],
        "source_urls": [url],
        "canonical_urls": [url],
        "source_links": list(copy["source_links"]),
        "published_at": str(principal.get("source_published_at") or "").strip(),
        "source_published_date": str(principal.get("source_published_at") or "").strip(),
        "summary_or_snippet": copy["summary"],
        "pressure_summary": copy["summary"],
        "approved_public_summary": copy["summary"],
        "evidence_text": passage,
        "exact_supporting_passage": passage,
        "evidence_text_basis": "protected_historical_editorial_review",
        "evidence_level": "source-backed historical review",
        "confidence": "high",
        "pressure_signal": True,
        "pressure_verification_status": "source_text_verified",
        "pressure_type": "service_reduction",
        "pressure_reason": "protected retrospective decision documents a demonstrated food-access condition",
        "pressure_match_terms": ["food access"],
        "source_role": "local_signal",
        "source_family": "historical_recovery",
        "source_type": "approved_retrospective",
        "source_purpose": "historical_retrospective",
        "collector_source_type": "protected_review",
        "location_name": location,
        "state": "",
        "location_scope": "state_local",
        "affected_groups": affected_groups,
        "supported_product_geography": True,
        "source_public_story_eligible": True,
        "freshness_status": "approved_historical_retrospective",
        "source_freshness_status": "approved_historical_retrospective",
        "source_freshness_date_basis": "source_published_at",
        "freshness_role": "historical_retrospective",
        "primary_eligible": True,
        "qualifies_for_public_inclusion": True,
        "public_inclusion_reason": "",
        "public_inclusion_bucket": "included_as_lead",
        "included": True,
        "included_as_lead": True,
        "review_status": "approved",
        "traceability_status": "traceable",
        "public_claim_eligible": True,
        "public_claim_blockers": [],
        "map_eligible": False,
    }


def _bound_decision(root: Path, decision: CommittedJson, copy: dict[str, Any]) -> dict[str, Any]:
    row = decision.payload
    event_id = str(row.get("event_id") or "")
    if not EVENT_RE.fullmatch(event_id):
        raise FoodLineRetrospectiveError("decision event identity is malformed")
    review_path = _safe_relative(str(row.get("review_artifact_path") or ""), prefix=SUBMISSION_PREFIX)
    submission = load_committed_json(root, commit=decision.commit, path=review_path, prefix=SUBMISSION_PREFIX)
    expected_review_sha = str(row.get("review_artifact_sha256") or "").removeprefix("sha256:")
    if expected_review_sha != submission.sha256:
        raise FoodLineRetrospectiveError("decision submission SHA-256 binding drifted")
    source_links = list(copy["source_links"])
    evidence = row.get("evidence_references")
    return {
        "event_id": event_id,
        "event_fingerprint": row.get("event_fingerprint"),
        "decision_commit": decision.commit,
        "decision_path": decision.path,
        "decision_blob_sha1": decision.blob_sha1,
        "decision_sha256": decision.sha256,
        "submission_path": submission.path,
        "submission_blob_sha1": submission.blob_sha1,
        "submission_sha256": submission.sha256,
        "recovery_identity_sha256": row.get("recovery_identity_sha256"),
        "recovery_artifact_set_sha256": row.get("recovery_artifact_set_sha256"),
        "batch_id": row.get("recommended_batch", {}).get("batch_id"),
        "batch_order": row.get("recommended_batch", {}).get("order"),
        "source_links_sha256": fingerprint(source_links),
        "evidence_sha256": fingerprint(evidence),
        "public_copy_sha256": fingerprint(copy),
        "public_copy": copy,
    }


def create_retrospective_approval(root: Path, request_path: Path) -> dict[str, Any]:
    root = _root(root)
    request_absolute = request_path.absolute().resolve(strict=True)
    if root in request_absolute.parents:
        raise FoodLineRetrospectiveError("approval request must remain private and outside the repository")
    try:
        request = json.loads(request_absolute.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FoodLineRetrospectiveError("approval request is not valid UTF-8 JSON") from exc
    expected_request_fields = {
        "schema_version", "batch_id", "edition_date", "edition_title", "edition_introduction",
        "retrospective_disclosure", "approved_by", "approved_at", "source_base_commit", "pages_head",
        "decision_bindings", "correction_bindings", "audio_authorized", "publication_authorized",
    }
    if not isinstance(request, dict) or set(request) != expected_request_fields or request.get("schema_version") != APPROVAL_REQUEST_SCHEMA:
        raise FoodLineRetrospectiveError("approval request fields are invalid")
    batch_id = str(request.get("batch_id") or "")
    approval_path = approval_path_for(batch_id)
    edition_date = date.fromisoformat(str(request.get("edition_date") or ""))
    if edition_date > datetime.now().astimezone().date():
        raise FoodLineRetrospectiveError("future retrospective edition dates are forbidden")
    approved_at, _ = _iso_timestamp(request.get("approved_at"), "approved_at")
    source_base = _require_commit(root, str(request.get("source_base_commit") or ""))
    current_head = _git(root, "rev-parse", "HEAD")
    if source_base != current_head:
        raise FoodLineRetrospectiveError("approval must be prepared from the exact clean protected source base")
    status_lines = [line for line in _git(root, "status", "--porcelain", "--untracked-files=all").splitlines() if line]
    unexpected_status = [
        line
        for line in status_lines
        if not (
            line.startswith("?? approvals/food-line/")
            and line.endswith("-approval.json")
            and ".." not in line
        )
    ]
    if unexpected_status:
        raise FoodLineRetrospectiveError("approval creation requires a clean source worktree")
    decisions_raw = request.get("decision_bindings")
    corrections_raw = request.get("correction_bindings")
    if not isinstance(decisions_raw, list) or not 1 <= len(decisions_raw) <= MAX_STORIES:
        raise FoodLineRetrospectiveError("retrospective approval must contain one through six decisions")
    if not isinstance(corrections_raw, list):
        raise FoodLineRetrospectiveError("correction_bindings must be a list")
    correction_by_event: dict[str, CommittedJson] = {}
    for item in corrections_raw:
        artifact = _load_binding(root, item, prefix=CORRECTION_PREFIX)
        event_id = str(artifact.payload.get("event_id") or "")
        if event_id in correction_by_event:
            raise FoodLineRetrospectiveError("duplicate correction binding")
        correction_by_event[event_id] = artifact
    bound: list[dict[str, Any]] = []
    correction_bindings: list[dict[str, Any]] = []
    reviewers: set[str] = set()
    for item in decisions_raw:
        decision = _load_binding(root, item, prefix=DECISION_PREFIX)
        if str(decision.payload.get("recommended_batch", {}).get("batch_id") or "") != batch_id:
            raise FoodLineRetrospectiveError("decision belongs to a different retrospective batch")
        event_id = str(decision.payload.get("event_id") or "")
        copy, overlay_binding = _apply_overlay(decision, correction_by_event.pop(event_id, None))
        bound.append(_bound_decision(root, decision, copy))
        if overlay_binding:
            correction_bindings.append(overlay_binding)
        reviewers.update(
            str(decision.payload.get(key) or "").strip().casefold()
            for key in ("operator", "reviewed_by")
            if str(decision.payload.get(key) or "").strip()
        )
    if correction_by_event:
        raise FoodLineRetrospectiveError("correction binding is unrelated to this approved batch")
    orders = [row["batch_order"] for row in bound]
    if sorted(orders) != list(range(1, len(bound) + 1)) or len(set(row["event_id"] for row in bound)) != len(bound):
        raise FoodLineRetrospectiveError("batch order must be unique and contiguous within the six-story cap")
    bound.sort(key=lambda row: row["batch_order"])
    identities = {(row["recovery_identity_sha256"], row["recovery_artifact_set_sha256"]) for row in bound}
    if len(identities) != 1:
        raise FoodLineRetrospectiveError("approval mixes recovery identities")
    approved_by = str(request.get("approved_by") or "").strip()
    if not approved_by or approved_by.casefold() in reviewers:
        raise FoodLineRetrospectiveError("approval authority must be independent from the editorial decision author")
    title = validate_public_text(request.get("edition_title"), "edition title")
    introduction = validate_public_text(request.get("edition_introduction"), "edition introduction")
    disclosure = validate_public_text(request.get("retrospective_disclosure"), "retrospective disclosure")
    disclosure_lower = disclosure.lower()
    if "retrospective" not in disclosure_lower or "august 2026" not in disclosure_lower or "previously missed" not in disclosure_lower:
        raise FoodLineRetrospectiveError("retrospective disclosure must identify previously missed August 2026 reporting")
    if request.get("publication_authorized") is not True:
        raise FoodLineRetrospectiveError("approval request must explicitly authorize only this batch for publication")
    if request.get("audio_authorized") is not False:
        raise FoodLineRetrospectiveError(
            "retrospective audio is optional under the existing Food Line rule and is not authorized by this owner"
        )
    recovery_identity, artifact_set = next(iter(identities))
    public_copy_set = [{"event_id": row["event_id"], "order": row["batch_order"], "copy": row["public_copy"]} for row in bound]
    approval = {
        "schema_version": APPROVAL_SCHEMA,
        "approval_type": "migrated_event_retrospective_batch",
        "batch_id": batch_id,
        "edition_date": edition_date.isoformat(),
        "edition_title": title,
        "edition_introduction": introduction,
        "retrospective_disclosure": disclosure,
        "approved_by": approved_by,
        "approved_at": approved_at,
        "source_base_commit": source_base,
        "pages_head": str(request.get("pages_head") or "").strip(),
        "recovery_identity_sha256": recovery_identity,
        "recovery_artifact_set_sha256": artifact_set,
        "story_count": len(bound),
        "decision_bindings": [{key: value for key, value in row.items() if key != "public_copy"} for row in bound],
        "correction_bindings": correction_bindings,
        "ordered_public_copy_sha256": fingerprint(public_copy_set),
        "ordered_rendered_copy_sha256": fingerprint([row["public_copy"] for row in bound]),
        "public_representation_scope": [
            "edition_html", "source_table_html", "claim_ledger_html", "sources_manifest",
            "curation_manifest", "edition_manifest", "food_line_homepage", "food_line_archive", "food_line_rss",
        ],
        "generation_authorized": True,
        "publication_authorized": True,
        "pages_authorized": True,
        "social_authorized": False,
        "scheduled_task_change_authorized": False,
        "daily_collection_authorized": False,
        "source_configuration_change_authorized": False,
        "audio_authorized": False,
        "audio_policy": "existing_optional_audio_explicitly_not_authorized",
        "executed": False,
        "published": False,
    }
    if not COMMIT_RE.fullmatch(approval["pages_head"]):
        raise FoodLineRetrospectiveError("Pages head binding is malformed")
    approval["approval_fingerprint"] = fingerprint(approval)
    target = root / approval_path
    raw = canonical_json(approval)
    if target.exists():
        if target.read_bytes() != raw:
            raise FoodLineRetrospectiveError("refusing a conflicting retrospective approval replay")
        status = "idempotent_noop"
    else:
        _atomic_write(target, raw)
        status = "approval_created"
    return {
        "status": status,
        "approval_path": approval_path,
        "approval_fingerprint": approval["approval_fingerprint"],
        "story_count": len(bound),
        "persistent_mutation": status == "approval_created",
        "generation_authorized": True,
        "publication_authorized": True,
        "pages_authorized": True,
        "social_authorized": False,
    }


def _approval_only_commit(root: Path, approval_commit: str, approval_path: str) -> None:
    changed = [line for line in _git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", approval_commit).splitlines() if line]
    if approval_path not in changed or any(
        not path.startswith(APPROVAL_PREFIX) or not path.endswith("-approval.json") for path in changed
    ):
        raise FoodLineRetrospectiveError("approval authority must come from an exact approval-only commit")


def _clean_pages(pages_root: Path, expected_head: str) -> str:
    pages = pages_root.absolute().resolve(strict=True)
    top = Path(_git(pages, "rev-parse", "--show-toplevel")).resolve(strict=True)
    if not os.path.samefile(pages, top):
        raise FoodLineRetrospectiveError("Pages root must be the exact checkout root")
    if _git(pages, "status", "--porcelain", "--untracked-files=all"):
        raise FoodLineRetrospectiveError("Pages checkout must be clean")
    head = _git(pages, "rev-parse", "HEAD")
    if head != expected_head:
        raise FoodLineRetrospectiveError("Pages checkout drifted from the approval binding")
    if _git(pages, "branch", "--show-current") != "gh-pages":
        raise FoodLineRetrospectiveError("Pages checkout must be on gh-pages")
    return head


def _vacancy_paths(root: Path, pages: Path, edition_date: str) -> list[Path]:
    return [
        root / "output" / "site" / "food-line" / "editions" / edition_date,
        root / "output" / "dispatches" / "food-line" / "editions" / edition_date,
        root / "data" / "dispatches" / "food-line" / "editions" / edition_date,
        root / "data" / "dispatches" / "food-line" / "publication-state" / f"{edition_date}.json",
        root / "data" / "dispatches" / "food-line" / "review" / "releases" / f"{edition_date}.json",
        root / "data" / "dispatches" / "food-line" / "review" / "proposed-editions" / f"{edition_date}.json",
        root / "data" / "dispatches" / "food-line" / "review" / "proposed-editions" / f"{edition_date}.md",
        root / "output" / "review" / "food-line" / edition_date,
        pages / "food-line" / "editions" / edition_date,
    ]


def assert_edition_vacant(root: Path, pages: Path, edition_date: str) -> None:
    occupied = [str(path) for path in _vacancy_paths(root, pages, edition_date) if path.exists()]
    needles = (
        f"/food-line/editions/{edition_date}/",
        f"food-line/editions/{edition_date}",
        f'"edition_date": "{edition_date}"',
        f'"edition_date":"{edition_date}"',
    )
    state_files = [
        root / "output" / "site" / "food-line" / "index.html",
        root / "output" / "site" / "food-line" / "archive.html",
        root / "output" / "site" / "food-line" / "rss.xml",
        root / "output" / "site" / "food-line" / "podcast.xml",
        root / "data" / "records" / "story_memory.json",
        root / "data" / "dispatches" / "food-line" / "review" / "current-signal-review.json",
        pages / "food-line" / "index.html",
        pages / "food-line" / "archive.html",
        pages / "food-line" / "rss.xml",
        pages / "food-line" / "podcast.xml",
    ]
    for path in state_files:
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="strict")
            if any(needle in text for needle in needles):
                occupied.append(str(path))
    if occupied:
        raise FoodLineRetrospectiveError("retrospective edition identity is already occupied: " + ", ".join(sorted(set(occupied))))


def _read_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FoodLineRetrospectiveError(f"unable to validate publication/dedupe state: {path}") from exc


def assert_no_dedupe_collision(
    root: Path,
    pages: Path,
    source_rows: Sequence[dict[str, Any]],
    decision_bindings: Sequence[dict[str, Any]],
) -> None:
    urls = {str(row.get("canonical_source_url") or "").strip() for row in source_rows}
    story_ids = {str(row.get("story_id") or "").strip() for row in source_rows}
    event_fingerprints = {str(row.get("event_fingerprint") or "").strip() for row in decision_bindings}
    collisions: list[str] = []
    memory_path = root / "data" / "records" / "story_memory.json"
    if memory_path.exists():
        memory = _read_json_file(memory_path)
        if not isinstance(memory, list):
            raise FoodLineRetrospectiveError("story memory must be a JSON list")
        for index, row in enumerate(memory):
            if not isinstance(row, dict):
                raise FoodLineRetrospectiveError("story memory contains a malformed row")
            row_urls = set(str(value) for value in (row.get("canonical_urls") or [])) | set(
                str(value) for value in (row.get("source_urls") or [])
            )
            if (
                str(row.get("story_id") or "") in story_ids
                or str(row.get("event_fingerprint") or "") in event_fingerprints
                or bool(urls & row_urls)
            ):
                collisions.append(f"{memory_path}#{index}")
    manifest_roots = (
        root / "output" / "site" / "food-line" / "editions",
        root / "output" / "dispatches" / "food-line" / "editions",
        pages / "food-line" / "editions",
    )
    for manifest_root in manifest_roots:
        if not manifest_root.exists():
            continue
        for path in manifest_root.glob("*/sources_manifest.json"):
            payload = _read_json_file(path)
            rows = payload if isinstance(payload, list) else payload.get("sources") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                raise FoodLineRetrospectiveError(f"source manifest has an invalid shape: {path}")
            for row in rows:
                if not isinstance(row, dict):
                    continue
                row_urls = {
                    str(row.get("canonical_source_url") or "").strip(),
                    str(row.get("canonical_url") or "").strip(),
                    str(row.get("url") or "").strip(),
                }
                if urls & row_urls:
                    collisions.append(str(path))
                    break
    if collisions:
        raise FoodLineRetrospectiveError(
            "retrospective candidate already occupies publication or dedupe state: " + ", ".join(sorted(set(collisions)))
        )


def _expected_paths(edition_date: str, *, audio: bool) -> tuple[str, ...]:
    base = f"output/site/food-line/editions/{edition_date}"
    paths = [
        f"{base}/index.html", f"{base}/source_table.html", f"{base}/claim_ledger.html",
        f"{base}/sources_manifest.json", f"{base}/curation_manifest.json", f"{base}/edition_manifest.json",
        "output/site/food-line/index.html", "output/site/food-line/archive.html", "output/site/food-line/rss.xml",
    ]
    if audio:
        paths.extend(
            [
                f"output/site/food-line/audio/{edition_date}.mp3",
                f"output/site/food-line/audio/{edition_date}.json",
                f"output/site/food-line/audio/{edition_date}-transcript.html",
                "output/site/food-line/audio/index.html",
                "output/site/food-line/audio/podcast.xml",
            ]
        )
    return tuple(paths)


def load_retrospective_plan(
    root: Path,
    pages_root: Path,
    *,
    approval_commit: str,
    approval_path: str,
    publication_timestamp: str,
) -> RetrospectiveBundle:
    root = _root(root)
    if _git(root, "status", "--porcelain", "--untracked-files=all"):
        raise FoodLineRetrospectiveError("retrospective authority requires a clean source worktree")
    approval_commit = _require_commit(root, approval_commit, strict_ancestor=True)
    approval_path = _safe_relative(approval_path, prefix=APPROVAL_PREFIX)
    _approval_only_commit(root, approval_commit, approval_path)
    approval = load_committed_json(root, commit=approval_commit, path=approval_path, prefix=APPROVAL_PREFIX)
    row = approval.payload
    if row.get("schema_version") != APPROVAL_SCHEMA or row.get("approval_type") != "migrated_event_retrospective_batch":
        raise FoodLineRetrospectiveError("retrospective approval schema is invalid")
    identity_source = dict(row)
    stored_fingerprint = identity_source.pop("approval_fingerprint", None)
    if stored_fingerprint != fingerprint(identity_source):
        raise FoodLineRetrospectiveError("retrospective approval fingerprint drifted")
    authority = {
        "generation_authorized": True,
        "publication_authorized": True,
        "pages_authorized": True,
        "social_authorized": False,
        "scheduled_task_change_authorized": False,
        "daily_collection_authorized": False,
        "source_configuration_change_authorized": False,
        "audio_authorized": False,
        "audio_policy": "existing_optional_audio_explicitly_not_authorized",
        "executed": False,
        "published": False,
    }
    if any(row.get(key) != value for key, value in authority.items()):
        raise FoodLineRetrospectiveError("retrospective approval authority flags are invalid")
    edition = date.fromisoformat(str(row.get("edition_date") or ""))
    timestamp_text, timestamp = _iso_timestamp(publication_timestamp, "publication timestamp")
    if edition > datetime.now().astimezone().date():
        raise FoodLineRetrospectiveError("future retrospective edition dates are forbidden")
    if timestamp.date() <= edition:
        raise FoodLineRetrospectiveError("publication timestamp must reflect the later real execution time")
    _, approved_at = _iso_timestamp(row.get("approved_at"), "approved_at")
    if timestamp < approved_at:
        raise FoodLineRetrospectiveError("publication timestamp predates approval")
    pages_head = _clean_pages(pages_root, str(row.get("pages_head") or ""))
    assert_edition_vacant(root, pages_root.resolve(), edition.isoformat())
    decisions = row.get("decision_bindings")
    corrections = row.get("correction_bindings")
    if not isinstance(decisions, list) or not 1 <= len(decisions) <= MAX_STORIES or row.get("story_count") != len(decisions):
        raise FoodLineRetrospectiveError("approval story inventory is invalid")
    if not isinstance(corrections, list):
        raise FoodLineRetrospectiveError("approval correction inventory is invalid")
    correction_by_event: dict[str, CommittedJson] = {}
    for binding in corrections:
        artifact = load_committed_json(
            root,
            commit=binding["commit"], path=binding["path"], prefix=CORRECTION_PREFIX,
            expected_blob_sha1=binding["blob_sha1"], expected_sha256=binding["sha256"],
        )
        correction_by_event[str(artifact.payload.get("event_id") or "")] = artifact
    public_copies: list[dict[str, Any]] = []
    source_rows: list[dict[str, Any]] = []
    checked_bindings: list[dict[str, Any]] = []
    for expected in sorted(decisions, key=lambda item: item["batch_order"]):
        decision = load_committed_json(
            root,
            commit=expected["decision_commit"], path=expected["decision_path"], prefix=DECISION_PREFIX,
            expected_blob_sha1=expected["decision_blob_sha1"], expected_sha256=expected["decision_sha256"],
        )
        copy, overlay = _apply_overlay(decision, correction_by_event.pop(expected["event_id"], None))
        actual = _bound_decision(root, decision, copy)
        without_copy = {key: value for key, value in actual.items() if key != "public_copy"}
        if without_copy != expected:
            raise FoodLineRetrospectiveError("approval decision, evidence, ordering, or public-copy binding drifted")
        public_copies.append(copy)
        source_rows.append(_source_row(decision.payload, copy))
        checked_bindings.append(expected)
    if correction_by_event:
        raise FoodLineRetrospectiveError("approval contains an unused correction binding")
    public_copy_set = [
        {"event_id": binding["event_id"], "order": binding["batch_order"], "copy": copy}
        for binding, copy in zip(checked_bindings, public_copies)
    ]
    if fingerprint(public_copy_set) != row.get("ordered_public_copy_sha256"):
        raise FoodLineRetrospectiveError("ordered public-copy identity drifted")
    if fingerprint(public_copies) != row.get("ordered_rendered_copy_sha256"):
        raise FoodLineRetrospectiveError("ordered rendered-copy identity drifted")
    assert_no_dedupe_collision(root, pages_root.resolve(), source_rows, checked_bindings)
    disclosure = validate_public_text(row.get("retrospective_disclosure"), "retrospective disclosure")
    if not all(term in disclosure.lower() for term in ("retrospective", "august 2026", "previously missed")):
        raise FoodLineRetrospectiveError("retrospective disclosure is missing required reader-facing context")
    source_head = _git(root, "rev-parse", "HEAD")
    source_base = str(row.get("source_base_commit") or "")
    _require_commit(root, source_base)
    protected_files = [
        "src/bluefern_dispatches/food_line_retrospective.py",
        "src/bluefern_dispatches/food_line_approved_proposal.py",
        "src/bluefern_dispatches/generator.py",
        "scripts/run_food_line_dispatch.py",
        "scripts/manage_food_line_retrospective.py",
        "scripts/validate_publish_scope.py",
        "src/bluefern_dispatches/pages_release_safety.py",
    ]
    for path in protected_files:
        if _git(root, "diff", "--name-only", source_base, source_head, "--", path):
            raise FoodLineRetrospectiveError("publication owner changed after approval; new approval is required")
    expected_paths = _expected_paths(edition.isoformat(), audio=bool(row.get("audio_authorized")))
    return RetrospectiveBundle(
        approval=approval,
        source_head=source_head,
        pages_head=pages_head,
        edition_date=edition.isoformat(),
        publication_timestamp=timestamp_text,
        batch_id=str(row["batch_id"]),
        title=validate_public_text(row.get("edition_title"), "edition title"),
        introduction=validate_public_text(row.get("edition_introduction"), "edition introduction"),
        disclosure=disclosure,
        source_rows=tuple(source_rows),
        public_copies=tuple(public_copies),
        decision_bindings=tuple(checked_bindings),
        correction_bindings=tuple(corrections),
        audio_authorized=bool(row.get("audio_authorized")),
        expected_public_paths=expected_paths,
    )


def plan_result(bundle: RetrospectiveBundle) -> dict[str, Any]:
    return {
        "schema_version": PLAN_SCHEMA,
        "status": "validated_plan",
        "ok": True,
        "persistent_mutation": False,
        "pages_mutation": False,
        "batch_id": bundle.batch_id,
        "edition_date": bundle.edition_date,
        "publication_timestamp": bundle.publication_timestamp,
        "source_head": bundle.source_head,
        "pages_head": bundle.pages_head,
        "approval_commit": bundle.approval.commit,
        "approval_path": bundle.approval.path,
        "approval_sha256": bundle.approval.sha256,
        "story_count": len(bundle.source_rows),
        "all_public_copy_validated": True,
        "retrospective_disclosure_validated": True,
        "edition_identity_vacant": True,
        "generation_authorized": True,
        "publication_authorized": True,
        "pages_authorized": True,
        "audio_authorized": bundle.audio_authorized,
        "social_authorized": False,
        "expected_public_paths": list(bundle.expected_public_paths),
    }


def _preview_payload(bundle: RetrospectiveBundle) -> dict[str, Any]:
    payload = {
        "schema_version": PREVIEW_SCHEMA,
        "batch_id": bundle.batch_id,
        "edition_date": bundle.edition_date,
        "publication_timestamp": bundle.publication_timestamp,
        "approval_commit": bundle.approval.commit,
        "approval_path": bundle.approval.path,
        "approval_sha256": bundle.approval.sha256,
        "source_head": bundle.source_head,
        "pages_head": bundle.pages_head,
        "title": bundle.title,
        "introduction": bundle.introduction,
        "retrospective_disclosure": bundle.disclosure,
        "stories": [
            {
                "order": index,
                "headline": copy["headline"],
                "summary": copy["summary"],
                "source_links": list(copy["source_links"]),
                "publisher": source["publisher"],
                "source_published_at": source["published_at"],
            }
            for index, (copy, source) in enumerate(zip(bundle.public_copies, bundle.source_rows), start=1)
        ],
        "expected_public_paths": list(bundle.expected_public_paths),
        "persistent_public_mutation": False,
        "publication_executed": False,
    }
    payload["preview_fingerprint"] = fingerprint(payload)
    return payload


def _preview_html(payload: dict[str, Any]) -> bytes:
    stories = "".join(
        "<article>"
        f"<h2>{html.escape(str(row['headline']))}</h2>"
        f"<p>{html.escape(str(row['summary']))}</p>"
        f"<p>Source: <a href=\"{html.escape(str(row['source_links'][0]), quote=True)}\">"
        f"{html.escape(str(row['publisher']))}</a></p>"
        "</article>"
        for row in payload["stories"]
    )
    rendered = (
        "<!doctype html><html lang=\"en\"><meta charset=\"utf-8\">"
        f"<title>{html.escape(str(payload['title']))}</title><body>"
        f"<h1>{html.escape(str(payload['title']))}</h1>"
        f"<p>{html.escape(str(payload['retrospective_disclosure']))}</p>"
        f"<p>{html.escape(str(payload['introduction']))}</p>{stories}</body></html>\n"
    )
    validate_public_text(rendered, "private preview HTML")
    return rendered.encode("utf-8")


def create_private_preview(bundle: RetrospectiveBundle, root: Path, preview_root: Path) -> dict[str, Any]:
    source_root = _root(root)
    destination_root = preview_root.absolute().resolve(strict=False)
    if destination_root == source_root or source_root in destination_root.parents:
        raise FoodLineRetrospectiveError("private preview root must remain outside the source repository")
    payload = _preview_payload(bundle)
    identity = str(payload["preview_fingerprint"]).removeprefix("sha256:")
    target = destination_root / f"food-line-retrospective-{identity}"
    json_path = target / "preview.json"
    html_path = target / "preview.html"
    expected = {
        json_path: canonical_json(payload),
        html_path: _preview_html(payload),
    }
    if target.exists():
        actual_files = {path for path in target.rglob("*") if path.is_file()}
        if actual_files != set(expected) or any(path.read_bytes() != raw for path, raw in expected.items()):
            raise FoodLineRetrospectiveError("refusing a conflicting or partial private preview replay")
        status = "idempotent_noop"
    else:
        target.mkdir(parents=True, exist_ok=False)
        for path, raw in expected.items():
            _atomic_write(path, raw)
        status = "preview_created"
    return {
        "status": status,
        "ok": True,
        "preview_root": str(target),
        "preview_fingerprint": payload["preview_fingerprint"],
        "file_count": len(expected),
        "persistent_public_mutation": False,
        "pages_mutation": False,
        "publication_executed": False,
    }


def verify_private_preview(bundle: RetrospectiveBundle, root: Path, preview_root: Path) -> dict[str, Any]:
    expected = _preview_payload(bundle)
    identity = str(expected["preview_fingerprint"]).removeprefix("sha256:")
    target = preview_root.absolute().resolve(strict=True) / f"food-line-retrospective-{identity}"
    json_path = target / "preview.json"
    html_path = target / "preview.html"
    if json_path.read_bytes() != canonical_json(expected) or html_path.read_bytes() != _preview_html(expected):
        raise FoodLineRetrospectiveError("private retrospective preview bytes drifted")
    if {path for path in target.rglob("*") if path.is_file()} != {json_path, html_path}:
        raise FoodLineRetrospectiveError("private retrospective preview inventory drifted")
    return {
        "status": "preview_verified",
        "ok": True,
        "preview_root": str(target),
        "preview_fingerprint": expected["preview_fingerprint"],
        "file_count": 2,
        "persistent_mutation": False,
        "pages_mutation": False,
    }


def publication_state(bundle: RetrospectiveBundle, *, pages_commit: str) -> dict[str, Any]:
    if not COMMIT_RE.fullmatch(str(pages_commit or "")):
        raise FoodLineRetrospectiveError("published Pages commit is malformed")
    event_fingerprints = [str(row.get("event_fingerprint") or "") for row in bundle.decision_bindings]
    return {
        "schema_version": PUBLICATION_STATE_SCHEMA,
        "batch_id": bundle.batch_id,
        "edition_date": bundle.edition_date,
        "published_at": bundle.publication_timestamp,
        "approval_commit": bundle.approval.commit,
        "approval_path": bundle.approval.path,
        "approval_sha256": bundle.approval.sha256,
        "source_commit": bundle.source_head,
        "pages_pre_publish_commit": bundle.pages_head,
        "pages_publish_commit": pages_commit,
        "event_fingerprints": event_fingerprints,
        "canonical_source_urls": [str(row.get("canonical_source_url") or "") for row in bundle.source_rows],
        "dedupe_recorded": True,
        "published": True,
    }


def _retrospective_memory_rows(bundle: RetrospectiveBundle) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source, binding in zip(bundle.source_rows, bundle.decision_bindings):
        rows.append(
            {
                "dispatch_slug": "food-line",
                "edition_date": bundle.edition_date,
                "published_at": bundle.publication_timestamp,
                "story_id": source["story_id"],
                "title": source["title"],
                "normalized_title": " ".join(re.sub(r"[^a-z0-9]+", " ", source["title"].lower()).split()),
                "summary": source["summary"],
                "source_urls": list(source["source_urls"]),
                "canonical_urls": list(source["canonical_urls"]),
                "publisher_names": list(source["publisher_names"]),
                "source_dates": [source["published_at"]],
                "event_fingerprint": binding["event_fingerprint"],
                "first_seen_date": bundle.edition_date,
                "last_seen_date": bundle.edition_date,
                "update_count": 0,
                "latest_classification": "new",
                "retrospective": True,
                "approval_sha256": bundle.approval.sha256,
            }
        )
    return rows


def record_retrospective_publication(
    root: Path,
    pages_root: Path,
    bundle: RetrospectiveBundle,
    *,
    pages_commit: str,
    live_check_ok: bool,
) -> dict[str, Any]:
    root = _root(root)
    pages = pages_root.absolute().resolve(strict=True)
    if not live_check_ok:
        raise FoodLineRetrospectiveError("publication state requires successful post-push live verification")
    if not COMMIT_RE.fullmatch(str(pages_commit or "")):
        raise FoodLineRetrospectiveError("published Pages commit is malformed")
    if _git(pages, "status", "--porcelain", "--untracked-files=all"):
        raise FoodLineRetrospectiveError("Pages checkout drifted before publication-state recording")
    if _git(pages, "branch", "--show-current") != "gh-pages" or _git(pages, "rev-parse", "HEAD") != pages_commit:
        raise FoodLineRetrospectiveError("Pages publication commit is not the clean current gh-pages head")
    if pages_commit == bundle.pages_head:
        raise FoodLineRetrospectiveError("Pages publication did not create the approved retrospective commit")
    verify_complete_output(root, bundle)
    for relative in bundle.expected_public_paths:
        source = root / relative
        pages_relative = relative.removeprefix("output/site/")
        target = pages / pages_relative
        if not target.is_file() or target.read_bytes() != source.read_bytes():
            raise FoodLineRetrospectiveError(f"published Pages surface is missing or drifted: {pages_relative}")
    state_path = root / "data" / "dispatches" / "food-line" / "publication-state" / f"{bundle.edition_date}.json"
    memory_path = root / "data" / "records" / "story_memory.json"
    state = publication_state(bundle, pages_commit=pages_commit)
    state_raw = canonical_json(state)
    new_rows = _retrospective_memory_rows(bundle)
    memory: list[dict[str, Any]] = []
    if memory_path.exists():
        existing = _read_json_file(memory_path)
        if not isinstance(existing, list) or any(not isinstance(row, dict) for row in existing):
            raise FoodLineRetrospectiveError("story memory must contain a JSON list of objects")
        memory = list(existing)
    exact_existing = [row for row in memory if row.get("approval_sha256") == bundle.approval.sha256]
    if state_path.exists():
        exact_existing.sort(key=lambda row: str(row.get("story_id") or ""))
        expected_existing = sorted(new_rows, key=lambda row: str(row.get("story_id") or ""))
        if state_path.read_bytes() != state_raw or exact_existing != expected_existing:
            raise FoodLineRetrospectiveError("conflicting retrospective publication-state replay")
        return {
            "status": "idempotent_noop",
            "ok": True,
            "publication_state_path": str(state_path),
            "story_memory_path": str(memory_path),
            "story_memory_rows": len(new_rows),
            "pages_commit": pages_commit,
        }
    if exact_existing:
        raise FoodLineRetrospectiveError("story memory contains a partial retrospective publication record")
    candidate_ids = {row["story_id"] for row in new_rows}
    candidate_urls = {url for row in new_rows for url in row["canonical_urls"]}
    candidate_events = {row["event_fingerprint"] for row in new_rows}
    for row in memory:
        if (
            row.get("story_id") in candidate_ids
            or row.get("event_fingerprint") in candidate_events
            or candidate_urls & set(row.get("canonical_urls") or [])
            or candidate_urls & set(row.get("source_urls") or [])
        ):
            raise FoodLineRetrospectiveError("intervening story-memory collision blocks retrospective publication recording")
    memory.extend(new_rows)
    memory.sort(key=lambda row: (str(row.get("dispatch_slug") or ""), str(row.get("edition_date") or ""), str(row.get("story_id") or "")))
    memory_raw = (json.dumps(memory, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    _atomic_write(memory_path, memory_raw)
    _atomic_write(state_path, state_raw)
    return {
        "status": "publication_recorded",
        "ok": True,
        "publication_state_path": str(state_path),
        "publication_state_sha256": sha256_bytes(state_raw),
        "story_memory_path": str(memory_path),
        "story_memory_rows": len(new_rows),
        "pages_commit": pages_commit,
        "dedupe_recorded": True,
    }


def verify_complete_output(root: Path, bundle: RetrospectiveBundle) -> dict[str, Any]:
    missing = [path for path in bundle.expected_public_paths if not (root / path).is_file()]
    expected_edition = {
        path
        for path in bundle.expected_public_paths
        if f"/editions/{bundle.edition_date}/" in path
    }
    edition_root = root / "output" / "site" / "food-line" / "editions" / bundle.edition_date
    actual_edition = {
        path.relative_to(root).as_posix()
        for path in edition_root.rglob("*")
        if path.is_file()
    } if edition_root.is_dir() else set()
    unexpected = sorted(actual_edition - expected_edition)
    if not bundle.audio_authorized:
        audio_root = root / "output" / "site" / "food-line" / "audio"
        unexpected.extend(
            path.relative_to(root).as_posix()
            for path in (
                audio_root / f"{bundle.edition_date}.mp3",
                audio_root / f"{bundle.edition_date}.json",
                audio_root / f"{bundle.edition_date}-transcript.html",
            )
            if path.exists()
        )
    unexpected_mojibake: list[str] = []
    for relative in bundle.expected_public_paths:
        path = root / relative
        if not path.is_file() or path.suffix.lower() not in {".html", ".xml", ".json"}:
            continue
        text = path.read_text(encoding="utf-8")
        if any(marker in text for marker in MOJIBAKE_MARKERS):
            unexpected_mojibake.append(relative)
    if missing or unexpected or unexpected_mojibake:
        raise FoodLineRetrospectiveError(
            "retrospective output is incomplete or unsafe: "
            + json.dumps({"missing": missing, "unexpected": unexpected, "mojibake": unexpected_mojibake}, sort_keys=True)
        )
    edition_manifest = root / "output" / "site" / "food-line" / "editions" / bundle.edition_date / "edition_manifest.json"
    payload = json.loads(edition_manifest.read_text(encoding="utf-8"))
    if payload.get("edition_date") != bundle.edition_date or payload.get("published_at") != bundle.publication_timestamp:
        raise FoodLineRetrospectiveError("edition date and actual publication timestamp are not kept separate")
    if payload.get("retrospective_disclosure") != bundle.disclosure:
        raise FoodLineRetrospectiveError("rendered retrospective disclosure drifted")
    return {
        "status": "preview_verified",
        "ok": True,
        "path_count": len(bundle.expected_public_paths),
        "expected_public_paths": list(bundle.expected_public_paths),
        "mojibake_free": True,
        "edition_date_separate_from_publication_timestamp": True,
    }
