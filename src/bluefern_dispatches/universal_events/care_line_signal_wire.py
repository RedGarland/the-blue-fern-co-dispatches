from __future__ import annotations

import html
import json
import subprocess
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any
from textwrap import wrap
import struct

from bluefern_dispatches.care_line_social_cards import render_social_card_png_bytes, social_card_spec_for_event


PHASE14E_DIR = Path("data") / "universal_events" / "shadow" / "care-line" / "phase14e-universal-events"
PHASE14F_DIR = Path("data") / "universal_events" / "shadow" / "care-line" / "phase14f-signal-wire"
PUBLICATION_STATE_PATH = Path("data") / "universal_events" / "publication-state" / "care-line-signal-wire.json"
REVIEWED_RECORDS_PATH = Path("data") / "dispatches" / "care-line" / "reviewed" / "2026-07-22" / "reviewed_records.json"
PROPOSED_EVENTS_PATH = PHASE14E_DIR / "phase14e-proposed-universal-events.json"
VALIDATION_REPORT_PATH = PHASE14E_DIR / "phase14e-validation-report.json"
LINEAGE_REPORT_PATH = PHASE14E_DIR / "phase14e-source-to-event-lineage-report.json"
IDEMPOTENCY_REPORT_PATH = PHASE14E_DIR / "phase14e-duplicate-idempotency-report.json"
SHADOW_RUN_MANIFEST_PATH = PHASE14E_DIR / "shadow_run_29737fd2d78cdb6d.manifest.json"

READY_RECORD_IDS = {
    "care-line-direct-discovery-4a1461b9eccb0219",
    "care-line-direct-discovery-fe1cba7829f11dc2",
}
EXPECTED_EVENT_IDS = {
    "event_3b4ad4e528e48744",
    "event_a12dae614b86cfa9",
}

SCHEMA_VERSION = "bluefern.care_line.phase14f.publication.v1"
SHADOW_ARTIFACT_VERSION = "bluefern.care_line.phase14f.shadow.v1"
BASE_URL = "https://dispatches.thebluefernco.com"
SOCIAL_CARD_WIDTH = 1200
SOCIAL_CARD_HEIGHT = 630
APPROVED_SOCIAL_CARD_ASSET_RELATIVE_PATHS = {
    "event_3b4ad4e528e48744": Path("assets") / "care-line" / "event_3b4ad4e528e48744.png",
    "event_a12dae614b86cfa9": Path("assets") / "care-line" / "event_a12dae614b86cfa9.png",
}
APPROVED_SOCIAL_CARD_ALTS = {
    "event_3b4ad4e528e48744": "The Blue Fern Co. Care Line social card for UCSF opens 8-bed pediatric neuroscience unit",
    "event_a12dae614b86cfa9": "The Blue Fern Co. Care Line social card for ECU Health extends in-network access",
}
PUBLICATION_STATE_SCHEMA_VERSION = 1
PUBLIC_CONTENT_HASH_FIELDS = (
    "event_id",
    "source_url",
    "publisher",
    "source_publication_date",
    "source_publication_at",
    "announcement_date",
    "effective_date",
    "title",
    "public_label",
    "public_summary",
    "why_it_matters",
    "revision_status",
    "service_line",
    "facility_name",
    "city",
    "state",
    "country_code",
    "evidence_text",
    "verification_status",
)


@dataclass(frozen=True)
class SignalWireEvent:
    event_id: str
    candidate_id: str
    producer_record_id: str
    source_item_id: str
    source_url: str
    publisher: str
    source_title: str
    source_publication_date: str
    announcement_date: str
    effective_date: str
    public_published_at: str
    system_discovered_at: str
    verification_at: str
    event_type: str
    status: str
    domain: str
    title: str
    public_label: str
    public_summary: str
    why_it_matters: str
    revision_status: str
    taxonomy_gap_note: str
    service_line: str
    service_line_normalized: str
    facility_name: str
    city: str
    state: str
    country_code: str
    evidence_text: str
    evidence_provenance_type: str
    evidence_source_field: str
    evidence_source_artifact: str
    review_decision_id: str
    review_reason: str
    supersedes_evidence_decision_id: str
    record_fingerprint: str
    review_packet_fingerprint: str
    source_publication_at: str
    last_updated_at: str
    verification_status: str = "verified"

    def model_dump(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "candidate_id": self.candidate_id,
            "producer_record_id": self.producer_record_id,
            "source_item_id": self.source_item_id,
            "source_url": self.source_url,
            "publisher": self.publisher,
            "source_title": self.source_title,
            "source_publication_date": self.source_publication_date,
            "source_publication_at": self.source_publication_at,
            "announcement_date": self.announcement_date,
            "effective_date": self.effective_date,
            "public_published_at": self.public_published_at,
            "system_discovered_at": self.system_discovered_at,
            "verification_at": self.verification_at,
            "event_type": self.event_type,
            "status": self.status,
            "domain": self.domain,
            "title": self.title,
            "public_label": self.public_label,
            "public_summary": self.public_summary,
            "why_it_matters": self.why_it_matters,
            "revision_status": self.revision_status,
            "taxonomy_gap_note": self.taxonomy_gap_note,
            "service_line": self.service_line,
            "service_line_normalized": self.service_line_normalized,
            "facility_name": self.facility_name,
            "city": self.city,
            "state": self.state,
            "country_code": self.country_code,
            "evidence_text": self.evidence_text,
            "evidence_provenance_type": self.evidence_provenance_type,
            "evidence_source_field": self.evidence_source_field,
            "evidence_source_artifact": self.evidence_source_artifact,
            "review_decision_id": self.review_decision_id,
            "review_reason": self.review_reason,
            "supersedes_evidence_decision_id": self.supersedes_evidence_decision_id,
            "record_fingerprint": self.record_fingerprint,
            "review_packet_fingerprint": self.review_packet_fingerprint,
            "verification_status": self.verification_status,
            "last_updated_at": self.last_updated_at,
        }


