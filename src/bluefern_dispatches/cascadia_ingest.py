from __future__ import annotations

import argparse
import json
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any


CASCADE_DATA_ROOT = Path("data") / "dispatches" / "cascadia"
DEFAULT_SOURCES_PATH = CASCADE_DATA_ROOT / "sources.yml"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_id(*parts: str) -> str:
    value = "|".join(parts)
    return sha256(value.encode("utf-8")).hexdigest()[:16]


def parse_scalar(value: str) -> Any:
    value = value.strip()
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    return value.strip("\"'")


def load_sources(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"source configuration not found: {path}")
    sources: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped == "sources:":
            continue
        if stripped.startswith("- "):
            if current:
                sources.append(current)
            current = {}
            stripped = stripped[2:].strip()
            if stripped:
                key, value = stripped.split(":", 1)
                current[key.strip()] = parse_scalar(value)
            continue
        if current is not None and ":" in stripped:
            key, value = stripped.split(":", 1)
            current[key.strip()] = parse_scalar(value)
    if current:
        sources.append(current)
    return sources


def source_lookup(sources: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(source["source_id"]): source for source in sources}


def read_manual_records(root: Path, source: dict[str, Any], retrieved_at: str) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    manual_path = root / str(source["url"])
    if not manual_path.exists():
        warnings.append(f"manual source file not found: {manual_path}")
        return [], warnings
    payload = json.loads(manual_path.read_text(encoding="utf-8"))
    records = []
    for item in payload:
        source_id = item.get("source_id") or source["source_id"]
        records.append(
            {
                "source_record_id": f"raw-{stable_id(str(source_id), item.get('url', ''), item.get('title', ''))}",
                "source_id": source_id,
                "source_name": source["name"],
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "published_at": item.get("published_at"),
                "retrieved_at": retrieved_at,
                "summary_or_snippet": item.get("summary_or_snippet", ""),
                "raw_payload": item,
                "region_scope": item.get("region_scope") or source.get("region_scope"),
                "category_hint": item.get("category_hint") or source.get("category_hint"),
            }
        )
    return records, warnings


def text_or_none(element: ET.Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    return element.text.strip()


def fetch_rss_records(source: dict[str, Any], retrieved_at: str, timeout: int = 10) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    records: list[dict[str, Any]] = []
    try:
        with urllib.request.urlopen(str(source["url"]), timeout=timeout) as response:
            body = response.read()
    except Exception as exc:  # pragma: no cover - network is optional and environment-dependent
        return [], [f"failed to fetch {source['source_id']}: {exc}"]
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        return [], [f"failed to parse RSS {source['source_id']}: {exc}"]
    for item in root.findall(".//item"):
        title = text_or_none(item.find("title")) or ""
        url = text_or_none(item.find("link")) or ""
        records.append(
            {
                "source_record_id": f"raw-{stable_id(str(source['source_id']), url, title)}",
                "source_id": source["source_id"],
                "source_name": source["name"],
                "title": title,
                "url": url,
                "published_at": text_or_none(item.find("pubDate")),
                "retrieved_at": retrieved_at,
                "summary_or_snippet": text_or_none(item.find("description")) or "",
                "raw_payload": ET.tostring(item, encoding="unicode"),
                "region_scope": source.get("region_scope"),
                "category_hint": source.get("category_hint"),
            }
        )
    return records, warnings


def ingest_sources(root: Path, edition_date: str, dry_run: bool = False, allow_network: bool = False) -> dict[str, Any]:
    root = root.resolve()
    data_root = root / CASCADE_DATA_ROOT
    sources = load_sources(root / DEFAULT_SOURCES_PATH)
    retrieved_at = utc_now()
    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    errors: list[str] = []
    for source in sources:
        if not source.get("enabled", False):
            continue
        if source.get("type") == "manual":
            manual_records, manual_warnings = read_manual_records(root, source, retrieved_at)
            records.extend(manual_records)
            warnings.extend(manual_warnings)
        elif source.get("type") == "rss":
            if allow_network:
                rss_records, rss_warnings = fetch_rss_records(source, retrieved_at)
                records.extend(rss_records)
                warnings.extend(rss_warnings)
            else:
                warnings.append(f"network disabled; skipped RSS source {source['source_id']}")
        else:
            warnings.append(f"source type {source.get('type')} is configured but has no ingester: {source['source_id']}")
    out_dir = data_root / "raw" / edition_date
    out_path = out_dir / "raw_sources.json"
    run_manifest_path = out_dir / "ingest_run_manifest.json"
    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
        run_manifest_path.write_text(
            json.dumps({"date": edition_date, "retrieved_at": retrieved_at, "raw_count": len(records), "warnings": warnings, "errors": errors}, indent=2),
            encoding="utf-8",
        )
    return {"ok": not errors, "raw_count": len(records), "raw_path": str(out_path), "run_manifest_path": str(run_manifest_path), "warnings": warnings, "errors": errors}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-network", action="store_true")
    args = parser.parse_args(argv)
    print(json.dumps(ingest_sources(Path.cwd(), args.date, args.dry_run, args.allow_network), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
