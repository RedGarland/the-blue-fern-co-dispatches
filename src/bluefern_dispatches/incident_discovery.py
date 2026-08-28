from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date as date_type, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping


SEVERE_INCIDENT_TYPES = {
    "evacuation",
    "flood",
    "hurricane",
    "infrastructure_failure",
    "power_outage",
    "severe_storm",
    "storm",
    "utility_failure",
    "water_outage",
    "wildfire",
}

SEVERITY_MARKERS = (
    "multi-day",
    "multiple days",
    "several days",
    "prolonged",
    "prolonged outage",
    "widespread closures",
    "large affected population",
    "emergency declaration",
    "declared a state of emergency",
    "evacuation",
    "evacuated",
    "explicit humanitarian response",
    "explicit relief response",
    "widespread power outage",
    "prolonged utility failure",
)

CARE_LINE_CONSEQUENCE_TERMS = (
    "hospital closed",
    "clinic closed",
    "emergency department closed",
    "emergency room closed",
    "appointments canceled",
    "appointments rescheduled",
    "dialysis disruption",
    "oxygen refill",
    "oxygen access",
    "medical equipment power",
    "medication refrigeration",
    "service suspension",
    "reduced hours",
    "reduced capacity",
    "evacuation",
)

FOOD_LINE_CONSEQUENCE_TERMS = (
    "food spoilage",
    "refrigeration loss",
    "unable to cook",
    "grocery closed",
    "grocery limited operations",
    "pantry demand",
    "emergency meal distribution",
    "hot meals",
    "food distribution",
    "SNAP disruption",
)

INCIDENT_SEED_LEDGER_DIR = Path("data") / "dispatches" / "incidents"
INCIDENT_SEED_LEDGER_PATH = INCIDENT_SEED_LEDGER_DIR / "incident_seeds.json"
INCIDENT_SEED_DISCOVERY_REPORT_PATH = INCIDENT_SEED_LEDGER_DIR / "incident_seed_discovery_report.json"
INCIDENT_SEED_SOURCE_FILENAMES = ("manual_sources.json", "discovered_sources.json", "auto_sources.json")
INCIDENT_SEED_EXPIRY_DAYS = 14
INCIDENT_SEED_DEFAULT_MAX_SOURCE_FILES = 120
INCIDENT_SEED_DEFAULT_MAX_RECORDS_PER_FILE = 400


def _text(value: Any) -> str:
    return str(value or "").strip()