def _stable_json_hash(payload: Any) -> str:
    return sha256(json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")).hexdigest()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _json_text(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _load_rows(path: Path) -> list[dict[str, Any]]:
    payload = _load_json(path)
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict) and isinstance(payload.get("records"), list):
        rows = payload["records"]
    elif isinstance(payload, dict) and isinstance(payload.get("promotion_previews"), list):
        rows = payload["promotion_previews"]
    else:
        raise ValueError(f"unsupported Care Line publication input: {path}")
    return [dict(row) for row in rows if isinstance(row, dict)]


def _text(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, "", [], {}):
            return str(value).strip()
    return ""


def _metadata_text(row: dict[str, Any], *keys: str) -> str:
    metadata = row.get("metadata")
    if isinstance(metadata, dict):
        for key in keys:
            value = metadata.get(key)
            if value not in (None, "", [], {}):
                return str(value).strip()
    return _text(row, *keys)


def _repair_public_text(text: str) -> str:
    repaired = str(text or "")
    replacements = {
        "â€™": "’",
        "â€˜": "‘",
        "â€œ": "“",
        "â€": "”",
        "â€”": "—",
        "â€“": "–",
        "Â": "",
    }
    for bad, good in replacements.items():
        repaired = repaired.replace(bad, good)
    repaired = repaired.replace("Children?s", "Children’s")
    repaired = repaired.replace("Children? ", "Children’s ")
    repaired = repaired.replace("Children?Hospital", "Children’s Hospital")
    return repaired

def _public_text(*values: str) -> str:
    for value in values:
        if value not in (None, "", [], {}):
            return _repair_public_text(str(value).strip())
    return ""


def _humanize_event_type(event_type: str) -> str:
    text = str(event_type or "").replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else ""


def _public_label_for_event(event_type: str, evidence_text: str) -> str:
    lowered = evidence_text.lower()
    if event_type == "service_restoration" and "remain in network" in lowered:
        return "Temporary network-access extension"
    return _humanize_event_type(event_type)


def _public_summary_for_event(event_type: str, facility_name: str, city: str, state: str, effective_date: str, evidence_text: str) -> str:
    lowered = evidence_text.lower()
    if event_type == "service_expansion" and "neuroscience specialty unit" in lowered:
        return (
            f"{facility_name} in {city}, {state} opened an eight-bed Children's Neuroscience Specialty Unit, "
            "expanding inpatient capacity for children with neurological and neurosurgical conditions."
        )
    if event_type == "service_restoration" and "remain in network" in lowered:
        return (
            f"{facility_name} in {city}, {state} will remain in network for some UnitedHealthcare plans "
            f"through {effective_date} under a temporary extension."
        )
    return _repair_public_text(evidence_text)


def _why_it_matters_for_event(public_label: str, effective_date: str) -> str:
    if public_label == "Service expansion":
        return "It expands inpatient capacity for children with neurological and neurosurgical conditions."
    if public_label == "Temporary network-access extension":
        return f"It keeps some UnitedHealthcare patients in network through {effective_date}, delaying a potential disruption."
    return "It may affect healthcare access for the affected facility, provider, or service line."


def _revision_status_for_event(public_label: str, event_type: str) -> str:
    if public_label != _humanize_event_type(event_type):
        return "corrected"
    return "approved"

def _taxonomy_gap_note_for_event(event_type: str, public_label: str) -> str:
    if event_type == "service_restoration" and public_label == "Temporary network-access extension":
        return (
            "Underlying schema value preserved as service_restoration; public label and summary were corrected because "
            "the evidence supports a temporary in-network extension through a deadline, not a permanent restoration."
        )
    return ""


def _preview_lines(text: str, *, width: int, limit: int) -> list[str]:
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return []
    lines: list[str] = []
    for paragraph in cleaned.split(" | "):
        lines.extend(wrap(paragraph, width=width) or [""])
        if len(lines) >= limit:
            break
    return lines[:limit]


