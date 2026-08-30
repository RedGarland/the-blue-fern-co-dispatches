"""Preservation-first archive and normalization for historical agent exports."""
from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
import subprocess
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .adapters.food_line_agent import adapt_food_line_agent_output, map_finding_to_food_line_candidate
from .ice_historical import (
    extract_detection_date,
    ice_aggregate_metrics,
    ice_historical_identity,
    ice_match_targets,
    ice_report as _ice_report,
    normalize_detection_date,
    normalize_ice_record,
)

DOMAINS = ("food-line", "care-line", "gaza", "ice")
SCHEMA_VERSION = "historical_agent_raw_v1"
GAZA_PUBLISHED_LINEAGE_SCHEMA = "gaza_published_story_lineage_v1"
GAZA_PUBLISHED_LINEAGE_CANONICALIZATION = "gaza_published_story_lineage_c14n_v1"
GAZA_PAGES_REPOSITORY = "https://github.com/RedGarland/the-blue-fern-co-dispatches.git"
GAZA_PUBLISHED_LINEAGE_ARTIFACTS = {
    "curation_manifest": "curation_manifest.json",
    "dedupe_report": "dedupe_report.json",
    "sources_manifest": "sources_manifest.json",
    "rendered_edition": "index.html",
}


class HistoricalEnvelopeError(ValueError):
    """Raised when a preserved text envelope contains an invalid structured payload."""


