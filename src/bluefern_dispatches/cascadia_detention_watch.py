from __future__ import annotations

import html
import json
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from bluefern_dispatches.generator import BASE_URL, footer, header, page


DISPATCH_SLUG = "cascadia"
WATCH_SLUG = "detention-watch"
WATCH_NAME = "Cascadia Detention Watch"
WATCH_TAGLINE = "Tracking immigration detention issues connected to Washington, Oregon, and Idaho, starting with the Northwest ICE Processing Center in Tacoma."
WATCH_DESCRIPTION = "This page collects sourced facts, reported concerns, open questions, and ongoing updates in one place."
WATCH_LOGO_EDITION_SRC = "../../../assets/cascadia-detention-logo.png"
WATCH_LOGO_INDEX_SRC = "../../assets/cascadia-detention-logo.png"
WATCH_DATA_ROOT = Path("data") / "dispatches" / "cascadia" / "detention_watch"
ALLOWED_CLAIM_CLASSES = {"documented", "reported", "alleged", "unknown"}
REQUIRED_SOURCE_TYPES = {
    "official_record",
    "court_record",
    "government_statement",
    "media_reporting",
    "operator_record",
    "advocacy_legal_report",
    "unverified_allegation",
    "unknown",
}
BLOCKED_PUBLIC_LABELS = {"atrocity", "evil regime", "extermination", "genocidal", "murder camp"}
SOURCE_TYPE_LABELS = {
    "official_record": "Official record",
    "court_record": "Court record",
    "government_statement": "Government statement",
    "media_reporting": "Media reporting",
    "operator_record": "Operator record",
    "advocacy_legal_report": "Advocacy/legal report",
    "unverified_allegation": "Unverified allegation",
    "unknown": "Unknown",
}