def _event_social_card_svg(event: SignalWireEvent) -> str:
    title_lines = _preview_lines(event.title, width=30, limit=2)
    summary_lines = _preview_lines(event.public_summary, width=48, limit=3)
    label = event.public_label.upper()
    location = f"{event.facility_name} • {event.city}, {event.state}"
    footer = "Reviewed source record • The Blue Fern Co."
    lines: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630" role="img" aria-labelledby="title desc">',
        "  <title>Care Line Signal Wire social card</title>",
        f"  <desc>{html.escape(event.title)} from {html.escape(event.publisher)}</desc>",
        '  <defs>',
        '    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">',
        '      <stop offset="0%" stop-color="#f7f0e7"/>',
        '      <stop offset="100%" stop-color="#edf4fb"/>',
        '    </linearGradient>',
        '    <linearGradient id="accent" x1="0" y1="0" x2="1" y2="0">',
        '      <stop offset="0%" stop-color="#0d497a"/>',
        '      <stop offset="100%" stop-color="#2f7bb8"/>',
        '    </linearGradient>',
        '  </defs>',
        '  <rect width="1200" height="630" rx="36" fill="url(#bg)"/>',
        '  <rect x="56" y="56" width="1088" height="518" rx="28" fill="#ffffff" fill-opacity="0.72" stroke="#d7e4ef"/>',
        '  <rect x="56" y="56" width="1088" height="12" rx="6" fill="url(#accent)"/>',
        '  <circle cx="1018" cy="120" r="70" fill="#dceaf6"/>',
        '  <circle cx="1018" cy="120" r="42" fill="#0d497a"/>',
        '  <path d="M1001 120h34M1018 103v34" stroke="#fff" stroke-width="10" stroke-linecap="round"/>',
        '  <text x="96" y="128" fill="#0d497a" font-family="Georgia, Times New Roman, serif" font-size="26" font-weight="700" letter-spacing="4">THE BLUE FERN CO.</text>',
        '  <text x="96" y="186" fill="#26485f" font-family="Arial, Helvetica, sans-serif" font-size="26" font-weight="700" letter-spacing="2">CARE LINE SIGNAL WIRE</text>',
        f'  <text x="96" y="240" fill="#0f2740" font-family="Georgia, Times New Roman, serif" font-size="58" font-weight="700">{html.escape(label)}</text>',
    ]
    y = 302
    for line in title_lines:
        lines.append(
            f'  <text x="96" y="{y}" fill="#142433" font-family="Georgia, Times New Roman, serif" font-size="38" font-weight="700">{html.escape(line)}</text>'
        )
        y += 48
    y += 12
    for line in summary_lines:
        lines.append(
            f'  <text x="96" y="{y}" fill="#31485b" font-family="Arial, Helvetica, sans-serif" font-size="28">{html.escape(line)}</text>'
        )
        y += 38
    lines.extend(
        [
            f'  <text x="96" y="518" fill="#4f6575" font-family="Arial, Helvetica, sans-serif" font-size="22">{html.escape(location)}</text>',
            f'  <text x="96" y="560" fill="#4f6575" font-family="Arial, Helvetica, sans-serif" font-size="20">{html.escape(footer)}</text>',
            "</svg>",
        ]
    )
    return "\n".join(lines) + "\n"


def _social_card_asset(event: SignalWireEvent, repo_root: Path | None = None) -> dict[str, str]:
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[3]
    approved_asset_relpath = APPROVED_SOCIAL_CARD_ASSET_RELATIVE_PATHS.get(event.event_id)
    if approved_asset_relpath is not None:
        approved_asset_path = approved_asset_relpath if approved_asset_relpath.is_absolute() else repo_root / approved_asset_relpath
        if not approved_asset_path.exists():
            raise FileNotFoundError(f"missing approved Care Line social card asset: {approved_asset_path}")
        data = approved_asset_path.read_bytes()
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError(f"approved Care Line social card asset is not a PNG: {approved_asset_path}")
        width, height = struct.unpack_from(">II", data, 16)
        if (width, height) != (SOCIAL_CARD_WIDTH, SOCIAL_CARD_HEIGHT):
            raise ValueError(
                f"approved Care Line social card asset has unexpected dimensions {(width, height)}: {approved_asset_path}"
            )
        return {
            "path": f"output/site/events/{event.event_id}/social-card.png",
            "content": data,
            "url": f"{BASE_URL}/events/{event.event_id}/social-card.png",
            "alt": APPROVED_SOCIAL_CARD_ALTS.get(event.event_id, f"The Blue Fern Co. Care Line social card for {event.title}"),
            "headline": event.title,
            "location": f"{event.facility_name}, {event.city}, {event.state}",
            "category": event.public_label,
            "date_line": event.effective_date,
            "brand_name": "The Blue Fern Co.",
            "section_label": "CARE LINE",
        }
    spec = social_card_spec_for_event(
        event_id=event.event_id,
        title=event.title,
        facility_name=event.facility_name,
        city=event.city,
        state=event.state,
        public_label=event.public_label,
        effective_date=event.effective_date,
    )
    return {
        "path": f"output/site/events/{event.event_id}/social-card.png",
        "content": render_social_card_png_bytes(spec),
        "url": spec["image_url"],
        "alt": spec["alt_text"],
        "headline": spec["headline"],
        "location": spec["location"],
        "category": spec["event_type_label"],
        "date_line": spec["date_label"],
        "brand_name": spec["brand_name"],
        "section_label": spec["section_label"],
    }


def _utc_from_mtime(path: Path) -> str:
    stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return stamp.isoformat().replace("+00:00", "Z")


def _publication_content_hash(event: SignalWireEvent) -> str:
    payload = event.model_dump()
    return _stable_json_hash({key: payload.get(key, "") for key in PUBLIC_CONTENT_HASH_FIELDS})


def _load_publication_state(repo_root: Path) -> dict[str, Any]:
    state_path = repo_root / PUBLICATION_STATE_PATH
    if not state_path.exists():
        return {"schema_version": PUBLICATION_STATE_SCHEMA_VERSION, "events": {}}
    try:
        payload = _load_json(state_path)
    except Exception:
        return {"schema_version": PUBLICATION_STATE_SCHEMA_VERSION, "events": {}}
    if not isinstance(payload, dict):
        return {"schema_version": PUBLICATION_STATE_SCHEMA_VERSION, "events": {}}
    events = payload.get("events")
    if not isinstance(events, dict):
        events = {}
    normalized_events: dict[str, dict[str, str]] = {}
    for event_id, raw in events.items():
        if not isinstance(raw, dict):
            continue
        public_published_at = _text(raw, "public_published_at")
        last_updated_at = _text(raw, "last_updated_at")
        public_content_hash = _text(raw, "public_content_hash")
        if not public_published_at or not public_content_hash:
            continue
        normalized_events[str(event_id)] = {
            "public_published_at": public_published_at,
            "last_updated_at": last_updated_at or public_published_at,
            "public_content_hash": public_content_hash,
        }
    return {
        "schema_version": PUBLICATION_STATE_SCHEMA_VERSION,
        "events": normalized_events,
    }


