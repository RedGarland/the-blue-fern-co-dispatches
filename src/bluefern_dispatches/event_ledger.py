from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
DATABASE_SCHEMA_VERSION = 1
DEFAULT_LEDGER_PATH = Path("data/state/dispatches.sqlite")
DISCOVERY_CLASSES = {
    "new_development",
    "meaningful_update",
    "duplicate_source",
    "historical_recovery",
}
MEANINGFUL_UPDATE_TERMS = {
    "became scheduled",
    "completed",
    "closed",
    "closure date",
    "expanded",
    "extension",
    "failed",
    "increased",
    "longer",
    "reopened",
    "reversed",
    "worsened",
}


@dataclass(frozen=True)
class EventRecord:
    event_id: str
    schema_version: int
    dispatch: str
    event_type: str | None
    normalized_subject: str | None
    location: dict[str, Any]
    effective_at: str | None
    first_discovered_at: str | None
    last_observed_at: str | None
    discovery_class: str
    status: str | None
    sources: list[dict[str, Any]] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    impact: dict[str, Any] = field(default_factory=dict)
    affected_population: Any = None
    confidence: str | None = None
    qualification: dict[str, Any] = field(default_factory=dict)
    review: dict[str, Any] = field(default_factory=dict)
    publication: dict[str, Any] = field(default_factory=dict)
    lineage: dict[str, Any] = field(default_factory=dict)
    domain_data: dict[str, Any] = field(default_factory=dict)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def nonempty(value: Any) -> str:
    return str(value or "").strip()


def normalized_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", nonempty(value)).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", " ", text).strip().lower()
    return re.sub(r"\s+", " ", text)


def slug(value: Any) -> str:
    text = normalized_text(value)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-") or "unknown"


def canonical_source_url(value: Any) -> str:
    url = nonempty(value)
    if not url:
        return ""
    parsed = urllib.parse.urlsplit(url)
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = re.sub(r"/+", "/", parsed.path or "/").rstrip("/")
    query_pairs = [
        (key, val)
        for key, val in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}
    ]
    query = urllib.parse.urlencode(query_pairs, doseq=True)
    return urllib.parse.urlunsplit((scheme, netloc, path, query, ""))


