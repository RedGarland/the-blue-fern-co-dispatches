from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from bluefern_dispatches.cascadia_ingest import CASCADE_DATA_ROOT


def canonicalize_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlsplit(url.strip())
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/") or parsed.path, "", ""))


def stable_source_record_id(url: str, title: str, source_id: str) -> str:
    raw = "|".join([canonicalize_url(url), title.strip().lower(), source_id])
    return f"src-{sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def normalize_text(value: str | None) -> str:
    return " ".join((value or "").split())


def normalize_sources(root: Path, edition_date: str, dry_run: bool = False) -> dict[str, object]:
    root = root.resolve()
    in_path = root / CASCADE_DATA_ROOT / "raw" / edition_date / "raw_sources.json"
    out_dir = root / CASCADE_DATA_ROOT / "normalized" / edition_date
    out_path = out_dir / "normalized_sources.json"
    warnings: list[str] = []
    errors: list[str] = []
    if not in_path.exists():
        errors.append(f"raw source file not found: {in_path}")
        return {"ok": False, "normalized_count": 0, "normalized_path": str(out_path), "warnings": warnings, "errors": errors}
    raw_records = json.loads(in_path.read_text(encoding="utf-8"))
    seen: set[tuple[str, str]] = set()
    normalized = []
    for record in raw_records:
        title = normalize_text(record.get("title"))
        canonical_url = canonicalize_url(record.get("url", ""))
        key = (canonical_url, title.lower())
        if key in seen:
            warnings.append(f"deduped duplicate record: {title or canonical_url}")
            continue
        seen.add(key)
        source_record_id = stable_source_record_id(canonical_url, title, str(record.get("source_id", "")))
        normalized.append(
            {
                "source_record_id": source_record_id,
                "canonical_url": canonical_url,
                "title": title,
                "publisher": normalize_text(record.get("source_name")),
                "published_at": record.get("published_at"),
                "retrieved_at": record.get("retrieved_at") or datetime.now(timezone.utc).isoformat(),
                "text": normalize_text(record.get("summary_or_snippet")),
                "source_id": record.get("source_id"),
                "source_name": normalize_text(record.get("source_name")),
                "region_scope": record.get("region_scope"),
                "category_hint": record.get("category_hint"),
                "trace": {
                    "raw_source_record_id": record.get("source_record_id"),
                    "raw_path": str(in_path),
                },
            }
        )
        for field in [
            "source_type",
            "provider_id",
            "provider_name",
            "query_used",
            "search_start_date",
            "search_end_date",
            "region_terms_matched",
            "state_hint",
            "reliability_tier",
            "source_url",
            "source_title",
            "weekly_date_basis",
            "traceability_note",
        ]:
            if field in record:
                normalized[-1][field] = record.get(field)
    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(normalized, indent=2), encoding="utf-8")
    return {"ok": not errors, "normalized_count": len(normalized), "normalized_path": str(out_path), "warnings": warnings, "errors": errors}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    result = normalize_sources(Path.cwd(), args.date, args.dry_run)
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