def parse_historical_input(raw: bytes) -> tuple[Any, dict[str, Any]]:
    """Parse JSON or one embedded JSON fence without changing the preserved bytes."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text), {"normalization_method": "structured_json"}
    except json.JSONDecodeError:
        pass

    fence_pattern = re.compile(r"(?ms)^```([^\r\n`]*)\r?\n(.*?)^```[ \t]*(?:\r?\n|$)")
    fences = list(fence_pattern.finditer(text))
    if not fences:
        return {"raw_text": text}, {"normalization_method": "text_envelope"}
    if len(fences) != 1:
        raise HistoricalEnvelopeError("text envelope must contain exactly one fenced JSON object")
    fence = fences[0]
    label = fence.group(1).strip().lower()
    if label not in {"", "json"}:
        raise HistoricalEnvelopeError("text envelope fence must be unlabeled or labeled json")
    try:
        payload = json.loads(fence.group(2))
    except json.JSONDecodeError as exc:
        raise HistoricalEnvelopeError("embedded JSON fence is invalid JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("findings"), list):
        raise HistoricalEnvelopeError("embedded JSON fence is not a valid agent-run envelope")
    return payload, {
        "normalization_method": "embedded_json_envelope",
        "private_text_provenance": {
            "before_fence": text[: fence.start()],
            "after_fence": text[fence.end() :],
        },
    }


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, separators=(",", ": ")) + "\n"


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical_json(value))
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_fingerprint(value: Any) -> str:
    return "sha256:" + sha256_bytes(canonical_json(value).encode("utf-8"))


def archive_root(root: Path, domain: str) -> Path:
    if domain not in DOMAINS: raise ValueError(f"unsupported domain: {domain}")
    return root / "data" / "agent-history" / domain


def _date_values(value: Any) -> list[str]:
    text = str(value or "")
    return re.findall(r"20\d{2}-\d{2}-\d{2}", text)


def _load_source(path: Path) -> tuple[bytes, Any]:
    raw = path.read_bytes()
    payload, _ = parse_historical_input(raw)
    return raw, payload


def validate_input(path: Path, *, domain: str) -> dict[str, Any]:
    raw = path.read_bytes()
    try:
        payload, parse_metadata = parse_historical_input(raw)
    except HistoricalEnvelopeError as exc:
        return {"valid": False, "domain": domain, "input_sha256": sha256_bytes(raw), "source_format": "text", "finding_count": 0, "invalid_records": [], "invalid_detection_dates": [], "missing_dates": [], "missing_evidence": [], "duplicates": [], "malformed_base64": False, "error": str(exc)}
    result: dict[str, Any] = {"valid": True, "domain": domain, "input_sha256": sha256_bytes(raw), "source_format": "json" if parse_metadata["normalization_method"] == "structured_json" else "text", "finding_count": 0, "invalid_records": [], "invalid_detection_dates": [], "missing_dates": [], "missing_evidence": [], "duplicates": [], "malformed_base64": False}
    result["normalization_method"] = parse_metadata["normalization_method"]
    if parse_metadata["normalization_method"] == "text_envelope":
        result["finding_count"] = 1
        if domain == "ice":
            try:
                extract_detection_date(str(payload.get("raw_text") or ""))
            except ValueError:
                result["invalid_detection_dates"].append(0)
                result["valid"] = False
        return result
    if domain not in DOMAINS: result.update(valid=False, error="unsupported_domain"); return result
    if payload is None:
        if not raw.strip(): result.update(valid=False, error="empty_input")
        result["finding_count"] = 1
        return result
    if isinstance(payload, dict) and "findings" in payload: rows = payload.get("findings")
    elif isinstance(payload, list): rows = payload
    elif isinstance(payload, dict): rows = [payload]
    else: rows = []
    if not isinstance(rows, list): result.update(valid=False, error="findings_must_be_list"); return result
    if isinstance(payload, dict) and payload.get("raw_bytes_base64") is not None:
        try: base64.b64decode(str(payload["raw_bytes_base64"]), validate=True)
        except (ValueError, TypeError): result["malformed_base64"] = True
    result["finding_count"] = len(rows)
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict): result["invalid_records"].append(index); continue
        if domain == "ice" and "detection_date" in row:
            try:
                normalize_detection_date(row.get("detection_date"))
            except ValueError:
                result["invalid_detection_dates"].append(index)
        identity = json.dumps({"url": str(row.get("canonical_source_url") or row.get("source_url") or row.get("url") or "").lower().split("?")[0].rstrip("/"), "title": str(row.get("title") or row.get("headline") or "").lower().strip(), "date": str(row.get("source_published_at") or row.get("published_at") or row.get("event_date") or "")[:10]}, sort_keys=True)
        if identity in seen: result["duplicates"].append(index)
        seen.add(identity)
        if not _date_values(row.get("source_published_at") or row.get("published_at") or row.get("event_date") or row.get("discovered_at")): result["missing_dates"].append(index)
        if not str(row.get("exact_supporting_passage") or row.get("evidence") or row.get("summary") or row.get("summary_or_snippet") or "").strip(): result["missing_evidence"].append(index)
    result["valid"] = not (result["invalid_records"] or result["invalid_detection_dates"] or result["missing_dates"] or result["missing_evidence"] or result["malformed_base64"])
    return result


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and "findings" in payload: payload = payload["findings"]
    if isinstance(payload, dict): payload = [payload]
    return [row for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []


def _existing_text(root: Path, domain: str) -> str:
    pieces: list[str] = []
    roots = [root / "data" / "dispatches" / domain, root / "data" / "universal_events"]
    if domain == "gaza": roots.append(root / "data" / "dispatches" / "gaza")
    for base in roots:
        if not base.exists(): continue
        for path in base.rglob("*.json"):
            try: pieces.append(path.read_text(encoding="utf-8", errors="ignore"))
            except OSError: pass
    return "\n".join(pieces).lower()


def _care_published_ids(root: Path) -> set[str]:
    text = _existing_text(root, "care-line")
    ids = set(re.findall(r"[\"']?(?:event_id|id)[\"']?\s*[:=]\s*[\"']([^\"']+)", text, flags=re.I))
    if "published" not in text: return set()
    return ids


def _care_json_objects(root: Path) -> list[tuple[str, dict[str, Any]]]:
    """Read only private Care Line JSON artifacts used for historical matching."""
    bases = [
        root / "data" / "universal_events" / "publication-state",
        root / "data" / "universal_events" / "shadow" / "care-line",
        root / "data" / "dispatches" / "care-line" / "reviewed",
        root / "data" / "dispatches" / "care-line" / "evidence-reviews",
        root / "data" / "dispatches" / "care-line" / "sources",
        root / "data" / "dispatches" / "care-line" / "queue-runs",
        root / "data" / "agent-history" / "care-line" / "normalized",
    ]
    objects: list[tuple[str, dict[str, Any]]] = []
    for base in bases:
        if not base.exists():
            continue
        for path in base.rglob("*.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue

            def visit(item: Any) -> None:
                if isinstance(item, dict):
                    objects.append((str(path.relative_to(root)), item))
                    for child in item.values():
                        visit(child)
                elif isinstance(item, list):
                    for child in item:
                        visit(child)

            visit(value)
    return objects


def _lineage_text(value: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())).strip()


def _lineage_story_text(story: dict[str, Any]) -> str:
    values = [story.get("title"), story.get("summary")]
    for source in story.get("source_records") or []:
        if isinstance(source, dict):
            values.extend((source.get("title"), source.get("summary_or_snippet")))
    return _lineage_text(" ".join(str(value or "") for value in values))


def gaza_stable_event_identity_inputs(story: dict[str, Any]) -> dict[str, str]:
    """Derive bounded, claim-independent Gaza event identity from evidenced story fields."""
    event_date = str(story.get("event_date") or "")[:10]
    location = _lineage_text(story.get("location"))
    location_key = _lineage_text(str(story.get("location") or "").split(",", 1)[0])
    text = _lineage_story_text(story)
    incident_type = _lineage_text(story.get("development_type"))
    if not incident_type and re.search(r"\b(kill|killed|death|dead|injur|injured|wound|wounded)\b", text):
        incident_type = "casualty event"
    mechanism = "strike" if re.search(r"\b(strike|struck|airstrike|attack|attacked)\b", text) else ""
    incident_object = "motorcycle" if re.search(
        r"\b(motorcycle|motorbike|motor bike|electric bike|electric motorcycle)\b", text
    ) else ""
    inputs = {
        "domain": "gaza",
        "event_date": event_date,
        "location": location,
        "location_key": location_key,
        "incident_type": incident_type,
        "mechanism": mechanism,
        "incident_object": incident_object,
    }
    missing = [key for key, value in inputs.items() if not value]
    if missing:
        raise ValueError(
            "published Gaza story lacks stable event identity fields: "
            + ", ".join(missing)
        )
    if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", event_date):
        raise ValueError("published Gaza story event_date is invalid")
    return inputs


def gaza_stable_event_fingerprint(inputs: dict[str, str]) -> str:
    expected = {
        "domain",
        "event_date",
        "location",
        "location_key",
        "incident_type",
        "mechanism",
        "incident_object",
    }
    if set(inputs) != expected:
        raise ValueError("Gaza stable event identity inputs are incomplete or unsupported")
    normalized = {key: _lineage_text(value) for key, value in inputs.items()}
    if normalized["domain"] != "gaza":
        raise ValueError("Gaza stable event identity domain must remain gaza")
    normalized["event_date"] = str(inputs["event_date"])
    return _canonical_fingerprint(
        {
            "canonicalization_version": GAZA_PUBLISHED_LINEAGE_CANONICALIZATION,
            "identity_type": "stable_event",
            "inputs": normalized,
        }
    )


def _number_from_claim(value: Any) -> int | None:
    text = _lineage_text(value)
    words = {
        "zero": 0,
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
    }
    match = re.search(r"\b(\d+|zero|one|two|three|four|five|six|seven|eight|nine|ten)\b", text)
    if not match:
        return None
    return int(match.group(1)) if match.group(1).isdigit() else words[match.group(1)]


def gaza_candidate_matches_published_lineage(
    finding: dict[str, Any],
    lineage: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> bool:
    """Fail-closed same-event check for a correction against private published lineage."""
    stable = lineage["stable_event_identity"]["inputs"]
    if str(finding.get("event_date") or "")[:10] != stable["event_date"]:
        return False
    candidate_text = _lineage_text(
        " ".join(
            [
                str(finding.get("title") or ""),
                str(finding.get("summary") or ""),
                str(finding.get("exact_supporting_passage") or ""),
            ]
        )
    )
    evidence_text = _lineage_text(
        " ".join(str(item.get("supporting_passage") or "") for item in evidence)
    )
    combined_text = f"{candidate_text} {evidence_text}".strip()
    if stable["location_key"] not in candidate_text:
        return False
    if stable["mechanism"] == "strike" and not re.search(
        r"\b(strike|struck|airstrike|attack|attacked)\b", candidate_text
    ):
        return False
    if stable["incident_object"] == "motorcycle" and not re.search(
        r"\b(motorcycle|motorbike|motor bike|electric bike|electric motorcycle)\b",
        combined_text,
    ):
        return False
    if stable["incident_type"] == "casualty event" and not re.search(
        r"\b(kill|killed|death|dead|injur|injured|wound|wounded|casualty|casualties)\b",
        candidate_text,
    ):
        return False
    prior_deaths = lineage["prior_claim"].get("casualty_counts", {}).get("new_deaths")
    initial_report = (finding.get("material_update_lineage") or {}).get("initial_report")
    return isinstance(prior_deaths, int) and _number_from_claim(initial_report) == prior_deaths


def _git_output(repo: Path, *args: str) -> bytes:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), *args],
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"Pages Git provenance check failed: {detail or 'git command failed'}") from exc


def _git_text(repo: Path, *args: str) -> str:
    return _git_output(repo, *args).decode("utf-8", errors="strict").strip()


def _lineage_artifact_paths(edition_date: str) -> dict[str, str]:
    if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", edition_date):
        raise ValueError("published lineage edition date must be an ISO date")
    base = f"gaza/editions/{edition_date}"
    return {
        role: f"{base}/{filename}"
        for role, filename in GAZA_PUBLISHED_LINEAGE_ARTIFACTS.items()
    }


def _dicts(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(value, dict):
        rows.append(value)
        for child in value.values():
            rows.extend(_dicts(child))
    elif isinstance(value, list):
        for child in value:
            rows.extend(_dicts(child))
    return rows


def _lineage_record_fingerprint(record: dict[str, Any]) -> str:
    payload = {key: value for key, value in record.items() if key != "record_fingerprint"}
    return _canonical_fingerprint(payload)


def build_gaza_published_story_lineage(
    pages_repo: Path,
    *,
    pages_commit: str,
    story_id: str,
    edition_date: str,
    expected_title: str,
    expected_prior_claim: str,
    backfill_reason: str,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Build one private lineage record from immutable local Pages Git objects."""
    pages_repo = pages_repo.resolve()
    if not (pages_repo / ".git").exists():
        raise ValueError("Pages repository path is not a Git checkout")
    if not re.fullmatch(r"[0-9a-f]{40}", pages_commit):
        raise ValueError("Pages commit must be an exact lowercase Git OID")
    if not re.fullmatch(r"gaza-story-20\d{2}-\d{2}-\d{2}-\d{3}", story_id):
        raise ValueError("published Gaza story ID is invalid")
    remote = _git_text(pages_repo, "remote", "get-url", "origin")
    if remote.rstrip("/") != GAZA_PAGES_REPOSITORY.rstrip("/"):
        raise ValueError("Pages repository identity does not match the protected repository")
    branch = _git_text(pages_repo, "branch", "--show-current")
    if branch != "gh-pages":
        raise ValueError("Pages repository must be on gh-pages")
    pinned_type = _git_text(pages_repo, "cat-file", "-t", pages_commit)
    if pinned_type != "commit":
        raise ValueError("pinned Pages object is not a commit")
    head = _git_text(pages_repo, "rev-parse", "HEAD")
    try:
        subprocess.run(
            ["git", "-C", str(pages_repo), "merge-base", "--is-ancestor", pages_commit, head],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as exc:
        raise ValueError("pinned Pages commit is not an ancestor of the observed Pages head") from exc

    paths = _lineage_artifact_paths(edition_date)
    artifact_bytes: dict[str, bytes] = {}
    artifacts: list[dict[str, Any]] = []
    for role, path in paths.items():
        blob = _git_text(pages_repo, "rev-parse", f"{pages_commit}:{path}")
        data = _git_output(pages_repo, "show", f"{pages_commit}:{path}")
        artifact_bytes[role] = data
        artifacts.append(
            {
                "role": role,
                "path": path,
                "git_blob_oid": blob,
                "sha256": sha256_bytes(data),
                "byte_length": len(data),
            }
        )

    try:
        curation = json.loads(artifact_bytes["curation_manifest"])
        sources = json.loads(artifact_bytes["sources_manifest"])
        dedupe = json.loads(artifact_bytes["dedupe_report"])
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("pinned Pages evidence is not valid JSON") from exc
    story_matches = [row for row in _dicts(curation) if row.get("story_id") == story_id]
    if len(story_matches) != 1:
        raise ValueError("pinned curation manifest must contain the story ID exactly once")
    story = story_matches[0]
    if story.get("title") != expected_title:
        raise ValueError("pinned Pages story title differs from the expected prior story")
    if story.get("summary") != expected_prior_claim:
        raise ValueError("pinned Pages prior claim differs from the expected claim")
    if story.get("public_rendered") is not True or story.get("included_in_public_summary") is not True:
        raise ValueError("pinned Pages story is not an included public story")
    if str(story.get("event_date") or "")[:10] != edition_date:
        raise ValueError("pinned Pages story event date differs from the edition date")

    source_ids = [str(value) for value in story.get("source_record_ids") or []]
    source_matches = [
        row
        for row in _dicts(sources)
        if str(row.get("source_record_id") or "") in source_ids
        and story_id in [str(value) for value in row.get("used_in_story_ids") or []]
    ]
    if not source_ids or len(source_matches) != len(set(source_ids)):
        raise ValueError("pinned Pages sources do not resolve the story lineage exactly")
    dedupe_matches = [
        row
        for row in _dicts(dedupe)
        if row.get("story_id") == story_id and row.get("title") == expected_title
    ]
    if not dedupe_matches or not all(row.get("public_rendered") is True for row in dedupe_matches):
        raise ValueError("pinned Pages dedupe evidence does not retain the public story")
    rendered = artifact_bytes["rendered_edition"].decode("utf-8", errors="strict")
    if html.escape(expected_title, quote=False) not in rendered or html.escape(
        expected_prior_claim, quote=False
    ) not in rendered:
        raise ValueError("pinned rendered edition does not contain the exact title and prior claim")

    story_evidence = {
        key: story.get(key)
        for key in (
            "story_id",
            "title",
            "summary",
            "category",
            "event_date",
            "location",
            "development_type",
            "casualty_counts",
            "attribution",
            "publisher_names",
            "source_record_ids",
            "source_urls",
            "public_rendered",
            "included_in_public_summary",
        )
    }
    source_evidence = [
        {
            key: row.get(key)
            for key in (
                "source_record_id",
                "title",
                "publisher",
                "url",
                "canonical_url",
                "published_at",
                "dispatch_slug",
                "category_hint",
                "claim_fingerprint",
                "used_in_story_ids",
            )
        }
        for row in source_matches
    ]
    dedupe_evidence: list[dict[str, Any]] = []
    for row in dedupe_matches:
        projection = {
            key: row.get(key)
            for key in (
                "story_id",
                "title",
                "classification",
                "include_decision",
                "public_rendered",
            )
        }
        if projection not in dedupe_evidence:
            dedupe_evidence.append(projection)
    stable_inputs = gaza_stable_event_identity_inputs(story)
    stable_fingerprint = gaza_stable_event_fingerprint(stable_inputs)
    from .story_dedupe import topic_fingerprint

    claim_inputs = {
        "title": story["title"],
        "summary": story["summary"],
        "category": story.get("category") or "",
    }
    prior_claim_fingerprint = "topic_fingerprint_v1:" + topic_fingerprint(claim_inputs)
    provenance = {
        "repository": GAZA_PAGES_REPOSITORY,
        "branch": "gh-pages",
        "pinned_commit": pages_commit,
        "observed_head_at_backfill": head,
        "observed_head_contains_pinned_commit": True,
        "artifacts": artifacts,
    }
    provenance["provenance_fingerprint"] = _canonical_fingerprint(provenance)
    record = {
        "schema_version": GAZA_PUBLISHED_LINEAGE_SCHEMA,
        "domain": "gaza",
        "story_id": story_id,
        "edition_date": edition_date,
        "story_title": expected_title,
        "prior_claim": {
            "text": expected_prior_claim,
            "casualty_counts": story.get("casualty_counts") or {},
        },
        "source_attribution": story.get("attribution") or "",
        "source_records": source_evidence,
        "pages_provenance": provenance,
        "evidence": {
            "story": story_evidence,
            "story_sha256": _canonical_fingerprint(story_evidence),
            "sources": source_evidence,
            "sources_sha256": _canonical_fingerprint(source_evidence),
            "dedupe": dedupe_evidence,
            "dedupe_sha256": _canonical_fingerprint(dedupe_evidence),
            "rendered_title": expected_title,
            "rendered_claim": expected_prior_claim,
        },
        "canonicalization_version": GAZA_PUBLISHED_LINEAGE_CANONICALIZATION,
        "stable_event_identity": {
            "inputs": stable_inputs,
            "fingerprint": stable_fingerprint,
        },
        "prior_claim_identity": {
            "inputs": claim_inputs,
            "fingerprint": prior_claim_fingerprint,
        },
        "backfill_reason": str(backfill_reason).strip(),
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "publication_mutation": False,
        "review_authority": False,
        "approval_authority": False,
    }
    if not record["backfill_reason"]:
        raise ValueError("published lineage backfill reason is required")
    record["record_fingerprint"] = _lineage_record_fingerprint(record)
    validate_gaza_published_story_lineage(record)
    return record


def validate_gaza_published_story_lineage(record: dict[str, Any]) -> dict[str, Any]:
    """Validate one repository-owned private published-story lineage record."""
    if not isinstance(record, dict) or record.get("schema_version") != GAZA_PUBLISHED_LINEAGE_SCHEMA:
        raise ValueError("Gaza published-story lineage schema is invalid")
    expected_record_fields = {
        "schema_version",
        "domain",
        "story_id",
        "edition_date",
        "story_title",
        "prior_claim",
        "source_attribution",
        "source_records",
        "pages_provenance",
        "evidence",
        "canonicalization_version",
        "stable_event_identity",
        "prior_claim_identity",
        "backfill_reason",
        "created_at",
        "publication_mutation",
        "review_authority",
        "approval_authority",
        "record_fingerprint",
    }
    if set(record) != expected_record_fields:
        raise ValueError("Gaza published-story lineage fields are incomplete or unsupported")
    if record.get("domain") != "gaza":
        raise ValueError("Gaza published-story lineage has a cross-domain record")
    if record.get("canonicalization_version") != GAZA_PUBLISHED_LINEAGE_CANONICALIZATION:
        raise ValueError("Gaza published-story lineage canonicalization version is invalid")
    story_id = str(record.get("story_id") or "")
    edition_date = str(record.get("edition_date") or "")
    if not re.fullmatch(r"gaza-story-20\d{2}-\d{2}-\d{2}-\d{3}", story_id):
        raise ValueError("Gaza published-story lineage story ID is invalid")
    expected_paths = _lineage_artifact_paths(edition_date)
    for key in ("publication_mutation", "review_authority", "approval_authority"):
        if record.get(key) is not False:
            raise ValueError(f"Gaza published-story lineage {key} must be false")
    if not str(record.get("backfill_reason") or "").strip():
        raise ValueError("Gaza published-story lineage backfill reason is missing")
    created_at = str(record.get("created_at") or "").strip()
    if not created_at:
        raise ValueError("Gaza published-story lineage created_at is missing")
    try:
        parsed_created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Gaza published-story lineage created_at is invalid") from exc
    if parsed_created_at.tzinfo is None:
        raise ValueError("Gaza published-story lineage created_at must include a timezone")
    if not str(record.get("source_attribution") or "").strip():
        raise ValueError("Gaza published-story lineage source attribution is missing")

    provenance = record.get("pages_provenance")
    if not isinstance(provenance, dict):
        raise ValueError("Gaza published-story lineage Pages provenance is missing")
    if set(provenance) != {
        "repository",
        "branch",
        "pinned_commit",
        "observed_head_at_backfill",
        "observed_head_contains_pinned_commit",
        "artifacts",
        "provenance_fingerprint",
    }:
        raise ValueError("Gaza published-story lineage Pages provenance fields are invalid")
    if provenance.get("repository") != GAZA_PAGES_REPOSITORY or provenance.get("branch") != "gh-pages":
        raise ValueError("Gaza published-story lineage Pages repository identity is invalid")
    for key in ("pinned_commit", "observed_head_at_backfill"):
        if not re.fullmatch(r"[0-9a-f]{40}", str(provenance.get(key) or "")):
            raise ValueError(f"Gaza published-story lineage {key} is invalid")
    if provenance.get("observed_head_contains_pinned_commit") is not True:
        raise ValueError("Gaza published-story lineage does not prove Pages ancestry")
    artifacts = provenance.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != len(expected_paths):
        raise ValueError("Gaza published-story lineage artifact inventory is incomplete")
    by_role = {str(item.get("role") or ""): item for item in artifacts if isinstance(item, dict)}
    if set(by_role) != set(expected_paths) or len(by_role) != len(artifacts):
        raise ValueError("Gaza published-story lineage artifact roles are duplicated or invalid")
    for role, path in expected_paths.items():
        artifact = by_role[role]
        if set(artifact) != {
            "role",
            "path",
            "git_blob_oid",
            "sha256",
            "byte_length",
        }:
            raise ValueError("Gaza published-story lineage artifact fields are invalid")
        if artifact.get("path") != path:
            raise ValueError("Gaza published-story lineage artifact path is invalid")
        if not re.fullmatch(r"[0-9a-f]{40}", str(artifact.get("git_blob_oid") or "")):
            raise ValueError("Gaza published-story lineage Git blob OID is invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", str(artifact.get("sha256") or "")):
            raise ValueError("Gaza published-story lineage artifact SHA-256 is invalid")
        if not isinstance(artifact.get("byte_length"), int) or artifact["byte_length"] <= 0:
            raise ValueError("Gaza published-story lineage artifact byte length is invalid")
    expected_provenance_fingerprint = _canonical_fingerprint(
        {key: value for key, value in provenance.items() if key != "provenance_fingerprint"}
    )
    if provenance.get("provenance_fingerprint") != expected_provenance_fingerprint:
        raise ValueError("Gaza published-story lineage Pages provenance fingerprint differs")

    evidence = record.get("evidence")
    if not isinstance(evidence, dict) or not isinstance(evidence.get("story"), dict):
        raise ValueError("Gaza published-story lineage story evidence is missing")
    if set(evidence) != {
        "story",
        "story_sha256",
        "sources",
        "sources_sha256",
        "dedupe",
        "dedupe_sha256",
        "rendered_title",
        "rendered_claim",
    }:
        raise ValueError("Gaza published-story lineage evidence fields are invalid")
    story = evidence["story"]
    if set(story) != {
        "story_id",
        "title",
        "summary",
        "category",
        "event_date",
        "location",
        "development_type",
        "casualty_counts",
        "attribution",
        "publisher_names",
        "source_record_ids",
        "source_urls",
        "public_rendered",
        "included_in_public_summary",
    }:
        raise ValueError("Gaza published-story lineage story evidence fields are invalid")
    if story.get("story_id") != story_id:
        raise ValueError("Gaza published-story lineage evidence points to another story")
    if story.get("title") != record.get("story_title"):
        raise ValueError("Gaza published-story lineage title differs from evidence")
    prior_claim = record.get("prior_claim")
    if (
        not isinstance(prior_claim, dict)
        or set(prior_claim) != {"text", "casualty_counts"}
        or story.get("summary") != prior_claim.get("text")
    ):
        raise ValueError("Gaza published-story lineage prior claim differs from evidence")
    if story.get("casualty_counts") != prior_claim.get("casualty_counts"):
        raise ValueError("Gaza published-story lineage casualty claim differs from evidence")
    if str(story.get("event_date") or "")[:10] != edition_date:
        raise ValueError("Gaza published-story lineage edition date differs from evidence")
    if story.get("public_rendered") is not True or story.get("included_in_public_summary") is not True:
        raise ValueError("Gaza published-story lineage evidence is not public")
    if evidence.get("rendered_title") != record.get("story_title") or evidence.get(
        "rendered_claim"
    ) != prior_claim.get("text"):
        raise ValueError("Gaza published-story lineage rendered evidence differs")
    for key in ("story", "sources", "dedupe"):
        if evidence.get(f"{key}_sha256") != _canonical_fingerprint(evidence.get(key)):
            raise ValueError(f"Gaza published-story lineage {key} evidence hash differs")
    sources = evidence.get("sources")
    if not isinstance(sources, list) or not sources or sources != record.get("source_records"):
        raise ValueError("Gaza published-story lineage source evidence differs")
    expected_source_fields = {
        "source_record_id",
        "title",
        "publisher",
        "url",
        "canonical_url",
        "published_at",
        "dispatch_slug",
        "category_hint",
        "claim_fingerprint",
        "used_in_story_ids",
    }
    if any(not isinstance(source, dict) or set(source) != expected_source_fields for source in sources):
        raise ValueError("Gaza published-story lineage source fields are invalid")
    story_source_ids = {str(value) for value in story.get("source_record_ids") or []}
    if {
        str(item.get("source_record_id") or "")
        for item in sources
        if isinstance(item, dict) and story_id in (item.get("used_in_story_ids") or [])
    } != story_source_ids:
        raise ValueError("Gaza published-story lineage source-to-story identity differs")
    from .gaza_sources import story_claim_fingerprint

    for source in sources:
        if source.get("dispatch_slug") != "gaza" or str(source.get("published_at") or "")[:10] != edition_date:
            raise ValueError("Gaza published-story lineage source domain or date differs")
        if source.get("claim_fingerprint") != story_claim_fingerprint(source):
            raise ValueError("Gaza published-story lineage source claim fingerprint differs")
    dedupe = evidence.get("dedupe")
    expected_dedupe_fields = {
        "story_id",
        "title",
        "classification",
        "include_decision",
        "public_rendered",
    }
    if not isinstance(dedupe, list) or not dedupe or any(
        not isinstance(item, dict)
        or set(item) != expected_dedupe_fields
        or
        item.get("story_id") != story_id
        or item.get("title") != record.get("story_title")
        or item.get("public_rendered") is not True
        for item in dedupe
    ):
        raise ValueError("Gaza published-story lineage dedupe evidence differs")

    stable = record.get("stable_event_identity")
    if not isinstance(stable, dict) or set(stable) != {"inputs", "fingerprint"}:
        raise ValueError("Gaza published-story lineage stable event identity is missing")
    derived_inputs = gaza_stable_event_identity_inputs(story)
    if stable.get("inputs") != derived_inputs:
        raise ValueError("Gaza published-story lineage stable event inputs differ")
    if stable.get("fingerprint") != gaza_stable_event_fingerprint(derived_inputs):
        raise ValueError("Gaza published-story lineage stable event fingerprint differs")
    claim_identity = record.get("prior_claim_identity")
    if not isinstance(claim_identity, dict) or set(claim_identity) != {"inputs", "fingerprint"}:
        raise ValueError("Gaza published-story lineage prior claim identity is missing")
    claim_inputs = {
        "title": story.get("title"),
        "summary": story.get("summary"),
        "category": story.get("category") or "",
    }
    if claim_identity.get("inputs") != claim_inputs:
        raise ValueError("Gaza published-story lineage prior claim inputs differ")
    from .story_dedupe import topic_fingerprint

    expected_claim_fingerprint = "topic_fingerprint_v1:" + topic_fingerprint(claim_inputs)
    if claim_identity.get("fingerprint") != expected_claim_fingerprint:
        raise ValueError("Gaza published-story lineage prior claim fingerprint differs")
    if record.get("record_fingerprint") != _lineage_record_fingerprint(record):
        raise ValueError("Gaza published-story lineage record fingerprint differs")
    return record


def gaza_published_lineage_path(root: Path, story_id: str) -> Path:
    return (
        archive_root(root, "gaza")
        / "lineage"
        / "published-stories"
        / f"{story_id}.json"
    )


def record_gaza_published_story_lineage(
    repo_root: Path,
    pages_repo: Path,
    *,
    pages_commit: str,
    story_id: str,
    edition_date: str,
    expected_title: str,
    expected_prior_claim: str,
    backfill_reason: str,
    dry_run: bool,
) -> dict[str, Any]:
    path = gaza_published_lineage_path(repo_root, story_id)
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        validate_gaza_published_story_lineage(existing)
        for key, expected in (
            ("story_id", story_id),
            ("edition_date", edition_date),
            ("story_title", expected_title),
        ):
            if existing.get(key) != expected:
                raise ValueError(f"existing published lineage conflicts at {key}")
        if existing.get("prior_claim", {}).get("text") != expected_prior_claim:
            raise ValueError("existing published lineage conflicts at prior_claim")
        if existing.get("pages_provenance", {}).get("pinned_commit") != pages_commit:
            raise ValueError("existing published lineage conflicts at pinned Pages commit")
        build_gaza_published_story_lineage(
            pages_repo,
            pages_commit=pages_commit,
            story_id=story_id,
            edition_date=edition_date,
            expected_title=expected_title,
            expected_prior_claim=expected_prior_claim,
            backfill_reason=backfill_reason,
            created_at=existing.get("created_at"),
        )
        return {
            "status": "idempotent_noop",
            "domain": "gaza",
            "story_id": story_id,
            "lineage_path": str(path),
            "publication_mutation": False,
            "review_authority": False,
        }
    record = build_gaza_published_story_lineage(
        pages_repo,
        pages_commit=pages_commit,
        story_id=story_id,
        edition_date=edition_date,
        expected_title=expected_title,
        expected_prior_claim=expected_prior_claim,
        backfill_reason=backfill_reason,
    )
    if dry_run:
        return {
            "status": "dry_run_validated",
            "domain": "gaza",
            "story_id": story_id,
            "lineage_path": str(path),
            "stable_event_fingerprint": record["stable_event_identity"]["fingerprint"],
            "prior_claim_fingerprint": record["prior_claim_identity"]["fingerprint"],
            "persistent_mutation": False,
            "publication_mutation": False,
            "review_authority": False,
        }
    atomic_json(path, record)
    return {
        "status": "lineage_recorded",
        "domain": "gaza",
        "story_id": story_id,
        "lineage_path": str(path),
        "stable_event_fingerprint": record["stable_event_identity"]["fingerprint"],
        "prior_claim_fingerprint": record["prior_claim_identity"]["fingerprint"],
        "publication_mutation": False,
        "review_authority": False,
    }


def load_gaza_published_story_lineages(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    lineage_root = archive_root(root, "gaza") / "lineage" / "published-stories"
    loaded: list[tuple[Path, dict[str, Any]]] = []
    seen: set[str] = set()
    for path in sorted(lineage_root.glob("*.json")) if lineage_root.exists() else []:
        value = json.loads(path.read_text(encoding="utf-8"))
        validate_gaza_published_story_lineage(value)
        story_id = value["story_id"]
        if story_id in seen:
            raise ValueError(f"duplicate Gaza published-story lineage: {story_id}")
        seen.add(story_id)
        loaded.append((path, value))
    return loaded


def care_line_match_targets(root: Path) -> dict[str, Any]:
    """Build the private Care Line identity index; public output is never consulted."""
    objects = _care_json_objects(root)
    published: dict[str, str] = {}
    reviewed: dict[str, str] = {}
    sources: dict[str, list[dict[str, str]]] = {}
    queue: dict[str, str] = {}
    historical: set[str] = set()
    ledger = root / "data" / "universal_events" / "publication-state" / "care-line-signal-wire.json"
    try:
        ledger_value = json.loads(ledger.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ledger_value = {}
    for event_id in (ledger_value.get("events", {}) if isinstance(ledger_value, dict) else {}):
        published[str(event_id)] = str(ledger)

    def clean_url(value: Any) -> str:
        return str(value or "").strip().lower().split("?")[0].rstrip("/")

    for path, item in objects:
        event_id = str(item.get("event_id") or item.get("proposed_event_id") or "").strip()
        source_url = clean_url(item.get("canonical_source_url") or item.get("source_url") or item.get("url"))
        source_id = str(item.get("source_record_id") or item.get("producer_record_id") or item.get("source_item_id") or item.get("record_id") or "")
        if source_url:
            sources.setdefault(source_url, []).append({"path": path, "source_record_id": source_id, "event_id": event_id})
        if event_id and event_id not in published:
            status = str(item.get("review_status") or item.get("revision_status") or item.get("state") or item.get("status") or "").lower()
            if ("queue" in path and status not in {"published", "failed", "rejected"}) or status in {"reviewed", "approved", "corrected", "review_ready", "approved_for_release", "queued", "publishing"}:
                reviewed.setdefault(event_id, path)
            if "queue" in path:
                queue.setdefault(event_id, path)
        if "agent-history" in path and (item.get("domain") == "care-line" or path.replace("\\", "/").startswith("data/agent-history/care-line/")):
            identity = json.dumps({"url": clean_url(item.get("canonical_source_url") or item.get("source_url") or item.get("url")), "title": str(item.get("title") or item.get("headline") or "").lower().strip(), "date": str(item.get("source_published_at") or item.get("published_at") or item.get("event_date") or "")[:10]}, sort_keys=True)
            historical.add(identity)
    return {"published_events": published, "reviewed_events": reviewed, "sources": sources, "queue": queue, "historical_identities": historical}


def _care_identity(row: dict[str, Any]) -> str:
    return json.dumps({
        "url": str(row.get("canonical_source_url") or row.get("source_url") or row.get("url") or "").lower().split("?")[0].rstrip("/"),
        "title": str(row.get("title") or row.get("headline") or "").lower().strip(),
        "date": str(row.get("source_published_at") or row.get("published_at") or row.get("event_date") or "")[:10],
    }, sort_keys=True)


def _care_report(record: dict[str, Any]) -> dict[str, Any]:
    """Return the stable per-finding operational report contract."""
    return {field: record.get(field) for field in (
        "raw_sha256", "agent_name", "agent_run_id", "source_url", "canonical_source_url",
        "source_published_at", "source_published_date", "event_date", "announcement_date", "effective_date",
        "facility_name", "facility", "organization", "location_name", "location", "city", "county", "state",
        "service_affected", "service_line", "event_type", "access_direction", "historical_outcome",
        "matched_event_id", "match_basis", "queue_action", "candidate_created", "review_status",
        "publication_eligible", "publication_approval", "exclusion_reason", "provenance_links",
    )}


def _clean_url(value: Any) -> str:
    return str(value or "").strip().lower().split("?")[0].rstrip("/")


def _normalized_headline(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _json_value(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _json_dicts(value: Any) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            objects.append(item)
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return objects


def food_line_match_targets(root: Path) -> dict[str, Any]:
    """Build exact Food Line public, intake, source, and historical indexes."""

    categorized_paths: dict[str, list[Path]] = {
        "editions": [
            root / "output" / "dispatches" / "food-line" / "editions",
            root / "output" / "site" / "food-line" / "editions",
        ],
        "intake": [
            root / "data" / "dispatches" / "food-line" / "agent-intake",
        ],
        "inbox": [
            root / "data" / "dispatches" / "food-line" / "agent-inbox",
        ],
        "source_ledgers": [
            root / "data" / "dispatches" / "food-line" / "sources",
            root / "data" / "dispatches" / "food-line" / "normalized",
            root / "data" / "dispatches" / "food-line" / "curated",
            root / "data" / "dispatches" / "food-line" / "editions",
            root / "data" / "dispatches" / "food-line" / "source_registry.json",
            root / "data" / "dispatches" / "food-line" / "pressure_source_registry.json",
            root / "data" / "records" / "sources.json",
        ],
    }

    def files_for(path: Path) -> list[Path]:
        if path.is_file():
            return [path]
        if not path.exists():
            return []
        return [
            candidate
            for candidate in path.rglob("*")
            if candidate.is_file()
            and candidate.suffix.lower() in {".csv", ".html", ".json", ".jsonl", ".md", ".txt"}
        ]

    categorized_urls: dict[str, dict[str, list[str]]] = {
        category: {} for category in categorized_paths
    }
    url_pattern = re.compile(r"https?://[^\s\"'<>]+", flags=re.I)
    url_fields = ("canonical_url", "canonical_source_url", "source_url", "url")
    for category, configured_paths in categorized_paths.items():
        for path in sorted(
            {candidate for configured in configured_paths for candidate in files_for(configured)}
        ):
            relative = str(path.relative_to(root))
            urls: set[str] = set()
            if path.suffix.lower() in {".json", ".jsonl"}:
                for item in _json_dicts(_json_value(path)):
                    for field in url_fields:
                        url = _clean_url(item.get(field))
                        if url:
                            urls.add(url)
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                text = ""
            urls.update(_clean_url(match) for match in url_pattern.findall(text))
            for url in sorted(url for url in urls if url):
                categorized_urls[category].setdefault(url, []).append(relative)

    historical: list[dict[str, str]] = []
    historical_root = root / "data" / "agent-history" / "food-line" / "normalized"
    if historical_root.exists():
        for path in sorted(historical_root.rglob("*.json")):
            relative = str(path.relative_to(root))
            for item in _json_dicts(_json_value(path)):
                finding_id = str(
                    item.get("finding_id")
                    or item.get("agent_finding_id")
                    or item.get("candidate_id")
                    or ""
                )
                duplicate_key = str(item.get("agent_duplicate_key") or "")
                url = _clean_url(
                    item.get("canonical_url")
                    or item.get("canonical_source_url")
                    or item.get("source_url")
                    or item.get("url")
                )
                published_date = str(
                    item.get("source_published_date")
                    or item.get("source_published_at")
                    or item.get("published_at")
                    or ""
                )[:10]
                if finding_id or duplicate_key or url:
                    historical.append(
                        {
                            "agent_duplicate_key": duplicate_key,
                            "finding_id": finding_id,
                            "path": relative,
                            "source_published_date": published_date,
                            "source_url": url,
                        }
                    )

    return {**categorized_urls, "historical": historical}


def gaza_match_targets(root: Path) -> dict[str, Any]:
    """Build Gaza's private edition, source, cluster, and historical identity indexes."""
    editions: dict[str, dict[str, str]] = {}
    publication_records: list[dict[str, str]] = []

    edition_records = root / "data" / "records" / "editions.json"
    for item in _json_dicts(_json_value(edition_records)):
        if str(item.get("dispatch_id") or "") != "dispatch-gaza" and str(item.get("dispatch_slug") or item.get("slug") or "") != "gaza":
            continue
        edition_date = str(item.get("edition_date") or "")[:10]
        status = str(item.get("status") or "").lower()
        if edition_date and (status == "public" or item.get("public_exposed") is True):
            editions[edition_date] = {
                "edition_id": str(item.get("edition_id") or f"gaza-{edition_date}"),
                "path": str(edition_records.relative_to(root)),
            }

    manifest_root = root / "output" / "dispatches" / "gaza" / "editions"
    if manifest_root.exists():
        for path in manifest_root.glob("*/edition_manifest.json"):
            value = _json_value(path)
            if not isinstance(value, dict) or str(value.get("dispatch_slug") or "") != "gaza":
                continue
            edition_date = str(value.get("edition_date") or path.parent.name)[:10]
            if edition_date and (value.get("public_exposed") is True or value.get("is_free_public") is True):
                editions.setdefault(edition_date, {
                    "edition_id": str(value.get("edition_id") or f"gaza-{edition_date}"),
                    "path": str(path.relative_to(root)),
                })

    run_root = root / "data" / "dispatches" / "gaza" / "editions"
    if run_root.exists():
        for path in run_root.glob("*/run_manifest.json"):
            value = _json_value(path)
            if isinstance(value, dict):
                publication_records.append({
                    "edition_date": str(value.get("edition_date") or path.parent.name)[:10],
                    "path": str(path.relative_to(root)),
                    "public_url": str(value.get("public_url") or ""),
                })

    source_paths: list[Path] = []
    source_patterns = (
        ("data/dispatches/gaza/sources", "**/*.json"),
        ("data/dispatches/gaza/raw", "**/raw_sources.json"),
        ("data/dispatches/gaza/normalized", "**/normalized_sources.json"),
        ("data/dispatches/gaza/curated", "**/curation_manifest.json"),
        ("output/dispatches/gaza/editions", "*/sources_manifest.json"),
    )
    for base_name, pattern in source_patterns:
        base = root / base_name
        if base.exists():
            source_paths.extend(path for path in base.glob(pattern) if ".template." not in path.name)
    shared_sources = root / "data" / "records" / "sources.json"
    if shared_sources.exists():
        source_paths.append(shared_sources)

    sources_by_url: dict[str, list[dict[str, Any]]] = {}
    sources_by_id: dict[str, list[dict[str, Any]]] = {}
    sources_by_composite: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(set(source_paths)):
        relative = str(path.relative_to(root))
        for item in _json_dicts(_json_value(path)):
            if path == shared_sources and str(item.get("dispatch_id") or "") != "dispatch-gaza" and not str(item.get("edition_id") or "").startswith("gaza-"):
                continue
            url = _clean_url(item.get("canonical_url") or item.get("canonical_source_url") or item.get("url") or item.get("source_url"))
            source_id = str(item.get("source_record_id") or item.get("source_id") or "")
            if not url and not source_id:
                continue
            edition_date = str(item.get("edition_date") or "")[:10]
            if not edition_date and str(item.get("edition_id") or "").startswith("gaza-"):
                edition_date = str(item.get("edition_id"))[5:15]
            if not edition_date and re.fullmatch(r"20\d{2}-\d{2}-\d{2}", path.parent.name):
                edition_date = path.parent.name
            story_ids = [str(value) for value in (item.get("used_in_story_ids") or item.get("source_record_ids") or [])]
            published = edition_date in editions and (
                bool(story_ids)
                or "output/dispatches/gaza/editions/" in relative.replace("\\", "/")
                or str(item.get("edition_id") or "").startswith("gaza-")
            )
            source = {
                "path": relative,
                "source_record_id": source_id,
                "url": url,
                "title": str(item.get("title") or ""),
                "publisher": str(item.get("publisher") or ""),
                "source_date": str(item.get("source_published_at") or item.get("published_at") or "")[:10],
                "edition_date": edition_date,
                "story_ids": story_ids,
                "published": published,
                "gaza_role": str(item.get("gaza_role") or item.get("story_scope") or item.get("region_scope") or ""),
                "source_role": str(item.get("source_role") or item.get("attribution_mode") or item.get("claim_status") or item.get("source_type") or ""),
            }
            if url:
                sources_by_url.setdefault(url, []).append(source)
            if source_id:
                sources_by_id.setdefault(source_id, []).append(source)
            composite = json.dumps({
                "title": _normalized_headline(source["title"]),
                "date": source["source_date"],
                "publisher": str(source["publisher"]).lower().strip(),
            }, sort_keys=True)
            if source["title"] and source["source_date"] and source["publisher"]:
                sources_by_composite.setdefault(composite, []).append(source)

    cluster_paths = [
        root / "data" / "records" / "story_memory.json",
        *sorted((root / "data" / "dispatches" / "gaza" / "editions").glob("*/dedupe_report.json")),
        *sorted((root / "output" / "dispatches" / "gaza" / "editions").glob("*/dedupe_report.json")),
        *sorted((root / "output" / "dispatches" / "gaza" / "editions").glob("*/curation_manifest.json")),
    ]
    clusters_by_id: dict[str, list[dict[str, Any]]] = {}
    clusters_by_url: dict[str, list[dict[str, Any]]] = {}
    clusters_by_composite: dict[str, list[dict[str, Any]]] = {}
    for path in cluster_paths:
        if not path.exists():
            continue
        relative = str(path.relative_to(root))
        for item in _json_dicts(_json_value(path)):
            if path.name == "story_memory.json" and str(item.get("dispatch_slug") or "") != "gaza":
                continue
            identifiers = {
                str(item.get(key) or "") for key in
                ("story_id", "event_cluster_id", "cluster_id", "topic_fingerprint", "normalized_event_key", "prior_story_matched")
                if item.get(key)
            }
            urls = {
                _clean_url(value) for value in
                [item.get("source_url"), item.get("canonical_url"), *(item.get("source_urls") or []), *(item.get("canonical_urls") or [])]
                if value
            }
            if not identifiers and not urls:
                continue
            cluster_id = str(
                item.get("event_cluster_id")
                or item.get("cluster_id")
                or item.get("story_id")
                or item.get("topic_fingerprint")
                or item.get("normalized_event_key")
                or item.get("prior_story_matched")
                or ""
            )
            cluster = {
                "path": relative,
                "cluster_id": cluster_id,
                "identifiers": sorted(identifiers),
                "edition_date": str(item.get("edition_date") or item.get("first_seen_date") or "")[:10],
                "title": str(item.get("title") or ""),
                "publisher": str((item.get("publisher_names") or [""])[0] if isinstance(item.get("publisher_names"), list) else item.get("publisher") or ""),
                "urls": sorted(urls),
            }
            for identifier in identifiers:
                clusters_by_id.setdefault(identifier, []).append(cluster)
            for url in urls:
                clusters_by_url.setdefault(url, []).append(cluster)
            composite = json.dumps({
                "title": _normalized_headline(cluster["title"]),
                "date": cluster["edition_date"],
                "publisher": str(cluster["publisher"]).lower().strip(),
            }, sort_keys=True)
            if cluster["title"] and cluster["edition_date"] and cluster["publisher"]:
                clusters_by_composite.setdefault(composite, []).append(cluster)

    for path, lineage in load_gaza_published_story_lineages(root):
        story_id = lineage["story_id"]
        source_records = lineage.get("source_records") or []
        urls = sorted(
            {
                _clean_url(source.get("canonical_url") or source.get("url"))
                for source in source_records
                if isinstance(source, dict)
                and (source.get("canonical_url") or source.get("url"))
            }
        )
        identifiers = [
            story_id,
            lineage["stable_event_identity"]["fingerprint"],
            lineage["prior_claim_identity"]["fingerprint"],
        ]
        cluster = {
            "path": str(path.relative_to(root)),
            "cluster_id": story_id,
            "identifiers": identifiers,
            "edition_date": lineage["edition_date"],
            "title": lineage["story_title"],
            "publisher": str(lineage.get("source_attribution") or ""),
            "urls": urls,
            "record_type": "private_published_story_lineage",
            "lineage_record": lineage,
        }
        for identifier in identifiers:
            clusters_by_id.setdefault(identifier, []).append(cluster)
        for url in urls:
            clusters_by_url.setdefault(url, []).append(cluster)
        composite = json.dumps(
            {
                "title": _normalized_headline(cluster["title"]),
                "date": cluster["edition_date"],
                "publisher": cluster["publisher"].lower().strip(),
            },
            sort_keys=True,
        )
        if cluster["title"] and cluster["edition_date"] and cluster["publisher"]:
            clusters_by_composite.setdefault(composite, []).append(cluster)

    historical_identities: set[str] = set()
    historical_root = root / "data" / "agent-history" / "gaza" / "normalized"
    if historical_root.exists():
        for path in historical_root.rglob("*.json"):
            for item in _json_dicts(_json_value(path)):
                if item.get("domain") not in (None, "", "gaza"):
                    continue
                identity = {
                    "url": _clean_url(item.get("canonical_source_url") or item.get("source_url") or item.get("url")),
                    "title": _normalized_headline(item.get("title") or item.get("headline")),
                    "date": str(item.get("source_published_at") or item.get("published_at") or item.get("event_date") or "")[:10],
                }
                if any(identity.values()):
                    historical_identities.add(json.dumps(identity, sort_keys=True))
    return {
        "editions": editions,
        "publication_records": publication_records,
        "sources_by_url": sources_by_url,
        "sources_by_id": sources_by_id,
        "sources_by_composite": sources_by_composite,
        "clusters_by_id": clusters_by_id,
        "clusters_by_url": clusters_by_url,
        "clusters_by_composite": clusters_by_composite,
        "historical_identities": historical_identities,
    }


def _gaza_identity(row: dict[str, Any]) -> str:
    return json.dumps({
        "url": _clean_url(row.get("canonical_source_url") or row.get("source_url") or row.get("url")),
        "title": _normalized_headline(row.get("title") or row.get("headline")),
        "date": str(row.get("source_published_at") or row.get("published_at") or row.get("event_date") or "")[:10],
    }, sort_keys=True)


def _gaza_report(record: dict[str, Any]) -> dict[str, Any]:
    return {field: record.get(field) for field in (
        "raw_sha256", "agent_name", "agent_run_id", "source_url", "canonical_source_url",
        "publisher", "source_published_at", "source_date", "event_date", "title",
        "gaza_role", "source_role", "matched_edition_date", "matched_source_or_cluster_id",
        "match_basis", "historical_outcome", "candidate_created", "provenance_only",
        "review_status", "publication_eligible", "publication_approval", "exclusion_reason",
        "ambiguity_reason", "provenance_links",
    )}


def _normalize_gaza_record(
    row: dict[str, Any],
    *,
    payload: Any,
    raw_sha256: str,
    targets: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    record = dict(row)
    source_url = _clean_url(row.get("canonical_source_url") or row.get("source_url") or row.get("url"))
    source_id = str(row.get("manual_source_identifier") or row.get("source_record_id") or row.get("source_id") or "")
    source_date = str(row.get("source_published_at") or row.get("published_at") or "")[:10]
    event_date = str(row.get("event_date") or "")[:10]
    title = str(row.get("title") or row.get("headline") or "")
    publisher = str(row.get("publisher") or "")
    edition_date = str(row.get("edition_date") or "")[:10]
    agent_name = str(payload.get("agent_name") or "") if isinstance(payload, dict) else ""
    agent_run_id = str(payload.get("agent_run_id") or "") if isinstance(payload, dict) else ""
    source_matches = list(targets["sources_by_url"].get(source_url, [])) if source_url else []
    if source_id:
        source_matches.extend(targets["sources_by_id"].get(source_id, []))
    if not source_matches and not source_url and not source_id and title and source_date and publisher:
        composite = json.dumps({"title": _normalized_headline(title), "date": source_date, "publisher": publisher.lower().strip()}, sort_keys=True)
        source_matches.extend(targets["sources_by_composite"].get(composite, []))
    source_matches = list({(item["path"], item["source_record_id"], item["url"]): item for item in source_matches}.values())

    cluster_identifiers = [
        str(row.get(key) or "") for key in
        ("event_cluster_id", "cluster_id", "story_id", "topic_fingerprint", "normalized_event_key")
        if row.get(key)
    ]
    cluster_matches: list[dict[str, Any]] = []
    for identifier in cluster_identifiers:
        cluster_matches.extend(targets["clusters_by_id"].get(identifier, []))
    if not cluster_matches and source_url:
        cluster_matches.extend(targets["clusters_by_url"].get(source_url, []))
    if not cluster_matches and not source_url and not cluster_identifiers and title and (source_date or event_date) and publisher:
        composite = json.dumps({"title": _normalized_headline(title), "date": source_date or event_date, "publisher": publisher.lower().strip()}, sort_keys=True)
        cluster_matches.extend(targets["clusters_by_composite"].get(composite, []))
    cluster_matches = list({(item["path"], item["cluster_id"]): item for item in cluster_matches}.values())

    historical_outcome = "new_historical_candidate"
    match_basis = "unmatched_traceable_finding"
    matched_edition_date = ""
    matched_id = ""
    candidate_created = True
    provenance_only = False
    review_status = "pending_review"
    exclusion_reason = ""
    ambiguity_reason = ""
    provenance_links: list[dict[str, str]] = []

    if _gaza_identity(row) in targets["historical_identities"]:
        historical_outcome, match_basis = "duplicate_historical", "historical_identity"
        candidate_created, review_status = False, "excluded"
    else:
        published_sources = [item for item in source_matches if item.get("published") and item.get("edition_date") in targets["editions"]]
        if published_sources:
            selected = sorted(published_sources, key=lambda item: (item["edition_date"], item["source_record_id"], item["path"]))[0]
            historical_outcome, match_basis = "matched_published_edition", "canonical_source_url_and_published_source"
            matched_edition_date = str(selected.get("edition_date") or "")
            matched_id = str(selected.get("source_record_id") or (selected.get("story_ids") or [""])[0])
            candidate_created, provenance_only, review_status = False, True, "excluded"
        elif source_matches:
            selected = sorted(source_matches, key=lambda item: (item["source_record_id"], item["path"]))[0]
            historical_outcome = "matched_existing_source"
            match_basis = "canonical_source_url" if source_url and selected.get("url") == source_url else ("manual_source_identifier" if source_id else "title_date_publisher")
            matched_edition_date = str(selected.get("edition_date") or "")
            matched_id = str(selected.get("source_record_id") or "")
            candidate_created, provenance_only, review_status = False, True, "excluded"
        elif cluster_matches:
            selected = sorted(cluster_matches, key=lambda item: (item["cluster_id"], item["path"]))[0]
            historical_outcome = "matched_existing_cluster"
            match_basis = "cluster_identifier" if cluster_identifiers else ("canonical_source_url" if source_url else "title_date_publisher")
            matched_edition_date = str(selected.get("edition_date") or "")
            matched_id = str(selected.get("cluster_id") or "")
            candidate_created, provenance_only, review_status = False, True, "excluded"
        else:
            context = " ".join(str(row.get(key) or "") for key in ("gaza_role", "story_scope", "region_scope", "location", "location_name")).lower()
            evidence = str(row.get("exact_supporting_passage") or row.get("evidence") or row.get("evidence_text") or "").strip()
            role_context = any(value in context for value in ("gaza_adjacent_context", "context_only", "archived_context"))
            west_bank_only = "west bank" in context and "gaza" not in context
            explicit_non_gaza = any(value in context for value in ("lebanon", "israel-only", "non-gaza")) and "gaza" not in context
            explicit_context = role_context or west_bank_only or explicit_non_gaza or row.get("is_gaza_relevant") is False
            if explicit_context:
                historical_outcome, match_basis = "archived_context", "non_gaza_or_west_bank_context"
                candidate_created, review_status = False, "historical_context"
                exclusion_reason = "traceable non-Gaza or West Bank-only material retained as historical context"
            elif not evidence:
                historical_outcome, match_basis = "archived_invalid", "missing_exact_evidence"
                candidate_created, review_status = False, "excluded"
                exclusion_reason = "missing exact supporting evidence"
            elif not source_url and not cluster_identifiers:
                historical_outcome, match_basis = "needs_manual_review", "missing_source_or_cluster_identity"
                candidate_created, review_status = False, "pending_review"
                ambiguity_reason = "finding lacks a canonical source URL or explicit cluster identity"

    for item in source_matches:
        provenance_links.append({
            "path": str(item.get("path") or ""),
            "source_record_id": str(item.get("source_record_id") or ""),
            "story_id": str((item.get("story_ids") or [""])[0]),
            "edition_date": str(item.get("edition_date") or ""),
        })
    for item in cluster_matches:
        provenance_links.append({
            "path": str(item.get("path") or ""),
            "source_record_id": "",
            "story_id": str(item.get("cluster_id") or ""),
            "edition_date": str(item.get("edition_date") or ""),
        })
    matched_source = source_matches[0] if source_matches else {}
    record.update({
        "domain": "gaza",
        "historical_backfill": True,
        "raw_sha256": raw_sha256,
        "agent_name": agent_name,
        "agent_run_id": agent_run_id,
        "source_url": str(row.get("source_url") or row.get("url") or ""),
        "canonical_source_url": str(row.get("canonical_source_url") or row.get("source_url") or row.get("url") or ""),
        "publisher": publisher or str(matched_source.get("publisher") or ""),
        "source_published_at": str(row.get("source_published_at") or row.get("published_at") or ""),
        "source_date": source_date,
        "event_date": str(row.get("event_date") or ""),
        "title": title,
        "gaza_role": str(row.get("gaza_role") or row.get("story_scope") or row.get("region_scope") or matched_source.get("gaza_role") or ""),
        "source_role": str(row.get("source_role") or row.get("attribution_mode") or row.get("claim_status") or row.get("source_type") or matched_source.get("source_role") or ""),
        "matched_edition_date": matched_edition_date,
        "matched_source_or_cluster_id": matched_id,
        "match_basis": match_basis,
        "historical_outcome": historical_outcome,
        "deduplication_outcome": historical_outcome,
        "candidate_created": candidate_created,
        "provenance_only": provenance_only,
        "review_status": review_status,
        "publication_eligible": False,
        "publication_approval": False,
        "exclusion_reason": exclusion_reason or None,
        "ambiguity_reason": ambiguity_reason or None,
        "provenance_links": provenance_links,
    })
    if historical_outcome in {"archived_context", "archived_invalid"}:
        record["archive_status"] = "archived"
    return record, historical_outcome


def normalize_records(root: Path, domain: str, payload: Any, *, raw_sha256: str, captured_at: str, correction: dict[str, Any] | None = None, normalization_metadata: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows = _rows(payload)
    if domain in {"care-line", "gaza", "ice"} and correction is not None:
        label = {"care-line": "Care Line", "gaza": "Gaza", "ice": "ICE"}[domain]
        if correction.get("raw_sha256") != raw_sha256:
            raise ValueError(f"{label} normalization sidecar raw_sha256 does not match the preserved alert")
        if correction.get("domain") != domain:
            raise ValueError(f"{label} normalization sidecar domain mismatch")
        if correction.get("normalization_type") != "prose_envelope_to_structured_findings":
            raise ValueError(f"unsupported {label} normalization sidecar type")
        if correction.get("approved") is not True or correction.get("approval_scope") != "historical_normalization_only":
            raise ValueError(f"{label} sidecar approval is not limited to historical normalization")
        if correction.get("publication_approval") is not False:
            raise ValueError(f"{label} normalization sidecar cannot grant publication approval")
        rows = [dict(row) for row in correction.get("findings", []) if isinstance(row, dict)]
        if not rows or len(rows) != len(correction.get("findings", [])):
            raise ValueError(f"{label} normalization sidecar findings must be a non-empty list of objects")
        if domain == "ice":
            finding_ids = [str(row.get("finding_id") or "").strip() for row in rows]
            identities = [ice_historical_identity(row) for row in rows]
            if any(not finding_id for finding_id in finding_ids):
                raise ValueError("ICE normalization sidecar findings require stable finding_id values")
            if len(set(finding_ids)) != len(finding_ids) or len(set(identities)) != len(identities):
                raise ValueError("ICE normalization sidecar contains conflicting findings")
    existing = _existing_text(root, domain)
    published_care = _care_published_ids(root) if domain == "care-line" else set()
    normalized: list[dict[str, Any]] = []
    outcomes: Counter[str] = Counter()
    if domain == "food-line":
        if isinstance(payload, dict) and "raw_text" in payload and "findings" not in payload:
            record = dict(payload); record.update({"domain": domain, "historical_backfill": True, "review_status": "pending_review", "raw_sha256": raw_sha256, "deduplication_outcome": "needs_manual_review"})
            if normalization_metadata: record.update(normalization_metadata)
            return [record], {"needs_manual_review": 1}
        working_payload = payload
        if correction:
            target_url = str(correction.get("source_url") or "").rstrip("/").lower()
            replacement = correction.get("replacement_exact_supporting_passage") or correction.get("supplemental_exact_supporting_passage")
            if not target_url or not isinstance(replacement, str) or not replacement.strip(): raise ValueError("correction requires source_url and exact supporting passage")
            working_payload = dict(payload) if isinstance(payload, dict) else payload
            if isinstance(working_payload, dict):
                working_payload["findings"] = [dict(row, exact_supporting_passage=replacement) if str(row.get("canonical_source_url") or row.get("source_url") or "").rstrip("/").lower() == target_url else row for row in _rows(payload)]
        findings = adapt_food_line_agent_output(working_payload, agent_name=str(payload.get("agent_name") if isinstance(payload, dict) else "historical-agent"), agent_run_id=str(payload.get("agent_run_id") if isinstance(payload, dict) else ""), discovered_at=captured_at)
        for finding in findings:
            candidate = map_finding_to_food_line_candidate(finding, edition_date=(finding.source_published_at[:10] if finding.source_published_at[:10] else captured_at[:10]))
            candidate.update({"historical_backfill": True, "review_status": "pending_review", "raw_sha256": raw_sha256})
            if normalization_metadata: candidate.update(normalization_metadata)
            if correction:
                candidate["evidence_correction_provenance"] = {
                    "schema_version": correction.get("schema_version", ""),
                    "raw_record_sha256": correction.get("raw_record_sha256", ""),
                    "source_url": correction.get("source_url", ""),
                    "reviewer": correction.get("reviewer", ""),
                    "reviewed_at": correction.get("reviewed_at"),
                    "approval_scope": correction.get("approval_scope", ""),
                    "publication_approval": correction.get("publication_approval", False),
                }
            key = finding.duplicate_key
            outcome = "duplicate_historical" if key and key in existing else ("invalid" if candidate.get("exclusion_reason") else "new_historical_candidate")
            candidate["deduplication_outcome"] = outcome
            candidate["historical_outcome"] = "archived_invalid" if outcome == "invalid" else outcome
            candidate["candidate_created"] = outcome in {"new_historical_candidate", "matched_existing"}
            candidate["publication_eligible"] = False if outcome == "invalid" else bool(candidate.get("eligible_for_review"))
            candidate["publication_approval"] = False
            if outcome == "invalid":
                candidate.update({"archive_status": "archived", "normalization_status": "completed_with_invalid_findings", "review_status": "excluded"})
            else:
                candidate.update({"archive_status": "archived", "normalization_status": "completed"})
            outcomes[outcome] += 1; normalized.append(candidate)
    elif domain == "gaza":
        targets = gaza_match_targets(root)
        for row in rows:
            record, outcome = _normalize_gaza_record(row, payload=payload, raw_sha256=raw_sha256, targets=targets)
            if normalization_metadata:
                record.update(normalization_metadata)
            if correction is not None:
                record["normalization_sidecar"] = {
                    "raw_sha256": correction.get("raw_sha256"),
                    "raw_file": correction.get("raw_file"),
                    "normalization_type": correction.get("normalization_type"),
                    "reviewer": correction.get("reviewer"),
                    "reviewed_at": correction.get("reviewed_at"),
                    "approved": correction.get("approved"),
                    "approval_scope": correction.get("approval_scope"),
                    "publication_approval": correction.get("publication_approval"),
                }
            outcomes[outcome] += 1
            normalized.append(record)
    elif domain == "ice":
        targets = ice_match_targets(root)
        explicit_raw_detection = (
            extract_detection_date(str(payload.get("raw_text") or ""))
            if isinstance(payload, dict) and isinstance(payload.get("raw_text"), str)
            else None
        )
        for row in rows:
            row = dict(row)
            if row.get("detection_date") in (None, "") and explicit_raw_detection:
                row["detection_date"] = explicit_raw_detection
            record, outcome = normalize_ice_record(row, payload=payload, raw_sha256=raw_sha256, targets=targets)
            record["captured_at"] = captured_at
            record.setdefault("imported_at", None)
            record.setdefault("last_normalized_at", None)
            if normalization_metadata:
                record.update(normalization_metadata)
            if correction is not None:
                record["normalization_sidecar"] = {
                    "raw_sha256": correction.get("raw_sha256"),
                    "raw_file": correction.get("raw_file"),
                    "normalization_type": correction.get("normalization_type"),
                    "reviewer": correction.get("reviewer"),
                    "reviewed_at": correction.get("reviewed_at"),
                    "approved": correction.get("approved"),
                    "approval_scope": correction.get("approval_scope"),
                    "publication_approval": correction.get("publication_approval"),
                }
            outcomes[outcome] += 1
            normalized.append(record)
    else:
        care_targets = care_line_match_targets(root) if domain == "care-line" else None
        for row in rows:
            source = str(row.get("canonical_source_url") or row.get("source_url") or row.get("url") or "")
            event_id = str(row.get("event_id") or row.get("id") or "")
            outcome = "matched_existing" if source and source.lower().split("?")[0].rstrip("/") in existing else "new_historical_candidate"
            if domain == "care-line" and event_id in published_care: outcome = "matched_existing"
            if not source and not event_id: outcome = "needs_manual_review"
            record = dict(row); record.update({"domain": domain, "historical_backfill": True, "review_status": "pending_review", "raw_sha256": raw_sha256, "deduplication_outcome": outcome})
            if domain == "care-line":
                for field in ("source_snapshot_refs", "evidence_review_refs", "reviewed_record_refs", "universal_event_ids"):
                    record.setdefault(field, [])
                assert care_targets is not None
                normalized_source = source.lower().split("?")[0].rstrip("/")
                source_matches = care_targets["sources"].get(normalized_source, [])
                matched_event_id = event_id if event_id in care_targets["published_events"] or event_id in care_targets["reviewed_events"] else ""
                match_basis = ""
                if event_id in care_targets["published_events"]:
                    historical_outcome, queue_action, match_basis = "matched_published_event", "provenance_only", "event_id"
                    record.update({"review_status": "excluded", "candidate_created": False, "publication_eligible": False})
                    matched_event_id = event_id
                elif event_id in care_targets["reviewed_events"] or event_id in care_targets["queue"]:
                    historical_outcome, queue_action, match_basis = "matched_reviewed_event", "none", "event_id"
                    record.update({"review_status": "pending_review" if event_id in care_targets["queue"] and event_id not in care_targets["reviewed_events"] else "excluded", "candidate_created": False, "publication_eligible": False})
                    matched_event_id = event_id
                elif source_matches:
                    matched_source_event = next((str(item.get("event_id")) for item in source_matches if item.get("event_id")), "")
                    if _care_identity(row) in care_targets["historical_identities"]:
                        historical_outcome, queue_action = "duplicate_historical", "none"
                    elif matched_source_event in care_targets["published_events"]:
                        historical_outcome, queue_action = "matched_published_event", "provenance_only"
                    else:
                        historical_outcome, queue_action = "matched_existing_source", "provenance_only"
                    match_basis = "canonical_source_url"
                    record.update({"review_status": "excluded", "candidate_created": False, "publication_eligible": False})
                    matched_event_id = matched_source_event
                elif _care_identity(row) in care_targets["historical_identities"]:
                    historical_outcome, queue_action, match_basis = "duplicate_historical", "none", "historical_identity"
                    record.update({"review_status": "excluded", "candidate_created": False, "publication_eligible": False})
                elif str(row.get("access_direction") or "").lower() == "access_expansion" or str(row.get("event_type") or "").lower() in {"planned_access_expansion", "service_expansion"}:
                    historical_outcome, queue_action, match_basis = "archived_context", "none", "access_expansion_not_loss_event"
                    record.update({"review_status": "historical_context", "candidate_created": False, "publication_eligible": False, "exclusion_reason": "access expansion retained as historical context; not a loss-event candidate"})
                elif not source and not event_id:
                    historical_outcome, queue_action, match_basis = "needs_manual_review", "none", "missing_identity"
                    record.update({"review_status": "pending_review", "candidate_created": False, "publication_eligible": False})
                elif not str(row.get("exact_supporting_passage") or row.get("evidence") or row.get("evidence_text") or "").strip():
                    historical_outcome, queue_action, match_basis = "archived_invalid", "none", "missing_exact_evidence"
                    record.update({"review_status": "excluded", "candidate_created": False, "publication_eligible": False, "archive_status": "archived", "normalization_status": "completed_with_invalid_findings", "exclusion_reason": "missing exact supporting evidence"})
                else:
                    historical_outcome, queue_action, match_basis = "new_historical_candidate", "historical_review_candidate", "unmatched_valid_finding"
                    record.update({"review_status": "pending_review", "candidate_created": True, "publication_eligible": False})
                record.update({
                    "historical_outcome": historical_outcome,
                    "matched_event_id": matched_event_id,
                    "match_basis": match_basis,
                    "queue_action": queue_action,
                    "provenance_links": [{"path": item["path"], "source_record_id": item.get("source_record_id", ""), "event_id": item.get("event_id", "")} for item in source_matches],
                    "agent_name": str(payload.get("agent_name") if isinstance(payload, dict) else "historical-agent"),
                    "agent_run_id": str(payload.get("agent_run_id") if isinstance(payload, dict) else ""),
                })
                if correction is not None:
                    record["normalization_sidecar"] = {
                        "raw_sha256": correction.get("raw_sha256"),
                        "normalization_type": correction.get("normalization_type"),
                        "reviewer": correction.get("reviewer"),
                        "reviewed_at": correction.get("reviewed_at"),
                        "approved": correction.get("approved"),
                        "approval_scope": correction.get("approval_scope"),
                        "publication_approval": correction.get("publication_approval"),
                    }
                outcome = historical_outcome
            outcomes[outcome] += 1; normalized.append(record)
    return normalized, dict(outcomes)


def build_inventory(root: Path) -> dict[str, Any]:
    inventory: dict[str, Any] = {"schema_version": "agent_history_index_v1", "generated_at": datetime.now(timezone.utc).isoformat(), "domains": {}}
    for domain in DOMAINS:
        base = archive_root(root, domain); raw_files = list((base / "raw").glob("*.json")) if (base / "raw").exists() else []; normalized_files = list((base / "normalized").rglob("*.json")) if (base / "normalized").exists() else []
        records = []
        for path in normalized_files:
            try: records.extend(json.loads(path.read_text(encoding="utf-8")).get("findings", []))
            except (OSError, ValueError, AttributeError): pass
        dates = [d for record in records for d in _date_values(record.get("source_published_at") or record.get("published_at") or record.get("event_date") or record.get("discovered_at"))]
        urls = {str(record.get("canonical_source_url") or record.get("source_url") or record.get("url")) for record in records if record.get("canonical_source_url") or record.get("source_url") or record.get("url")}
        outcomes = Counter(str(record.get("deduplication_outcome") or "needs_manual_review") for record in records)
        historical_outcomes = Counter(str(record.get("historical_outcome") or record.get("deduplication_outcome") or "needs_manual_review") for record in records)
        domain_inventory = {"raw_run_count": len(raw_files), "normalized_finding_count": len(records), "date_range": [min(dates), max(dates)] if dates else [], "unique_urls": len(urls), "duplicates": historical_outcomes.get("duplicate_historical", 0), "matched_existing_records": outcomes.get("matched_existing", 0), "unmatched_records": historical_outcomes.get("new_historical_candidate", 0), "invalid_records": outcomes.get("invalid", 0) + historical_outcomes.get("archived_invalid", 0), "historical_candidate_count": sum(1 for r in records if r.get("candidate_created") is True), "invalid_archived_count": historical_outcomes.get("archived_invalid", 0), "archived_context_count": historical_outcomes.get("archived_context", 0), "matched_published_event_count": historical_outcomes.get("matched_published_event", 0), "matched_published_edition_count": historical_outcomes.get("matched_published_edition", 0), "matched_reviewed_event_count": historical_outcomes.get("matched_reviewed_event", 0), "matched_existing_source_count": historical_outcomes.get("matched_existing_source", 0), "matched_existing_cluster_count": historical_outcomes.get("matched_existing_cluster", 0), "duplicate_historical_count": historical_outcomes.get("duplicate_historical", 0), "new_historical_candidate_count": historical_outcomes.get("new_historical_candidate", 0), "needs_manual_review_count": historical_outcomes.get("needs_manual_review", 0), "excluded_count": sum(1 for r in records if r.get("review_status") == "excluded"), "candidate_creation_count": sum(1 for r in records if r.get("candidate_created") is True), "publication_ready_count": sum(1 for r in records if r.get("publication_eligible") is True), "missing_dates": sum(1 for r in records if not _date_values(r.get("source_published_at") or r.get("published_at") or r.get("event_date"))), "missing_evidence": sum(1 for r in records if not str(r.get("exact_supporting_passage") or r.get("evidence") or r.get("evidence_text") or r.get("summary") or "").strip()), "pending_review_count": sum(1 for r in records if r.get("review_status") == "pending_review")}
        domain_inventory.update({
            "pending_substantive_review": sum(
                1
                for record in records
                if (
                    record.get("historical_outcome")
                    or record.get("deduplication_outcome")
                )
                == "new_historical_candidate"
                and record.get("review_status") == "pending_review"
            ),
            "queue_entries": sum(
                1
                for record in records
                if record.get("queue_action")
                not in {
                    None,
                    "",
                    "none",
                    "provenance_only",
                    "historical_review_candidate",
                }
            ),
            "substantively_reviewed": sum(
                1
                for record in records
                if record.get("review_status") == "substantively_reviewed"
            ),
        })
        if domain == "ice":
            domain_inventory.update(ice_aggregate_metrics(records, raw_runs=len(raw_files)))
        inventory["domains"][domain] = domain_inventory
    return inventory
