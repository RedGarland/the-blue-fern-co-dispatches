from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
REPAIR_FIELDS = ("traceability_note", "attribution_mode", "claim_status")
DEFAULT_ATTRIBUTION_MODE = "reported_public_source"
DEFAULT_CLAIM_STATUS = "reported_public_source"


def validate_date(value: str) -> str:
    if not DATE_RE.match(value):
        raise ValueError(f"date must use YYYY-MM-DD: {value}")
    return value


def manual_sources_path(root: Path, edition_date: str) -> Path:
    return root / "data" / "dispatches" / "gaza" / "sources" / edition_date / "manual_sources.json"


def _relative_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _load_manual_sources(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(payload, list):
        records = payload
        container = {"kind": "list", "payload": records}
    elif isinstance(payload, dict):
        if isinstance(payload.get("sources"), list):
            records = payload["sources"]
            container = {"kind": "sources", "payload": payload}
        elif isinstance(payload.get("records"), list):
            records = payload["records"]
            container = {"kind": "records", "payload": payload}
        else:
            raise ValueError("manual_sources.json must be a list or an object with a sources/records list")
    else:
        raise ValueError("manual_sources.json must be a list or an object with a sources/records list")
    if any(not isinstance(record, dict) for record in records):
        raise ValueError("manual_sources.json contains non-object records")
    return [dict(record) for record in records], container


def _write_manual_sources(path: Path, container: dict[str, Any], records: list[dict[str, Any]]) -> None:
    if container["kind"] == "list":
        payload: Any = records
    else:
        payload = dict(container["payload"])
        payload[container["kind"]] = records
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _value_is_missing(value: Any) -> bool:
    return not str(value or "").strip()


def _traceability_note(record: dict[str, Any]) -> str:
    url = str(record.get("url") or "").strip()
    publisher = str(record.get("publisher") or "").strip()
    published_at = str(record.get("published_at") or "").strip()
    source_kind = "Google News RSS wrapper" if "news.google.com" in url.lower() else "direct publisher URL"
    bits = [f"Traceable to {publisher or 'the source'} via a {source_kind}"]
    if published_at:
        bits.append(f"dated {published_at}")
    return " ".join(bits) + "; title, publisher, URL, and published_at are preserved in the record."


def _repair_record(record: dict[str, Any]) -> tuple[dict[str, Any], list[str], str | None]:
    repaired = dict(record)
    added: list[str] = []
    proposed_note: str | None = None
    if _value_is_missing(repaired.get("traceability_note")):
        proposed_note = _traceability_note(repaired)
        repaired["traceability_note"] = proposed_note
        added.append("traceability_note")
    if _value_is_missing(repaired.get("attribution_mode")):
        repaired["attribution_mode"] = DEFAULT_ATTRIBUTION_MODE
        added.append("attribution_mode")
    if _value_is_missing(repaired.get("claim_status")):
        repaired["claim_status"] = DEFAULT_CLAIM_STATUS
        added.append("claim_status")
    return repaired, added, proposed_note


def summarize_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    missing_by_record: list[dict[str, Any]] = []
    field_counts = {field: 0 for field in REPAIR_FIELDS}
    proposed_notes: dict[int, str] = {}
    for index, record in enumerate(records, start=1):
        missing = [field for field in REPAIR_FIELDS if _value_is_missing(record.get(field))]
        for field in REPAIR_FIELDS:
            if not _value_is_missing(record.get(field)):
                field_counts[field] += 1
        if missing:
            entry: dict[str, Any] = {"record_number": index, "missing": missing}
            if "traceability_note" in missing:
                note = _traceability_note(record)
                entry["proposed_traceability_note"] = note
                proposed_notes[index] = note
            missing_by_record.append(entry)
    return {
        "record_count": len(records),
        "field_counts": field_counts,
        "missing_by_record": missing_by_record,
        "proposed_traceability_notes": proposed_notes,
        "status": "valid" if not missing_by_record else "repair needed",
    }


def build_report(edition_date: str, *, apply: bool) -> dict[str, Any]:
    path = manual_sources_path(ROOT, edition_date)
    if not path.exists():
        return {
            "date": edition_date,
            "path": str(path),
            "exists": False,
            "status": "missing",
            "record_count": 0,
            "missing_by_record": [],
            "field_counts": {field: 0 for field in REPAIR_FIELDS},
            "fields_added": {field: 0 for field in REPAIR_FIELDS},
            "next_action": f"Create {path.name} or rerun Gaza manual source intake.",
        }

    try:
        records, container = _load_manual_sources(path)
    except Exception as exc:  # noqa: BLE001
        return {
            "date": edition_date,
            "path": str(path),
            "exists": True,
            "status": "invalid",
            "record_count": 0,
            "missing_by_record": [],
            "field_counts": {field: 0 for field in REPAIR_FIELDS},
            "fields_added": {field: 0 for field in REPAIR_FIELDS},
            "error": str(exc),
            "next_action": "Fix the manual_sources.json structure before rerunning.",
        }

    summary = summarize_records(records)
    fields_added = {field: 0 for field in REPAIR_FIELDS}
    if apply and summary["missing_by_record"]:
        repaired_records: list[dict[str, Any]] = []
        for record in records:
            repaired, added, _note = _repair_record(record)
            for field in added:
                fields_added[field] += 1
            repaired_records.append(repaired)
        _write_manual_sources(path, container, repaired_records)
        summary = summarize_records(repaired_records)
        status = "repaired"
    else:
        status = summary["status"]
    return {
        "date": edition_date,
        "path": str(path),
        "exists": True,
        "status": status,
        "record_count": summary["record_count"],
        "missing_by_record": summary["missing_by_record"],
        "field_counts": summary["field_counts"],
        "fields_added": fields_added,
        "proposed_traceability_notes": summary["proposed_traceability_notes"],
        "next_action": (
            f"python scripts\\gaza_operator_status.py --date {edition_date} --no-live"
            if status == "repaired"
            else "Run with --apply to write missing fields."
            if status == "repair needed"
            else "No action needed."
        ),
    }


def render_report(report: dict[str, Any]) -> str:
    lines = [
        f"GAZA MANUAL SOURCE REPAIR - {report['date']}",
        "",
        f"Path: {_relative_path(Path(report['path']))}",
        f"Records: {report['record_count']}",
    ]
    if report["status"] == "missing":
        lines.append("Status: missing")
        lines.append("")
        lines.append("Next action:")
        lines.append(f"Run manual source intake or create {_relative_path(Path(report['path']))}.")
        return "\n".join(lines)
    if report["status"] == "invalid":
        lines.append("Status: invalid")
        lines.append(f"Error: {report.get('error')}")
        lines.append("")
        lines.append("Next action:")
        lines.append(str(report["next_action"]))
        return "\n".join(lines)
    if report["status"] == "valid":
        lines.append("Status: valid")
    elif report["status"] == "repair needed":
        lines.append("Status: repair needed")
    elif report["status"] == "repaired":
        lines.append("Status: repaired")
    else:
        lines.append(f"Status: {report['status']}")

    if report["status"] in {"repair needed", "repaired"}:
        if report["status"] == "repair needed":
            for entry in report["missing_by_record"]:
                lines.append("")
                lines.append(f"Record {entry['record_number']}")
                lines.append(f"- Missing: {', '.join(entry['missing'])}")
                proposed = entry.get("proposed_traceability_note")
                if proposed:
                    lines.append(f"- Proposed traceability_note: {proposed}")
        else:
            lines.append("Fields added:")
            for field in REPAIR_FIELDS:
                lines.append(f"- {field}: {report['fields_added'][field]}")
    lines.append("")
    lines.append("Next action:")
    lines.append(str(report["next_action"]))
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check or repair Gaza manual source traceability fields.")
    parser.add_argument("--date", required=True, help="Gaza edition date in YYYY-MM-DD format.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Inspect manual source completeness without writing.")
    mode.add_argument("--apply", action="store_true", help="Fill missing manual source fields in place.")
    args = parser.parse_args(argv)
    args.date = validate_date(args.date)
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report(args.date, apply=bool(args.apply))
    print(render_report(report))
    if report["status"] in {"valid", "repaired"}:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
