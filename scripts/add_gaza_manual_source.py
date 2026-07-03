from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.gaza_sources import validate_source_records as validate_collected_source_records
from scripts import run_daily_gaza as daily
from scripts.run_gaza_daily_operator import validate_manual_source_records


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DEFAULT_SOURCE_TYPE = "manual"
DEFAULT_REGION_SCOPE = "Gaza"
DEFAULT_RELIABILITY_TIER = "reported-public-source"
DEFAULT_PROVIDER_ID = "manual-supplement"
DEFAULT_ATTRIBUTION_MODE = "reported_public_source"
DEFAULT_CLAIM_STATUS = "reported_public_source"
TRACEABILITY_NOTE = "Traceable to the listed public URL, title, publisher, and published_at retained in this manual source record."


def validate_date(value: str) -> str:
    if not DATE_RE.match(value):
        raise ValueError(f"date must use YYYY-MM-DD: {value}")
    return value


def manual_source_path(edition_date: str) -> Path:
    return ROOT / "data" / "dispatches" / "gaza" / "sources" / edition_date / "manual_sources.json"


def _read_manual_payload(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    records = payload.get("sources") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise ValueError(f"{path} must contain a JSON list of records or an object with a sources list")
    out: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            raise ValueError(f"source record {index} is not a JSON object")
        out.append(record)
    return out


def _write_manual_payload(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, indent=2), encoding="utf-8")


