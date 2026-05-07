from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.generator import BASE_URL, footer, header, page


DISPATCH_SLUG = "gaza"
DISPATCH_ID = "dispatch-gaza"
DISPATCH_NAME = "Dispatches From Gaza"
BACKUP_ROOT = Path(r"C:\Users\Admin\Desktop\Python\dispatches-bluefern-backups") / DISPATCH_SLUG
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LINK_RE = re.compile(r"https?://[^\s<>)\"']+")
HTML_LINK_RE = re.compile(r"<a\s+[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)
PUBLIC_COPY_NAMES = {
    "edition.html",
    "edition.md",
    "substack_post.md",
    "sources_manifest.json",
    "source_manifest.json",
    "sources.json",
    "source_metadata.json",
    "curation_manifest.json",
    "edition_manifest.json",
}
BLOCKED_PUBLIC_PARTS = {"detail", "paid", "premium", "private"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any, dry_run: bool, wrote: list[str]) -> None:
    wrote.append(str(path))
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_text(path: Path, content: str, dry_run: bool, wrote: list[str]) -> None:
    wrote.append(str(path))
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def copy_file(source: Path, target: Path, dry_run: bool, wrote: list[str]) -> None:
    wrote.append(str(target))
    if dry_run:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def validate_date(value: str) -> str:
    if not DATE_RE.match(value):
        raise ValueError(f"date must use YYYY-MM-DD: {value}")
    datetime.strptime(value, "%Y-%m-%d")
    return value


def is_public_safe_file(path: Path) -> bool:
    lowered_parts = {part.lower() for part in path.parts}
    if lowered_parts & BLOCKED_PUBLIC_PARTS:
        return False
    return path.name in PUBLIC_COPY_NAMES


def find_best_source_file(source_dir: Path) -> Path:
    for name in ("edition.html", "index.html", "edition.md", "substack_post.md"):
        candidate = source_dir / name
        if candidate.exists() and candidate.is_file():
            return candidate
    html_files = sorted(path for path in source_dir.glob("*.html") if path.is_file())
    if html_files:
        return html_files[0]
    md_files = sorted(path for path in source_dir.glob("*.md") if path.is_file())
    if md_files:
        return md_files[0]
    raise FileNotFoundError(f"no source HTML or Markdown file found in {source_dir}")


def markdown_to_html(markdown: str) -> str:
    chunks: list[str] = []
    list_open = False
    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            if list_open:
                chunks.append("</ul>")
                list_open = False
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if heading:
            if list_open:
                chunks.append("</ul>")
                list_open = False
            level = len(heading.group(1))
            chunks.append(f"<h{level}>{html.escape(heading.group(2))}</h{level}>")
            continue
        bullet = re.match(r"^[-*]\s+(.+)$", stripped)
        if bullet:
            if not list_open:
                chunks.append("<ul>")
                list_open = True
            chunks.append(f"<li>{linkify(html.escape(bullet.group(1)))}</li>")
            continue
        if list_open:
            chunks.append("</ul>")
            list_open = False
        chunks.append(f"<p>{linkify(html.escape(stripped))}</p>")
    if list_open:
        chunks.append("</ul>")
    return "\n".join(chunks)


def linkify(text: str) -> str:
    return LINK_RE.sub(lambda match: f'<a href="{match.group(0)}" target="_blank" rel="noopener noreferrer">{match.group(0)}</a>', text)


def extract_body_from_html(content: str) -> str:
    match = re.search(r"<body[^>]*>(.*?)</body>", content, re.IGNORECASE | re.DOTALL)
    body = match.group(1) if match else content
    body = re.sub(r"<script\b.*?</script>", "", body, flags=re.IGNORECASE | re.DOTALL)
    body = re.sub(r"<style\b.*?</style>", "", body, flags=re.IGNORECASE | re.DOTALL)
    return body.strip()


def render_imported_edition(edition_date: str, source_file: Path) -> str:
    content = source_file.read_text(encoding="utf-8", errors="replace")
    body_html = extract_body_from_html(content) if source_file.suffix.lower() in {".html", ".htm"} else markdown_to_html(content)
    body = f"""{header(DISPATCH_NAME, "../../", "../../archive.html", "/gaza/")}
  <main class="briefing">
    <section class="hero">
      <img class="hero-logo" src="../../assets/gaza-logo.png" alt="Dispatches From Gaza">
    </section>
    <p class="eyebrow">Daily briefing / {edition_date}</p>
    {body_html}
  </main>
{footer("../../")}"""
    return page(f"{DISPATCH_NAME} - {edition_date}", f"{BASE_URL}/gaza/editions/{edition_date}/", "../../assets/site.css", body, DISPATCH_NAME)


def source_manifest_candidates(source_dir: Path) -> list[Path]:
    names = ("sources_manifest.json", "source_manifest.json", "sources.json", "source_metadata.json")
    return [source_dir / name for name in names if (source_dir / name).exists()]


def normalize_sources(payload: Any, edition_date: str, now: str) -> list[dict[str, Any]]:
    records = payload
    if isinstance(payload, dict):
        for key in ("sources", "records", "items"):
            if isinstance(payload.get(key), list):
                records = payload[key]
                break
    if not isinstance(records, list):
        return []
    normalized: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            continue
        url = record.get("url") or record.get("canonical_url") or record.get("link")
        title = record.get("title") or record.get("name") or url
        if not url:
            continue
        normalized.append(
            {
                "source_id": record.get("source_id") or record.get("source_record_id") or f"gaza-import-{edition_date}-{index:03d}",
                "title": title,
                "url": url,
                "publisher": record.get("publisher") or record.get("source") or record.get("site") or "Unknown",
                "published_at": record.get("published_at"),
                "retrieved_at": record.get("retrieved_at") or now,
                "archive_path": record.get("archive_path"),
                "used_in_story_ids": record.get("used_in_story_ids") or [f"gaza-import-story-{edition_date}"],
                "claim_ids": record.get("claim_ids") or [],
                "dispatch_slug": "gaza",
                "edition_date": edition_date,
                "imported_from_structured_manifest": True,
            }
        )
    return normalized


def detect_source_links(source_file: Path) -> list[dict[str, Any]]:
    content = source_file.read_text(encoding="utf-8", errors="replace")
    detected: list[dict[str, Any]] = []
    if source_file.suffix.lower() in {".html", ".htm"}:
        for match in HTML_LINK_RE.finditer(content):
            url = html.unescape(match.group(1)).strip()
            label = re.sub(r"<[^>]+>", "", match.group(2)).strip() or url
            if url.startswith(("http://", "https://")):
                detected.append({"url": url, "label": html.unescape(label)})
    else:
        for match in LINK_RE.finditer(content):
            url = match.group(0).rstrip(".,]")
            detected.append({"url": url, "label": url})
    seen: set[str] = set()
    unique = []
    for link in detected:
        if link["url"] in seen:
            continue
        seen.add(link["url"])
        unique.append(link)
    return unique


def build_source_manifest(source_dir: Path, source_file: Path, edition_date: str, now: str, warnings: list[str]) -> tuple[list[dict[str, Any]], list[Path], bool]:
    candidates = source_manifest_candidates(source_dir)
    for candidate in candidates:
        try:
            sources = normalize_sources(read_json(candidate), edition_date, now)
        except json.JSONDecodeError:
            warnings.append(f"structured source manifest could not be parsed: {candidate}")
            continue
        if sources:
            return sources, candidates, True
    links = detect_source_links(source_file)
    warnings.append("missing structured source records; detected links are recorded only as import evidence")
    if not links:
        warnings.append("no source links detected in imported edition content")
    return [], candidates, False


def build_curation_manifest(edition_date: str, sources: list[dict[str, Any]], links_detected: bool, structured: bool, warnings: list[str], now: str) -> list[dict[str, Any]]:
    return [
        {
            "story_id": f"gaza-import-story-{edition_date}",
            "title": f"Dispatches From Gaza - {edition_date}",
            "summary": "Imported older Gaza edition. Original factual content was preserved without rewrite.",
            "category": "imported-public-edition",
            "score": None,
            "scoring_reasons": ["Imported from older Gaza edition directory."],
            "included_in_public_summary": True,
            "included_in_detail_dataset": False,
            "source_ids": [source["source_id"] for source in sources] if structured else [],
            "source_links_detected": links_detected,
            "structured_sources_available": structured,
            "warnings": warnings,
            "generated_at": now,
        }
    ]


def discover_existing_gaza_dates(site_root: Path) -> list[str]:
    editions_root = site_root / "gaza" / "editions"
    if not editions_root.exists():
        return []
    return sorted((path.name for path in editions_root.iterdir() if path.is_dir() and DATE_RE.match(path.name)), reverse=True)


def render_archive(dates: list[str]) -> str:
    items = "\n".join(
        f'      <li><span class="edition-date">{date}</span><a href="editions/{date}/">{DISPATCH_NAME} - {date}</a></li>'
        for date in dates
    )
    body = f"""{header(DISPATCH_NAME, "", "archive.html")}
  <main class="archive">
    <section class="hero">
      <img class="hero-logo" src="assets/gaza-logo.png" alt="Dispatches From Gaza">
    </section>
    <p class="eyebrow">Archive</p>
    <h1>Edition Archive</h1>
    <ul class="edition-list">
{items}
    </ul>
  </main>
{footer("")}"""
    return page(f"{DISPATCH_NAME} Archive", f"{BASE_URL}/gaza/archive.html", "assets/site.css", body, DISPATCH_NAME)


def render_index(dates: list[str]) -> str:
    latest = dates[0]
    recent = "\n".join(
        f'      <li><span class="edition-date">{date}</span><a href="editions/{date}/">{DISPATCH_NAME} - {date}</a></li>'
        for date in dates[:10]
    )
    body = f"""{header(DISPATCH_NAME, "", "archive.html")}
  <main class="home">
    <section class="hero">
      <img class="hero-logo" src="assets/gaza-logo.png" alt="Dispatches From Gaza">
    </section>
    <p class="eyebrow">Daily briefing archive</p>
    <p class="lede">Structured briefings compiled from traceable source records.</p>
    <p><a href="editions/{latest}/">Read the latest briefing</a></p>
    <h2>Recent Editions</h2>
    <ul class="edition-list">
{recent}
    </ul>
  </main>
{footer("")}"""
    return page(DISPATCH_NAME, f"{BASE_URL}/gaza/", "assets/site.css", body, DISPATCH_NAME)


def render_rss(dates: list[str]) -> str:
    items = "\n".join(
        f"""  <item>
    <title>{html.escape(DISPATCH_NAME)} - {date}</title>
    <link>{BASE_URL}/gaza/editions/{date}/</link>
    <guid>{BASE_URL}/gaza/editions/{date}/</guid>
  </item>"""
        for date in dates
    )
    return f"""<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0">
<channel>
  <title>{html.escape(DISPATCH_NAME)}</title>
  <link>{BASE_URL}/gaza/</link>
  <description>Daily briefing</description>
{items}
</channel>
</rss>
"""


def refresh_gaza_archive(root: Path, dry_run: bool, wrote: list[str]) -> list[str]:
    site_root = root / "output" / "site"
    dates = discover_existing_gaza_dates(site_root)
    if not dates:
        return []
    write_text(site_root / "gaza" / "archive.html", render_archive(dates), dry_run, wrote)
    write_text(site_root / "gaza" / "index.html", render_index(dates), dry_run, wrote)
    write_text(site_root / "gaza" / "rss.xml", render_rss(dates), dry_run, wrote)
    return dates


def upsert(rows: list[dict[str, Any]], key: str, incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key = {str(row.get(key)): row for row in rows if row.get(key) is not None}
    for row in incoming:
        by_key[str(row[key])] = row
    return sorted(by_key.values(), key=lambda row: str(row.get(key, "")))


def update_shared_records(root: Path, edition_date: str, edition_dir: Path, backup_dir: Path, sources: list[dict[str, Any]], curation: list[dict[str, Any]], now: str, dry_run: bool, wrote: list[str]) -> None:
    records_root = root / "data" / "records"
    files = {
        "dispatches": "dispatches.json",
        "editions": "editions.json",
        "sources": "sources.json",
        "records": "records.json",
        "curation_decisions": "curation_decisions.json",
        "detail_packages": "detail_packages.json",
    }
    payloads: dict[str, list[dict[str, Any]]] = {}
    for name, filename in files.items():
        path = records_root / filename
        payloads[name] = read_json(path) if path.exists() else []
    payloads["dispatches"] = upsert(
        payloads["dispatches"],
        "dispatch_id",
        [
            {
                "dispatch_id": DISPATCH_ID,
                "slug": "gaza",
                "public_name": DISPATCH_NAME,
                "internal_name": "Gaza Dispatch",
                "description": "Free public Gaza briefing compiled from traceable source records.",
                "is_free_public": True,
                "has_detail_tier": False,
                "created_at": "2026-05-03T00:00:00Z",
                "updated_at": now,
            }
        ],
    )
    edition_id = f"gaza-{edition_date}"
    payloads["editions"] = upsert(
        payloads["editions"],
        "edition_id",
        [
            {
                "edition_id": edition_id,
                "dispatch_id": DISPATCH_ID,
                "dispatch_slug": "gaza",
                "slug": "gaza",
                "edition_date": edition_date,
                "public_url": f"{BASE_URL}/gaza/editions/{edition_date}/",
                "output_path": str(edition_dir),
                "backup_path": str(backup_dir),
                "generated_at": now,
                "status": "public",
                "is_free_public": True,
                "public_exposed": True,
                "has_detail_tier": False,
            }
        ],
    )
    source_rows = [
        {
            "source_id": source["source_id"],
            "dispatch_id": DISPATCH_ID,
            "dispatch_slug": "gaza",
            "edition_id": edition_id,
            "publisher": source.get("publisher"),
            "title": source.get("title"),
            "url": source.get("url"),
            "published_at": source.get("published_at"),
            "retrieved_at": source.get("retrieved_at") or now,
            "archive_path": source.get("archive_path"),
            "reliability_tier": "imported-structured-source",
            "public_exposed": True,
        }
        for source in sources
    ]
    if source_rows:
        payloads["sources"] = upsert(payloads["sources"], "source_id", source_rows)
    story = curation[0]
    payloads["records"] = upsert(
        payloads["records"],
        "record_id",
        [
            {
                "record_id": story["story_id"],
                "dispatch_id": DISPATCH_ID,
                "dispatch_slug": "gaza",
                "edition_id": edition_id,
                "category": story.get("category"),
                "title": story.get("title"),
                "public_summary": story.get("summary"),
                "detail_summary": None,
                "score": story.get("score"),
                "included_public": True,
                "included_detail": False,
                "source_ids": [source["source_id"] for source in sources],
                "generated_at": now,
                "is_free_public": True,
                "public_exposed": True,
            }
        ],
    )
    payloads["curation_decisions"] = upsert(
        payloads["curation_decisions"],
        "decision_id",
        [
            {
                "decision_id": f"decision-gaza-import-{edition_date}",
                "record_id": story["story_id"],
                "dispatch_id": DISPATCH_ID,
                "dispatch_slug": "gaza",
                "edition_id": edition_id,
                "included_public": True,
                "included_detail": False,
                "exclusion_reason": None,
                "scoring_reasons": story.get("scoring_reasons") or [],
                "public_exposed": True,
            }
        ],
    )
    for name, filename in files.items():
        write_json(records_root / filename, payloads[name], dry_run, wrote)


def import_one(root: Path, edition_date: str, source_dir: Path, dry_run: bool, force: bool) -> dict[str, Any]:
    edition_date = validate_date(edition_date)
    source_dir = source_dir.resolve()
    if not source_dir.exists() or not source_dir.is_dir():
        raise FileNotFoundError(f"source edition directory does not exist: {source_dir}")
    source_file = find_best_source_file(source_dir)
    now = utc_now()
    warnings: list[str] = []
    wrote: list[str] = []
    site_dir = root / "output" / "site" / "gaza" / "editions" / edition_date
    dispatch_dir = root / "output" / "dispatches" / "gaza" / "editions" / edition_date
    backup_dir = BACKUP_ROOT / edition_date
    if site_dir.exists() and not force:
        raise FileExistsError(f"target edition already exists; use --force to replace: {site_dir}")

    sources, structured_candidates, structured_available = build_source_manifest(source_dir, source_file, edition_date, now, warnings)
    detected_links = detect_source_links(source_file)
    html_out = render_imported_edition(edition_date, source_file)
    source_files = sorted(path for path in source_dir.iterdir() if path.is_file() and is_public_safe_file(path))
    imported_file_names = [path.name for path in source_files]
    source_links_detected = bool(detected_links)
    curation = build_curation_manifest(edition_date, sources, source_links_detected, structured_available, warnings, now)
    edition_manifest = {
        "dispatch_name": DISPATCH_NAME,
        "dispatch_slug": "gaza",
        "edition_date": edition_date,
        "generated_at": now,
        "public_url": f"{BASE_URL}/gaza/editions/{edition_date}/",
        "local_output_path": str(site_dir),
        "local_dispatch_output_path": str(dispatch_dir),
        "local_backup_path": str(backup_dir),
        "template_version": "gaza-import-v1",
        "source_count": len(sources),
        "story_count": len(curation),
        "source_manifest_path": str(site_dir / "sources_manifest.json"),
        "curation_manifest_path": str(site_dir / "curation_manifest.json"),
        "free_public_artifacts": [str(site_dir / "index.html"), str(site_dir / "sources_manifest.json"), str(site_dir / "curation_manifest.json")],
        "paid_or_detail_artifacts": [],
        "detail_artifacts_publicly_exposed": False,
        "is_free_public": True,
        "has_detail_tier": False,
        "public_exposed": True,
        "warnings": warnings,
        "errors": [],
    }
    import_manifest = {
        "imported_at": now,
        "edition_date": edition_date,
        "original_source_edition_directory": str(source_dir),
        "best_source_file": str(source_file),
        "imported_files": imported_file_names,
        "structured_source_manifest_candidates": [str(path) for path in structured_candidates],
        "structured_source_records_available": structured_available,
        "source_links_detected": source_links_detected,
        "detected_source_links": detected_links,
        "warnings": warnings,
        "did_not_invent_sources": True,
        "blocked_public_parts": sorted(BLOCKED_PUBLIC_PARTS),
    }

    for target_dir in (site_dir, dispatch_dir):
        write_text(target_dir / "index.html", html_out, dry_run, wrote)
        write_json(target_dir / "edition_manifest.json", edition_manifest, dry_run, wrote)
        write_json(target_dir / "sources_manifest.json", sources, dry_run, wrote)
        write_json(target_dir / "curation_manifest.json", curation, dry_run, wrote)
        write_json(target_dir / "import_manifest.json", import_manifest, dry_run, wrote)
    for manifest_name, payload in (
        ("edition_manifest.json", edition_manifest),
        ("sources_manifest.json", sources),
        ("curation_manifest.json", curation),
        ("import_manifest.json", import_manifest),
    ):
        write_json(backup_dir / manifest_name, payload, dry_run, wrote)
    write_text(backup_dir / "index.html", html_out, dry_run, wrote)
    for source in source_files:
        copy_file(source, backup_dir / "original" / source.name, dry_run, wrote)
    update_shared_records(root, edition_date, site_dir, backup_dir, sources, curation, now, dry_run, wrote)
    dates = refresh_gaza_archive(root, dry_run, wrote)
    return {
        "ok": True,
        "dry_run": dry_run,
        "edition_date": edition_date,
        "source_edition_dir": str(source_dir),
        "site_edition_dir": str(site_dir),
        "dispatch_edition_dir": str(dispatch_dir),
        "backup_dir": str(backup_dir),
        "archive_dates": dates,
        "warnings": warnings,
        "errors": [],
        "wrote": wrote,
        "structured_source_records_available": structured_available,
        "source_links_detected": source_links_detected,
        "imported_files": imported_file_names,
    }


def source_dirs_from_root(source_root: Path, start_date: str | None, end_date: str | None) -> list[tuple[str, Path]]:
    source_root = source_root.resolve()
    if not source_root.exists() or not source_root.is_dir():
        raise FileNotFoundError(f"source root does not exist: {source_root}")
    start = validate_date(start_date) if start_date else None
    end = validate_date(end_date) if end_date else None
    found = []
    for path in sorted(source_root.iterdir()):
        if not path.is_dir() or not DATE_RE.match(path.name):
            continue
        if start and path.name < start:
            continue
        if end and path.name > end:
            continue
        found.append((path.name, path))
    return found


def run_imports(args: argparse.Namespace) -> dict[str, Any]:
    root = ROOT
    if args.source_root:
        imports = source_dirs_from_root(Path(args.source_root), args.start_date, args.end_date)
    else:
        if not args.date or not args.source_edition_dir:
            raise ValueError("--date and --source-edition-dir are required for single edition import")
        imports = [(validate_date(args.date), Path(args.source_edition_dir))]
    results = [import_one(root, date, source_dir, args.dry_run, args.force) for date, source_dir in imports]
    errors = [error for result in results for error in result.get("errors", [])]
    warnings = [warning for result in results for warning in result.get("warnings", [])]
    return {
        "ok": not errors,
        "dry_run": args.dry_run,
        "imported_dates": [result["edition_date"] for result in results],
        "results": results,
        "warnings": warnings,
        "errors": errors,
        "push_skipped": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import older Dispatches From Gaza editions into the unified public site.")
    parser.add_argument("--date", help="Edition date to import, YYYY-MM-DD.")
    parser.add_argument("--source-edition-dir", help="Old Gaza edition directory for a single import.")
    parser.add_argument("--source-root", help="Old Gaza output/editions root for batch import.")
    parser.add_argument("--start-date", help="Batch import start date, inclusive.")
    parser.add_argument("--end-date", help="Batch import end date, inclusive.")
    parser.add_argument("--dry-run", action="store_true", help="Report planned writes without changing files.")
    parser.add_argument("--force", action="store_true", help="Replace an existing imported public edition.")
    args = parser.parse_args(argv)
    try:
        result = run_imports(args)
    except Exception as exc:
        result = {"ok": False, "errors": [str(exc)], "warnings": [], "push_skipped": True}
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