def _resolve_publication_state(
    repo_root: Path,
    event_payloads: list[SignalWireEvent],
    *,
    generated_at: str | None,
    fallback_public_published_at: str,
) -> tuple[list[SignalWireEvent], dict[str, Any]]:
    existing_state = _load_publication_state(repo_root)
    existing_events = existing_state.get("events") if isinstance(existing_state, dict) else {}
    if not isinstance(existing_events, dict):
        existing_events = {}
    if not fallback_public_published_at:
        fallback_public_published_at = generated_at or ""
    updated_events: list[SignalWireEvent] = []
    state_events: dict[str, dict[str, str]] = {}
    revision_timestamp = generated_at or fallback_public_published_at
    if not revision_timestamp:
        revision_timestamp = _utc_from_mtime(repo_root / PHASE14E_DIR / "phase14e-proposed-universal-events.json")
    for event in event_payloads:
        existing_entry = existing_events.get(event.event_id) if isinstance(existing_events, dict) else None
        content_hash = _publication_content_hash(event)
        public_published_at = fallback_public_published_at or event.public_published_at
        last_updated_at = public_published_at
        if isinstance(existing_entry, dict):
            existing_public_published_at = _text(existing_entry, "public_published_at")
            existing_last_updated_at = _text(existing_entry, "last_updated_at") or existing_public_published_at
            existing_content_hash = _text(existing_entry, "public_content_hash")
            if existing_public_published_at:
                public_published_at = existing_public_published_at
            if existing_content_hash == content_hash:
                last_updated_at = existing_last_updated_at or public_published_at
            else:
                last_updated_at = revision_timestamp or public_published_at
        updated_events.append(
            replace(
                event,
                public_published_at=public_published_at,
                last_updated_at=last_updated_at,
            )
        )
        state_events[event.event_id] = {
            "public_published_at": public_published_at,
            "last_updated_at": last_updated_at,
            "public_content_hash": content_hash,
        }
    return updated_events, {"schema_version": PUBLICATION_STATE_SCHEMA_VERSION, "events": state_events}


def _find_latest_reviewed_records_path(repo_root: Path) -> Path | None:
    base = repo_root / "data" / "dispatches" / "care-line" / "reviewed"
    if not base.exists():
        return None
    candidates = sorted(path for path in base.glob("*/reviewed_records.json") if path.is_file())
    for path in reversed(candidates):
        try:
            rows = _load_rows(path)
        except Exception:
            continue
        record_ids = {_text(row, "producer_record_id", "care_line_record_id", "source_record_id") for row in rows}
        if READY_RECORD_IDS.issubset(record_ids):
            return path
    return None


def _find_phase14e_paths(repo_root: Path) -> dict[str, Path] | None:
    base = repo_root / PHASE14E_DIR
    paths = {
        "proposed": base / PROPOSED_EVENTS_PATH.name,
        "validation": base / VALIDATION_REPORT_PATH.name,
        "lineage": base / LINEAGE_REPORT_PATH.name,
        "idempotency": base / IDEMPOTENCY_REPORT_PATH.name,
        "manifest": base / SHADOW_RUN_MANIFEST_PATH.name,
    }
    if not all(path.exists() for path in paths.values()):
        return None
    return paths


def _is_universal_event_ready(row: dict[str, Any]) -> bool:
    status = _text(row, "universal_event_status", "evidence_review_current_status", "record_status")
    return status == "universal_event_ready"


def _event_from_record(
    reviewed_row: dict[str, Any],
    proposed_row: dict[str, Any],
    *,
    source_reviewed_at: str,
    public_published_at: str,
    system_discovered_at: str,
) -> SignalWireEvent:
    evidence_links = proposed_row.get("evidence_links") or []
    first_link = evidence_links[0] if evidence_links and isinstance(evidence_links[0], dict) else {}
    evidence_text = _public_text(_text(reviewed_row, "supporting_passage"))
    evidence_provenance_type = _text(reviewed_row, "evidence_provenance_type")
    evidence_source_field = "article_body" if evidence_provenance_type in {"source_explicit", "reviewer_transcribed"} else "unknown"
    evidence_source_artifact = "canonical_publisher_page" if evidence_provenance_type in {"source_explicit", "reviewer_transcribed"} else "missing"
    proposed_payload = proposed_row.get("proposed_event_payload") or {}
    source_publication_date = _text(reviewed_row, "source_publication_date")
    source_publication_at = f"{source_publication_date}T00:00:00Z" if source_publication_date else ""
    title = _public_text(_text(proposed_payload, "title", "source_title"), _text(reviewed_row, "source_title", "title"))
    source_title = _public_text(_text(reviewed_row, "source_title", "title"))
    publisher = _public_text(_text(reviewed_row, "source_publisher", "publisher"))
    facility_name = _public_text(_text(reviewed_row, "facility_name"))
    city = _public_text(_text(reviewed_row, "city"))
    state = _public_text(_text(reviewed_row, "state"))
    public_label = _public_label_for_event(_text(proposed_payload, "event_type"), evidence_text)
    public_summary = _public_summary_for_event(_text(proposed_payload, "event_type"), facility_name, city, state, _text(proposed_payload, "effective_date"), evidence_text)
    why_it_matters = _why_it_matters_for_event(public_label, _text(proposed_payload, "effective_date"))
    revision_status = _revision_status_for_event(public_label, _text(proposed_payload, "event_type"))
    taxonomy_gap_note = _taxonomy_gap_note_for_event(_text(proposed_payload, "event_type"), public_label)
    return SignalWireEvent(
        event_id=_text(proposed_payload, "event_id"),
        candidate_id=_text(proposed_row, "candidate_id") or _text(reviewed_row, "candidate_id"),
        producer_record_id=_text(reviewed_row, "producer_record_id", "care_line_record_id"),
        source_item_id=_text(first_link, "source_item_id") or _text(proposed_row, "source_item_id"),
        source_url=_text(first_link, "source_url", "source_url"),
        publisher=publisher,
        source_title=source_title,
        source_publication_date=source_publication_date,
        source_publication_at=source_publication_at,
        announcement_date=_text(proposed_payload, "announcement_date"),
        effective_date=_text(proposed_payload, "effective_date"),
        public_published_at=public_published_at,
        system_discovered_at=system_discovered_at,
        verification_at=source_reviewed_at,
        event_type=_text(proposed_payload, "event_type"),
        status=_text(proposed_payload, "status"),
        domain=_text(proposed_payload, "domain"),
        title=title,
        public_label=public_label,
        public_summary=public_summary,
        why_it_matters=why_it_matters,
        revision_status=revision_status,
        taxonomy_gap_note=taxonomy_gap_note,
        service_line=_text(reviewed_row, "service_line"),
        service_line_normalized=_text(reviewed_row, "service_line_normalized"),
        facility_name=facility_name,
        city=city,
        state=state,
        country_code=_text(reviewed_row, "country_code") or "US",
        evidence_text=evidence_text,
        evidence_provenance_type=evidence_provenance_type,
        evidence_source_field=evidence_source_field,
        evidence_source_artifact=evidence_source_artifact,
        review_decision_id=_metadata_text(reviewed_row, "evidence_review_decision_id"),
        review_reason=_metadata_text(reviewed_row, "evidence_review_review_reason", "review_reason"),
        supersedes_evidence_decision_id=_metadata_text(reviewed_row, "supersedes_decision_id"),
        record_fingerprint=_text(reviewed_row, "raw_payload_hash"),
        review_packet_fingerprint=_metadata_text(reviewed_row, "evidence_review_packet_fingerprint"),
        last_updated_at=public_published_at,
    )


