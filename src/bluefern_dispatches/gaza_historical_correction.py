"""Approval-gated packaging for formal Gaza historical corrections.

This module does not publish, mutate Pages, create approvals, or reuse the daily
historical publisher.  It validates a complete, independently approved set of
replacement representations and can atomically stage that set outside both Git
repositories for a later, separately controlled release step.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from .historical_agent_archive import validate_gaza_published_story_lineage


PROPOSAL_SCHEMA = "gaza_formal_historical_correction_proposal_v1"
APPROVAL_SCHEMA = "gaza_formal_historical_correction_release_approval_v1"
PACKAGE_SCHEMA = "gaza_formal_historical_correction_package_v1"

# The full claim-bearing public representation set.  source_quality_report is
# intentionally not here: it is a collection diagnostic and does not expose the
# public claim.  sources_manifest is protected as an unchanged dependency while
# the new correction manifest records the correcting evidence.
REPLACEMENT_PATHS = {
    "edition_html": "gaza/editions/{date}/index.html",
    "curation_manifest": "gaza/editions/{date}/curation_manifest.json",
    "dedupe_report": "gaza/editions/{date}/dedupe_report.json",
    "edition_manifest": "gaza/editions/{date}/edition_manifest.json",
    "rss": "gaza/rss.xml",
    "original_audio_metadata": "gaza/audio/{date}.json",
    "original_transcript": "gaza/audio/{date}-transcript.html",
    "podcast": "gaza/podcast.xml",
    "audio_podcast": "gaza/audio/podcast.xml",
    "flash_briefing": "gaza/flash-briefing.json",
    "audio_index": "gaza/audio/index.html",
    "gaza_index": "gaza/index.html",
    "gaza_archive": "gaza/archive.html",
    "root_index": "index.html",
    "correction_page": "gaza/corrections/{correction_id}/index.html",
    "correction_manifest": "gaza/corrections/{correction_id}/correction.json",
    "correction_audio_metadata": "gaza/audio/corrections/{correction_id}.json",
    "correction_transcript": "gaza/audio/corrections/{correction_id}-transcript.html",
    "correction_audio": "gaza/audio/corrections/{correction_id}.mp3",
}

UNCHANGED_PATHS = {
    "sources_manifest": "gaza/editions/{date}/sources_manifest.json",
    "source_quality_report": "gaza/editions/{date}/source_quality_report.md",
    "original_audio": "gaza/audio/{date}.mp3",
}

NEW_ROLES = {
    "correction_page",
    "correction_manifest",
    "correction_audio_metadata",
    "correction_transcript",
    "correction_audio",
}


class CorrectionValidationError(ValueError):
    """The proposed correction failed a closed validation boundary."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def fingerprint_payload(value: dict[str, Any], field: str) -> str:
    return "sha256:" + sha256_bytes(_canonical_bytes({k: v for k, v in value.items() if k != field}))


def correction_identity(
    story_id: str,
    event_fingerprint: str,
    prior_fingerprint: str,
    corrected_fingerprint: str,
) -> str:
    payload = {
        "schema": PACKAGE_SCHEMA,
        "story_id": story_id,
        "event_fingerprint": event_fingerprint,
        "prior_fingerprint": prior_fingerprint,
        "corrected_fingerprint": corrected_fingerprint,
    }
    return "gaza-correction-v1-" + sha256_bytes(_canonical_bytes(payload))[:20]


def _load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CorrectionValidationError(f"{label} is not readable canonical JSON: {exc}") from exc