def _first_present(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = nonempty(row.get(key))
        if value:
            return value
    return ""


def _date_or_none(value: Any) -> str | None:
    text = nonempty(value)
    if not text:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    return text


def food_line_event_identity_components(candidate: dict[str, Any]) -> dict[str, str]:
    location = _first_present(
        candidate,
        "location_name",
        "metro",
        "state_or_territory",
        "state_hint",
        "discovered_publisher",
        "direct_source_name",
    )
    organization = _first_present(
        candidate,
        "organization",
        "organization_name",
        "facility",
        "facility_name",
        "direct_source_name",
        "publisher",
        "discovered_publisher",
    )
    event_type = _first_present(candidate, "pressure_type", "event_type", "classification_status")
    normalized_subject = _first_present(
        candidate,
        "normalized_subject",
        "pressure_summary",
        "public_summary",
        "selected_title",
        "title",
        "discovered_title",
    )
    effective_at = _first_present(candidate, "effective_at", "event_date", "source_published_date")
    return {
        "dispatch": "food-line",
        "location": normalized_text(location),
        "organization": normalized_text(organization),
        "event_type": normalized_text(event_type),
        "effective_at": normalized_text(effective_at),
        "normalized_subject": normalized_text(normalized_subject),
    }


def event_id_for_components(components: dict[str, str]) -> str:
    dispatch = slug(components.get("dispatch") or "event")
    location = slug(components.get("location"))
    organization = slug(components.get("organization"))
    event_type = slug(components.get("event_type"))
    subject = slug(components.get("normalized_subject"))
    effective = re.sub(r"[^0-9]", "", nonempty(components.get("effective_at")))
    base = "-".join(part for part in (dispatch, location, organization, event_type) if part and part != "unknown")
    if effective:
        base = f"{base}-{effective}" if base else effective
    if not base or base == dispatch:
        digest = hashlib.sha256(json.dumps(components, sort_keys=True).encode("utf-8")).hexdigest()[:12]
        base = "-".join(part for part in (dispatch, subject[:48], digest) if part and part != "unknown")
    return base[:180]


def food_line_event_id(candidate: dict[str, Any]) -> str:
    return event_id_for_components(food_line_event_identity_components(candidate))


def source_observation_from_food_line_candidate(candidate: dict[str, Any], *, discovered_at: str | None = None) -> dict[str, Any]:
    source_url = _first_present(candidate, "source_url", "final_trace_url", "original_source_url", "discovered_url")
    canonical = _first_present(candidate, "canonical_source_url", "canonical_url") or canonical_source_url(source_url)
    title = _first_present(candidate, "selected_title", "title", "discovered_title")
    passage = _first_present(candidate, "exact_supporting_passage", "evidence_text", "pressure_evidence_summary", "summary_or_snippet")
    return {
        "source_url": source_url or None,
        "canonical_source_url": canonical or None,
        "publisher": _first_present(candidate, "publisher", "discovered_publisher", "direct_source_name") or None,
        "source_published_at": _date_or_none(_first_present(candidate, "source_published_at", "source_published_date", "published_at")),
        "title": title or None,
        "exact_supporting_passage": passage or None,
        "discovered_at": discovered_at or utc_now(),
        "confidence": _first_present(candidate, "confidence", "discovery_confidence") or None,
        "source_role": _first_present(candidate, "source_role", "discovery_source_type", "discovery_channel") or None,
    }


def event_record_from_food_line_candidate(
    candidate: dict[str, Any],
    *,
    discovery_class: str,
    event_id: str | None = None,
    discovered_at: str | None = None,
) -> EventRecord:
    if discovery_class not in DISCOVERY_CLASSES:
        raise ValueError(f"unsupported discovery_class: {discovery_class}")
    observed_at = discovered_at or utc_now()
    location = {
        "name": _first_present(candidate, "location_name", "metro", "state_or_territory") or None,
        "state_or_territory": _first_present(candidate, "state_or_territory", "state_hint") or None,
        "metro": _first_present(candidate, "metro") or None,
    }
    source = source_observation_from_food_line_candidate(candidate, discovered_at=observed_at)
    return EventRecord(
        event_id=event_id or food_line_event_id(candidate),
        schema_version=SCHEMA_VERSION,
        dispatch="food-line",
        event_type=_first_present(candidate, "pressure_type", "event_type", "classification_status") or None,
        normalized_subject=normalized_text(_first_present(candidate, "normalized_subject", "pressure_summary", "public_summary", "selected_title", "title", "discovered_title")) or None,
        location=location,
        effective_at=_date_or_none(_first_present(candidate, "effective_at", "event_date", "source_published_date")),
        first_discovered_at=observed_at,
        last_observed_at=observed_at,
        discovery_class=discovery_class,
        status=_first_present(candidate, "status", "classification_status", "candidate_review_status") or None,
        sources=[source],
        evidence={
            "exact_supporting_passage": source.get("exact_supporting_passage"),
            "basis": _first_present(candidate, "evidence_text_basis", "pressure_summary_derivation_status") or None,
        },
        impact={"pressure_type": _first_present(candidate, "pressure_type") or None},
        affected_population=candidate.get("affected_population") or candidate.get("affected_groups"),
        confidence=_first_present(candidate, "confidence", "discovery_confidence") or None,
        qualification={
            "classification_status": _first_present(candidate, "classification_status") or None,
            "public_claim_eligible": bool(candidate.get("public_claim_eligible")),
            "public_claim_blockers": list(candidate.get("public_claim_blockers") or []),
        },
        review={
            "manual_review_required": bool(candidate.get("manual_review_required")),
            "candidate_review_status": _first_present(candidate, "candidate_review_status", "review_status") or None,
        },
        publication={"phase1_shadow_only": True, "eligible_to_publish_from_ledger": False},
        lineage={
            "candidate_id": _first_present(candidate, "candidate_id", "review_item_id") or None,
            "duplicate_of": _first_present(candidate, "duplicate_of") or None,
            "discovery_channel": _first_present(candidate, "discovery_channel") or None,
        },
        domain_data={key: candidate.get(key) for key in ("pressure_type", "source_role", "date_match_status", "discovery_lane") if key in candidate},
    )


def initialize_ledger(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
            event_id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL,
            dispatch TEXT NOT NULL,
            event_type TEXT,
            normalized_subject TEXT,
            location_json TEXT NOT NULL,
            effective_at TEXT,
            first_discovered_at TEXT,
            last_observed_at TEXT,
            discovery_class TEXT NOT NULL CHECK (discovery_class IN ('new_development','meaningful_update','duplicate_source','historical_recovery')),
            status TEXT,
            sources_json TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            impact_json TEXT NOT NULL,
            affected_population_json TEXT,
            confidence TEXT,
            qualification_json TEXT NOT NULL,
            review_json TEXT NOT NULL,
            publication_json TEXT NOT NULL,
            lineage_json TEXT NOT NULL,
            domain_data_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS source_observations (
            observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
            source_url TEXT,
            canonical_source_url TEXT NOT NULL,
            publisher TEXT,
            source_published_at TEXT,
            title TEXT,
            exact_supporting_passage TEXT,
            discovered_at TEXT,
            confidence TEXT,
            source_role TEXT,
            UNIQUE(event_id, canonical_source_url)
        );
        CREATE TABLE IF NOT EXISTS event_observations (
            observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
            observed_at TEXT NOT NULL,
            discovery_class TEXT NOT NULL,
            candidate_json TEXT NOT NULL,
            material_change_reasons_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS agent_runs (
            run_id TEXT PRIMARY KEY,
            dispatch TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            status TEXT NOT NULL,
            summary_json TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
        (DATABASE_SCHEMA_VERSION, utc_now()),
    )


def connect_ledger(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    initialize_ledger(conn)
    return conn


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _event_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "event_id": row["event_id"],
        "schema_version": row["schema_version"],
        "dispatch": row["dispatch"],
        "event_type": row["event_type"],
        "normalized_subject": row["normalized_subject"],
        "location": json.loads(row["location_json"]),
        "effective_at": row["effective_at"],
        "first_discovered_at": row["first_discovered_at"],
        "last_observed_at": row["last_observed_at"],
        "discovery_class": row["discovery_class"],
        "status": row["status"],
        "sources": json.loads(row["sources_json"]),
        "evidence": json.loads(row["evidence_json"]),
        "impact": json.loads(row["impact_json"]),
        "affected_population": json.loads(row["affected_population_json"]) if row["affected_population_json"] else None,
        "confidence": row["confidence"],
        "qualification": json.loads(row["qualification_json"]),
        "review": json.loads(row["review_json"]),
        "publication": json.loads(row["publication_json"]),
        "lineage": json.loads(row["lineage_json"]),
        "domain_data": json.loads(row["domain_data_json"]),
    }


def get_event(conn: sqlite3.Connection, event_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM events WHERE event_id = ?", (event_id,)).fetchone()
    return _event_from_row(row) if row else None


def _material_change_reasons(candidate: dict[str, Any], existing: dict[str, Any] | None) -> list[str]:
    if not existing:
        return []
    reasons: list[str] = []
    status = _first_present(candidate, "status", "classification_status", "candidate_review_status")
    if status and status != nonempty(existing.get("status")):
        reasons.append("status changed")
    affected = candidate.get("affected_population") or candidate.get("affected_groups")
    if affected and affected != existing.get("affected_population"):
        reasons.append("affected population changed")
    text = normalized_text(
        " ".join(
            _first_present(candidate, key)
            for key in ("selected_title", "title", "pressure_summary", "public_summary", "evidence_text", "summary_or_snippet")
        )
    )
    for term in MEANINGFUL_UPDATE_TERMS:
        if term in text:
            reasons.append(f"material update term: {term}")
            break
    return reasons


def classify_food_line_candidate(
    conn: sqlite3.Connection,
    candidate: dict[str, Any],
    *,
    monitoring_start: str | None = None,
    event_id: str | None = None,
) -> tuple[str, list[str]]:
    resolved_event_id = event_id or food_line_event_id(candidate)
    existing = get_event(conn, resolved_event_id)
    effective_at = _date_or_none(_first_present(candidate, "effective_at", "event_date", "source_published_date"))
    if not existing and monitoring_start and effective_at and effective_at < monitoring_start:
        return "historical_recovery", ["effective date predates live monitoring window"]
    if not existing:
        return "new_development", ["no existing event with same event identity"]
    reasons = _material_change_reasons(candidate, existing)
    if reasons:
        return "meaningful_update", reasons
    return "duplicate_source", ["same event identity without material change"]


def upsert_event_record(conn: sqlite3.Connection, event: EventRecord, *, candidate: dict[str, Any], material_change_reasons: list[str] | None = None) -> dict[str, Any]:
    source = event.sources[0] if event.sources else {}
    canonical = nonempty(source.get("canonical_source_url")) or canonical_source_url(source.get("source_url")) or f"missing:{hashlib.sha256(_json(source).encode('utf-8')).hexdigest()}"
    with conn:
        conn.execute(
            """
            INSERT INTO events (
                event_id, schema_version, dispatch, event_type, normalized_subject, location_json,
                effective_at, first_discovered_at, last_observed_at, discovery_class, status,
                sources_json, evidence_json, impact_json, affected_population_json, confidence,
                qualification_json, review_json, publication_json, lineage_json, domain_data_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET
                last_observed_at = excluded.last_observed_at,
                discovery_class = excluded.discovery_class,
                status = COALESCE(excluded.status, events.status),
                sources_json = excluded.sources_json,
                evidence_json = excluded.evidence_json,
                impact_json = excluded.impact_json,
                affected_population_json = excluded.affected_population_json,
                confidence = COALESCE(excluded.confidence, events.confidence),
                qualification_json = excluded.qualification_json,
                review_json = excluded.review_json,
                publication_json = excluded.publication_json,
                lineage_json = excluded.lineage_json,
                domain_data_json = excluded.domain_data_json
            """,
            (
                event.event_id,
                event.schema_version,
                event.dispatch,
                event.event_type,
                event.normalized_subject,
                _json(event.location),
                event.effective_at,
                event.first_discovered_at,
                event.last_observed_at,
                event.discovery_class,
                event.status,
                _json(event.sources),
                _json(event.evidence),
                _json(event.impact),
                _json(event.affected_population) if event.affected_population is not None else None,
                event.confidence,
                _json(event.qualification),
                _json(event.review),
                _json(event.publication),
                _json(event.lineage),
                _json(event.domain_data),
            ),
        )
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO source_observations (
                event_id, source_url, canonical_source_url, publisher, source_published_at,
                title, exact_supporting_passage, discovered_at, confidence, source_role
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                source.get("source_url"),
                canonical,
                source.get("publisher"),
                source.get("source_published_at"),
                source.get("title"),
                source.get("exact_supporting_passage"),
                source.get("discovered_at"),
                source.get("confidence"),
                source.get("source_role"),
            ),
        )
        conn.execute(
            """
            INSERT INTO event_observations(event_id, observed_at, discovery_class, candidate_json, material_change_reasons_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.last_observed_at or utc_now(),
                event.discovery_class,
                _json(candidate),
                _json(material_change_reasons or []),
            ),
        )
    return {
        "event_id": event.event_id,
        "discovery_class": event.discovery_class,
        "source_observation_inserted": cursor.rowcount > 0,
    }


def write_food_line_shadow_events(
    root: Path,
    candidates: list[dict[str, Any]],
    *,
    run_id: str,
    edition_date: str,
    ledger_path: Path | None = None,
    monitoring_start: str | None = None,
) -> dict[str, Any]:
    path = ledger_path or root / DEFAULT_LEDGER_PATH
    written = 0
    duplicate_sources = 0
    counts = {key: 0 for key in sorted(DISCOVERY_CLASSES)}
    with connect_ledger(path) as conn:
        for candidate in candidates:
            event_id = food_line_event_id(candidate)
            discovery_class, reasons = classify_food_line_candidate(
                conn,
                candidate,
                monitoring_start=monitoring_start,
                event_id=event_id,
            )
            record = event_record_from_food_line_candidate(
                candidate,
                discovery_class=discovery_class,
                event_id=event_id,
            )
            result = upsert_event_record(conn, record, candidate=candidate, material_change_reasons=reasons)
            written += 1
            counts[discovery_class] += 1
            if not result["source_observation_inserted"]:
                duplicate_sources += 1
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO agent_runs(run_id, dispatch, started_at, completed_at, status, summary_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    "food-line",
                    None,
                    utc_now(),
                    "completed",
                    _json({"edition_date": edition_date, "candidate_count": len(candidates), "discovery_class_counts": counts}),
                ),
            )
    return {
        "ok": True,
        "shadow_mode": True,
        "ledger_path": str(path),
        "candidate_count": len(candidates),
        "events_written": written,
        "duplicate_source_observations": duplicate_sources,
        "discovery_class_counts": counts,
    }


def inspect_ledger(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"ok": True, "ledger_path": str(path), "event_count": 0, "discovery_class_counts": {}, "latest_observations": []}
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        counts = {
            row["discovery_class"]: row["count"]
            for row in conn.execute("SELECT discovery_class, COUNT(*) AS count FROM events GROUP BY discovery_class ORDER BY discovery_class")
        }
        latest = [
            dict(row)
            for row in conn.execute(
                """
                SELECT event_id, observed_at, discovery_class
                FROM event_observations
                ORDER BY observed_at DESC, observation_id DESC
                LIMIT 10
                """
            )
        ]
        event_count = conn.execute("SELECT COUNT(*) AS count FROM events").fetchone()["count"]
    return {
        "ok": True,
        "ledger_path": str(path),
        "event_count": event_count,
        "discovery_class_counts": counts,
        "latest_observations": latest,
    }