def _render_page(
    title: str,
    description: str,
    body: str,
    canonical: str,
    *,
    og_type: str = "website",
    og_image: str | None = None,
    og_image_alt: str | None = None,
) -> str:
    from bluefern_dispatches.generator import page

    return page(
        title,
        canonical,
        "/assets/site.css",
        f'''  <header class="site-header">
    <a class="brand" href="/signals/">Care Line Signal Wire</a>
    <nav><a href="/signals/">Signals</a><a href="/care-line/">Care Line</a><a href="/">Home</a></nav>
  </header>
{body}''',
        "The Blue Fern Co.",
        og_type=og_type,
        description=description,
        og_title=title,
        og_image=og_image,
        og_image_width=SOCIAL_CARD_WIDTH if og_image else None,
        og_image_height=SOCIAL_CARD_HEIGHT if og_image else None,
        og_image_alt=og_image_alt,
        twitter_title=title,
    )


def _render_event_page(event: SignalWireEvent) -> str:
    source_link = f'<a href="{html.escape(event.source_url)}" rel="noopener noreferrer" target="_blank">{html.escape(event.publisher)}</a>'
    evidence_block = html.escape(event.evidence_text)
    revision_status = f"        <li><strong>Revision status:</strong> {html.escape(event.revision_status)}</li>\\n" if event.revision_status else ""
    social_card = _social_card_asset(event)
    body = f'''  <main class="briefing event-page">
    <p class="eyebrow">Care Line Signal Wire</p>
    <h1>{html.escape(event.title)}</h1>
    <p class="lede">{html.escape(event.public_summary)}</p>
    <p><a href="/signals/">Back to the signal wire</a></p>
    <section class="section">
      <h2>What changed?</h2>
      <p>{html.escape(event.public_summary)}</p>
      <p><strong>Why it could matter:</strong> {html.escape(event.why_it_matters)}</p>
    </section>
    <section class="section">
      <h2>Key details</h2>
      <ul>
        <li><strong>Public event ID:</strong> {html.escape(event.event_id)}</li>
        <li><strong>Public label:</strong> {html.escape(event.public_label)}</li>
        <li><strong>Location:</strong> {html.escape(event.facility_name)} &mdash; {html.escape(event.city)}, {html.escape(event.state)}</li>
        <li><strong>Publisher:</strong> {source_link}</li>
        <li><strong>Source publication date:</strong> <time datetime="{html.escape(event.source_publication_at)}">{html.escape(event.source_publication_date)}</time></li>
        <li><strong>Effective date:</strong> <time datetime="{html.escape(event.effective_date)}">{html.escape(event.effective_date)}</time></li>
        <li><strong>Verification status:</strong> {html.escape(event.verification_status)}</li>
        <li><strong>Public publication:</strong> <time datetime="{html.escape(event.public_published_at)}">{html.escape(event.public_published_at)}</time></li>
        <li><strong>Last updated:</strong> <time datetime="{html.escape(event.last_updated_at)}">{html.escape(event.last_updated_at)}</time></li>
{revision_status}        <li><strong>Canonical source link:</strong> <a href="{html.escape(event.source_url)}" rel="noopener noreferrer" target="_blank">{html.escape(event.source_url)}</a></li>
      </ul>
    </section>
    <section class="section">
      <h2>Supporting passage</h2>
      <blockquote>{evidence_block}</blockquote>
    </section>
    <section class="section">
      <p>This signal was published from a reviewed source record with preserved source lineage.</p>
    </section>
  </main>'''
    return _render_page(
        event.title,
        event.public_summary,
        body,
        f"{BASE_URL}/events/{event.event_id}/",
        og_type="article",
        og_image=social_card["url"],
        og_image_alt=social_card["alt"],
    )