def _validate_iso_like(value: str, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    candidate = text.replace("Z", "+00:00")
    try:
        datetime.fromisoformat(candidate)
    except ValueError as exc:
        if not DATE_RE.match(text):
            raise ValueError(f"{field_name} must be ISO-like text: {text}") from exc
    return text


def _build_record(args: argparse.Namespace) -> dict[str, str]:
    published_at = _validate_iso_like(args.published_at, "published_at")
    record = {
        "source_record_id": str(args.source_record_id).strip(),
        "title": str(args.title).strip(),
        "url": str(args.url).strip(),
        "publisher": str(args.publisher).strip(),
        "published_at": published_at,
        "retrieved_at": published_at,
        "summary_or_snippet": str(args.summary).strip(),
        "source_type": str(args.source_type).strip() or DEFAULT_SOURCE_TYPE,
        "provider_id": DEFAULT_PROVIDER_ID,
        "region_scope": str(args.region_scope).strip() or DEFAULT_REGION_SCOPE,
        "category_hint": str(args.category_hint).strip(),
        "reliability_tier": str(args.reliability_tier).strip() or DEFAULT_RELIABILITY_TIER,
        "attribution_mode": DEFAULT_ATTRIBUTION_MODE,
        "claim_status": DEFAULT_CLAIM_STATUS,
        "traceability_note": TRACEABILITY_NOTE,
    }
    return record


def _validate_record(record: dict[str, Any]) -> list[str]:
    errors = validate_manual_source_records([record])
    errors.extend(error for error in validate_collected_source_records([record], min_sources=1) if error not in errors)
    return errors


def _summarize_records(records: list[dict[str, Any]]) -> None:
    print("Records:")
    for record in records:
        title = " ".join(str(record.get("title") or "").split())
        if len(title) > 110:
            title = title[:107] + "..."
        print(f"- {record.get('source_record_id')} | {record.get('publisher')} | {title}")


def _load_existing_records(path: Path, *, replace_file: bool) -> list[dict[str, Any]]:
    if not path.exists() or replace_file:
        return []
    return _read_manual_payload(path)


def _upsert_record(records: list[dict[str, Any]], record: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    source_record_id = str(record.get("source_record_id") or "").strip()
    updated: list[dict[str, Any]] = []
    replaced = False
    for existing in records:
        if str(existing.get("source_record_id") or "").strip() == source_record_id:
            updated.append(record)
            replaced = True
        else:
            updated.append(existing)
    if not replaced:
        updated.append(record)
        return updated, "added"
    return updated, "replaced"


def _validate_records(records: list[dict[str, Any]], path: Path) -> list[str]:
    errors = validate_manual_source_records(records)
    errors.extend(error for error in validate_collected_source_records(records, min_sources=1) if error not in errors)
    if path.exists():
        file_records = daily.validate_source_file(path, min_sources=1)[1]
        errors.extend(error for error in file_records if error not in errors)
    return errors


def _validate_existing_records(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    records = _read_manual_payload(path)
    errors = _validate_records(records, path)
    return records, errors


def _print_validation_result(path: Path, records: list[dict[str, Any]], *, valid: bool) -> None:
    print(f"Path: {path}")
    print(f"Record count: {len(records)}")
    print(f"Status: {'valid' if valid else 'invalid'}")
    _summarize_records(records)


def _validate_only(path: Path) -> int:
    if not path.exists():
        print(f"manual_sources.json not found: {path}", file=sys.stderr)
        return 1
    try:
        records, errors = _validate_existing_records(path)
    except Exception as exc:  # noqa: BLE001
        print(f"manual_sources.json is invalid: {path}: {exc}", file=sys.stderr)
        return 1
    if errors:
        print(f"manual_sources.json is invalid: {path}", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    _print_validation_result(path, records, valid=True)
    return 0


def _add_record(args: argparse.Namespace, path: Path) -> int:
    if not str(args.url).strip().startswith(("http://", "https://")):
        print("url must start with http:// or https://", file=sys.stderr)
        return 1
    record = _build_record(args)
    if not record["source_record_id"]:
        print("source_record_id is required", file=sys.stderr)
        return 1
    if not record["title"]:
        print("title is required", file=sys.stderr)
        return 1
    if not record["publisher"]:
        print("publisher is required", file=sys.stderr)
        return 1
    try:
        existing_records = _load_existing_records(path, replace_file=bool(args.replace_file))
    except Exception as exc:  # noqa: BLE001
        print(f"manual_sources.json is invalid and must be replaced with --replace-file: {path}: {exc}", file=sys.stderr)
        return 1
    if path.exists() and not args.replace_file:
        _, existing_errors = _validate_existing_records(path)
        if existing_errors:
            print(f"manual_sources.json is invalid and must be replaced with --replace-file: {path}", file=sys.stderr)
            for error in existing_errors:
                print(f"- {error}", file=sys.stderr)
            return 1
    updated_records, action = _upsert_record(existing_records, record)
    errors = validate_manual_source_records(updated_records)
    errors.extend(error for error in validate_collected_source_records(updated_records, min_sources=1) if error not in errors)
    if errors:
        print(f"manual source record failed validation: {path}", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    _write_manual_payload(path, updated_records)
    reloaded_records, reloaded_errors = _validate_existing_records(path)
    if reloaded_errors:
        print(f"manual source file failed post-write validation: {path}", file=sys.stderr)
        for error in reloaded_errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"Path: {path}")
    print(f"Record count: {len(reloaded_records)}")
    print(f"Source record ID: {record['source_record_id']} ({action})")
    _summarize_records(reloaded_records)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Add or validate a Gaza manual source record.")
    parser.add_argument("--date", required=True, type=validate_date, help="Edition date in YYYY-MM-DD format.")
    parser.add_argument("--source-record-id", help="Unique Gaza source record ID.")
    parser.add_argument("--title", help="Source title.")
    parser.add_argument("--url", help="Canonical source URL.")
    parser.add_argument("--publisher", help="Source publisher.")
    parser.add_argument("--published-at", help="Published-at timestamp or ISO-like date text.")
    parser.add_argument("--summary", help="Summary or snippet for the manual source record.")
    parser.add_argument("--category-hint", help="Category hint for the source record.")
    parser.add_argument("--source-type", default=DEFAULT_SOURCE_TYPE, help="Source type to store; defaults to manual.")
    parser.add_argument("--region-scope", default=DEFAULT_REGION_SCOPE, help="Region scope to store; defaults to Gaza.")
    parser.add_argument(
        "--reliability-tier",
        default=DEFAULT_RELIABILITY_TIER,
        help="Reliability tier to store; defaults to reported-public-source.",
    )
    parser.add_argument("--validate-only", action="store_true", help="Inspect and validate the manual source file only.")
    parser.add_argument("--replace-file", action="store_true", help="Discard any existing file contents before writing the new record.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    path = manual_source_path(args.date)
    if args.validate_only:
        return _validate_only(path)
    required = ("source_record_id", "title", "url", "publisher", "published_at", "summary", "category_hint")
    missing = [field for field in required if not str(getattr(args, field) or "").strip()]
    if missing:
        parser.error("missing required arguments for add mode: " + ", ".join(missing))
    return _add_record(args, path)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
