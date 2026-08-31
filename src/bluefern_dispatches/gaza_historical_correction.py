"""Approval-gated packaging for formal Gaza historical corrections.

This module cannot publish, mutate Pages, or reuse the daily historical
publisher. It derives non-authorizing previews, creates commit-ready package
approval artifacts from those validator-owned outputs, and can atomically stage
an approved set outside both Git repositories for a later release step.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from xml.etree import ElementTree

from .historical_agent_archive import validate_gaza_published_story_lineage


PROPOSAL_SCHEMA = "gaza_formal_historical_correction_proposal_v1"
APPROVAL_SCHEMA = "gaza_formal_historical_correction_release_approval_v1"
APPROVAL_REQUEST_SCHEMA = "gaza_formal_historical_correction_approval_request_v1"
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

# The MP3 is intentionally absent from preapproval preview output. Approval
# binds a deterministic script/configuration request. A rendered binary can be
# supplied only after that approval and is then bound into the private staged
# package for a later publication review.
PREVIEW_PATHS = {
    role: path for role, path in REPLACEMENT_PATHS.items() if role != "correction_audio"
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


_UTF8_BOM = b"\xef\xbb\xbf"


@dataclass(frozen=True)
class _TextArtifact:
    text: str
    bom: bytes
    newline: str
    final_newline: bool

    def encode(self, text: str, label: str) -> bytes:
        detected = _text_newline_state(text, label)
        if detected[0] is not None and detected[0] != self.newline:
            raise CorrectionValidationError(f"{label} changed newline convention")
        if detected[1] != self.final_newline:
            raise CorrectionValidationError(f"{label} changed final-newline state")
        try:
            return self.bom + text.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise CorrectionValidationError(f"{label} is not encodable as UTF-8") from exc


def _text_newline_state(text: str, label: str) -> tuple[str | None, bool]:
    without_crlf = text.replace("\r\n", "")
    if "\r" in without_crlf:
        raise CorrectionValidationError(f"{label} has unsupported bare-CR newlines")
    has_crlf = "\r\n" in text
    has_lf = "\n" in without_crlf
    if has_crlf and has_lf:
        raise CorrectionValidationError(f"{label} has ambiguous mixed newlines")
    newline = "\r\n" if has_crlf else "\n" if has_lf else None
    final_newline = text.endswith("\r\n" if has_crlf else "\n") if newline else False
    return newline, final_newline


def _read_text_artifact(path: Path, label: str) -> _TextArtifact:
    raw = path.read_bytes()
    bom = _UTF8_BOM if raw.startswith(_UTF8_BOM) else b""
    body = raw[len(bom):]
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CorrectionValidationError(f"{label} uses an unsupported non-UTF-8 encoding") from exc
    newline, final_newline = _text_newline_state(text, label)
    return _TextArtifact(
        text=text,
        bom=bom,
        newline=newline or "\n",
        final_newline=final_newline,
    )


def _json_document_like(value: Any, artifact: _TextArtifact, label: str) -> bytes:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if artifact.newline != "\n":
        text = text.replace("\n", artifact.newline)
    if not artifact.final_newline:
        text = text[:-len(artifact.newline)]
    return artifact.encode(text, label)


def _load_json_artifact(path: Path, label: str) -> tuple[Any, _TextArtifact]:
    artifact = _read_text_artifact(path, label)
    try:
        return json.loads(artifact.text), artifact
    except json.JSONDecodeError as exc:
        raise CorrectionValidationError(f"{label} is not readable canonical JSON: {exc}") from exc


_VOID_HTML_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}


@dataclass
class _HtmlNode:
    tag: str
    attrs: dict[str, str | None]
    start: int
    start_tag_end: int
    parent: "_HtmlNode | None"
    end_tag_start: int | None = None
    end: int | None = None
    children: list["_HtmlNode"] = field(default_factory=list)
    text_parts: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(self.text_parts)


class _StrictHtmlTreeParser(HTMLParser):
    """Record exact element spans while rejecting malformed target HTML."""

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=True)
        self.source = source
        self.line_starts = [0]
        self.line_starts.extend(index + 1 for index, char in enumerate(source) if char == "\n")
        self.roots: list[_HtmlNode] = []
        self.nodes: list[_HtmlNode] = []
        self.stack: list[_HtmlNode] = []

    def _offset(self) -> int:
        line, column = self.getpos()
        return self.line_starts[line - 1] + column

    def _tag_end(self, start: int, raw_tag: str | None = None) -> int:
        if raw_tag:
            return start + len(raw_tag)
        end = self.source.find(">", start)
        if end < 0:
            raise CorrectionValidationError("edition HTML contains an unterminated tag")
        return end + 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        start = self._offset()
        names = [name.lower() for name, _ in attrs]
        if len(names) != len(set(names)):
            raise CorrectionValidationError(
                f"edition HTML has duplicate attributes on tag: {tag.lower()}"
            )
        parent = self.stack[-1] if self.stack else None
        node = _HtmlNode(
            tag.lower(), dict(attrs), start,
            self._tag_end(start, self.get_starttag_text()), parent,
        )
        (parent.children if parent else self.roots).append(node)
        self.nodes.append(node)
        if node.tag in _VOID_HTML_TAGS:
            node.end_tag_start = node.start_tag_end
            node.end = node.start_tag_end
        else:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if self.stack and self.stack[-1].tag == tag.lower():
            node = self.stack.pop()
            node.end_tag_start = node.start_tag_end
            node.end = node.start_tag_end

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if not self.stack or self.stack[-1].tag != normalized:
            raise CorrectionValidationError(
                f"edition HTML has a mismatched closing tag: {normalized}"
            )
        node = self.stack.pop()
        node.end_tag_start = self._offset()
        node.end = self._tag_end(node.end_tag_start)

    def handle_data(self, data: str) -> None:
        for node in self.stack:
            node.text_parts.append(data)

    def finish(self) -> list[_HtmlNode]:
        try:
            self.feed(self.source)
            self.close()
        except CorrectionValidationError:
            raise
        except Exception as exc:
            raise CorrectionValidationError(f"edition HTML is malformed: {exc}") from exc
        if self.stack:
            raise CorrectionValidationError(
                f"edition HTML has an unclosed tag: {self.stack[-1].tag}"
            )
        return self.nodes


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


def _json_document(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _public_correction_record(correction: dict[str, Any]) -> dict[str, Any]:
    return {
        key: correction[key]
        for key in (
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
        )
    }


@dataclass(frozen=True)
class _ReaderCorrectionCopy:
    heading: str
    notice: str
    story_update: str
    today_update: str
    audio_script: str
    feed_description: str
    source_labels: dict[str, str]


def _natural_date(value: Any, label: str, *, include_year: bool = True) -> str:
    try:
        parsed = datetime.strptime(str(value), "%Y-%m-%d")
    except ValueError as exc:
        raise CorrectionValidationError(f"{label} is not a valid calendar date") from exc
    rendered = f"{parsed.strftime('%B')} {parsed.day}"
    return f"{rendered}, {parsed.year}" if include_year else rendered


def _number_word(value: int) -> str:
    words = (
        "zero", "one", "two", "three", "four", "five", "six", "seven",
        "eight", "nine", "ten", "eleven", "twelve",
    )
    return words[value] if 0 <= value < len(words) else str(value)


def _casualty_phrase(value: int, outcome: str) -> str:
    subject = "person" if value == 1 else "people"
    verb = "was" if value == 1 else "were"
    return f"{_number_word(value)} {subject} {verb} {outcome}"


def _casualty_count_key(correction: dict[str, Any]) -> str:
    field_name = str((correction.get("casualty_change") or {}).get("field") or "")
    prefix = "casualty_counts."
    if not field_name.startswith(prefix) or not field_name[len(prefix):]:
        raise CorrectionValidationError("correction casualty field is not a bounded story count")
    return field_name[len(prefix):]


def _evidence_source_label(item: dict[str, Any]) -> str:
    passage = str(item.get("supporting_passage") or "").strip()
    match = re.match(
        r"^(.+?)(?:'s|\s+(?:reported|described|said|stated|documented)\b)",
        passage,
        flags=re.IGNORECASE,
    )
    if match:
        return match.group(1).strip()
    host = (urlsplit(str(item.get("url") or "")).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        raise CorrectionValidationError("correction evidence lacks a reader-facing source label")
    return host


def _join_names(values: list[str]) -> str:
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} and {values[1]}"
    return ", ".join(values[:-1]) + f", and {values[-1]}"


def _reader_correction_copy(correction: dict[str, Any]) -> _ReaderCorrectionCopy:
    prior = str(correction["prior_claim"]).strip()
    prior_without_period = prior[:-1] if prior.endswith(".") else prior
    prior_match = re.fullmatch(
        r"(?P<source>.+?)\s+reported\s+(?P<deaths>\d+)\s+killed\s+and\s+"
        r"(?P<injuries>\d+)\s+injured(?P<context>.*)",
        prior_without_period,
        flags=re.IGNORECASE,
    )
    if not prior_match:
        raise CorrectionValidationError(
            "correction prior claim cannot be rendered as bounded reader-facing casualty copy"
        )
    prior_deaths = int(prior_match.group("deaths"))
    prior_injuries = int(prior_match.group("injuries"))
    change = correction.get("casualty_change") or {}
    if prior_deaths != change.get("previous_value"):
        raise CorrectionValidationError("reader-facing prior death count differs from correction model")
    corrected_deaths = change.get("corrected_value")
    if not isinstance(corrected_deaths, int):
        raise CorrectionValidationError("reader-facing corrected death count is not an integer")
    context = prior_match.group("context").strip()
    context = f" {context}" if context else ""

    evidence = correction.get("evidence_references")
    reports = (correction.get("injury_disagreement") or {}).get("reports")
    if not isinstance(evidence, list) or not evidence or not isinstance(reports, list) or len(reports) < 2:
        raise CorrectionValidationError("reader-facing correction lacks disputed source evidence")
    labels_by_url = {
        str(item.get("url") or ""): _evidence_source_label(item)
        for item in evidence
        if isinstance(item, dict)
    }
    labeled_reports: list[tuple[str, int]] = []
    for report in reports:
        if not isinstance(report, dict) or not isinstance(report.get("value"), int):
            raise CorrectionValidationError("reader-facing injury report is incomplete")
        url = str(report.get("source_url") or "")
        label = labels_by_url.get(url)
        if not label:
            raise CorrectionValidationError("injury report lacks matching attributed evidence")
        labeled_reports.append((label, report["value"]))
    if len({label for label, _ in labeled_reports}) != len(labeled_reports):
        raise CorrectionValidationError("reader-facing injury sources are ambiguous")
    correcting_sources = _join_names([label for label, _ in labeled_reports])
    injury_parts = []
    for index, (label, value) in enumerate(labeled_reports):
        count = _number_word(value)
        detail = f"{count} {'person' if value == 1 else 'people'} injured" if index == 0 else count
        injury_parts.append(f"{label} reported {detail}")
    injury_reports = (
        f"{injury_parts[0]}, while {injury_parts[1]}"
        if len(injury_parts) == 2
        else "; ".join(injury_parts[:-1]) + f"; and {injury_parts[-1]}"
    )
    prior_source = prior_match.group("source").strip()
    edition_date = _natural_date(correction["owning_edition_date"], "owning edition", include_year=False)
    correction_date = _natural_date(correction["correction_date"], "correction date")
    prior_fact = (
        f"{_casualty_phrase(prior_deaths, 'killed')} and "
        f"{_number_word(prior_injuries)} {'person was' if prior_injuries == 1 else 'were'} injured"
        f"{context}"
    )
    corrected_fact = f"{_casualty_phrase(corrected_deaths, 'killed')}{context}"
    story_update = (
        f"Reporting from {correcting_sources} said {corrected_fact}. "
        f"{injury_reports}; the injury count remains unresolved."
    )
    notice = (
        f"The {edition_date} Gaza dispatch originally reported, citing {prior_source}, "
        f"that {prior_fact}. {story_update} "
        "The story has been updated to reflect the revised death toll and the differing injury reports."
    )
    audio_script = (
        f"A correction to our {edition_date} Gaza dispatch: The original story, citing "
        f"{prior_source}, reported that {prior_fact}. Reporting from {correcting_sources} "
        f"said {_casualty_phrase(corrected_deaths, 'killed')}. {injury_reports}, so the injury "
        "count remains unresolved."
    )
    heading = f"Correction — {correction_date}"
    return _ReaderCorrectionCopy(
        heading=heading,
        notice=notice,
        story_update=story_update,
        today_update=f"Corrected: {story_update}",
        audio_script=audio_script,
        feed_description=f"{heading}. {notice}",
        source_labels=labels_by_url,
    )


def _correction_script(correction: dict[str, Any]) -> str:
    return _reader_correction_copy(correction).audio_script


def _correction_notice_html(correction: dict[str, Any]) -> str:
    record = _public_correction_record(correction)
    reader = _reader_correction_copy(correction)
    links = "".join(
        f'<li><a href="{html.escape(item["url"], quote=True)}">'
        f'{html.escape(reader.source_labels[item["url"]], quote=False)}</a>: '
        f'{html.escape(item["supporting_passage"], quote=False)}</li>'
        for item in record["evidence_references"]
    )
    return (
        f'<section class="formal-correction" id="correction-{record["correction_id"]}">'
        f'<h2>{html.escape(reader.heading, quote=False)}</h2>'
        f'<p>{html.escape(reader.notice, quote=False)}</p>'
        "<p><strong>Sources</strong></p>"
        f'<ul>{links}</ul></section>'
    )


def _append_before(text: str, marker: str, addition: str, label: str) -> str:
    if text.count(marker) != 1:
        raise CorrectionValidationError(f"{label} lacks one unambiguous insertion boundary")
    return text.replace(marker, addition + marker, 1)


def _normalized_html_text(node: _HtmlNode) -> str:
    return " ".join(node.text.split())


def _is_descendant(node: _HtmlNode, ancestor: _HtmlNode) -> bool:
    current: _HtmlNode | None = node
    while current is not None:
        if current is ancestor:
            return True
        current = current.parent
    return False


def _descendants(nodes: list[_HtmlNode], ancestor: _HtmlNode, tag: str) -> list[_HtmlNode]:
    return [node for node in nodes if node.tag == tag and _is_descendant(node, ancestor)]


def _apply_html_replacements(
    source: str, replacements: list[tuple[int, int, str]]
) -> str:
    result = source
    previous_start = len(source) + 1
    for start, end, replacement in sorted(replacements, reverse=True):
        if start < 0 or end < start or end > len(source) or end > previous_start:
            raise CorrectionValidationError("edition HTML correction ranges overlap or are invalid")
        result = result[:start] + replacement + result[end:]
        previous_start = start
    return result


def _render_story_scoped_edition_html(
    source: str,
    *,
    correction: dict[str, Any],
    curation: list[Any],
    sources: list[Any],
) -> str:
    """Correct only the manifest-owned Today summary and full story article.

    Legacy Gaza HTML did not emit story IDs. For that shape, ownership is
    established by the stable story's unique curation ordinal (the generator's
    first-three Today projection) and its source-manifest URL set. The corrected
    preview emits an explicit story anchor and Today link for future validation.
    """

    nodes = _StrictHtmlTreeParser(source).finish()
    story_id = str(correction["story_id"])
    prior_claim = str(correction["prior_claim"])
    reader = _reader_correction_copy(correction)

    story_matches = [
        (index, row)
        for index, row in enumerate(curation)
        if isinstance(row, dict) and row.get("story_id") == story_id
    ]
    if len(story_matches) != 1:
        raise CorrectionValidationError(
            "edition HTML target does not resolve to one curation story"
        )
    story_ordinal, story = story_matches[0]
    if story_ordinal >= 3:
        raise CorrectionValidationError(
            "edition HTML target is outside the generator's Today’s Read projection"
        )

    today_headings = [
        node for node in nodes
        if node.tag == "h2" and _normalized_html_text(node) == "Today’s Read"
    ]
    glance_headings = [
        node for node in nodes
        if node.tag == "h2" and _normalized_html_text(node) == "At A Glance"
    ]
    if len(today_headings) != 1 or len(glance_headings) != 1:
        raise CorrectionValidationError(
            "edition HTML must contain one Today’s Read and one At A Glance boundary"
        )
    today_heading = today_headings[0]
    glance_heading = glance_headings[0]
    if today_heading.parent is None or today_heading.parent is not glance_heading.parent:
        raise CorrectionValidationError("edition HTML summary boundaries do not share an owner")
    siblings = today_heading.parent.children
    today_index = siblings.index(today_heading)
    glance_index = siblings.index(glance_heading)
    if glance_index <= today_index:
        raise CorrectionValidationError("edition HTML summary boundaries are reordered")
    today_paragraphs = [
        node for node in siblings[today_index + 1:glance_index] if node.tag == "p"
    ]
    # The first paragraph is the generated lead; the next three map to the
    # first three curation rows in stable order.
    target_summary_index = story_ordinal + 1
    if target_summary_index >= len(today_paragraphs):
        raise CorrectionValidationError("edition HTML lacks the target Today’s Read summary")
    today_paragraph = today_paragraphs[target_summary_index]
    if today_paragraph.text.count(prior_claim) != 1:
        raise CorrectionValidationError(
            "target Today’s Read summary does not contain the exact prior claim once"
        )
    today_links = _descendants(nodes, today_paragraph, "a")
    expected_anchor = f"#{story_id}"
    if today_links and (
        len(today_links) != 1 or today_links[0].attrs.get("href") != expected_anchor
    ):
        raise CorrectionValidationError("target Today’s Read summary links to another story")

    source_record_ids = {
        str(value) for value in story.get("source_record_ids", []) if str(value)
    }
    expected_urls = {
        str(value) for value in story.get("source_urls", []) if str(value)
    }
    matched_source_records = [
        row for row in sources
        if isinstance(row, dict) and str(row.get("source_record_id", "")) in source_record_ids
    ]
    if len(matched_source_records) != len(source_record_ids) or not source_record_ids:
        raise CorrectionValidationError(
            "edition HTML target source records do not resolve exactly in the source manifest"
        )
    for row in matched_source_records:
        for field_name in ("url", "canonical_url", "resolved_canonical_url"):
            value = str(row.get(field_name) or "")
            if value:
                expected_urls.add(value)
    if not expected_urls:
        raise CorrectionValidationError("edition HTML target has no manifest-backed source URL")

    articles = [node for node in nodes if node.tag == "article"]
    article_matches: list[_HtmlNode] = []
    for article in articles:
        hrefs = {
            str(link.attrs.get("href") or "")
            for link in _descendants(nodes, article, "a")
        }
        if hrefs & expected_urls:
            article_matches.append(article)
    if len(article_matches) != 1:
        raise CorrectionValidationError(
            "edition HTML target article does not resolve exactly by manifest source URL"
        )
    article = article_matches[0]
    article_id = article.attrs.get("id")
    if article_id not in (None, story_id):
        raise CorrectionValidationError("edition HTML target article anchor changed")
    headings = _descendants(nodes, article, "h3")
    if len(headings) != 1 or _normalized_html_text(headings[0]) != str(story.get("title") or ""):
        raise CorrectionValidationError("edition HTML target article title differs from curation")
    try:
        owning_date = datetime.strptime(
            str(correction["owning_edition_date"]), "%Y-%m-%d"
        )
    except ValueError as exc:
        raise CorrectionValidationError("edition HTML target owning date is invalid") from exc
    rendered_date = f"{owning_date.strftime('%B')} {owning_date.day}, {owning_date.year}"
    metadata = _descendants(nodes, article, "em")
    if len(metadata) != 1 or rendered_date not in _normalized_html_text(metadata[0]):
        raise CorrectionValidationError("edition HTML target article owning date changed")

    body_paragraphs = [
        node for node in _descendants(nodes, article, "p")
        if node.text.count(prior_claim)
    ]
    if len(body_paragraphs) != 1 or body_paragraphs[0].text.count(prior_claim) != 1:
        raise CorrectionValidationError(
            "target article body does not contain the exact prior claim once"
        )
    body_paragraph = body_paragraphs[0]

    # Count only the smallest owning elements so nested link text is not
    # double-counted. Every occurrence must belong to one of the two selected
    # story surfaces; a third or unrelated occurrence fails closed.
    claim_nodes = [node for node in nodes if prior_claim in node.text]
    leaf_claim_nodes = [
        node for node in claim_nodes
        if not any(prior_claim in child.text for child in node.children)
    ]
    occurrence_count = sum(node.text.count(prior_claim) for node in leaf_claim_nodes)
    if occurrence_count != 2 or any(
        not _is_descendant(node, today_paragraph)
        and not _is_descendant(node, body_paragraph)
        for node in leaf_claim_nodes
    ):
        raise CorrectionValidationError(
            "edition HTML prior claim has an unrelated or ambiguous occurrence"
        )

    escaped_today = html.escape(reader.today_update, quote=False)
    escaped_story = html.escape(reader.story_update, quote=False)
    replacements = [
        (
            today_paragraph.start,
            today_paragraph.end or today_paragraph.start,
            f'<p><a href="{html.escape(expected_anchor, quote=True)}">{escaped_today}</a></p>',
        ),
        (
            body_paragraph.start,
            body_paragraph.end or body_paragraph.start,
            f"<p>{escaped_story}</p>",
        ),
        (
            article.end_tag_start or article.start_tag_end,
            article.end_tag_start or article.start_tag_end,
            _correction_notice_html(correction),
        ),
    ]
    if article_id is None:
        start_tag = source[article.start:article.start_tag_end]
        replacements.append(
            (
                article.start,
                article.start_tag_end,
                start_tag[:-1] + f' id="{html.escape(story_id, quote=True)}">',
            )
        )
    return _apply_html_replacements(source, replacements)


def _render_preview_payloads(
    pages_root: Path,
    correction: dict[str, Any],
    audio_request: dict[str, Any],
) -> dict[str, bytes]:
    record = _public_correction_record(correction)
    reader = _reader_correction_copy(correction)
    payloads: dict[str, bytes] = {}
    date = correction["owning_edition_date"]

    curation_path = _repo_path(pages_root, f"gaza/editions/{date}/curation_manifest.json", "curation")
    curation, curation_artifact = _load_json_artifact(curation_path, "curation manifest")
    if not isinstance(curation, list):
        raise CorrectionValidationError("curation manifest must remain a story list")
    matches = [row for row in curation if isinstance(row, dict) and row.get("story_id") == correction["story_id"]]
    if len(matches) != 1:
        raise CorrectionValidationError("prior public story does not resolve exactly once in curation")
    story = matches[0]
    if story.get("summary") != correction["prior_claim"]:
        raise CorrectionValidationError("current curation prior claim differs from lineage")
    history = story.get("correction_history")
    if history not in (None, []):
        raise CorrectionValidationError("current curation already contains correction history")
    sources = _load_json(
        _repo_path(pages_root, f"gaza/editions/{date}/sources_manifest.json", "sources"),
        "sources manifest",
    )
    if not isinstance(sources, list):
        raise CorrectionValidationError("sources manifest must remain a source list")

    notice = _correction_notice_html(correction)
    edition_html_path = _repo_path(pages_root, f"gaza/editions/{date}/index.html", "edition HTML")
    edition_artifact = _read_text_artifact(edition_html_path, "edition HTML")
    edition_html = _render_story_scoped_edition_html(
        edition_artifact.text,
        correction=correction,
        curation=curation,
        sources=sources,
    )
    payloads["edition_html"] = edition_artifact.encode(edition_html, "edition HTML")

    story["summary"] = reader.story_update
    casualty_change = correction["casualty_change"]
    story["casualty_counts"] = {
        _casualty_count_key(correction): casualty_change["corrected_value"]
    }
    story["event_fingerprint"] = correction["stable_event_fingerprint"]
    story["injury_disagreement"] = correction["injury_disagreement"]
    story["correction_history"] = [record]
    payloads["curation_manifest"] = _json_document_like(
        curation, curation_artifact, "curation manifest"
    )

    dedupe, dedupe_artifact = _load_json_artifact(
        _repo_path(pages_root, f"gaza/editions/{date}/dedupe_report.json", "dedupe"),
        "dedupe report",
    )
    if not isinstance(dedupe, dict) or dedupe.get("corrections") not in (None, []):
        raise CorrectionValidationError("dedupe report already has correction state")
    dedupe["corrections"] = [record]
    payloads["dedupe_report"] = _json_document_like(
        dedupe, dedupe_artifact, "dedupe report"
    )

    edition, edition_artifact = _load_json_artifact(
        _repo_path(pages_root, f"gaza/editions/{date}/edition_manifest.json", "edition manifest"),
        "edition manifest",
    )
    if not isinstance(edition, dict) or edition.get("corrections") not in (None, []):
        raise CorrectionValidationError("edition manifest already has correction state")
    edition_record = {**record, "aggregates_recomputed_from_corrected_story_versions": True}
    edition["corrections"] = [edition_record]
    payloads["edition_manifest"] = _json_document_like(
        edition, edition_artifact, "edition manifest"
    )
    payloads["correction_manifest"] = _json_document(record)

    prior_audio, prior_audio_artifact = _load_json_artifact(
        _repo_path(pages_root, f"gaza/audio/{date}.json", "prior audio metadata"),
        "prior audio metadata",
    )
    if not isinstance(prior_audio, dict):
        raise CorrectionValidationError("prior audio metadata must be an object")
    prior_audio.update(
        {
            "audio_status": "superseded_by_formal_correction",
            "superseded_by_correction_id": correction["correction_id"],
            "replacement_audio_url": f"/gaza/audio/corrections/{correction['correction_id']}.mp3",
        }
    )
    payloads["original_audio_metadata"] = _json_document_like(
        prior_audio, prior_audio_artifact, "prior audio metadata"
    )
    payloads["correction_audio_metadata"] = _json_document(
        {
            "audio_status": "formal_correction",
            "render_status": "pending_approved_render",
            "rendered_audio_sha256": None,
            "correction_id": correction["correction_id"],
            "story_id": correction["story_id"],
            "owning_edition_date": correction["owning_edition_date"],
            "correction_date": correction["correction_date"],
            "script_text": audio_request["script_text"],
            "script_sha256": audio_request["script_sha256"],
            "tts_provider": audio_request["tts_provider"],
            "tts_model": audio_request["tts_model"],
            "tts_voice": audio_request["tts_voice"],
            "injury_disagreement": correction["injury_disagreement"],
        }
    )

    prior_transcript_path = _repo_path(pages_root, f"gaza/audio/{date}-transcript.html", "prior transcript")
    prior_transcript = _read_text_artifact(prior_transcript_path, "prior transcript")
    payloads["original_transcript"] = prior_transcript.encode(
        _append_before(prior_transcript.text, "</main>", notice, "prior transcript"),
        "prior transcript",
    )
    payloads["correction_transcript"] = (
        "<!doctype html><html><body><main>" + notice +
        f"<p>{html.escape(audio_request['script_text'], quote=False)}</p></main></body></html>"
    ).encode("utf-8")
    payloads["correction_page"] = (
        "<!doctype html><html><body><main>" + notice + "</main></body></html>"
    ).encode("utf-8")

    description = reader.feed_description
    link = f"https://dispatches.thebluefernco.com/gaza/corrections/{correction['correction_id']}/"
    rss_item = (
        f"<item><title>{html.escape(reader.heading, quote=False)}</title>"
        f"<link>{html.escape(link)}</link><guid isPermaLink=\"false\">{correction['correction_id']}</guid>"
        f"<description>{html.escape(description, quote=False)}</description></item>"
    )
    podcast_item = (
        f"<item><title>{html.escape(reader.heading, quote=False)}</title>"
        f"<link>{html.escape(link)}</link><guid isPermaLink=\"false\">{correction['correction_id']}</guid>"
        f"<description>{html.escape(description, quote=False)}</description>"
        f"<enclosure url=\"https://dispatches.thebluefernco.com/gaza/audio/corrections/{correction['correction_id']}.mp3\" "
        "type=\"audio/mpeg\" length=\"0\" /></item>"
    )
    for role, relative, item in (
        ("rss", "gaza/rss.xml", rss_item),
        ("podcast", "gaza/podcast.xml", podcast_item),
        ("audio_podcast", "gaza/audio/podcast.xml", podcast_item),
    ):
        current = _read_text_artifact(_repo_path(pages_root, relative, role), role)
        payloads[role] = current.encode(
            _append_before(current.text, "</channel>", item, role), role
        )

    flash_path = _repo_path(pages_root, "gaza/flash-briefing.json", "flash briefing")
    _, flash_artifact = _load_json_artifact(flash_path, "flash briefing")
    payloads["flash_briefing"] = _json_document_like(
        [
            {
                "uid": correction["correction_id"],
                "updateDate": correction["correction_date"],
                "titleText": reader.heading,
                "mainText": audio_request["script_text"],
                "redirectionUrl": link,
            }
        ],
        flash_artifact,
        "flash briefing",
    )
    for role, relative in (
        ("audio_index", "gaza/audio/index.html"),
        ("gaza_index", "gaza/index.html"),
        ("gaza_archive", "gaza/archive.html"),
        ("root_index", "index.html"),
    ):
        current = _read_text_artifact(_repo_path(pages_root, relative, role), role)
        if "</main>" in current.text:
            marker = "</main>"
        elif "</body>" in current.text:
            marker = "</body>"
        else:
            raise CorrectionValidationError(f"{role} lacks a bounded insertion marker")
        payloads[role] = current.encode(
            _append_before(current.text, marker, notice, role), role
        )
    if set(payloads) != set(PREVIEW_PATHS):
        missing = sorted(set(PREVIEW_PATHS) - set(payloads))
        raise CorrectionValidationError(f"preview renderer is incomplete: {missing}")
    return payloads


def prepare_correction_proposal(
    *,
    source_root: Path,
    pages_root: Path,
    story_id: str,
    review_path: str,
    decision_audit_path: str,
    correction_date: str,
    output_root: Path,
    tts_provider: str,
    tts_model: str,
    tts_voice: str,
) -> dict[str, Any]:
    source_root = source_root.resolve()
    pages_root = pages_root.resolve()
    output_root = output_root.resolve()
    for forbidden, label in ((source_root, "source"), (pages_root, "Pages")):
        try:
            output_root.relative_to(forbidden)
        except ValueError:
            pass
        else:
            raise CorrectionValidationError(f"proposal output must be outside the {label} repository")
    if _git(pages_root, "status", "--porcelain"):
        raise CorrectionValidationError("Pages repository is dirty")
    if _git(pages_root, "rev-parse", "--abbrev-ref", "HEAD") != "gh-pages":
        raise CorrectionValidationError("Pages repository is not on gh-pages")
    source_commit = _git(source_root, "rev-parse", "HEAD")
    pages_head = _git(pages_root, "rev-parse", "HEAD")
    lineage_relative = (
        f"data/agent-history/gaza/lineage/published-stories/{story_id}.json"
    )
    lineage_path = _repo_path(source_root, lineage_relative, "published lineage")
    review_file = _repo_path(source_root, review_path, "editorial review")
    audit_file = _repo_path(source_root, decision_audit_path, "decision audit")
    lineage = _load_json(lineage_path, "published lineage")
    review = _load_json(review_file, "editorial review")
    audit = _load_json(audit_file, "decision audit")
    correction_lineage = review.get("correction_lineage", {})
    correction = {
        "correction_id": "",
        "story_id": story_id,
        "owning_edition_date": lineage.get("edition_date"),
        "correction_date": correction_date,
        "stable_event_fingerprint": lineage.get("stable_event_identity", {}).get("fingerprint"),
        "prior_claim_fingerprint": lineage.get("prior_claim_identity", {}).get("fingerprint"),
        "corrected_claim_fingerprint": review.get("candidate_event_fingerprint"),
        "prior_claim": lineage.get("prior_claim", {}).get("text"),
        "corrected_claim": review.get("attribution_assessment", {}).get("safe_future_wording"),
        "change_reason": review.get("decision_reason"),
        "source_attribution": review.get("attribution_assessment", {}).get("attributed_to"),
        "evidence_references": review.get("evidence_references"),
        "casualty_change": {
            "field": correction_lineage.get("field_or_claim"),
            "previous_value": correction_lineage.get("previous_value"),
            "corrected_value": correction_lineage.get("corrected_value"),
            "operation": "replace",
        },
        "injury_disagreement": {
            "unresolved": review.get("attribution_assessment", {}).get("dispute_unresolved"),
            "reports": review.get("attribution_assessment", {}).get("disputed_values"),
        },
    }
    correction["correction_id"] = correction_identity(
        story_id,
        correction["stable_event_fingerprint"],
        correction["prior_claim_fingerprint"],
        correction["corrected_claim_fingerprint"],
    )
    correction = _validate_correction({"correction": correction})
    private_evidence = {
        "lineage_path": lineage_relative,
        "lineage_sha256": sha256_file(lineage_path),
        "review_path": review_path,
        "review_sha256": sha256_file(review_file),
        "decision_audit_path": decision_audit_path,
        "decision_audit_sha256": sha256_file(audit_file),
        "raw_sha256": review.get("raw_sha256"),
        "normalized_artifact_sha256": review.get("normalized_artifact_sha256"),
        "report_artifact_sha256": review.get("report_artifact_sha256"),
    }
    validation_shell = {"private_evidence": private_evidence}
    _validate_private_evidence(source_root, validation_shell, correction)
    script = _correction_script(correction)
    audio_request = {
        "schema_version": "gaza_formal_historical_correction_audio_request_v1",
        "correction_id": correction["correction_id"],
        "story_id": story_id,
        "owning_edition_date": correction["owning_edition_date"],
        "correction_date": correction_date,
        "script_text": script,
        "script_sha256": "sha256:" + sha256_bytes(script.encode("utf-8")),
        "tts_provider": str(tts_provider).strip(),
        "tts_model": str(tts_model).strip(),
        "tts_voice": str(tts_voice).strip(),
        "public_path": _expected_public_path("correction_audio", correction),
        "render_authorized": False,
        "publication_authorized": False,
    }
    if any(not audio_request[key] for key in ("tts_provider", "tts_model", "tts_voice")):
        raise CorrectionValidationError("audio request provider, model, and voice are required")
    preview_payloads = _render_preview_payloads(pages_root, correction, audio_request)
    target = output_root / correction["correction_id"]
    representations = []
    for role in sorted(PREVIEW_PATHS):
        public_path = _expected_public_path(role, correction)
        current_path = _repo_path(pages_root, public_path, f"{role} public artifact")
        representations.append(
            {
                "role": role,
                "public_path": public_path,
                "input_path": f"preview/{public_path}",
                "prior_sha256": None if role in NEW_ROLES else sha256_file(current_path),
                "corrected_sha256": sha256_bytes(preview_payloads[role]),
            }
        )
    unchanged = [
        {
            "role": role,
            "public_path": template.format(date=correction["owning_edition_date"]),
            "sha256": sha256_file(
                _repo_path(
                    pages_root,
                    template.format(date=correction["owning_edition_date"]),
                    f"{role} dependency",
                )
            ),
        }
        for role, template in sorted(UNCHANGED_PATHS.items())
    ]
    audio_request_bytes = _json_document(audio_request)
    audio_request_entry = {
        "input_path": "audio_request.json",
        "public_path": audio_request["public_path"],
        "sha256": sha256_bytes(audio_request_bytes),
        "script_sha256": audio_request["script_sha256"],
    }
    set_payload = [
        {
            "role": row["role"],
            "public_path": row["public_path"],
            "corrected_sha256": row["corrected_sha256"],
        }
        for row in representations
    ] + [{"role": "correction_audio_request", **audio_request_entry}]
    artifact_set_sha = "sha256:" + sha256_bytes(_canonical_bytes(set_payload))
    proposal = {
        "schema_version": PROPOSAL_SCHEMA,
        "operation": "formal_historical_correction",
        "domain": "gaza",
        "source_commit": source_commit,
        "correction": correction,
        "private_evidence": private_evidence,
        "pages_state": {
            "branch": "gh-pages",
            "expected_head": pages_head,
            "representations": representations,
            "unchanged_dependencies": unchanged,
            "audio_request": audio_request_entry,
            "artifact_set_sha256": artifact_set_sha,
        },
        "proposal_sha256": "",
    }
    proposal["proposal_sha256"] = fingerprint_payload(proposal, "proposal_sha256")
    approval_request = {
        "schema_version": APPROVAL_REQUEST_SCHEMA,
        "scope": "formal_historical_correction_package_and_audio",
        "proposal_sha256": proposal["proposal_sha256"],
        "correction_id": correction["correction_id"],
        "source_commit": source_commit,
        "pages_head": pages_head,
        "artifact_set_sha256": artifact_set_sha,
        "audio_request_sha256": audio_request_entry["sha256"],
        "package_authorized": False,
        "audio_authorized": False,
        "publication_authorized": False,
        "request_sha256": "",
    }
    approval_request["request_sha256"] = fingerprint_payload(approval_request, "request_sha256")
    expected_files = {
        "proposal.json": _json_document(proposal),
        "audio_request.json": audio_request_bytes,
        "approval_request.json": _json_document(approval_request),
        **{
            f"preview/{_expected_public_path(role, correction)}": preview_payloads[role]
            for role in PREVIEW_PATHS
        },
    }
    if target.exists():
        for relative, expected in expected_files.items():
            path = _repo_path(target, relative, "existing proposal output")
            if not path.is_file() or path.read_bytes() != expected:
                raise CorrectionValidationError("existing proposal conflicts with deterministic output")
        return {
            "status": "idempotent_noop",
            "correction_id": correction["correction_id"],
            "proposal_path": str(target / "proposal.json"),
            "approval_request_path": str(target / "approval_request.json"),
            "proposal_sha256": proposal["proposal_sha256"],
            "artifact_set_sha256": artifact_set_sha,
            "persistent_mutation": False,
            "publication_authorized": False,
        }
    output_root.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=f".{correction['correction_id']}-", dir=output_root))
    try:
        for relative, content in expected_files.items():
            path = _repo_path(temp, relative, "proposal output")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        os.replace(temp, target)
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    return {
        "status": "proposal_created",
        "correction_id": correction["correction_id"],
        "proposal_path": str(target / "proposal.json"),
        "approval_request_path": str(target / "approval_request.json"),
        "proposal_sha256": proposal["proposal_sha256"],
        "artifact_set_sha256": artifact_set_sha,
        "persistent_mutation": True,
        "pages_mutation": False,
        "publication_authorized": False,
    }


def create_package_approval(
    *,
    source_root: Path,
    pages_root: Path,
    proposal_path: Path,
    input_root: Path,
    approval_request_path: Path,
    output_path: Path,
    approval_id: str,
    approver: str,
    approved_at: str,
) -> dict[str, Any]:
    source_root = source_root.resolve()
    pages_root = pages_root.resolve()
    input_root = input_root.resolve()
    output_path = output_path.resolve()
    approvals_root = (source_root / "approvals").resolve()
    try:
        output_path.relative_to(approvals_root)
    except ValueError as exc:
        raise CorrectionValidationError(
            "package approval output must be under the source approvals directory"
        ) from exc
    proposal = _validate_proposal_shape(_load_json(proposal_path, "correction proposal"))
    correction = _validate_correction(proposal)
    if _git(source_root, "rev-parse", "HEAD") != proposal["source_commit"]:
        raise CorrectionValidationError("source history drifted before package approval")
    _validate_private_evidence(source_root, proposal, correction)
    rows = _validate_pages_and_representations(
        pages_root, input_root, proposal, correction
    )
    _validate_representation_semantics(input_root, rows, correction)
    request = _exact_fields(
        _load_json(approval_request_path, "approval request"),
        {
            "schema_version",
            "scope",
            "proposal_sha256",
            "correction_id",
            "source_commit",
            "pages_head",
            "artifact_set_sha256",
            "audio_request_sha256",
            "package_authorized",
            "audio_authorized",
            "publication_authorized",
            "request_sha256",
        },
        "approval request",
    )
    if request["schema_version"] != APPROVAL_REQUEST_SCHEMA:
        raise CorrectionValidationError("approval request schema is unsupported")
    if request["request_sha256"] != fingerprint_payload(request, "request_sha256"):
        raise CorrectionValidationError("approval request fingerprint differs")
    if any(request[key] is not False for key in ("package_authorized", "audio_authorized", "publication_authorized")):
        raise CorrectionValidationError("approval request must remain non-authorizing")
    expected_request = {
        "schema_version": APPROVAL_REQUEST_SCHEMA,
        "scope": "formal_historical_correction_package_and_audio",
        "proposal_sha256": proposal["proposal_sha256"],
        "correction_id": correction["correction_id"],
        "source_commit": proposal["source_commit"],
        "pages_head": proposal["pages_state"]["expected_head"],
        "artifact_set_sha256": proposal["pages_state"]["artifact_set_sha256"],
        "audio_request_sha256": proposal["pages_state"]["audio_request"]["sha256"],
        "package_authorized": False,
        "audio_authorized": False,
        "publication_authorized": False,
        "request_sha256": "",
    }
    expected_request["request_sha256"] = fingerprint_payload(
        expected_request, "request_sha256"
    )
    if request != expected_request:
        raise CorrectionValidationError(
            "approval request is not validator-produced for the current proposal"
        )
    if not all(str(value).strip() for value in (approval_id, approver, approved_at)):
        raise CorrectionValidationError("approval ID, approver, and approval time are required")
    approval = {
        "schema_version": APPROVAL_SCHEMA,
        "scope": "formal_historical_correction",
        "approval_id": approval_id,
        "proposal_sha256": request["proposal_sha256"],
        "correction_id": request["correction_id"],
        "source_commit": request["source_commit"],
        "pages_head": request["pages_head"],
        "artifact_set_sha256": request["artifact_set_sha256"],
        "audio_request_sha256": request["audio_request_sha256"],
        "approved_at": approved_at,
        "approver": approver,
        "package_authorized": True,
        "audio_authorized": True,
        "publication_authorized": False,
        "approval_fingerprint": "",
    }
    approval["approval_fingerprint"] = fingerprint_payload(approval, "approval_fingerprint")
    content = _json_document(approval)
    if output_path.exists():
        if output_path.read_bytes() == content:
            return {"status": "idempotent_noop", "approval_path": str(output_path)}
        raise CorrectionValidationError("conflicting package approval already exists")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.tmp")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, output_path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return {
        "status": "package_approval_created",
        "approval_path": str(output_path),
        "approval_fingerprint": approval["approval_fingerprint"],
        "publication_authorized": False,
    }


def _validate_pages_and_representations(
    pages_root: Path,
    input_root: Path,
    proposal: dict[str, Any],
    correction: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    pages = _exact_fields(
        proposal["pages_state"],
        {
            "branch",
            "expected_head",
            "representations",
            "unchanged_dependencies",
            "audio_request",
            "artifact_set_sha256",
        },
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
    if not isinstance(rows, list) or len(rows) != len(PREVIEW_PATHS):
        raise CorrectionValidationError("correction representation inventory is partial")
    by_role = {str(row.get("role")): row for row in rows if isinstance(row, dict)}
    if set(by_role) != set(PREVIEW_PATHS) or len(by_role) != len(rows):
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

    audio_request_entry = _exact_fields(
        pages["audio_request"],
        {"input_path", "public_path", "sha256", "script_sha256"},
        "audio request entry",
    )
    if audio_request_entry["public_path"] != _expected_public_path("correction_audio", correction):
        raise CorrectionValidationError("audio request public path is invalid")
    request_path = _repo_path(input_root, audio_request_entry["input_path"], "audio request")
    if not request_path.is_file() or sha256_file(request_path) != _require_sha(
        audio_request_entry["sha256"], "audio request hash"
    ):
        raise CorrectionValidationError("audio request hash differs")
    audio_request = _load_json(request_path, "audio request")
    if audio_request.get("correction_id") != correction["correction_id"]:
        raise CorrectionValidationError("audio request binds another correction")
    if audio_request.get("script_sha256") != audio_request_entry["script_sha256"]:
        raise CorrectionValidationError("audio request script hash differs")
    if audio_request.get("render_authorized") is not False or audio_request.get("publication_authorized") is not False:
        raise CorrectionValidationError("preapproval audio request must be non-authorizing")
    if audio_request.get("script_text") != _correction_script(correction):
        raise CorrectionValidationError("audio request script is not validator-derived")
    set_payload = [
        {"role": role, "public_path": by_role[role]["public_path"], "corrected_sha256": by_role[role]["corrected_sha256"]}
        for role in sorted(by_role)
    ] + [{"role": "correction_audio_request", **audio_request_entry}]
    expected_set_hash = "sha256:" + sha256_bytes(_canonical_bytes(set_payload))
    if pages["artifact_set_sha256"] != expected_set_hash:
        raise CorrectionValidationError("approved artifact-set fingerprint differs")
    return by_role


def _require_reader_prose(
    text: str,
    correction: dict[str, Any],
    role: str,
    *,
    expected: tuple[str, ...],
) -> None:
    normalized = " ".join(text.split())
    for value in expected:
        if " ".join(value.split()) not in normalized:
            raise CorrectionValidationError(f"{role} lacks required reader-facing correction copy")
    for field in ("correction_id", "story_id"):
        if str(correction[field]) in normalized:
            raise CorrectionValidationError(f"{role} exposes machine correction {field} as prose")
    expected_copy = " ".join(" ".join(value.split()) for value in expected)
    for field in ("owning_edition_date", "correction_date"):
        if str(correction[field]) in expected_copy:
            raise CorrectionValidationError(f"{role} exposes an ISO correction date as prose")


def _visible_html_text(path: Path, role: str) -> str:
    artifact = _read_text_artifact(path, role)
    nodes = _StrictHtmlTreeParser(artifact.text).finish()
    roots = [node for node in nodes if node.parent is None]
    return " ".join(node.text for node in roots)


def _validate_json_semantics(path: Path, role: str, correction: dict[str, Any]) -> None:
    payload = _load_json(path, role)
    if role == "curation_manifest":
        if not isinstance(payload, list):
            raise CorrectionValidationError("curation manifest must remain a story list")
        matches = [row for row in payload if isinstance(row, dict) and row.get("story_id") == correction["story_id"]]
        if len(matches) != 1:
            raise CorrectionValidationError("curation must preserve exactly one owning story")
        story = matches[0]
        reader = _reader_correction_copy(correction)
        if story.get("summary") != reader.story_update:
            raise CorrectionValidationError("curation story does not use reviewed corrected wording")
        counts = story.get("casualty_counts")
        casualty_change = correction["casualty_change"]
        if counts != {_casualty_count_key(correction): casualty_change["corrected_value"]}:
            raise CorrectionValidationError(
                "curation must replace the corrected casualty field and omit a resolved injury total"
            )
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
        if script != _correction_script(correction):
            raise CorrectionValidationError("correction audio script is not validator-derived")
        _require_reader_prose(
            script,
            correction,
            role,
            expected=(_reader_correction_copy(correction).audio_script,),
        )
        if payload.get("injury_disagreement") != correction["injury_disagreement"]:
            raise CorrectionValidationError("correction audio resolves the injury disagreement")
    elif role == "flash_briefing":
        if not isinstance(payload, list) or len(payload) != 1:
            raise CorrectionValidationError("flash briefing correction must be one atomic update")
        item = payload[0]
        reader = _reader_correction_copy(correction)
        if not isinstance(item, dict) or item.get("uid") != correction["correction_id"]:
            raise CorrectionValidationError("flash briefing lacks its machine correction identity")
        if item.get("updateDate") != correction["correction_date"]:
            raise CorrectionValidationError("flash briefing machine date differs")
        if item.get("titleText") != reader.heading or item.get("mainText") != reader.audio_script:
            raise CorrectionValidationError("flash briefing reader copy differs")
        _require_reader_prose(
            str(item["titleText"]) + " " + str(item["mainText"]),
            correction,
            role,
            expected=(reader.heading, reader.audio_script),
        )


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
    reader = _reader_correction_copy(correction)
    link = item.findtext("link", default="")
    title = item.findtext("title", default="")
    description = item.findtext("description", default="")
    if correction["correction_id"] not in link:
        raise CorrectionValidationError(f"{role} correction item is not linked to the correction record")
    if title != reader.heading or description != reader.feed_description:
        raise CorrectionValidationError(f"{role} correction item lacks readable correction copy")
    if " | " in description:
        raise CorrectionValidationError(f"{role} exposes a pipe-delimited machine record")
    _require_reader_prose(
        title + " " + description,
        correction,
        role,
        expected=(reader.heading, reader.notice),
    )
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
            visible = _visible_html_text(path, role)
            reader = _reader_correction_copy(correction)
            expected = [reader.heading, reader.notice]
            if role == "edition_html":
                expected.extend((reader.today_update, reader.story_update))
            elif role == "correction_transcript":
                expected.append(reader.audio_script)
            _require_reader_prose(
                visible,
                correction,
                role,
                expected=tuple(expected),
            )


def _load_independent_approval(
    source_root: Path,
    approval_ref: str,
    approval_path: str,
    proposal: dict[str, Any],
    correction: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    if not approval_ref or not approval_path:
        raise CorrectionValidationError("a separately committed approval ref and path are required")
    approval_ref_commit = _git(source_root, "rev-parse", f"{approval_ref}^{{commit}}")
    approval_commit = _git(
        source_root,
        "log",
        "-1",
        "--format=%H",
        approval_ref_commit,
        "--",
        approval_path,
    )
    if not approval_commit:
        raise CorrectionValidationError("committed approval artifact is missing")
    changed_paths = {
        line
        for line in _git(
            source_root,
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-r",
            approval_commit,
        ).splitlines()
        if line
    }
    if changed_paths != {approval_path}:
        raise CorrectionValidationError(
            "package approval commit must contain only the approval artifact"
        )
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
            "audio_request_sha256",
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
    if approval["audio_request_sha256"] != proposal["pages_state"]["audio_request"]["sha256"]:
        raise CorrectionValidationError("release approval binds another audio request")
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
    if source_head != approval_commit:
        raise CorrectionValidationError(
            "source history drifted from the committed package approval"
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
        "audio_request": proposal["pages_state"]["audio_request"],
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
    rendered_audio_path: Path,
    staging_root: Path,
    source_root: Path,
    pages_root: Path,
) -> dict[str, Any]:
    input_root = input_root.resolve()
    staging_root = staging_root.resolve()
    source_root = source_root.resolve()
    pages_root = pages_root.resolve()
    rendered_audio_path = rendered_audio_path.resolve()
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
    if _git(source_root, "rev-parse", "HEAD") != plan.get("approval_commit"):
        raise CorrectionValidationError("source history drifted before package staging")
    if _git(pages_root, "status", "--porcelain"):
        raise CorrectionValidationError("Pages repository is dirty")
    if _git(pages_root, "rev-parse", "HEAD") != plan.get("pages_head"):
        raise CorrectionValidationError("Pages history drifted before package staging")
    if not rendered_audio_path.is_file() or rendered_audio_path.read_bytes()[:3] != b"ID3":
        raise CorrectionValidationError("approved audio render is missing or is not an MP3 asset")
    audio_content = rendered_audio_path.read_bytes()
    rendered_audio_sha256 = sha256_bytes(audio_content)
    preview_rows = plan.get("representations")
    if not isinstance(preview_rows, list) or len(preview_rows) != len(PREVIEW_PATHS):
        raise CorrectionValidationError("validated preview representation set is incomplete")
    final_content: dict[str, bytes] = {}
    final_rows: list[dict[str, str]] = []
    for row in preview_rows:
        source = _repo_path(input_root, row["input_path"], "approved preview input")
        if not source.is_file() or sha256_file(source) != row["corrected_sha256"]:
            raise CorrectionValidationError(f"approved preview input is missing for {row['role']}")
        content = source.read_bytes()
        if row["role"] == "correction_audio_metadata":
            metadata = json.loads(content.decode("utf-8"))
            metadata["render_status"] = "rendered_for_private_review"
            metadata["rendered_audio_sha256"] = rendered_audio_sha256
            content = _json_document(metadata)
        elif row["role"] in {"podcast", "audio_podcast"}:
            marker = b'type="audio/mpeg" length="0"'
            if content.count(marker) != 1:
                raise CorrectionValidationError(
                    f"{row['role']} lacks one correction-audio length placeholder"
                )
            content = content.replace(
                marker,
                f'type="audio/mpeg" length="{len(audio_content)}"'.encode("ascii"),
                1,
            )
        final_content[row["role"]] = content
        final_rows.append(
            {
                "role": row["role"],
                "public_path": row["public_path"],
                "sha256": sha256_bytes(content),
            }
        )
    audio_public_path = plan["audio_request"]["public_path"]
    final_content["correction_audio"] = audio_content
    final_rows.append(
        {
            "role": "correction_audio",
            "public_path": audio_public_path,
            "sha256": sha256_bytes(audio_content),
        }
    )
    final_rows.sort(key=lambda row: row["role"])
    package_artifact_set_sha256 = "sha256:" + sha256_bytes(_canonical_bytes(final_rows))
    target = staging_root / correction_id
    manifest = {
        **{
            k: v
            for k, v in plan.items()
            if k not in {"status", "persistent_mutation", "representations"}
        },
        "status": "staged_correction_package",
        "preview_representations": preview_rows,
        "representations": final_rows,
        "rendered_audio_sha256": rendered_audio_sha256,
        "package_artifact_set_sha256": package_artifact_set_sha256,
        "publication_authorized": False,
        "package_manifest_sha256": "",
    }
    manifest["package_manifest_sha256"] = fingerprint_payload(manifest, "package_manifest_sha256")
    if target.exists():
        existing_path = target / "package_manifest.json"
        if existing_path.is_file() and _load_json(existing_path, "existing package manifest") == manifest:
            for row in manifest["representations"]:
                staged = _repo_path(target, row["public_path"], "staged representation")
                if not staged.is_file() or sha256_file(staged) != row["sha256"]:
                    raise CorrectionValidationError("existing package conflicts with approved artifact hashes")
            return {**manifest, "status": "idempotent_noop", "package_path": str(target)}
        raise CorrectionValidationError("conflicting correction package already exists")
    staging_root.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=f".{correction_id}-", dir=staging_root))
    try:
        for row in manifest["representations"]:
            destination = _repo_path(temp, row["public_path"], "staged representation")
            destination.parent.mkdir(parents=True, exist_ok=True)
            if row["role"] == "correction_audio":
                shutil.copyfile(rendered_audio_path, destination)
            elif row["role"] in {
                "correction_audio_metadata",
                "podcast",
                "audio_podcast",
            }:
                destination.write_bytes(final_content[row["role"]])
            else:
                preview = next(item for item in preview_rows if item["role"] == row["role"])
                source = _repo_path(input_root, preview["input_path"], "approved input")
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


def verify_staged_package(
    *,
    plan: dict[str, Any],
    package_root: Path,
    source_root: Path,
    pages_root: Path,
) -> dict[str, Any]:
    package_root = package_root.resolve()
    source_root = source_root.resolve()
    pages_root = pages_root.resolve()
    manifest_path = package_root / "package_manifest.json"
    manifest = _load_json(manifest_path, "staged package manifest")
    if manifest.get("schema_version") != PACKAGE_SCHEMA:
        raise CorrectionValidationError("staged package schema is unsupported")
    if manifest.get("correction_id") != plan.get("correction_id"):
        raise CorrectionValidationError("staged package binds another correction")
    if manifest.get("proposal_sha256") != plan.get("proposal_sha256"):
        raise CorrectionValidationError("staged package binds another proposal")
    if manifest.get("artifact_set_sha256") != plan.get("artifact_set_sha256"):
        raise CorrectionValidationError("staged package binds another approved preview set")
    if manifest.get("publication_authorized") is not False:
        raise CorrectionValidationError("staged package improperly carries publication authority")
    if manifest.get("package_manifest_sha256") != fingerprint_payload(
        manifest, "package_manifest_sha256"
    ):
        raise CorrectionValidationError("staged package manifest fingerprint differs")
    rows = manifest.get("representations")
    if not isinstance(rows, list) or {row.get("role") for row in rows if isinstance(row, dict)} != set(REPLACEMENT_PATHS):
        raise CorrectionValidationError("staged package representation set is incomplete")
    for row in rows:
        path = _repo_path(package_root, row["public_path"], "staged representation")
        if not path.is_file() or sha256_file(path) != row["sha256"]:
            raise CorrectionValidationError(f"staged representation differs: {row['role']}")
    if manifest.get("package_artifact_set_sha256") != "sha256:" + sha256_bytes(
        _canonical_bytes(sorted(rows, key=lambda row: row["role"]))
    ):
        raise CorrectionValidationError("staged package artifact-set fingerprint differs")
    if _git(source_root, "rev-parse", "HEAD") != plan.get("approval_commit"):
        raise CorrectionValidationError("source history drifted after package approval")
    if _git(pages_root, "status", "--porcelain"):
        raise CorrectionValidationError("Pages repository is dirty")
    if _git(pages_root, "rev-parse", "HEAD") != plan.get("pages_head"):
        raise CorrectionValidationError("Pages history drifted after package staging")
    return {
        "status": "staged_package_verified",
        "correction_id": plan["correction_id"],
        "package_manifest_sha256": manifest["package_manifest_sha256"],
        "package_artifact_set_sha256": manifest["package_artifact_set_sha256"],
        "publication_authorized": False,
        "persistent_mutation": False,
    }