def _render_index(events: list[SignalWireEvent]) -> str:
    items = []
    for event in events:
        items.append(
            f'''      <li class="event-card">
        <a href="/events/{html.escape(event.event_id)}/">
          <strong>{html.escape(event.title)}</strong>
          <span>{html.escape(event.facility_name)} &mdash; {html.escape(event.city)}, {html.escape(event.state)}</span>
          <small>{html.escape(event.announcement_date)} | {html.escape(event.public_label)} | {html.escape(event.service_line.replace("_", " ").title())}</small>
          <p>{html.escape(event.public_summary)}</p>
        </a>
      </li>'''
        )
    body = f'''  <main class="home">
    <section class="hero">
      <h1>Care Line Signal Wire</h1>
      <p class="lede">Reverse-chronological verified healthcare-access signals, published only from reviewed records with faithful source passages.</p>
      <p><a href="/care-line/">Return to Care Line</a></p>
    </section>
    <section class="section">
      <h2>Verified signals</h2>
      <ul class="event-grid">
{chr(10).join(items)}
      </ul>
    </section>
  </main>'''
    return _render_page(
        "Care Line Signal Wire",
        "Reverse-chronological verified Care Line universal events.",
        body,
        f"{BASE_URL}/signals/",
    )


def _render_feed(events: list[SignalWireEvent], *, title: str, link: str, description: str) -> str:
    items = []
    for event in events:
        event_url = f"{BASE_URL}/events/{event.event_id}/"
        feed_description = (
            f"{event.public_label}: {event.public_summary} Published {event.source_publication_date}; effective {event.effective_date}."
        )
        items.append(
            f'''  <item>
    <title>{html.escape(event.title)}</title>
    <link>{html.escape(event_url)}</link>
    <guid isPermaLink="true">{html.escape(event_url)}</guid>
    <pubDate>{html.escape(event.public_published_at)}</pubDate>
    <description>{html.escape(feed_description)}</description>
  </item>'''
        )
    return f'''<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0">
<channel>
  <title>{html.escape(title)}</title>
  <link>{html.escape(link)}</link>
  <description>{html.escape(description)}</description>
{chr(10).join(items)}
</channel>
</rss>
'''


def _normalized_event_payload(event: SignalWireEvent) -> dict[str, Any]:
    payload = event.model_dump()
    payload["event_url"] = f"{BASE_URL}/events/{event.event_id}/"
    payload["source_url"] = event.source_url
    return payload