def _exact_fields(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise CorrectionValidationError(f"{label} fields are incomplete or unsupported")
    return value


def _repo_path(root: Path, relative: str, label: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise CorrectionValidationError(f"{label} escapes its repository") from exc
    return candidate


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise CorrectionValidationError(f"Git validation failed ({' '.join(args)}): {detail}")
    return result.stdout.strip()


def _require_sha(value: Any, label: str, *, prefix: bool = False) -> str:
    text = str(value or "")
    pattern = r"sha256:[0-9a-f]{64}" if prefix else r"[0-9a-f]{64}"
    if not re.fullmatch(pattern, text):
        raise CorrectionValidationError(f"{label} is not an exact SHA-256")
    return text


def _validate_proposal_shape(proposal: Any) -> dict[str, Any]:
    proposal = _exact_fields(
        proposal,
        {
            "schema_version",
            "operation",
            "domain",
            "source_commit",
            "correction",
            "private_evidence",
            "pages_state",
            "proposal_sha256",
        },
        "correction proposal",
    )
    if proposal["schema_version"] != PROPOSAL_SCHEMA:
        raise CorrectionValidationError("correction proposal schema is unsupported")
    if proposal["operation"] != "formal_historical_correction" or proposal["domain"] != "gaza":
        raise CorrectionValidationError("correction proposal is cross-domain or not a formal correction")
    if proposal["proposal_sha256"] != fingerprint_payload(proposal, "proposal_sha256"):
        raise CorrectionValidationError("correction proposal fingerprint differs")
    if not re.fullmatch(r"[0-9a-f]{40}", str(proposal["source_commit"])):
        raise CorrectionValidationError("correction proposal source commit is invalid")
    return proposal


def _validate_correction(proposal: dict[str, Any]) -> dict[str, Any]:
    correction = _exact_fields(
        proposal["correction"],
        {
            "correction_id",
            "story_id",
            "owning_edition_date",
            "correction_date",
            "stable_event_fingerprint",
            "prior_claim_fingerprint",
            "corrected_claim_fingerprint",
            "prior_claim",
            "corrected_claim",
            "change_reason",
            "source_attribution",
            "evidence_references",
            "casualty_change",
            "injury_disagreement",
        },
        "correction identity",
    )
    story_id = str(correction["story_id"])
    edition_date = str(correction["owning_edition_date"])
    correction_date = str(correction["correction_date"])
    if not re.fullmatch(r"gaza-story-20\d{2}-\d{2}-\d{2}-\d{3}", story_id):
        raise CorrectionValidationError("correction story ID is invalid")
    if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", edition_date) or not re.fullmatch(
        r"20\d{2}-\d{2}-\d{2}", correction_date
    ):
        raise CorrectionValidationError("correction dates are invalid")
    for field in ("stable_event_fingerprint", "corrected_claim_fingerprint"):
        _require_sha(correction[field], f"correction {field}", prefix=True)
    if not re.fullmatch(r"topic_fingerprint_v1:[0-9a-f]{16}", str(correction["prior_claim_fingerprint"])):
        raise CorrectionValidationError("prior claim fingerprint is invalid")
    for field in ("prior_claim", "corrected_claim", "change_reason"):
        if not isinstance(correction[field], str) or not correction[field].strip():
            raise CorrectionValidationError(f"correction {field} is required")
    if not isinstance(correction["source_attribution"], str) or not correction["source_attribution"].strip():
        raise CorrectionValidationError("correction source attribution is required")
    evidence_references = correction["evidence_references"]
    if not isinstance(evidence_references, list) or len(evidence_references) != 2:
        raise CorrectionValidationError("correction requires both reviewed evidence references")
    if any(
        not isinstance(item, dict)
        or set(item) != {"role", "url", "supporting_passage"}
        or not str(item["role"]).strip()
        or not str(item["url"]).startswith("https://")
        or not str(item["supporting_passage"]).strip()
        for item in evidence_references
    ):
        raise CorrectionValidationError("correction evidence references are invalid")
    casualty = _exact_fields(
        correction["casualty_change"],
        {"field", "previous_value", "corrected_value", "operation"},
        "casualty correction",
    )
    if casualty != {
        "field": "casualty_counts.new_deaths",
        "previous_value": 1,
        "corrected_value": 2,
        "operation": "replace",
    }:
        raise CorrectionValidationError("casualty correction must replace one death with two, not add two")
    injury = _exact_fields(
        correction["injury_disagreement"],
        {"unresolved", "reports"},
        "injury disagreement",
    )
    reports = injury.get("reports")
    if injury.get("unresolved") is not True or not isinstance(reports, list) or len(reports) != 2:
        raise CorrectionValidationError("the two-source injury disagreement must remain unresolved")
    values = {item.get("value") for item in reports if isinstance(item, dict)}
    urls = {str(item.get("source_url") or "") for item in reports if isinstance(item, dict)}
    if values != {1, 2} or len(urls) != 2 or any(not url.startswith("https://") for url in urls):
        raise CorrectionValidationError("injury disagreement must retain both attributed values")
    expected_id = correction_identity(
        story_id,
        correction["stable_event_fingerprint"],
        correction["prior_claim_fingerprint"],
        correction["corrected_claim_fingerprint"],
    )
    if correction["correction_id"] != expected_id:
        raise CorrectionValidationError("correction identity is not deterministic")
    return correction


def _validate_private_evidence(
    source_root: Path, proposal: dict[str, Any], correction: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    evidence = _exact_fields(
        proposal["private_evidence"],
        {
            "lineage_path",
            "lineage_sha256",
            "review_path",
            "review_sha256",
            "decision_audit_path",
            "decision_audit_sha256",
            "raw_sha256",
            "normalized_artifact_sha256",
            "report_artifact_sha256",
        },
        "private evidence",
    )
    paths: dict[str, Path] = {}
    for key in ("lineage", "review", "decision_audit"):
        relative = evidence[f"{key}_path"]
        if not isinstance(relative, str) or not relative:
            raise CorrectionValidationError(f"private {key} path is required")
        paths[key] = _repo_path(source_root, relative, f"private {key}")
        if sha256_file(paths[key]) != _require_sha(evidence[f"{key}_sha256"], f"private {key} hash"):
            raise CorrectionValidationError(f"private {key} hash differs")

    lineage_matches = []
    lineage_root = source_root / "data" / "agent-history" / "gaza" / "lineage" / "published-stories"
    for path in lineage_root.glob("*.json"):
        payload = _load_json(path, "published lineage")
        if isinstance(payload, dict) and payload.get("story_id") == correction["story_id"]:
            lineage_matches.append(path.resolve())
    if lineage_matches != [paths["lineage"]]:
        raise CorrectionValidationError("published lineage is missing, ambiguous, or duplicated")

    lineage = _load_json(paths["lineage"], "published lineage")
    try:
        validate_gaza_published_story_lineage(lineage)
    except ValueError as exc:
        raise CorrectionValidationError(f"published lineage is invalid: {exc}") from exc
    review = _load_json(paths["review"], "editorial review")
    audit = _load_json(paths["decision_audit"], "decision audit")

    if lineage.get("story_id") != correction["story_id"]:
        raise CorrectionValidationError("lineage resolves another story")
    if lineage.get("edition_date") != correction["owning_edition_date"]:
        raise CorrectionValidationError("lineage resolves another owning edition")
    if lineage.get("stable_event_identity", {}).get("fingerprint") != correction["stable_event_fingerprint"]:
        raise CorrectionValidationError("stable event fingerprint changed")
    if lineage.get("prior_claim_identity", {}).get("fingerprint") != correction["prior_claim_fingerprint"]:
        raise CorrectionValidationError("prior claim fingerprint changed")
    if lineage.get("prior_claim", {}).get("text") != correction["prior_claim"]:
        raise CorrectionValidationError("prior public claim differs from immutable lineage")
    prior_counts = lineage.get("prior_claim", {}).get("casualty_counts")
    if prior_counts != {"new_deaths": 1, "new_injuries": 2}:
        raise CorrectionValidationError("prior casualty state is not the validated one-to-two correction")

    if review.get("schema_version") != "gaza_historical_editorial_review_v2":
        raise CorrectionValidationError("editorial review schema is unsupported")
    if review.get("decision") != "corrected" or review.get("resulting_review_state") != "substantively_reviewed":
        raise CorrectionValidationError("candidate lacks a substantively reviewed corrected decision")
    if review.get("current_publication_eligible") is not False or review.get("current_publication_approval") is not False:
        raise CorrectionValidationError("private review improperly carries publication authority")
    if review.get("candidate_event_fingerprint") != correction["corrected_claim_fingerprint"]:
        raise CorrectionValidationError("corrected claim fingerprint differs from review")
    if review.get("attribution_assessment", {}).get("safe_future_wording") != correction["corrected_claim"]:
        raise CorrectionValidationError("corrected public wording differs from reviewed wording")
    if review.get("attribution_assessment", {}).get("attributed_to") != correction["source_attribution"]:
        raise CorrectionValidationError("correction source attribution differs from review")
    if review.get("evidence_references") != correction["evidence_references"]:
        raise CorrectionValidationError("correction evidence references differ from review")
    correction_lineage = review.get("correction_lineage", {})
    if correction_lineage.get("prior_reference") != {
        "type": "published_story",
        "id": correction["story_id"],
        "edition_date": correction["owning_edition_date"],
    }:
        raise CorrectionValidationError("review correction lineage resolves another public story")
    if correction_lineage.get("prior_event_fingerprint", {}).get("event_identity") != correction["stable_event_fingerprint"]:
        raise CorrectionValidationError("review prior event identity differs")
    if correction_lineage.get("corrected_event_fingerprint", {}).get("event_identity") != correction["stable_event_fingerprint"]:
        raise CorrectionValidationError("review corrected event identity differs")
    if correction_lineage.get("prior_event_fingerprint", {}).get("fingerprint") != correction["prior_claim_fingerprint"]:
        raise CorrectionValidationError("review prior claim fingerprint differs")
    if correction_lineage.get("corrected_event_fingerprint", {}).get("fingerprint") != correction["corrected_claim_fingerprint"]:
        raise CorrectionValidationError("review corrected claim fingerprint differs")
    if correction_lineage.get("previous_value") != 1 or correction_lineage.get("corrected_value") != 2:
        raise CorrectionValidationError("review casualty correction would double count")
    if correction_lineage.get("prior_public_artifact_overwritten") is not False:
        raise CorrectionValidationError("review permits silent replacement")
    review_dispute = review.get("attribution_assessment", {}).get("disputed_values")
    if review_dispute != correction["injury_disagreement"]["reports"]:
        raise CorrectionValidationError("injury disagreement differs from the reviewed evidence")

    expected_review_sha = evidence["review_sha256"]
    if audit.get("review_artifact_sha256") != expected_review_sha:
        raise CorrectionValidationError("decision audit does not bind the exact review")
    for key in (
        "raw_sha256",
        "normalized_artifact_sha256",
        "report_artifact_sha256",
        "candidate_event_fingerprint",
        "decision",
        "resulting_review_state",
    ):
        expected = review.get(key)
        if audit.get(key) != expected:
            raise CorrectionValidationError(f"decision audit differs from review at {key}")
    for key in (
        "publication_eligible",
        "publication_approval",
        "publication_authorized",
        "queue_authorized",
        "archive_content_change_authorized",
        "edition_authorized",
        "source_record_authorized",
        "cluster_authorized",
        "audio_authorized",
    ):
        if audit.get(key) is not False:
            raise CorrectionValidationError(f"private decision audit improperly authorizes {key}")
    for key in ("raw_sha256", "normalized_artifact_sha256", "report_artifact_sha256"):
        if evidence[key] != review[key]:
            raise CorrectionValidationError(f"proposal differs from reviewed evidence at {key}")
    for path_field, hash_field in (
        ("raw_archive_artifact_path", "raw_archive_artifact_sha256"),
        ("normalized_artifact_path", "normalized_artifact_sha256"),
        ("report_artifact_path", "report_artifact_sha256"),
    ):
        artifact = _repo_path(source_root, audit[path_field], path_field)
        if sha256_file(artifact) != audit[hash_field]:
            raise CorrectionValidationError(f"private artifact is tampered: {path_field}")
    return lineage, review, audit


def _expected_public_path(role: str, correction: dict[str, Any]) -> str:
    return REPLACEMENT_PATHS[role].format(
        date=correction["owning_edition_date"], correction_id=correction["correction_id"]
    )


def _validate_pages_and_representations(
    pages_root: Path,
    input_root: Path,
    proposal: dict[str, Any],
    correction: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    pages = _exact_fields(
        proposal["pages_state"],
        {"branch", "expected_head", "representations", "unchanged_dependencies", "artifact_set_sha256"},
        "Pages state",
    )
    if pages["branch"] != "gh-pages" or not re.fullmatch(r"[0-9a-f]{40}", str(pages["expected_head"])):
        raise CorrectionValidationError("Pages branch or expected head is invalid")
    if _git(pages_root, "status", "--porcelain"):
        raise CorrectionValidationError("Pages repository is dirty")
    if _git(pages_root, "rev-parse", "--abbrev-ref", "HEAD") != "gh-pages":
        raise CorrectionValidationError("Pages repository is not on gh-pages")
    if _git(pages_root, "rev-parse", "HEAD") != pages["expected_head"]:
        raise CorrectionValidationError("Pages history drifted from the approved head")

    rows = pages["representations"]
    if not isinstance(rows, list) or len(rows) != len(REPLACEMENT_PATHS):
        raise CorrectionValidationError("correction representation inventory is partial")
    by_role = {str(row.get("role")): row for row in rows if isinstance(row, dict)}
    if set(by_role) != set(REPLACEMENT_PATHS) or len(by_role) != len(rows):
        raise CorrectionValidationError("correction representation roles are missing or duplicated")
    for role, row in by_role.items():
        _exact_fields(row, {"role", "public_path", "input_path", "prior_sha256", "corrected_sha256"}, f"{role} representation")
        if row["public_path"] != _expected_public_path(role, correction):
            raise CorrectionValidationError(f"{role} public path is invalid")
        input_path = _repo_path(input_root, row["input_path"], f"{role} input")
        if not input_path.is_file() or sha256_file(input_path) != _require_sha(row["corrected_sha256"], f"{role} corrected hash"):
            raise CorrectionValidationError(f"{role} corrected artifact hash differs")
        public_path = _repo_path(pages_root, row["public_path"], f"{role} public artifact")
        if role in NEW_ROLES:
            if row["prior_sha256"] is not None or public_path.exists():
                raise CorrectionValidationError(f"{role} would overwrite existing correction history")
        else:
            if not public_path.is_file() or sha256_file(public_path) != _require_sha(row["prior_sha256"], f"{role} prior hash"):
                raise CorrectionValidationError(f"{role} Pages artifact drifted")

    unchanged = pages["unchanged_dependencies"]
    if not isinstance(unchanged, list) or len(unchanged) != len(UNCHANGED_PATHS):
        raise CorrectionValidationError("unchanged Pages dependency inventory is incomplete")
    unchanged_by_role = {str(row.get("role")): row for row in unchanged if isinstance(row, dict)}
    if set(unchanged_by_role) != set(UNCHANGED_PATHS) or len(unchanged_by_role) != len(unchanged):
        raise CorrectionValidationError("unchanged Pages dependencies are missing or duplicated")
    for role, template in UNCHANGED_PATHS.items():
        row = _exact_fields(unchanged_by_role[role], {"role", "public_path", "sha256"}, f"{role} dependency")
        expected = template.format(date=correction["owning_edition_date"])
        if row["public_path"] != expected:
            raise CorrectionValidationError(f"{role} dependency path is invalid")
        path = _repo_path(pages_root, expected, f"{role} dependency")
        if not path.is_file() or sha256_file(path) != _require_sha(row["sha256"], f"{role} dependency hash"):
            raise CorrectionValidationError(f"{role} dependency drifted")

    set_payload = [
        {"role": role, "public_path": by_role[role]["public_path"], "corrected_sha256": by_role[role]["corrected_sha256"]}
        for role in sorted(by_role)
    ]
    expected_set_hash = "sha256:" + sha256_bytes(_canonical_bytes(set_payload))
    if pages["artifact_set_sha256"] != expected_set_hash:
        raise CorrectionValidationError("approved artifact-set fingerprint differs")
    return by_role


def _require_text_semantics(text: str, correction: dict[str, Any], role: str) -> None:
    for field in ("correction_id", "story_id", "correction_date", "prior_claim", "corrected_claim"):
        if str(correction[field]) not in text:
            raise CorrectionValidationError(f"{role} does not visibly preserve correction {field}")


def _validate_json_semantics(path: Path, role: str, correction: dict[str, Any]) -> None:
    payload = _load_json(path, role)
    if role == "curation_manifest":
        if not isinstance(payload, list):
            raise CorrectionValidationError("curation manifest must remain a story list")
        matches = [row for row in payload if isinstance(row, dict) and row.get("story_id") == correction["story_id"]]
        if len(matches) != 1:
            raise CorrectionValidationError("curation must preserve exactly one owning story")
        story = matches[0]
        if story.get("summary") != correction["corrected_claim"]:
            raise CorrectionValidationError("curation story does not use reviewed corrected wording")
        counts = story.get("casualty_counts")
        if counts != {"new_deaths": 2}:
            raise CorrectionValidationError("curation must replace deaths with two and omit a resolved injury total")
        if story.get("event_fingerprint") != correction["stable_event_fingerprint"]:
            raise CorrectionValidationError("curation changed stable event identity")
        history = story.get("correction_history")
        if not isinstance(history, list) or len(history) != 1 or history[0].get("correction_id") != correction["correction_id"]:
            raise CorrectionValidationError("curation lacks visible prior-version history")
        if any(row.get("correction_id") == correction["correction_id"] for row in payload if row is not story and isinstance(row, dict)):
            raise CorrectionValidationError("correction was modeled as a second story")
    elif role in {"dedupe_report", "edition_manifest", "correction_manifest"}:
        if not isinstance(payload, dict):
            raise CorrectionValidationError(f"{role} must be an object")
        corrections = payload.get("corrections") if role != "correction_manifest" else [payload]
        if not isinstance(corrections, list) or len(corrections) != 1:
            raise CorrectionValidationError(f"{role} must contain one formal correction")
        record = corrections[0]
        if record.get("correction_id") != correction["correction_id"] or record.get("story_id") != correction["story_id"]:
            raise CorrectionValidationError(f"{role} correction identity differs")
        if record.get("owning_edition_date") != correction["owning_edition_date"] or record.get("correction_date") != correction["correction_date"]:
            raise CorrectionValidationError(f"{role} confuses correction and owning dates")
        if record.get("prior_claim") != correction["prior_claim"] or record.get("corrected_claim") != correction["corrected_claim"]:
            raise CorrectionValidationError(f"{role} does not preserve public claim history")
        if record.get("stable_event_fingerprint") != correction["stable_event_fingerprint"]:
            raise CorrectionValidationError(f"{role} changed stable event identity")
        if record.get("injury_disagreement") != correction["injury_disagreement"]:
            raise CorrectionValidationError(f"{role} resolves or alters the injury disagreement")
        if record.get("casualty_change") != correction["casualty_change"]:
            raise CorrectionValidationError(f"{role} casualty change can double count")
        if record.get("source_attribution") != correction["source_attribution"]:
            raise CorrectionValidationError(f"{role} loses source attribution")
        if record.get("evidence_references") != correction["evidence_references"]:
            raise CorrectionValidationError(f"{role} loses correction evidence")
        if role == "edition_manifest" and record.get("aggregates_recomputed_from_corrected_story_versions") is not True:
            raise CorrectionValidationError("edition aggregates were not recomputed from corrected story versions")
    elif role == "original_audio_metadata":
        if payload.get("audio_status") != "superseded_by_formal_correction":
            raise CorrectionValidationError("prior audio metadata is not explicitly superseded")
        if payload.get("superseded_by_correction_id") != correction["correction_id"]:
            raise CorrectionValidationError("prior audio metadata points to another correction")
    elif role == "correction_audio_metadata":
        if payload.get("audio_status") != "formal_correction" or payload.get("correction_id") != correction["correction_id"]:
            raise CorrectionValidationError("correction audio metadata identity differs")
        if payload.get("story_id") != correction["story_id"] or payload.get("owning_edition_date") != correction["owning_edition_date"]:
            raise CorrectionValidationError("correction audio was modeled as a new story or edition")
        script = str(payload.get("script_text") or "")
        _require_text_semantics(script, correction, role)
        if payload.get("injury_disagreement") != correction["injury_disagreement"]:
            raise CorrectionValidationError("correction audio resolves the injury disagreement")
    elif role == "flash_briefing":
        if not isinstance(payload, list) or len(payload) != 1:
            raise CorrectionValidationError("flash briefing correction must be one atomic update")
        _require_text_semantics(_canonical_bytes(payload).decode("utf-8"), correction, role)


def _validate_feed(path: Path, role: str, correction: dict[str, Any]) -> None:
    try:
        root = ElementTree.fromstring(path.read_bytes())
    except ElementTree.ParseError as exc:
        raise CorrectionValidationError(f"{role} is invalid XML: {exc}") from exc
    items = root.findall(".//item")
    matches = []
    for item in items:
        guid = item.findtext("guid", default="")
        if guid == correction["correction_id"]:
            matches.append(item)
    if len(matches) != 1:
        raise CorrectionValidationError(f"{role} requires one correction item with a unique correction GUID")
    item = matches[0]
    link = item.findtext("link", default="")
    description = item.findtext("description", default="")
    if correction["correction_id"] not in link:
        raise CorrectionValidationError(f"{role} correction item is not linked to the correction record")
    _require_text_semantics(description, correction, role)
    if role in {"podcast", "audio_podcast"}:
        enclosure = item.find("enclosure")
        if enclosure is None or correction["correction_id"] not in enclosure.attrib.get("url", ""):
            raise CorrectionValidationError(f"{role} correction item lacks distinct correction audio")


def _validate_representation_semantics(
    input_root: Path, rows: dict[str, dict[str, Any]], correction: dict[str, Any]
) -> None:
    for role, row in rows.items():
        path = _repo_path(input_root, row["input_path"], f"{role} input")
        if role == "correction_audio":
            if path.stat().st_size < 4 or path.read_bytes()[:3] != b"ID3":
                raise CorrectionValidationError("correction audio is not an independently supplied MP3 asset")
            continue
        if role in {"curation_manifest", "dedupe_report", "edition_manifest", "original_audio_metadata", "correction_manifest", "correction_audio_metadata", "flash_briefing"}:
            _validate_json_semantics(path, role, correction)
        elif role in {"rss", "podcast", "audio_podcast"}:
            _validate_feed(path, role, correction)
        else:
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeError as exc:
                raise CorrectionValidationError(f"{role} is not UTF-8 text") from exc
            _require_text_semantics(text, correction, role)


def _load_independent_approval(
    source_root: Path,
    approval_ref: str,
    approval_path: str,
    proposal: dict[str, Any],
    correction: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    if not approval_ref or not approval_path:
        raise CorrectionValidationError("a separately committed approval ref and path are required")
    approval_commit = _git(source_root, "rev-parse", f"{approval_ref}^{{commit}}")
    try:
        raw = subprocess.run(
            ["git", "-C", str(source_root), "show", f"{approval_commit}:{approval_path}"],
            check=True,
            capture_output=True,
        ).stdout
        approval = json.loads(raw.decode("utf-8"))
    except (subprocess.CalledProcessError, UnicodeError, json.JSONDecodeError) as exc:
        raise CorrectionValidationError("independently committed approval artifact is missing or invalid") from exc
    approval = _exact_fields(
        approval,
        {
            "schema_version",
            "scope",
            "approval_id",
            "proposal_sha256",
            "correction_id",
            "source_commit",
            "pages_head",
            "artifact_set_sha256",
            "approved_at",
            "approver",
            "package_authorized",
            "audio_authorized",
            "publication_authorized",
            "approval_fingerprint",
        },
        "release approval",
    )
    if approval["schema_version"] != APPROVAL_SCHEMA or approval["scope"] != "formal_historical_correction":
        raise CorrectionValidationError("release approval schema or scope is invalid")
    if approval["approval_fingerprint"] != fingerprint_payload(approval, "approval_fingerprint"):
        raise CorrectionValidationError("release approval fingerprint differs")
    if approval["proposal_sha256"] != proposal["proposal_sha256"]:
        raise CorrectionValidationError("release approval binds another proposal")
    if approval["correction_id"] != correction["correction_id"]:
        raise CorrectionValidationError("release approval binds another correction")
    if approval["source_commit"] != proposal["source_commit"]:
        raise CorrectionValidationError("release approval binds another source commit")
    if approval["pages_head"] != proposal["pages_state"]["expected_head"]:
        raise CorrectionValidationError("release approval binds another Pages history")
    if approval["artifact_set_sha256"] != proposal["pages_state"]["artifact_set_sha256"]:
        raise CorrectionValidationError("release approval binds another artifact set")
    if approval["package_authorized"] is not True or approval["audio_authorized"] is not True:
        raise CorrectionValidationError("release approval does not authorize the complete package and audio")
    if approval["publication_authorized"] is not False:
        raise CorrectionValidationError("package approval must not smuggle publication authority")
    if not str(approval["approver"]).strip() or not str(approval["approved_at"]).strip():
        raise CorrectionValidationError("release approval lacks a named authority and timestamp")
    _git(source_root, "merge-base", "--is-ancestor", proposal["source_commit"], approval_commit)
    current = _git(source_root, "rev-parse", "HEAD")
    _git(source_root, "merge-base", "--is-ancestor", proposal["source_commit"], current)
    return approval, approval_commit


def plan_correction(
    *,
    source_root: Path,
    pages_root: Path,
    proposal_path: Path,
    input_root: Path,
    approval_ref: str,
    approval_path: str,
) -> dict[str, Any]:
    source_root = source_root.resolve()
    pages_root = pages_root.resolve()
    input_root = input_root.resolve()
    proposal = _validate_proposal_shape(_load_json(proposal_path, "correction proposal"))
    correction = _validate_correction(proposal)
    source_head = _git(source_root, "rev-parse", "HEAD")
    if source_head != proposal["source_commit"] and _git(
        source_root, "merge-base", "--is-ancestor", proposal["source_commit"], source_head
    ) != "":
        raise CorrectionValidationError("source checkout does not contain the proposal source commit")
    _validate_private_evidence(source_root, proposal, correction)
    rows = _validate_pages_and_representations(pages_root, input_root, proposal, correction)
    _validate_representation_semantics(input_root, rows, correction)
    approval, approval_commit = _load_independent_approval(
        source_root, approval_ref, approval_path, proposal, correction
    )
    return {
        "schema_version": PACKAGE_SCHEMA,
        "status": "validated_plan",
        "operation": "formal_historical_correction",
        "domain": "gaza",
        "correction_id": correction["correction_id"],
        "story_id": correction["story_id"],
        "owning_edition_date": correction["owning_edition_date"],
        "correction_date": correction["correction_date"],
        "source_commit": proposal["source_commit"],
        "pages_head": proposal["pages_state"]["expected_head"],
        "proposal_sha256": proposal["proposal_sha256"],
        "approval_id": approval["approval_id"],
        "approval_commit": approval_commit,
        "artifact_set_sha256": proposal["pages_state"]["artifact_set_sha256"],
        "representations": [
            {
                "role": role,
                "public_path": rows[role]["public_path"],
                "input_path": rows[role]["input_path"],
                "corrected_sha256": rows[role]["corrected_sha256"],
            }
            for role in sorted(rows)
        ],
        "unchanged_dependencies": sorted(
            proposal["pages_state"]["unchanged_dependencies"],
            key=lambda row: row["role"],
        ),
        "persistent_mutation": False,
        "pages_mutation": False,
        "publication_authorized": False,
    }


def stage_correction_package(
    *,
    plan: dict[str, Any],
    input_root: Path,
    staging_root: Path,
    source_root: Path,
    pages_root: Path,
) -> dict[str, Any]:
    input_root = input_root.resolve()
    staging_root = staging_root.resolve()
    source_root = source_root.resolve()
    pages_root = pages_root.resolve()
    for forbidden, label in ((source_root, "source"), (pages_root, "Pages")):
        try:
            staging_root.relative_to(forbidden)
        except ValueError:
            pass
        else:
            raise CorrectionValidationError(f"staging root must be outside the {label} repository")
    correction_id = str(plan.get("correction_id") or "")
    if plan.get("status") != "validated_plan" or not correction_id:
        raise CorrectionValidationError("only a fully validated plan may be staged")
    target = staging_root / correction_id
    manifest = {
        **{k: v for k, v in plan.items() if k not in {"status", "persistent_mutation"}},
        "status": "staged_correction_package",
        "package_manifest_sha256": "",
    }
    manifest["package_manifest_sha256"] = fingerprint_payload(manifest, "package_manifest_sha256")
    if target.exists():
        existing_path = target / "package_manifest.json"
        if existing_path.is_file() and _load_json(existing_path, "existing package manifest") == manifest:
            for row in plan["representations"]:
                staged = _repo_path(target, row["public_path"], "staged representation")
                if not staged.is_file() or sha256_file(staged) != row["corrected_sha256"]:
                    raise CorrectionValidationError("existing package conflicts with approved artifact hashes")
            return {**manifest, "status": "idempotent_noop", "package_path": str(target)}
        raise CorrectionValidationError("conflicting correction package already exists")
    staging_root.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=f".{correction_id}-", dir=staging_root))
    try:
        for row in plan["representations"]:
            source = _repo_path(input_root, row["input_path"], "approved input")
            if not source.is_file() or sha256_file(source) != row["corrected_sha256"]:
                raise CorrectionValidationError(f"approved input is missing for {row['role']}")
            destination = _repo_path(temp, row["public_path"], "staged representation")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        (temp / "package_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temp, target)
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    return {**manifest, "package_path": str(target)}