def _write_text(path: Path, content: str, dry_run: bool) -> None:
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_json(path: Path, payload: Any, dry_run: bool) -> None:
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _resolve_last_checked_value(payload: dict[str, Any] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    for field in ("last_checked", "checked_at", "retrieved_at", "generated_at", "record_generated_at"):
        value = str(payload.get(field) or "").strip()
        if value:
            return value
    return None


def _parse_iso_timestamp(value: str) -> datetime | None:
    token = str(value or "").strip()
    if not token:
        return None
    try:
        return datetime.fromisoformat(token.replace("Z", "+00:00"))
    except ValueError:
        return None


def _format_public_timestamp(value: str | None) -> str:
    token = str(value or "").strip()
    if not token:
        return "Not listed"
    parsed = _parse_iso_timestamp(token)
    if not parsed:
        return token
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    utc_value = parsed.astimezone(timezone.utc)
    hour = utc_value.hour % 12 or 12
    minute = utc_value.minute
    meridiem = "AM" if utc_value.hour < 12 else "PM"
    return f"{utc_value.strftime('%b')} {utc_value.day}, {utc_value.year}, {hour}:{minute:02d} {meridiem} UTC"


def _clean_source_ref_ids(value: Any) -> list[str]:
    refs: list[str] = []
    for ref in list(value or []):
        token = str(ref).strip()
        if token and token not in refs:
            refs.append(token)
    return refs


def _normalize_source_refs_for_sections(payload: dict[str, Any], sections: list[Any]) -> None:
    for section in sections:
        for item in list(section or []):
            if not isinstance(item, dict):
                continue
            item["source_refs"] = _clean_source_ref_ids(item.get("source_refs"))


def _normalize_payload_source_references(payload: dict[str, Any], is_update: bool) -> dict[str, Any]:
    normalized = json.loads(json.dumps(payload))
    sources = [item for item in list(normalized.get("sources") or []) if isinstance(item, dict)]
    canonical_sources: list[dict[str, Any]] = []
    seen_source_ids: set[str] = set()
    for source in sources:
        source_id = str(source.get("source_id") or "").strip()
        if not source_id or source_id in seen_source_ids:
            continue
        seen_source_ids.add(source_id)
        canonical_sources.append(source)
    normalized["sources"] = canonical_sources
    sections: list[Any] = [
        list(normalized.get("facility_profile", {}).get("notes") or []),
        list(normalized.get("open_questions") or []),
    ]
    if is_update:
        sections.extend(
            [
                list(normalized.get("changed_this_week") or []),
                list(normalized.get("current_indicators_delta") or []),
                list(normalized.get("timeline_additions") or []),
                list(normalized.get("claims") or []),
            ]
        )
    else:
        sections.extend(
            [
                list(normalized.get("what_changed_this_week") or []),
                list(normalized.get("current_indicators") or []),
                list(normalized.get("timeline") or []),
                list(normalized.get("documented_facts") or []),
                list(normalized.get("reported_allegations") or []),
            ]
        )
    _normalize_source_refs_for_sections(normalized, sections)
    return normalized


def _derive_expected_next_review(edition_date: str) -> str | None:
    token = str(edition_date or "").strip()
    try:
        parsed = date.fromisoformat(token)
    except ValueError:
        return None
    return (parsed + timedelta(days=7)).isoformat()


def _claim_rows_for_count(payload: dict[str, Any], is_update: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rows.extend([item for item in list(payload.get("facility_profile", {}).get("notes") or []) if isinstance(item, dict)])
    rows.extend([item for item in list(payload.get("what_changed_this_week") or []) if isinstance(item, dict)])
    rows.extend([item for item in list(payload.get("current_indicators") or []) if isinstance(item, dict)])
    rows.extend([item for item in list(payload.get("timeline") or []) if isinstance(item, dict)])
    rows.extend([item for item in list(payload.get("documented_facts") or []) if isinstance(item, dict)])
    rows.extend([item for item in list(payload.get("reported_allegations") or []) if isinstance(item, dict)])
    if is_update:
        rows.extend([item for item in list(payload.get("changed_this_week") or []) if isinstance(item, dict)])
        rows.extend([item for item in list(payload.get("current_indicators_delta") or []) if isinstance(item, dict)])
        rows.extend([item for item in list(payload.get("timeline_additions") or []) if isinstance(item, dict)])
        rows.extend([item for item in list(payload.get("claims") or []) if isinstance(item, dict)])
    return rows


def _compute_claim_counts(payload: dict[str, Any], is_update: bool) -> dict[str, int]:
    counts: dict[str, int] = {}
    for claim in _claim_rows_for_count(payload, is_update=is_update):
        claim_class = str(claim.get("claim_class") or "unknown").strip().lower()
        if not claim_class:
            claim_class = "unknown"
        counts[claim_class] = counts.get(claim_class, 0) + 1
    return counts


def default_input_path(root: Path, edition_date: str) -> Path:
    return root / WATCH_DATA_ROOT / f"baseline_{edition_date}.json"


def latest_available_baseline_date(root: Path) -> str | None:
    data_root = root / WATCH_DATA_ROOT
    if not data_root.exists():
        return None
    dates: list[str] = []
    for path in data_root.glob("baseline_*.json"):
        token = path.stem.replace("baseline_", "", 1)
        if len(token) == 10:
            dates.append(token)
    return sorted(dates)[-1] if dates else None


def _latest_available_approved_update_date(root: Path) -> str | None:
    data_root = root / WATCH_DATA_ROOT
    if not data_root.exists():
        return None
    approved_dates: list[str] = []
    for path in data_root.glob("update_*.json"):
        try:
            payload = load_payload(path)
        except Exception:
            continue
        if str(payload.get("review_status") or "").strip().lower() == "approved":
            token = str(payload.get("date") or path.stem.replace("update_", "", 1))
            if len(token) == 10:
                approved_dates.append(token)
    return sorted(approved_dates)[-1] if approved_dates else None


def load_payload(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("detention watch payload must be a JSON object")
    return payload


def _validate_claim_rows(rows: list[dict[str, Any]], source_ids: set[str], errors: list[str], prefix: str) -> None:
    for item in rows:
        if not isinstance(item, dict):
            errors.append(f"{prefix} item must be an object")
            continue
        claim_class = str(item.get("claim_class") or "").strip().lower()
        if claim_class not in ALLOWED_CLAIM_CLASSES:
            errors.append(f"invalid claim_class: {claim_class}")
            continue
        refs = [str(ref).strip() for ref in item.get("source_refs", []) if str(ref).strip()]
        if claim_class in {"documented", "reported", "alleged"} and not refs:
            errors.append(f"{claim_class} claim missing source_refs: {item}")
        for ref in refs:
            if ref not in source_ids:
                errors.append(f"claim references unknown source_id: {ref}")


def validate_payload(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    source_ids = {str(item.get("source_id") or "") for item in payload.get("sources", []) if isinstance(item, dict)}
    for source in payload.get("sources", []):
        if not isinstance(source, dict):
            errors.append("source record must be an object")
            continue
        source_type = str(source.get("source_type") or "").strip()
        if source_type not in REQUIRED_SOURCE_TYPES:
            errors.append(f"unsupported source_type: {source_type}")
        if not str(source.get("url") or "").strip():
            errors.append(f"source {source.get('source_id')} missing URL")
    sections = [
        payload.get("facility_profile", {}).get("notes", []),
        payload.get("what_changed_this_week", []),
        payload.get("current_indicators", []),
        payload.get("timeline", []),
        payload.get("documented_facts", []),
        payload.get("reported_allegations", []),
    ]
    for section in sections:
        _validate_claim_rows([item for item in section if isinstance(item, dict)], source_ids, errors, "claim")
    for item in payload.get("open_questions", []):
        if isinstance(item, str):
            continue
        if not isinstance(item, dict):
            errors.append("open question item must be a string or object")
            continue
        refs = [str(ref).strip() for ref in item.get("source_refs", []) if str(ref).strip()]
        claim_class = str(item.get("claim_class") or "").strip().lower()
        label = str(item.get("label") or "").strip().lower()
        if refs:
            for ref in refs:
                if ref not in source_ids:
                    errors.append(f"open question references unknown source_id: {ref}")
        elif claim_class not in {"unknown"} and label not in {"open_question", "unknown"}:
            errors.append("open question without source_refs must be labeled open_question or unknown")
    return errors


def validate_update_payload(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required = [
        "date",
        "title",
        "summary",
        "sources",
        "changed_this_week",
        "current_indicators_delta",
        "timeline_additions",
        "claims",
        "open_questions",
        "method_note",
        "review_status",
    ]
    for key in required:
        if key not in payload:
            errors.append(f"update payload missing required field: {key}")
    if str(payload.get("review_status") or "").strip().lower() != "approved":
        errors.append("update review_status must be approved")
    sources = payload.get("sources", [])
    if not isinstance(sources, list):
        errors.append("update sources must be a list")
        sources = []
    source_ids = {str(item.get("source_id") or "") for item in sources if isinstance(item, dict)}
    for source in sources:
        if not isinstance(source, dict):
            errors.append("update source record must be an object")
            continue
        source_type = str(source.get("source_type") or "").strip()
        if source_type not in REQUIRED_SOURCE_TYPES:
            errors.append(f"unsupported source_type: {source_type}")
        if not str(source.get("url") or "").strip():
            errors.append(f"update source {source.get('source_id')} missing URL")
    _validate_claim_rows([item for item in payload.get("changed_this_week", []) if isinstance(item, dict)], source_ids, errors, "changed_this_week")
    _validate_claim_rows(
        [item for item in payload.get("current_indicators_delta", []) if isinstance(item, dict)], source_ids, errors, "current_indicators_delta"
    )
    _validate_claim_rows([item for item in payload.get("timeline_additions", []) if isinstance(item, dict)], source_ids, errors, "timeline_additions")
    _validate_claim_rows([item for item in payload.get("claims", []) if isinstance(item, dict)], source_ids, errors, "claims")
    for item in payload.get("open_questions", []):
        if isinstance(item, str):
            continue
        if not isinstance(item, dict):
            errors.append("update open question item must be a string or object")
            continue
        refs = [str(ref).strip() for ref in item.get("source_refs", []) if str(ref).strip()]
        status = str(item.get("status") or "").strip().lower()
        label = str(item.get("label") or "").strip().lower()
        if refs:
            for ref in refs:
                if ref not in source_ids:
                    errors.append(f"update open question references unknown source_id: {ref}")
        elif status not in {"open", "resolved"} and label not in {"open_question", "unknown"}:
            errors.append("update open question without source_refs must include open/resolved status or open_question label")
    return errors


def merge_baseline_with_update(baseline: dict[str, Any], update: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    merged_sources: dict[str, dict[str, Any]] = {}
    for src in baseline.get("sources", []):
        if isinstance(src, dict):
            merged_sources[str(src.get("source_id") or "")] = src
    for src in update.get("sources", []):
        if not isinstance(src, dict):
            continue
        source_id = str(src.get("source_id") or "")
        existing = merged_sources.get(source_id)
        if existing:
            if str(existing.get("url") or "") != str(src.get("url") or "") or str(existing.get("title") or "") != str(src.get("title") or ""):
                errors.append(f"conflicting duplicate source_id in update: {source_id}")
                continue
        merged_sources[source_id] = src
    open_questions: list[Any] = list(baseline.get("open_questions", []))
    for row in update.get("open_questions", []):
        if not isinstance(row, dict):
            open_questions.append(row)
            continue
        if str(row.get("status") or "").strip().lower() == "resolved":
            target = str(row.get("text") or "").strip()
            if not target:
                continue
            open_questions = [
                item
                for item in open_questions
                if str(item.get("text") if isinstance(item, dict) else item).strip().lower() != target.lower()
            ]
            continue
        open_questions.append(row)
    update_claims = [item for item in update.get("claims", []) if isinstance(item, dict)]
    merged = {
        "edition_date": str(update.get("date") or baseline.get("edition_date") or ""),
        "title": str(update.get("title") or f"{WATCH_NAME} Update"),
        "summary": str(update.get("summary") or baseline.get("summary") or ""),
        "facility_profile": dict(baseline.get("facility_profile") or {}),
        "current_indicators": list(baseline.get("current_indicators") or []),
        "current_indicators_delta": list(update.get("current_indicators_delta") or []),
        "changed_this_week": list(update.get("changed_this_week") or []),
        "timeline": list(baseline.get("timeline") or []),
        "timeline_additions": list(update.get("timeline_additions") or []),
        "claims": update_claims,
        "open_questions": open_questions,
        "sources": list(merged_sources.values()),
        "method_note": str(update.get("method_note") or baseline.get("method_note") or ""),
        "baseline_file": str(baseline.get("_source_path") or ""),
        "update_file": str(update.get("_source_path") or ""),
        "review_status": str(update.get("review_status") or ""),
    }
    merged["timeline_full"] = merged["timeline"] + merged["timeline_additions"]
    return merged, errors


def _render_claim_list(rows: list[dict[str, Any]], sources_by_id: dict[str, dict[str, Any]]) -> str:
    parts: list[str] = ["<ul>"]
    for row in rows:
        claim_class = html.escape(str(row.get("claim_class") or "unknown").capitalize())
        text_raw = str(row.get("text") or row.get("item") or row.get("event") or row.get("value") or "").strip()
        if not text_raw:
            continue
        text = html.escape(text_raw)
        text_suffix = "" if text_raw[-1:] in {".", "!", "?"} else "."
        refs = [str(ref) for ref in list(row.get("source_refs") or [])]
        refs_html = ""
        if refs:
            links = []
            for ref in refs:
                src = sources_by_id.get(ref)
                if not src:
                    continue
                links.append(
                    f'<a href="{html.escape(str(src.get("url") or ""))}" target="_blank" rel="noopener noreferrer">{html.escape(str(src.get("publisher") or src.get("title") or ref))}</a>'
                )
            if links:
                refs_html = f" Sources: {', '.join(links)}."
        parts.append(f"<li><strong>{claim_class}:</strong> {text}{text_suffix}{refs_html}</li>")
    if len(parts) == 1:
        parts.append("<li>No source-backed entries in this section.</li>")
    parts.append("</ul>")
    return "".join(parts)


def _render_indicator_list(rows: list[dict[str, Any]], sources_by_id: dict[str, dict[str, Any]]) -> str:
    parts: list[str] = ["<ul>"]
    for row in rows:
        if not isinstance(row, dict):
            continue
        claim_class = html.escape(str(row.get("claim_class") or "unknown").capitalize())
        indicator = str(row.get("indicator") or "").strip()
        status = str(row.get("status") or "").strip()
        basis = str(row.get("basis") or "").strip()
        if not indicator and not basis and not status:
            continue
        lead = indicator or "Indicator"
        if basis and status:
            text_raw = f"{lead} — {status.capitalize()}: {basis}"
        elif basis:
            text_raw = f"{lead} — {basis}"
        elif status:
            text_raw = f"{lead} — {status.capitalize()}."
        else:
            text_raw = lead
        text = html.escape(text_raw)
        text_suffix = "" if text_raw[-1:] in {".", "!", "?"} else "."
        refs = [str(ref) for ref in list(row.get("source_refs") or [])]
        refs_html = ""
        if refs:
            links = []
            for ref in refs:
                src = sources_by_id.get(ref)
                if not src:
                    continue
                links.append(
                    f'<a href="{html.escape(str(src.get("url") or ""))}" target="_blank" rel="noopener noreferrer">{html.escape(str(src.get("publisher") or src.get("title") or ref))}</a>'
                )
            if links:
                refs_html = f" Sources: {', '.join(links)}."
        parts.append(f"<li><strong>{claim_class}:</strong> {text}{text_suffix}{refs_html}</li>")
    if len(parts) == 1:
        parts.append("<li>No source-backed entries in this section.</li>")
    parts.append("</ul>")
    return "".join(parts)


def _render_sources(sources: list[dict[str, Any]]) -> str:
    return "".join(
        f'<li><a href="{html.escape(str(src.get("url") or ""))}" target="_blank" rel="noopener noreferrer">{html.escape(str(src.get("title") or ""))}</a> ({html.escape(str(src.get("source_type") or ""))}; {html.escape(str(src.get("publisher") or ""))})</li>'
        for src in sources
    )


def _collect_cited_source_ids(payload: dict[str, Any], is_update: bool) -> set[str]:
    cited: set[str] = set()
    sections: list[Any] = []
    sections.extend(payload.get("facility_profile", {}).get("notes", []))
    sections.extend(payload.get("open_questions", []))
    if is_update:
        sections.extend(payload.get("changed_this_week", []))
        sections.extend(payload.get("current_indicators_delta", []))
        sections.extend(payload.get("timeline_additions", []))
        sections.extend(payload.get("claims", []))
    else:
        sections.extend(payload.get("what_changed_this_week", []))
        sections.extend(payload.get("current_indicators", []))
        sections.extend(payload.get("timeline", []))
        sections.extend(payload.get("documented_facts", []))
        sections.extend(payload.get("reported_allegations", []))
    for item in sections:
        if not isinstance(item, dict):
            continue
        for ref in item.get("source_refs", []) or []:
            token = str(ref).strip()
            if token:
                cited.add(token)
    return cited


def _source_support_note(source_id: str, payload: dict[str, Any], is_update: bool) -> str:
    noted: list[str] = []

    def _scan(rows: list[Any], label: str) -> None:
        for row in rows:
            if not isinstance(row, dict):
                continue
            refs = {str(ref).strip() for ref in (row.get("source_refs") or [])}
            if source_id in refs:
                noted.append(label)
                return

    _scan(list(payload.get("facility_profile", {}).get("notes") or []), "Facility profile")
    _scan(list(payload.get("open_questions") or []), "Open questions")
    if is_update:
        _scan(list(payload.get("changed_this_week") or []), "This week changes")
        _scan(list(payload.get("current_indicators_delta") or []), "Indicator delta")
        _scan(list(payload.get("timeline_additions") or []), "Timeline updates")
        _scan(list(payload.get("claims") or []), "Claims")
    else:
        _scan(list(payload.get("what_changed_this_week") or []), "Record scope")
        _scan(list(payload.get("current_indicators") or []), "Monitoring checklist")
        _scan(list(payload.get("timeline") or []), "Timeline")
        _scan(list(payload.get("documented_facts") or []), "Documented facts")
        _scan(list(payload.get("reported_allegations") or []), "Reported allegations")
    return ", ".join(noted) if noted else "Referenced in record context"


def render_source_table_html(edition_date: str, payload: dict[str, Any], is_update: bool) -> str:
    sources = [item for item in payload.get("sources", []) if isinstance(item, dict)]
    cited_ids = _collect_cited_source_ids(payload, is_update=is_update)
    rows: list[str] = []
    for src in sources:
        source_id = str(src.get("source_id") or "").strip()
        if source_id and source_id not in cited_ids:
            continue
        url = str(src.get("url") or "").strip()
        title = str(src.get("title") or source_id or "Source").strip()
        source_type = str(src.get("source_type") or "unknown").strip()
        source_type_label = SOURCE_TYPE_LABELS.get(source_type, source_type.replace("_", " ").title())
        publisher = str(src.get("publisher") or "Unknown").strip()
        verified = "Verified source link" if url else "Link missing"
        checked = _format_public_timestamp(str(src.get("retrieved_at") or src.get("published_at") or "").strip())
        support = _source_support_note(source_id, payload, is_update=is_update)
        link_html = f'<a href="{html.escape(url)}" target="_blank" rel="noopener noreferrer">{html.escape(title)}</a>' if url else html.escape(title)
        rows.append(
            "<tr>"
            f'<th scope="row">{link_html}</th>'
            f"<td>{html.escape(source_type_label)}</td>"
            f"<td>{html.escape(publisher)}</td>"
            f"<td>{html.escape(support)}</td>"
            f"<td>{html.escape(verified)}</td>"
            f"<td>{html.escape(checked)}</td>"
            "</tr>"
        )
    if not rows:
        rows.append('<tr><td colspan="6">No cited sources are available for this record.</td></tr>')
    body = f"""{header("The Cascadia Briefing", "../../../", None, "/cascadia/")}
  <main class="briefing">
    <section class="hero">
      <img class="dispatch-logo detention-watch-logo" src="{WATCH_LOGO_EDITION_SRC}" alt="Cascadia Detention Watch">
      <h1>Cascadia Detention Watch Source Table</h1>
      <p><strong>Record date:</strong> {html.escape(edition_date)}</p>
      <p><a href="/cascadia/detention-watch/editions/{html.escape(edition_date)}/">Back to record</a> | <a href="/cascadia/detention-watch/">Back to Detention Watch</a></p>
    </section>
    <section>
      <table>
        <thead>
          <tr><th scope="col">Source</th><th scope="col">Source type</th><th scope="col">Publisher / agency</th><th scope="col">What this source supports</th><th scope="col">Verification status</th><th scope="col">Last checked</th></tr>
        </thead>
        <tbody>
          {''.join(rows)}
        </tbody>
      </table>
    </section>
  </main>
{footer("../../../")}"""
    return page(
        f"{WATCH_NAME} Source Table - {edition_date}",
        f"{BASE_URL}/cascadia/detention-watch/editions/{edition_date}/source_table.html",
        "../../../assets/site.css",
        body,
        WATCH_NAME,
    )


def _render_open_questions(open_questions: list[Any]) -> str:
    parts: list[str] = []
    for item in open_questions:
        if isinstance(item, dict):
            text = str(item.get("text") or "")
            status = str(item.get("status") or "").strip().lower()
            if status == "resolved":
                continue
            parts.append(f"<li>{html.escape(text)}</li>")
        else:
            parts.append(f"<li>{html.escape(str(item))}</li>")
    return "".join(parts)


def render_html(payload: dict[str, Any], is_update: bool = False) -> str:
    edition_date = str(payload.get("edition_date") or "")
    sources = [item for item in payload.get("sources", []) if isinstance(item, dict)]
    sources_by_id = {str(item.get("source_id") or ""): item for item in sources}
    facility = dict(payload.get("facility_profile") or {})
    facility_notes = list(facility.get("notes") or [])
    title = str(payload.get("title") or WATCH_NAME)
    summary = str(payload.get("summary") or "")
    method_note = str(payload.get("method_note") or "")
    latest_source_update_raw = max((str(item.get("published_at") or "").strip() for item in sources if str(item.get("published_at") or "").strip()), default="")
    latest_source_update = _format_public_timestamp(latest_source_update_raw) if latest_source_update_raw else "Unknown"
    open_questions = list(payload.get("open_questions") or [])
    open_question_count = len([item for item in open_questions if str(item.get("status") if isinstance(item, dict) else "").strip().lower() != "resolved"])
    last_checked_iso = _resolve_last_checked_value(payload)
    last_checked = _format_public_timestamp(last_checked_iso)
    expected_next_review = str(payload.get("expected_next_review") or "").strip()
    expected_next_review_public = _format_public_timestamp(f"{expected_next_review}T00:00:00+00:00") if expected_next_review else "Not listed"
    monitoring_status = "Weekly monitoring active"
    source_coverage_summary = f"{len(sources)} cited sources in current record"
    record_status_lines = [
        "Baseline record" if not is_update else "Update record",
        "No verified weekly development asserted" if not is_update else "Verified weekly developments are limited to cited entries above",
        "Open questions remain",
    ]
    body_sections: list[str] = [
        f"""    <section class="hero">
      <img class="dispatch-logo detention-watch-logo" src="{WATCH_LOGO_EDITION_SRC}" alt="Cascadia Detention Watch">
      <h1>{html.escape(title)}</h1>
      <p>{html.escape(WATCH_TAGLINE)}</p>
      <p><strong>Edition date:</strong> {html.escape(edition_date)}</p>
      <p><a href="/cascadia/">Back to Cascadia</a> | <a href="/cascadia/detention-watch/">Back to Detention Watch index</a> | <a href="source_table.html">View source table</a></p>
    </section>""",
        f"""    <section><h2>Summary</h2><p>{html.escape(summary)}</p></section>""",
        (
            "    <section><h2>Detention Watch Summary</h2>"
            f"<p><strong>Last checked:</strong> {html.escape(last_checked)}</p>"
            f"<p><strong>Latest source update:</strong> {html.escape(latest_source_update)}</p>"
            f"<p><strong>Next review expected:</strong> {html.escape(expected_next_review_public)}</p>"
            f"<p><strong>Monitoring status:</strong> {html.escape(monitoring_status)}</p>"
            f"<p><strong>Facility focus:</strong> {html.escape(str(facility.get('name') or 'Northwest ICE Processing Center in Tacoma'))}</p>"
            f"<p><strong>Source coverage summary:</strong> {html.escape(source_coverage_summary)}</p>"
            f"<p><strong>Open questions:</strong> {open_question_count}</p>"
            '<p><a href="/cascadia/">Latest Cascadia briefing</a> | <a href="/cascadia/map/">Latest Cascadia map</a></p>'
            "</section>"
        ),
        (
            "    <section><h2>Record status</h2><ul>"
            + "".join(f"<li>{html.escape(line)}</li>" for line in record_status_lines)
            + "</ul></section>"
        ),
    ]
    if is_update:
        claims = [item for item in payload.get("claims", []) if isinstance(item, dict)]
        documented = [item for item in claims if str(item.get("claim_class") or "").lower() == "documented"]
        reported = [item for item in claims if str(item.get("claim_class") or "").lower() == "reported"]
        alleged = [item for item in claims if str(item.get("claim_class") or "").lower() == "alleged"]
        body_sections.extend(
            [
                f"""    <section><h2>What changed this week</h2>{_render_claim_list(list(payload.get("changed_this_week") or []), sources_by_id)}</section>""",
                (
                    "    <section><h2>Facility profile</h2>"
                    "<table><thead><tr><th scope=\"col\">Field</th><th scope=\"col\">Status</th></tr></thead><tbody>"
                    f"<tr><th scope=\"row\">Facility name</th><td>{html.escape(str(facility.get('name') or ''))}</td></tr>"
                    f"<tr><th scope=\"row\">Location</th><td>{html.escape(str(facility.get('location') or ''))}</td></tr>"
                    "<tr><th scope=\"row\">Official listing status</th><td>Documented in cited official/public records</td></tr>"
                    "<tr><th scope=\"row\">Operator record status</th><td>Documented with current operator source</td></tr>"
                    "<tr><th scope=\"row\">Current contract/operator responsibilities status</th><td>Open verification question</td></tr>"
                    "</tbody></table>"
                    f"{_render_claim_list(facility_notes, sources_by_id)}</section>"
                ),
                f"""    <section><h2>Monitoring checklist</h2>{_render_indicator_list(list(payload.get("current_indicators") or []), sources_by_id)}<h3>Monitoring checklist delta</h3>{_render_indicator_list(list(payload.get("current_indicators_delta") or []), sources_by_id)}</section>""",
                f"""    <section><h2>New timeline entries</h2>{_render_claim_list(list(payload.get("timeline_additions") or []), sources_by_id)}</section>""",
                f"""    <section><h2>New documented facts</h2>{_render_claim_list(documented, sources_by_id)}</section>""",
                f"""    <section><h2>New reported allegations</h2>{_render_claim_list(reported, sources_by_id)}</section>""",
                f"""    <section><h2>New alleged claims</h2>{_render_claim_list(alleged, sources_by_id)}</section>""",
            ]
        )
    else:
        body_sections.extend(
            [
                f"""    <section><h2>Why this matters</h2><p>{html.escape(str(payload.get("why_this_matters") or ""))}</p></section>""",
                (
                    "    <section><h2>Facility profile</h2>"
                    "<table><thead><tr><th scope=\"col\">Field</th><th scope=\"col\">Status</th></tr></thead><tbody>"
                    f"<tr><th scope=\"row\">Facility name</th><td>{html.escape(str(facility.get('name') or ''))}</td></tr>"
                    f"<tr><th scope=\"row\">Location</th><td>{html.escape(str(facility.get('location') or ''))}</td></tr>"
                    "<tr><th scope=\"row\">Official listing status</th><td>Documented in cited official/public records</td></tr>"
                    "<tr><th scope=\"row\">Operator record status</th><td>Documented with current operator source</td></tr>"
                    "<tr><th scope=\"row\">Current contract/operator responsibilities status</th><td>Open verification question</td></tr>"
                    "</tbody></table>"
                    f"{_render_claim_list(facility_notes, sources_by_id)}</section>"
                ),
                f"""    <section><h2>What this page does</h2>{_render_claim_list(list(payload.get("what_changed_this_week") or []), sources_by_id)}</section>""",
                f"""    <section><h2>Monitoring checklist</h2>{_render_indicator_list(list(payload.get("current_indicators") or []), sources_by_id)}</section>""",
                f"""    <section><h2>Timeline</h2>{_render_claim_list(list(payload.get("timeline") or []), sources_by_id)}</section>""",
                f"""    <section><h2>Documented facts</h2>{_render_claim_list(list(payload.get("documented_facts") or []), sources_by_id)}</section>""",
                f"""    <section><h2>Reported allegations</h2>{_render_claim_list(list(payload.get("reported_allegations") or []), sources_by_id)}</section>""",
            ]
        )
    body_sections.extend(
        [
            f"""    <section><h2>Open questions</h2><ul>{_render_open_questions(list(payload.get("open_questions") or []))}</ul></section>""",
            f"""    <section><h2>Sources</h2><ul>{_render_sources(sources)}</ul></section>""",
            f"""    <section><h2>Method note</h2><p>{html.escape(method_note)}</p></section>""",
        ]
    )
    body = f"""{header("The Cascadia Briefing", "../../../", None, "/cascadia/")}
  <main class="briefing">
{''.join(body_sections)}
  </main>
{footer("../../../")}"""
    return page(
        f"{WATCH_NAME} - {edition_date}",
        f"{BASE_URL}/cascadia/detention-watch/editions/{edition_date}/",
        "../../../assets/site.css",
        body,
        WATCH_NAME,
    )


def build_detention_watch(
    root: Path,
    edition_date: str | None = None,
    input_path: Path | None = None,
    update_path: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    edition = edition_date or latest_available_baseline_date(root) or date.today().isoformat()
    resolved_input = input_path or default_input_path(root, edition)
    if not resolved_input.exists():
        return {"ok": False, "errors": [f"detention watch input not found: {resolved_input}"], "warnings": [], "edition_date": edition}
    baseline_payload = load_payload(resolved_input)
    baseline_payload["_source_path"] = str(resolved_input)
    baseline_payload["edition_date"] = str(baseline_payload.get("edition_date") or edition)
    baseline_payload = _normalize_payload_source_references(baseline_payload, is_update=False)
    errors = validate_payload(baseline_payload)
    if errors:
        return {"ok": False, "errors": errors, "warnings": [], "edition_date": str(baseline_payload["edition_date"])}
    render_payload = baseline_payload
    is_update = False
    update_payload: dict[str, Any] | None = None
    if update_path:
        if not update_path.exists():
            return {"ok": False, "errors": [f"detention watch update input not found: {update_path}"], "warnings": [], "edition_date": edition}
        update_payload = load_payload(update_path)
        update_payload["_source_path"] = str(update_path)
        update_payload = _normalize_payload_source_references(update_payload, is_update=True)
        update_errors = validate_update_payload(update_payload)
        if update_errors:
            return {"ok": False, "errors": update_errors, "warnings": [], "edition_date": edition}
        merged_payload, merge_errors = merge_baseline_with_update(baseline_payload, update_payload)
        if merge_errors:
            return {"ok": False, "errors": merge_errors, "warnings": [], "edition_date": edition}
        render_payload = merged_payload
        edition = str(merged_payload.get("edition_date") or edition)
        is_update = True
    else:
        edition = str(baseline_payload.get("edition_date") or edition)
    now = datetime.now(timezone.utc).isoformat()
    resolved_last_checked = _resolve_last_checked_value(render_payload) or now
    render_payload["last_checked"] = resolved_last_checked
    if not str(render_payload.get("generated_at") or "").strip():
        render_payload["generated_at"] = now
    if not str(render_payload.get("expected_next_review") or "").strip():
        derived_next = _derive_expected_next_review(str(edition))
        if derived_next:
            render_payload["expected_next_review"] = derived_next
    html_text = render_html(render_payload, is_update=is_update)
    dispatch_root = root / "output" / "dispatches" / DISPATCH_SLUG / WATCH_SLUG
    dispatch_edition = dispatch_root / "editions" / edition
    site_root = root / "output" / "site" / DISPATCH_SLUG / WATCH_SLUG
    site_edition = site_root / "editions" / edition
    if not dry_run and site_root.exists():
        # Remove stale generated detention-watch artifacts before rewrite.
        shutil.rmtree(site_root)
    latest_update_date = _latest_available_approved_update_date(root)
    latest_date = latest_update_date or edition
    baseline_date = latest_available_baseline_date(root) or edition
    latest_label = "approved update edition" if latest_update_date else "starting record"
    source_table_href = f"/cascadia/detention-watch/editions/{latest_date}/source_table.html"
    last_checked_text = "Not listed"
    monitoring_status = "Weekly monitoring active"
    latest_payload_path = site_root / "editions" / latest_date / "detention_watch_payload.json"
    if latest_payload_path.exists():
        try:
            latest_payload = json.loads(latest_payload_path.read_text(encoding="utf-8"))
            last_checked_text = _resolve_last_checked_value(latest_payload) or last_checked_text
        except json.JSONDecodeError:
            pass
    if last_checked_text == "Not listed":
        last_checked_text = _resolve_last_checked_value(render_payload) or "Not listed"
    last_checked_text_public = _format_public_timestamp(last_checked_text)
    expected_next_review = str(render_payload.get("expected_next_review") or "").strip()
    expected_next_review_public = _format_public_timestamp(f"{expected_next_review}T00:00:00+00:00") if expected_next_review else "Not listed"
    facility = dict(render_payload.get("facility_profile") or {})
    open_questions = list(render_payload.get("open_questions") or [])
    unresolved_open_questions = [
        item for item in open_questions if str(item.get("status") if isinstance(item, dict) else "").strip().lower() != "resolved"
    ]
    source_coverage_summary = f"{len(render_payload.get('sources') or [])} cited sources"
    index_body = f"""{header("The Cascadia Briefing", "../../", None, "/cascadia/")}
  <main class="briefing">
    <section class="hero">
      <img class="dispatch-logo detention-watch-logo" src="{WATCH_LOGO_INDEX_SRC}" alt="Cascadia Detention Watch">
      <h1>{html.escape(WATCH_NAME)}</h1>
      <p>{html.escape(WATCH_TAGLINE)}</p>
      <p>{html.escape(WATCH_DESCRIPTION)}</p>
      <p><strong>Latest record date:</strong> {html.escape(latest_date)}</p>
      <p><strong>Last checked:</strong> {html.escape(last_checked_text_public)}</p>
      <p><strong>Next review expected:</strong> {html.escape(expected_next_review_public)}</p>
      <p><strong>Monitoring status:</strong> {html.escape(monitoring_status)}</p>
      <p><strong>Facility focus:</strong> {html.escape(str(facility.get("name") or "Northwest ICE Processing Center in Tacoma"))}</p>
      <p><strong>Source coverage summary:</strong> {html.escape(source_coverage_summary)}</p>
      <p><strong>Open questions summary:</strong> {len(unresolved_open_questions)} unresolved question(s)</p>
      <p><a href="/cascadia/detention-watch/editions/{html.escape(latest_date)}/">Open latest record</a></p>
      <p><a href="{html.escape(source_table_href)}">View source table</a></p>
      <p><a href="/cascadia/">Back to Cascadia</a></p>
    </section>
  </main>
{footer("../../")}"""
    index_html = page(WATCH_NAME, f"{BASE_URL}/cascadia/detention-watch/", "../../assets/site.css", index_body, WATCH_NAME)
    archive_body = f"""{header("The Cascadia Briefing", "../../", None, "/cascadia/")}
  <main class="briefing">
    <section class="hero">
      <img class="dispatch-logo detention-watch-logo" src="{WATCH_LOGO_INDEX_SRC}" alt="Cascadia Detention Watch">
      <h1>{html.escape(WATCH_NAME)} Archive</h1>
      <p><a href="/cascadia/detention-watch/editions/{html.escape(latest_date)}/">Open latest record</a></p>
      <ul class="edition-list"><li><span class="edition-date">{html.escape(latest_date)}</span><a href="/cascadia/detention-watch/editions/{html.escape(latest_date)}/">{html.escape(latest_label.title())}</a></li></ul>
    </section>
  </main>
{footer("../../")}"""
    archive_html = page(f"{WATCH_NAME} Archive", f"{BASE_URL}/cascadia/detention-watch/archive.html", "../../assets/site.css", archive_body, WATCH_NAME)
    source_table_html = render_source_table_html(edition, render_payload, is_update=is_update)
    claim_instance_count_by_class = _compute_claim_counts(render_payload, is_update=is_update)
    manifest = {
        "dispatch_slug": DISPATCH_SLUG,
        "sub_dispatch_slug": WATCH_SLUG,
        "edition_date": edition,
        "generated_at": now,
        "public_url": f"{BASE_URL}/cascadia/detention-watch/editions/{edition}/",
        "source_count": len(render_payload.get("sources", [])),
        "claim_instance_count_by_class": claim_instance_count_by_class,
        "claim_count_by_class": claim_instance_count_by_class,
        "claim_count_semantics": "claim_instance_count_by_class counts each claim item instance across facility profile, record scope, checklist, timeline, and claims sections.",
        "expected_next_review": str(render_payload.get("expected_next_review") or ""),
        "baseline_file": str(resolved_input),
        "update_file": str(update_path) if update_path else None,
        "edition_kind": "update" if is_update else "baseline",
        "errors": [],
    }
    for target in (dispatch_root, site_root):
        _write_text(target / "index.html", index_html, dry_run)
        _write_text(target / "archive.html", archive_html, dry_run)
    for target in (dispatch_edition, site_edition):
        _write_text(target / "index.html", html_text, dry_run)
        _write_text(target / "source_table.html", source_table_html, dry_run)
        _write_json(target / "edition_manifest.json", manifest, dry_run)
        _write_json(target / "sources_manifest.json", render_payload["sources"], dry_run)
        _write_json(target / "detention_watch_payload.json", render_payload, dry_run)
    validation_errors = validate_detention_watch_artifacts(root, edition)
    if validation_errors:
        return {"ok": False, "errors": validation_errors, "warnings": [], "edition_date": edition}
    return {
        "ok": True,
        "errors": [],
        "warnings": [],
        "edition_date": edition,
        "input_path": str(resolved_input),
        "update_path": str(update_path) if update_path else None,
        "paths": {
            "dispatch_index": str(dispatch_root / "index.html"),
            "dispatch_archive": str(dispatch_root / "archive.html"),
            "dispatch_edition_index": str(dispatch_edition / "index.html"),
            "site_index": str(site_root / "index.html"),
            "site_archive": str(site_root / "archive.html"),
            "site_edition_index": str(site_edition / "index.html"),
            "site_edition_source_table": str(site_edition / "source_table.html"),
        },
    }


def unsupported_public_label_hits(html_text: str) -> list[str]:
    text = html_text.lower()
    return sorted([label for label in BLOCKED_PUBLIC_LABELS if label in text])


def validate_detention_watch_artifacts(root: Path, edition_date: str) -> list[str]:
    errors: list[str] = []
    site_root = root / "output" / "site" / "cascadia" / "detention-watch"
    if not site_root.exists():
        return []
    index_path = site_root / "index.html"
    effective_edition = edition_date
    if not (site_root / "editions" / effective_edition / "index.html").exists():
        editions_root = site_root / "editions"
        if editions_root.exists():
            candidates = sorted(path.name for path in editions_root.iterdir() if path.is_dir() and len(path.name) == 10)
            if candidates:
                effective_edition = candidates[-1]
    edition_path = site_root / "editions" / effective_edition / "index.html"
    source_table_path = site_root / "editions" / effective_edition / "source_table.html"
    sources_manifest_path = site_root / "editions" / effective_edition / "sources_manifest.json"
    manifest_path = site_root / "editions" / effective_edition / "edition_manifest.json"
    if not index_path.exists() and not (site_root / "editions").exists():
        return []
    if not index_path.exists() or not edition_path.exists() or not source_table_path.exists():
        errors.append("detention watch required public artifact missing")
        return errors
    index_html = index_path.read_text(encoding="utf-8")
    edition_html = edition_path.read_text(encoding="utf-8")
    table_html = source_table_path.read_text(encoding="utf-8")
    manifest_payload: dict[str, Any] = {}
    if index_html.count("Open latest") > 1 or index_html.count("starting record") > 1:
        errors.append("duplicate latest/starting links found on detention watch index")
    if 'href="/cascadia/detention-watch/rss.xml"' in index_html or 'href="/cascadia/detention-watch/rss.xml"' in edition_html:
        errors.append("broken detention-watch RSS link present")
    try:
        sources = json.loads(sources_manifest_path.read_text(encoding="utf-8"))
    except Exception:
        sources = []
    try:
        manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        manifest_payload = {}
    payload_path = site_root / "editions" / effective_edition / "detention_watch_payload.json"
    try:
        cited_payload = json.loads(payload_path.read_text(encoding="utf-8"))
        cited_source_ids = _collect_cited_source_ids(cited_payload, is_update="Update /" in edition_html)
    except Exception:
        cited_source_ids = set()
        cited_payload = {}
    root_source_ids = {str(src.get("source_id") or "").strip() for src in (sources if isinstance(sources, list) else []) if isinstance(src, dict)}
    for source_id in sorted(cited_source_ids):
        if source_id not in root_source_ids:
            errors.append(f"source_ref does not resolve to root source: {source_id}")
    for src in sources if isinstance(sources, list) else []:
        src_id = str(src.get("source_id") or "")
        src_title = str(src.get("title") or "")
        if src_id and src_title and src_id in cited_source_ids:
            if src_title not in table_html:
                errors.append(f"cited source missing from source table: {src_id}")
    if "<thead>" not in table_html or "<tbody>" not in table_html:
        errors.append("detention watch source table missing table section semantics")
    if '<th scope="col">Source type</th>' not in table_html or '<th scope="col">What this source supports</th>' not in table_html:
        errors.append("detention watch source table missing required columns")
    if "<thead>" not in edition_html or '<th scope="row">Facility name</th>' not in edition_html:
        errors.append("facility profile table accessibility markup missing")
    if str(cited_payload.get("monitoring_status") or "active").strip().lower() == "active" and not str(cited_payload.get("expected_next_review") or "").strip():
        errors.append("expected_next_review missing for active detention watch record")
    if _resolve_last_checked_value(cited_payload):
        raw_last_checked = _resolve_last_checked_value(cited_payload) or ""
        if raw_last_checked and raw_last_checked in index_html:
            errors.append("public index contains raw ISO last_checked timestamp")
        if raw_last_checked and raw_last_checked in edition_html:
            errors.append("public edition contains raw ISO last_checked timestamp")
    manifest_counts = manifest_payload.get("claim_instance_count_by_class") or manifest_payload.get("claim_count_by_class") or {}
    payload_counts = _compute_claim_counts(cited_payload if isinstance(cited_payload, dict) else {}, is_update="Update /" in edition_html)
    if dict(manifest_counts) != dict(payload_counts):
        errors.append("claim count manifest does not match payload-derived counts")
    return errors