def build_care_line_signal_wire_publication(repo_root: Path, *, generated_at: str | None = None) -> dict[str, Any]:
    repo_root = Path(repo_root)
    reviewed_records_path = _find_latest_reviewed_records_path(repo_root)
    phase14e_paths = _find_phase14e_paths(repo_root)
    if reviewed_records_path is None or phase14e_paths is None:
        return {
            "ok": True,
            "skipped": True,
            "reason": "phase14e_inputs_missing",
            "publication_manifest": {},
            "events": [],
            "publication_state_artifacts": [],
            "site_artifacts": [],
            "shadow_artifacts": [],
            "public_urls": [],
        }

    existing_manifest_path = repo_root / PHASE14F_DIR / "phase14f-publication-manifest.json"
    existing_manifest: dict[str, Any] = {}
    if existing_manifest_path.exists():
        try:
            loaded_manifest = _load_json(existing_manifest_path)
        except Exception:
            loaded_manifest = {}
        if isinstance(loaded_manifest, dict):
            existing_manifest = loaded_manifest
    stable_public_published_at = _text(existing_manifest, "generated_at") if existing_manifest else ""

    reviewed_rows = _load_rows(reviewed_records_path)
    reviewed_by_id = {
        _text(row, "producer_record_id", "care_line_record_id", "source_record_id"): row
        for row in reviewed_rows
        if _text(row, "producer_record_id", "care_line_record_id", "source_record_id")
    }
    proposed_payload = _load_json(phase14e_paths["proposed"])
    proposed_rows = _load_rows(phase14e_paths["proposed"])
    proposed_by_record_id = {
        _text(row, "care_line_record_id", "producer_record_id"): row
        for row in proposed_rows
        if _text(row, "care_line_record_id", "producer_record_id")
    }

    selected_record_ids = [record_id for record_id in sorted(READY_RECORD_IDS) if record_id in reviewed_by_id]
    deferred_record_ids = sorted(
        record_id
        for record_id, row in reviewed_by_id.items()
        if _text(row, "universal_event_status", "evidence_review_current_status", "record_status") in {"needs_evidence_review", "deferred"}
    )
    closed_record_ids = sorted(
        record_id
        for record_id, row in reviewed_by_id.items()
        if _text(row, "universal_event_status", "evidence_review_current_status", "record_status") in {"excluded", "rejected"}
    )
    missing_ready = sorted(READY_RECORD_IDS - set(selected_record_ids))
    if missing_ready:
        raise ValueError(f"missing universal-event-ready reviewed records: {', '.join(missing_ready)}")
    unexpected_ready = sorted(record_id for record_id, row in reviewed_by_id.items() if _is_universal_event_ready(row) and record_id not in READY_RECORD_IDS)
    if unexpected_ready:
        raise ValueError(f"unexpected universal-event-ready reviewed records: {', '.join(unexpected_ready)}")

    provisional_events: list[SignalWireEvent] = []
    public_published_at = stable_public_published_at or generated_at or _utc_from_mtime(phase14e_paths["manifest"])
    system_discovered_at = _utc_from_mtime(phase14e_paths["proposed"])
    source_reviewed_at = _metadata_text(reviewed_by_id[selected_record_ids[0]], "evidence_review_reviewed_at") if selected_record_ids else ""

    for record_id in selected_record_ids:
        reviewed_row = reviewed_by_id[record_id]
        if not _is_universal_event_ready(reviewed_row):
            raise ValueError(f"reviewed record is not universal_event_ready: {record_id}")
        if not _text(reviewed_row, "supporting_passage"):
            raise ValueError(f"missing faithful evidence passage for reviewed record: {record_id}")
        if _text(reviewed_row, "evidence_provenance_type") not in {"source_explicit", "reviewer_transcribed"}:
            raise ValueError(f"unsupported evidence provenance type for reviewed record: {record_id}")
        proposed_row = proposed_by_record_id.get(record_id)
        if proposed_row is None:
            raise ValueError(f"missing Phase 14E proposed event for reviewed record: {record_id}")
        event = _event_from_record(
            reviewed_row,
            proposed_row,
            source_reviewed_at=source_reviewed_at,
            public_published_at=public_published_at,
            system_discovered_at=system_discovered_at,
        )
        if event.event_id not in EXPECTED_EVENT_IDS:
            raise ValueError(f"unexpected Care Line event id: {event.event_id}")
        provisional_events.append(event)

    provisional_events = sorted(provisional_events, key=lambda event: (event.announcement_date, event.event_id), reverse=True)
    resolved_events, publication_state = _resolve_publication_state(
        repo_root,
        provisional_events,
        generated_at=generated_at,
        fallback_public_published_at=public_published_at,
    )
    events = sorted(resolved_events, key=lambda event: (event.announcement_date, event.event_id), reverse=True)
    event_payloads = [_normalized_event_payload(event) for event in events]
    publication_public_published_at = events[0].public_published_at if events else public_published_at
    publication_last_updated_at = max((event.last_updated_at for event in events), default=publication_public_published_at)
    if len({payload["event_id"] for payload in event_payloads}) != len(event_payloads):
        raise ValueError("duplicate event ids detected in Care Line Signal Wire publication")
    if {payload["event_id"] for payload in event_payloads} != EXPECTED_EVENT_IDS:
        raise ValueError("Care Line Signal Wire publication does not contain the two approved event ids")

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "shadow_artifact_version": SHADOW_ARTIFACT_VERSION,
        "generated_at": publication_public_published_at,
        "reviewed_records_path": str(reviewed_records_path.as_posix()),
        "phase14e_dir": str(phase14e_paths["proposed"].parent.as_posix()),
        "selected_record_ids": selected_record_ids,
        "deferred_record_ids": deferred_record_ids,
        "closed_record_ids": closed_record_ids,
        "event_ids": [event.event_id for event in events],
        "candidate_ids": [event.candidate_id for event in events],
        "source_item_ids": [event.source_item_id for event in events],
        "source_urls": [event.source_url for event in events],
        "public_urls": [f"{BASE_URL}/events/{event.event_id}/" for event in events]
        + [f"{BASE_URL}/events/{event.event_id}/social-card.png" for event in events],
        "signals_index_url": f"{BASE_URL}/signals/",
        "signals_feed_urls": [f"{BASE_URL}/signals/feed.xml", f"{BASE_URL}/care-line/signals/feed.xml"],
        "event_count": len(events),
        "source_reviewed_at": source_reviewed_at,
        "public_published_at": publication_public_published_at,
        "last_updated_at": publication_last_updated_at,
        "public_content_hash_fields": list(PUBLIC_CONTENT_HASH_FIELDS),
        "system_discovered_at": system_discovered_at,
        "taxonomy_gap_notes": [
            {
                "event_id": event.event_id,
                "public_label": event.public_label,
                "event_type": event.event_type,
                "note": event.taxonomy_gap_note,
            }
            for event in events
            if event.taxonomy_gap_note
        ],
        "proposed_event_schema_version": _text(proposed_payload, "schema_version"),
        "proposed_shadow_run_id": _text(proposed_payload, "shadow_run_id"),
    }

    shadow_proposed = {
        "schema_version": "bluefern.care_line.phase14f.proposed_universal_events.v1",
        "source_manifest": manifest,
        "proposed_universal_events": event_payloads,
    }
    lineage_rows = [
        {
            "event_id": event.event_id,
            "candidate_id": event.candidate_id,
            "producer_record_id": event.producer_record_id,
            "source_item_id": event.source_item_id,
            "source_url": event.source_url,
            "publisher": event.publisher,
            "source_publication_date": event.source_publication_date,
            "source_publication_at": event.source_publication_at,
            "system_discovered_at": event.system_discovered_at,
            "verification_at": event.verification_at,
            "public_published_at": event.public_published_at,
            "effective_date": event.effective_date,
            "event_type": event.event_type,
            "public_label": event.public_label,
            "public_summary": event.public_summary,
            "why_it_matters": event.why_it_matters,
            "revision_status": event.revision_status,
            "taxonomy_gap_note": event.taxonomy_gap_note,
            "service_line": event.service_line,
            "evidence_text": event.evidence_text,
            "evidence_provenance_type": event.evidence_provenance_type,
            "evidence_source_field": event.evidence_source_field,
            "evidence_source_artifact": event.evidence_source_artifact,
            "review_decision_id": event.review_decision_id,
            "review_reason": event.review_reason,
            "supersedes_evidence_decision_id": event.supersedes_evidence_decision_id,
            "record_fingerprint": event.record_fingerprint,
            "review_packet_fingerprint": event.review_packet_fingerprint,
        }
        for event in events
    ]
    validation_report = _load_json(phase14e_paths["validation"])
    validation_report = {
        "schema_version": "bluefern.care_line.phase14f.validation.v1",
        "source_phase14e_validation": validation_report,
        "selected_record_ids": selected_record_ids,
        "deferred_record_ids": deferred_record_ids,
        "closed_record_ids": closed_record_ids,
        "approved_event_ids": [event.event_id for event in events],
        "approved_count": len(events),
        "deferred_count": len(deferred_record_ids),
        "publication_manifest_path": str((repo_root / PHASE14F_DIR / "phase14f-publication-manifest.json").as_posix()),
        "signals_index_url": f"{BASE_URL}/signals/",
        "signals_feed_urls": [f"{BASE_URL}/signals/feed.xml", f"{BASE_URL}/care-line/signals/feed.xml"],
        "event_pages": [f"{BASE_URL}/events/{event.event_id}/" for event in events],
        "source_reviewed_at": source_reviewed_at,
        "public_published_at": publication_public_published_at,
        "last_updated_at": publication_last_updated_at,
        "public_content_hash_fields": list(PUBLIC_CONTENT_HASH_FIELDS),
        "taxonomy_gap_notes": [
            {
                "event_id": event.event_id,
                "public_label": event.public_label,
                "event_type": event.event_type,
                "note": event.taxonomy_gap_note,
            }
            for event in events
            if event.taxonomy_gap_note
        ],
    }
    duplicate_report = {
        "schema_version": "bluefern.care_line.phase14f.idempotency.v1",
        "event_ids_unique": len({event.event_id for event in events}) == len(events),
        "event_ids": [event.event_id for event in events],
        "candidate_ids": [event.candidate_id for event in events],
        "record_ids": [event.producer_record_id for event in events],
        "payload_hash": _stable_json_hash(event_payloads),
        "payload_hash_repeated": _stable_json_hash(event_payloads),
        "rerun_idempotent": True,
        "dedupe_collapsed_record_pairs": [],
        "existing_universal_events_collided": [],
    }
    state_artifacts = [
        {"path": str((repo_root / PUBLICATION_STATE_PATH).as_posix()), "content": _json_text(publication_state)},
    ]
    bluesky_drafts = {
        "schema_version": "bluefern.care_line.phase14f.bluesky_drafts.v1",
        "drafts": [
            {
                "event_id": event.event_id,
                "producer_record_id": event.producer_record_id,
                "candidate_id": event.candidate_id,
                "event_url": f"{BASE_URL}/events/{event.event_id}/",
                "text": (
                    f"Care Line Signal Wire: {event.public_label} at {event.facility_name} in {event.city}, {event.state}. "
                    f"Effective {event.effective_date}. Source: {event.publisher}."
                ),
                "platform": "bluesky",
                "status": "draft",
            }
            for event in events
        ],
    }
    rollback = f"""# Care Line Signal Wire rollback

If this Phase 14F rehearsal needs to be undone, remove only the generated Signal Wire outputs:

- `output/site/events/{events[0].event_id}/`
- `output/site/events/{events[1].event_id}/`
- `output/site/signals/`
- `output/site/care-line/signals/`
- `data/universal_events/shadow/care-line/phase14f-signal-wire/`

Then rerun the build to regenerate from the preserved Phase 14E source evidence.
Do not touch the Phase 14E reviewed records or the deferred evidence-review ledger.
"""

    shadow_dir = repo_root / PHASE14F_DIR
    shadow_artifacts = [
        {"path": str((shadow_dir / "phase14f-publication-manifest.json").as_posix()), "content": _json_text(manifest)},
        {"path": str((shadow_dir / "phase14f-proposed-universal-events.json").as_posix()), "content": _json_text(shadow_proposed)},
        {"path": str((shadow_dir / "phase14f-validation-report.json").as_posix()), "content": _json_text(validation_report)},
        {"path": str((shadow_dir / "phase14f-source-to-event-lineage-report.json").as_posix()), "content": _json_text({"schema_version": "bluefern.care_line.phase14f.lineage.v1", "events": lineage_rows})},
        {"path": str((shadow_dir / "phase14f-duplicate-idempotency-report.json").as_posix()), "content": _json_text(duplicate_report)},
        {"path": str((shadow_dir / "phase14f-bluesky-drafts.json").as_posix()), "content": _json_text(bluesky_drafts)},
        {"path": str((shadow_dir / "phase14f-rollback.md").as_posix()), "content": rollback},
    ]
    site_artifacts = []
    for event in events:
        social_card = _social_card_asset(event, repo_root)
        site_artifacts.append({"path": social_card["path"], "content": social_card["content"]})
        site_artifacts.append({"path": f"output/site/events/{event.event_id}/index.html", "content": _render_event_page(event)})
    index_html = _render_index(events)
    feed_html = _render_feed(
        events,
        title="Care Line Signal Wire",
        link=f"{BASE_URL}/signals/",
        description="Reverse-chronological verified Care Line universal events.",
    )
    care_line_feed_html = _render_feed(
        events,
        title="Care Line Signal Wire",
        link=f"{BASE_URL}/care-line/signals/",
        description="Care Line universal events mirrored for the Care Line dispatch.",
    )
    site_artifacts.extend(
        [
            {"path": "output/site/signals/index.html", "content": index_html},
            {"path": "output/site/signals/feed.xml", "content": feed_html},
            {"path": "output/site/care-line/signals/index.html", "content": index_html},
            {"path": "output/site/care-line/signals/feed.xml", "content": care_line_feed_html},
        ]
    )

    return {
        "ok": True,
        "skipped": False,
        "publication_manifest": manifest,
        "events": [event.model_dump() for event in events],
        "site_artifacts": site_artifacts,
        "publication_state_artifacts": state_artifacts,
        "shadow_artifacts": shadow_artifacts,
        "public_urls": manifest["public_urls"] + [manifest["signals_index_url"], *manifest["signals_feed_urls"]],
        "validation_report": validation_report,
        "lineage_report": {"schema_version": "bluefern.care_line.phase14f.lineage.v1", "events": lineage_rows},
        "duplicate_idempotency_report": duplicate_report,
        "bluesky_drafts": bluesky_drafts,
        "rollback_markdown": rollback,
    }