def _first_text(seed: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        text = _text(seed.get(key))
        if text:
            return text
    return ""


def _normalize_place(seed: Mapping[str, Any]) -> str:
    place = _first_text(seed, "place", "location_name", "city", "incident_place", "jurisdiction")
    state = _first_text(seed, "state", "source_state", "jurisdiction_state", "state_hint")
    if place and state and state.casefold() not in place.casefold():
        return f"{place}, {state}"
    return place or state


def _incident_type(seed: Mapping[str, Any]) -> str:
    return _first_text(seed, "incident_type", "incident_kind", "type", "category").casefold().replace(" ", "_")


def _severity_evidence(seed: Mapping[str, Any]) -> str:
    support_text = _seed_support_text(seed)
    if support_text:
        return " ".join(
            part
            for part in (
                support_text,
                _first_text(seed, "title", "source_title"),
            )
            if part
        ).casefold()
    return " ".join(
        part
        for part in (
            _first_text(seed, "severity_evidence"),
            _first_text(seed, "summary"),
            _first_text(seed, "description"),
            _first_text(seed, "title"),
            _first_text(seed, "source_title"),
        )
        if part
    ).casefold()


def _seed_id(seed: Mapping[str, Any]) -> str:
    return _first_text(seed, "incident_id", "seed_id", "source_id", "id") or "incident-seed"


def _seed_support_text(record: Mapping[str, Any]) -> str:
    exact_support = _first_text(record, "exact_supporting_passage", "claim_supported", "supporting_context_excerpt")
    if exact_support:
        return exact_support
    body_text = _first_text(record, "content_text", "body_text", "article_text")
    if body_text:
        return body_text
    return _first_text(record, "summary", "summary_or_snippet", "description", "evidence_text")


def _trigger_reason(seed: Mapping[str, Any]) -> str:
    severity_text = _severity_evidence(seed)
    incident_type = _incident_type(seed)
    if incident_type not in SEVERE_INCIDENT_TYPES:
        return "incident_type_out_of_scope"
    if any(marker in severity_text for marker in SEVERITY_MARKERS):
        return "severe_incident_evidence"
    if re.search(r"\b\d+\s+day(?:s)?\b", severity_text) or re.search(r"\b\d+\s+hours?\b", severity_text):
        return "duration_based_severity"
    return "insufficient_severity_evidence"


def _nonempty(value: Any) -> str:
    return str(value or "").strip()


def _load_json_rows(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        return []
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("incident_seeds", "seeds", "sources", "records", "items"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
    return []


def _unique_texts(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = _nonempty(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _parse_date(value: str) -> date_type | None:
    text = _nonempty(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%a, %d %b %Y %H:%M:%S %Z"):
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return parsed.date()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _format_date(value: date_type | None) -> str:
    return value.isoformat() if value else ""


def _seed_text(record: Mapping[str, Any]) -> str:
    support_text = _seed_support_text(record)
    if support_text:
        return _normalize_text(
            " ".join(
                part
                for part in (
                    support_text,
                    _first_text(record, "title", "source_title"),
                    _first_text(record, "publisher", "source_name"),
                    _first_text(record, "place", "location_name", "city", "incident_place", "jurisdiction"),
                    _first_text(record, "state", "source_state", "jurisdiction_state", "state_hint"),
                    _first_text(record, "incident_type", "incident_kind", "type", "category"),
                )
                if part
            )
        )
    parts = [
        _first_text(record, "title", "source_title"),
        _first_text(record, "summary", "summary_or_snippet", "description", "content_text", "body_text", "article_text"),
        _first_text(record, "publisher", "source_name"),
        _first_text(record, "place", "location_name", "city", "incident_place", "jurisdiction"),
        _first_text(record, "state", "source_state", "jurisdiction_state", "state_hint"),
        _first_text(record, "incident_type", "incident_kind", "type", "category"),
    ]
    return _normalize_text(" ".join(part for part in parts if part))


def _normalize_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _nonempty(value).casefold()).strip()


def _seed_place(record: Mapping[str, Any]) -> str:
    place = _first_text(record, "place", "location_name", "city", "incident_place", "jurisdiction", "location_scope", "geographic_scope")
    return place


def _seed_state(record: Mapping[str, Any]) -> str:
    return _first_text(record, "state", "source_state", "jurisdiction_state", "state_hint")


def _seed_source_url(record: Mapping[str, Any]) -> str:
    return _first_text(record, "source_url", "url", "canonical_url")


def _seed_source_title(record: Mapping[str, Any]) -> str:
    return _first_text(record, "source_title", "title")


def _seed_source_date(record: Mapping[str, Any]) -> str:
    for key in ("source_date", "published_at", "date", "published_date", "retrieved_at"):
        text = _first_text(record, key)
        if text:
            parsed = _parse_date(text)
            if parsed:
                return parsed.isoformat()
    return ""


def _seed_incident_start_date(record: Mapping[str, Any]) -> str:
    for key in ("incident_start_date", "incident_date", "start_date"):
        text = _first_text(record, key)
        if text:
            parsed = _parse_date(text)
            if parsed:
                return parsed.isoformat()
    return ""


def _seed_window_key(record: Mapping[str, Any]) -> str:
    return _seed_incident_start_date(record)


def _seed_dispatch_targets(record: Mapping[str, Any], incident_type: str) -> list[str]:
    targets = [str(item).strip() for item in (record.get("dispatch_targets") or []) if str(item).strip()] if isinstance(record.get("dispatch_targets"), list) else []
    if targets:
        return list(dict.fromkeys(targets))
    if incident_type in SEVERE_INCIDENT_TYPES:
        return ["care-line", "food-line"]
    return []


def _seed_severity_basis(record: Mapping[str, Any]) -> tuple[str, str]:
    text = _severity_evidence(record)
    evidence_bits: list[str] = []
    for marker in SEVERITY_MARKERS:
        if marker in text and marker not in evidence_bits:
            evidence_bits.append(marker)
    if re.search(r"\b(?:\d+|multiple|several)\s+days?\b", text) and "multi-day duration" not in evidence_bits:
        evidence_bits.append("multi-day duration")
    if re.search(r"\b(?:\d+|multiple|several)\s+hours?\b", text) and "prolonged hours" not in evidence_bits:
        evidence_bits.append("prolonged hours")
    if "state of emergency" in text and "state of emergency" not in evidence_bits:
        evidence_bits.append("state of emergency")
    if "emergency declaration" in text and "emergency declaration" not in evidence_bits:
        evidence_bits.append("emergency declaration")
    if "evacuation" in text and "evacuation" not in evidence_bits:
        evidence_bits.append("evacuation")
    if "widespread" in text and "widespread impact" not in evidence_bits:
        evidence_bits.append("widespread impact")
    return "; ".join(evidence_bits), text


def _seed_incident_type(record: Mapping[str, Any]) -> str:
    explicit = _incident_type(record)
    if explicit in SEVERE_INCIDENT_TYPES:
        return explicit
    text = _seed_text(record)
    if any(term in text for term in ("power outage", "blackout", "utility outage", "electric outage")):
        return "power_outage"
    if any(term in text for term in ("water outage", "boil advisory", "water service failure", "no water")):
        return "water_outage"
    if any(term in text for term in ("wildfire", "wild land fire", "smoke evacuation", "smoke from wildfire")):
        return "wildfire"
    if any(term in text for term in ("hurricane", "tropical storm", "storm surge")):
        return "hurricane"
    if any(term in text for term in ("flood", "flooding", "flash flood", "river flood")):
        return "flood"
    if any(term in text for term in ("severe storm", "storm damage", "storm emergency")):
        return "severe_storm"
    if any(term in text for term in ("evacuation order", "mandatory evacuation", "evacuated")):
        return "evacuation"
    if any(term in text for term in ("utility failure", "grid failure", "infrastructure failure", "outage")):
        return "utility_failure"
    return ""


def _seed_identity_key(seed: Mapping[str, Any]) -> str:
    parts = [
        _incident_type(seed),
        _normalize_text(_seed_place(seed) or _first_text(seed, "location_scope", "geographic_scope")),
        _normalize_text(_seed_state(seed)),
        _first_text(seed, "incident_window_key"),
    ]
    return "|".join(part for part in parts if part)


def _seed_record_key(record: Mapping[str, Any], source_path: Path) -> str:
    source_url = _seed_source_url(record)
    if source_url:
        return source_url
    source_id = _first_text(record, "incident_id", "seed_id", "source_id", "id")
    if source_id:
        return f"{source_path.as_posix()}::{source_id}"
    title = _seed_source_title(record)
    return f"{source_path.as_posix()}::{title}"


def _seed_from_record(record: Mapping[str, Any], *, source_path: Path, discovered_at: str) -> dict[str, Any] | None:
    incident_type = _seed_incident_type(record)
    if incident_type not in SEVERE_INCIDENT_TYPES:
        return None
    severity_basis, severity_text = _seed_severity_basis(record)
    trigger_reason = _trigger_reason(
        {
            "incident_type": incident_type,
            "severity_evidence": severity_text,
            "summary": record.get("summary") or record.get("summary_or_snippet") or "",
            "description": record.get("description") or record.get("content_text") or record.get("body_text") or "",
            "title": record.get("title") or record.get("source_title") or "",
            "source_title": record.get("source_title") or record.get("title") or "",
        }
    )
    if trigger_reason == "incident_type_out_of_scope" or trigger_reason == "insufficient_severity_evidence":
        return None
    place = _seed_place(record)
    state = _seed_state(record)
    if not place and not state:
        return None
    source_date = _seed_source_date(record)
    explicit_incident_start_date = _seed_window_key(record)
    incident_start_date = explicit_incident_start_date or source_date
    source_title = _seed_source_title(record)
    source_url = _seed_source_url(record)
    if not source_title and not source_url:
        return None
    incident_id = _first_text(record, "incident_id", "seed_id", "source_id", "id")
    if not incident_id:
        key_source = _seed_identity_key(
        {
            "incident_type": incident_type,
            "place": place,
            "state": state,
            "incident_window_key": explicit_incident_start_date,
        }
    )
        incident_id = f"incident-{re.sub(r'[^a-z0-9]+', '-', key_source.lower()).strip('-')[:48] or 'seed'}"
    dispatch_targets = _seed_dispatch_targets(record, incident_type)
    source_text = _seed_text(record)
    if not severity_basis:
        severity_basis = "incident type plus source evidence"
    return {
        "incident_id": incident_id,
        "seed_key": _seed_identity_key(
            {
                "incident_type": incident_type,
                "place": place,
                "state": state,
                "incident_window_key": explicit_incident_start_date,
            }
        ),
        "place": place,
        "state": state,
        "incident_type": incident_type,
        "incident_start_date": incident_start_date,
        "incident_window_key": explicit_incident_start_date,
        "source_url": source_url,
        "source_title": source_title,
        "source_date": source_date,
        "severity_basis": severity_basis,
        "severity_evidence": severity_text or source_text,
        "discovered_at": discovered_at,
        "provenance": "source_record",
        "source_record_key": _seed_record_key(record, source_path),
        "source_record_id": _first_text(record, "incident_id", "seed_id", "source_id", "id"),
        "source_path": str(source_path),
        "source_dispatch_slug": (
            _first_text(record, "dispatch_slug")
            or (
                source_path.parts[source_path.parts.index("dispatches") + 1]
                if "dispatches" in source_path.parts and source_path.parts.index("dispatches") + 1 < len(source_path.parts)
                else ""
            )
        ),
        "dispatch_targets": dispatch_targets,
        "incident_status": _first_text(record, "incident_status") or "active",
        "recheck_after": "",
        "expires_on": "",
    }


def _seed_is_expired(seed: Mapping[str, Any], today: date_type | None = None) -> bool:
    today = today or datetime.now(timezone.utc).date()
    status = _first_text(seed, "incident_status").casefold()
    if status in {"closed", "expired", "resolved"}:
        return True
    expiry = _parse_date(_first_text(seed, "expires_on", "expires_at", "recheck_after"))
    if expiry and today > expiry:
        return True
    source_date = _parse_date(_first_text(seed, "last_seen_date", "source_date", "incident_start_date"))
    if source_date and today > source_date + timedelta(days=INCIDENT_SEED_EXPIRY_DAYS):
        return True
    return False


def _incident_source_paths(root: Path, *, max_source_files: int = INCIDENT_SEED_DEFAULT_MAX_SOURCE_FILES) -> list[Path]:
    candidates: list[Path] = []
    dispatches_root = root / "data" / "dispatches"
    if dispatches_root.exists():
        for dispatch_dir in sorted([path for path in dispatches_root.iterdir() if path.is_dir()]):
            sources_root = dispatch_dir / "sources"
            if not sources_root.exists():
                continue
            for path in sorted(sources_root.rglob("*.json")):
                if path.name in INCIDENT_SEED_SOURCE_FILENAMES or path.name == "story_memory.json":
                    candidates.append(path)
    story_memory = root / "data" / "records" / "story_memory.json"
    if story_memory.exists():
        candidates.append(story_memory)
    unique_candidates: list[Path] = []
    seen: set[Path] = set()
    for path in sorted(candidates):
        if path in seen:
            continue
        seen.add(path)
        unique_candidates.append(path)
    if len(unique_candidates) > max_source_files:
        unique_candidates = sorted(unique_candidates, key=lambda path: path.stat().st_mtime, reverse=True)[:max_source_files]
    return unique_candidates


def discover_incident_seeds(
    root: Path,
    *,
    source_paths: list[Path] | None = None,
    max_source_files: int = INCIDENT_SEED_DEFAULT_MAX_SOURCE_FILES,
    max_records_per_file: int = INCIDENT_SEED_DEFAULT_MAX_RECORDS_PER_FILE,
    write: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    discovered_at = datetime.now(timezone.utc).isoformat()
    paths = list(source_paths or _incident_source_paths(root, max_source_files=max_source_files))
    existing = _load_json_rows(root / INCIDENT_SEED_LEDGER_PATH)
    seeds_by_key: dict[str, dict[str, Any]] = {}
    for seed in existing:
        key = _nonempty(seed.get("seed_key")) or _seed_identity_key(seed)
        if key and not _seed_is_expired(seed):
            refreshed = dict(seed)
            refreshed.setdefault("seed_key", key)
            seeds_by_key[key] = refreshed
    source_file_count = 0
    source_record_count = 0
    discovered_seed_count = 0
    deduped_seed_count = 0
    per_dispatch_counts: Counter[str] = Counter()
    for source_path in paths:
        rows = _load_json_rows(source_path)
        if not rows:
            continue
        source_file_count += 1
        for record in rows[:max_records_per_file]:
            source_record_count += 1
            seed = _seed_from_record(record, source_path=source_path, discovered_at=discovered_at)
            if not seed:
                continue
            discovered_seed_count += 1
            key = _nonempty(seed.get("seed_key")) or _seed_identity_key(seed)
            if not key:
                continue
            existing_seed = seeds_by_key.get(key)
            if existing_seed is None:
                seed["first_seen_date"] = _first_text(seed, "source_date", "incident_start_date") or discovered_at[:10]
                seed["last_seen_date"] = _first_text(seed, "source_date", "incident_start_date") or discovered_at[:10]
                seed["expires_on"] = _format_date(
                    (_parse_date(seed["last_seen_date"]) or datetime.now(timezone.utc).date()) + timedelta(days=INCIDENT_SEED_EXPIRY_DAYS)
                )
                seeds_by_key[key] = seed
                for dispatch_slug in seed.get("dispatch_targets") or []:
                    per_dispatch_counts[str(dispatch_slug)] += 1
                continue
            deduped_seed_count += 1
            merged = dict(existing_seed)
            merged["seed_key"] = key
            merged["incident_id"] = _first_text(existing_seed, "incident_id") or _first_text(seed, "incident_id") or f"incident-{key[:12]}"
            merged["place"] = _first_text(seed, "place") or _first_text(existing_seed, "place")
            merged["state"] = _first_text(seed, "state") or _first_text(existing_seed, "state")
            merged["incident_type"] = _first_text(seed, "incident_type") or _first_text(existing_seed, "incident_type")
            merged["incident_start_date"] = _first_text(seed, "incident_start_date") or _first_text(existing_seed, "incident_start_date")
            merged["incident_window_key"] = _first_text(seed, "incident_window_key") or _first_text(existing_seed, "incident_window_key")
            merged["source_url"] = _first_text(seed, "source_url") or _first_text(existing_seed, "source_url")
            merged["source_title"] = _first_text(seed, "source_title") or _first_text(existing_seed, "source_title")
            merged["source_date"] = max(_first_text(existing_seed, "source_date"), _first_text(seed, "source_date")) or _first_text(seed, "source_date") or _first_text(existing_seed, "source_date")
            merged["severity_basis"] = "; ".join(_unique_texts([_first_text(existing_seed, "severity_basis"), _first_text(seed, "severity_basis")]))
            merged["severity_evidence"] = "; ".join(_unique_texts([_first_text(existing_seed, "severity_evidence"), _first_text(seed, "severity_evidence")]))
            merged["discovered_at"] = min(
                _first_text(existing_seed, "discovered_at") or discovered_at,
                discovered_at,
            )
            merged["provenance"] = "source_record"
            merged["source_record_key"] = _first_text(existing_seed, "source_record_key") or _first_text(seed, "source_record_key")
            merged["source_record_id"] = _first_text(seed, "source_record_id") or _first_text(existing_seed, "source_record_id")
            merged["source_path"] = _first_text(seed, "source_path") or _first_text(existing_seed, "source_path")
            merged["source_dispatch_slug"] = _first_text(seed, "source_dispatch_slug") or _first_text(existing_seed, "source_dispatch_slug")
            merged["dispatch_targets"] = _unique_texts([*(existing_seed.get("dispatch_targets") or []), *(seed.get("dispatch_targets") or [])])
            merged["incident_status"] = _first_text(seed, "incident_status") or _first_text(existing_seed, "incident_status") or "active"
            merged["first_seen_date"] = min(
                _first_text(existing_seed, "first_seen_date") or _first_text(seed, "source_date") or discovered_at[:10],
                _first_text(seed, "source_date") or discovered_at[:10],
            )
            merged["last_seen_date"] = max(
                _first_text(existing_seed, "last_seen_date") or "",
                _first_text(seed, "source_date") or discovered_at[:10],
                _first_text(seed, "incident_start_date") or discovered_at[:10],
            )
            merged["expires_on"] = _format_date(
                (_parse_date(merged["last_seen_date"]) or datetime.now(timezone.utc).date()) + timedelta(days=INCIDENT_SEED_EXPIRY_DAYS)
            )
            seeds_by_key[key] = merged
    seeds = [
        seed
        for seed in sorted(seeds_by_key.values(), key=lambda row: (str(row.get("source_date") or ""), str(row.get("incident_id") or "")), reverse=True)
        if not _seed_is_expired(seed)
    ]
    report = {
        "ok": True,
        "discovered_at": discovered_at,
        "source_file_count": source_file_count,
        "source_record_count": source_record_count,
        "incident_seed_count": len(seeds),
        "incident_seed_created_count": max(0, len(seeds_by_key) - len(existing)),
        "incident_seed_deduped_count": deduped_seed_count,
        "incident_seed_expired_count": len(seeds_by_key) - len(seeds),
        "incident_seed_dispatch_counts": dict(sorted(per_dispatch_counts.items())),
        "incident_seed_ledger_path": str(root / INCIDENT_SEED_LEDGER_PATH),
        "incident_seed_report_path": str(root / INCIDENT_SEED_DISCOVERY_REPORT_PATH),
        "source_paths": [str(path) for path in paths],
    }
    if write and not dry_run:
        ledger_path = root / INCIDENT_SEED_LEDGER_PATH
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        ledger_path.write_text(json.dumps({"incident_seeds": seeds}, indent=2), encoding="utf-8")
        report_path = root / INCIDENT_SEED_DISCOVERY_REPORT_PATH
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def load_incident_seeds(root: Path, dispatch_slug: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    shared_rows = _load_json_rows(root / INCIDENT_SEED_LEDGER_PATH)
    if shared_rows:
        rows.extend(
            row
            for row in shared_rows
            if isinstance(row, dict)
            and not _seed_is_expired(row)
            and (
                not row.get("dispatch_targets")
                or dispatch_slug in {str(item).strip() for item in row.get("dispatch_targets") or [] if str(item).strip()}
            )
        )
    manual_path = root / "data" / "dispatches" / dispatch_slug / "incident_seeds.json"
    manual_rows = _load_json_rows(manual_path)
    rows.extend(row for row in manual_rows if isinstance(row, dict))
    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = _nonempty(row.get("seed_key")) or _seed_identity_key(row)
        if not key:
            key = _nonempty(row.get("incident_id")) or _nonempty(row.get("source_url")) or _nonempty(row.get("source_title"))
        if not key:
            continue
        if key in deduped:
            continue
        deduped[key] = row
    return list(deduped.values())


def build_incident_follow_up_queries(
    seed: Mapping[str, Any],
    *,
    dispatch_slug: str,
    max_queries: int = 8,
) -> dict[str, Any]:
    place = _normalize_place(seed)
    incident_type = _incident_type(seed)
    trigger_reason = _trigger_reason(seed)
    source_url = _first_text(seed, "source_url", "url")
    source_date = _first_text(seed, "source_date", "published_at", "date")
    if not place or trigger_reason in {"incident_type_out_of_scope", "insufficient_severity_evidence"}:
        return {
            "ok": False,
            "dispatch_slug": dispatch_slug,
            "seed_id": _seed_id(seed),
            "place": place,
            "incident_type": incident_type,
            "source_url": source_url,
            "source_date": source_date,
            "trigger_reason": trigger_reason,
            "query_count": 0,
            "queries": [],
        }

    if dispatch_slug == "care-line":
        consequence_terms = CARE_LINE_CONSEQUENCE_TERMS
        incident_terms = {
            "power_outage": ("power outage", "outage"),
            "water_outage": ("water outage", "water service", "boil advisory"),
            "flood": ("flood", "flooding"),
            "wildfire": ("wildfire", "smoke"),
            "storm": ("storm", "severe storm"),
            "severe_storm": ("storm", "severe storm"),
            "hurricane": ("hurricane", "storm"),
            "evacuation": ("evacuation", "displacement"),
            "utility_failure": ("utility failure", "outage"),
            "infrastructure_failure": ("infrastructure failure", "outage"),
        }.get(incident_type, ("outage", "incident"))
    elif dispatch_slug == "food-line":
        consequence_terms = FOOD_LINE_CONSEQUENCE_TERMS
        incident_terms = {
            "power_outage": ("power outage", "outage"),
            "water_outage": ("water outage", "water service", "boil advisory"),
            "flood": ("flood", "flooding"),
            "wildfire": ("wildfire", "smoke"),
            "storm": ("storm", "severe storm"),
            "severe_storm": ("storm", "severe storm"),
            "hurricane": ("hurricane", "storm"),
            "evacuation": ("evacuation", "displacement"),
            "utility_failure": ("utility failure", "outage"),
            "infrastructure_failure": ("infrastructure failure", "outage"),
        }.get(incident_type, ("outage", "incident"))
    else:
        raise ValueError(f"Unsupported dispatch slug: {dispatch_slug}")

    queries: list[dict[str, Any]] = []
    for consequence in consequence_terms:
        if len(queries) >= max_queries:
            break
        search_terms = [f'"{place}"', f'"{consequence}"']
        if incident_terms:
            search_terms.insert(1, f'"{incident_terms[0]}"')
        query = " ".join(search_terms)
        queries.append(
            {
                "query": query,
                "query_template": query,
                "category": "incident_consequence",
                "source_family": "local_news",
                "query_family": "incident_follow_up",
                "incident_seed_id": _seed_id(seed),
                "incident_type": incident_type,
                "incident_place": place,
                "incident_source_url": source_url,
                "incident_source_date": source_date,
                "trigger_reason": trigger_reason,
                "consequence_domain": dispatch_slug,
            }
        )
    if incident_terms and len(queries) < max_queries:
        query = f'"{place}" "{incident_terms[0]}"'
        queries.append(
            {
                "query": query,
                "query_template": query,
                "category": "incident_consequence",
                "source_family": "local_news",
                "query_family": "incident_follow_up",
                "incident_seed_id": _seed_id(seed),
                "incident_type": incident_type,
                "incident_place": place,
                "incident_source_url": source_url,
                "incident_source_date": source_date,
                "trigger_reason": trigger_reason,
                "consequence_domain": dispatch_slug,
            }
        )

    return {
        "ok": True,
        "dispatch_slug": dispatch_slug,
        "seed_id": _seed_id(seed),
        "place": place,
        "incident_type": incident_type,
        "source_url": source_url,
        "source_date": source_date,
        "trigger_reason": trigger_reason,
        "query_count": len(queries),
        "queries": queries,
    }
