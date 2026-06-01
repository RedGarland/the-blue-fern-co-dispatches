from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

BASE_URL = "https://dispatches.thebluefernco.com"
MAX_ITEMS = 20
PODCAST_TITLE = "The Gaza Dispatch from The Blue Fern Co."
PODCAST_AUTHOR = "The Blue Fern Co."
PODCAST_OWNER_NAME = "The Blue Fern Co."
PODCAST_OWNER_EMAIL = "bluefernco@thebluefernco.com"
PODCAST_LINK = f"{BASE_URL}/gaza/audio/"
PODCAST_FEED_URL = f"{BASE_URL}/gaza/audio/podcast.xml"
PODCAST_ARTWORK_PATH = "gaza/audio/podcast-artwork.png"
PODCAST_ARTWORK_URL = f"{BASE_URL}/{PODCAST_ARTWORK_PATH}"
PODCAST_LANGUAGE = "en-us"
PODCAST_COPYRIGHT = "\u00a9 2026 The Blue Fern Co."
PODCAST_CATEGORY = "News"
PODCAST_EXPLICIT = "false"
PODCAST_DESCRIPTION = "Text-first Gaza audio briefing transcripts derived from source-backed daily editions."


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _audio_root(project_root: Path) -> Path:
    return project_root / "output" / "site" / "gaza" / "audio"


def _gaza_root(project_root: Path) -> Path:
    return project_root / "output" / "site" / "gaza"


def _site_root(project_root: Path) -> Path:
    return project_root / "output" / "site"


def _podcast_artwork_source(project_root: Path) -> Path | None:
    candidates = [
        project_root / "assets" / "gaza-podcast-artwork.png",
        project_root / "assets" / "gaza-logo.png",
        project_root / "assets" / "bluefern.png",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def _write_podcast_artwork(project_root: Path, *, dry_run: bool = False) -> Path:
    target = _site_root(project_root) / PODCAST_ARTWORK_PATH
    source = _podcast_artwork_source(project_root)
    if source is None:
        raise FileNotFoundError("no suitable podcast artwork source found under assets/")
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    return target


def _parse_date(edition_date: str) -> datetime:
    return datetime.strptime(edition_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _format_human_date(edition_date: str) -> str:
    dt = datetime.strptime(edition_date, "%Y-%m-%d")
    return f"{dt.strftime('%B')} {dt.day}, {dt.year}"


def _episode_title(edition_date: str) -> str:
    return f"Gaza Briefing for {_format_human_date(edition_date)}"


def _normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _sentence_chunks(text: str) -> list[str]:
    clean = _normalize_spaces(text)
    if not clean:
        return []
    out: list[str] = []
    for part in re.split(r"(?<=[.!?])\s+", clean):
        chunk = part.strip()
        if not chunk:
            continue
        if chunk[-1] not in ".!?":
            chunk = f"{chunk}."
        out.append(chunk)
    return out


def _headline_fallback(row: dict[str, Any]) -> str:
    source_rows = row.get("sources")
    if not isinstance(source_rows, list):
        return ""
    titles: list[str] = []
    for src in source_rows:
        if not isinstance(src, dict):
            continue
        title = _normalize_spaces(str(src.get("title") or ""))
        if not title:
            continue
        titles.append(title)
        if len(titles) >= 3:
            break
    if not titles:
        return ""
    if len(titles) == 1:
        joined = titles[0]
    elif len(titles) == 2:
        joined = f"{titles[0]} and {titles[1]}"
    else:
        joined = f"{titles[0]}, {titles[1]}, and {titles[2]}"
    return f"Today's Gaza Dispatch covers {joined}."


def _episode_description(row: dict[str, Any], edition_date: str) -> str:
    preferred = _normalize_spaces(str(row.get("episode_summary") or row.get("summary") or ""))
    if preferred:
        base = _sentence_chunks(preferred)[:2]
    else:
        script_text = _normalize_spaces(str(row.get("script_text") or ""))
        if script_text:
            base = _sentence_chunks(script_text)[:2]
        else:
            fallback = _headline_fallback(row)
            base = _sentence_chunks(fallback)[:1] if fallback else []
    if not base:
        base = [f"Source-backed Gaza Dispatch audio briefing for {_format_human_date(edition_date)}."]
    suffix = "Transcript and source links are available from The Blue Fern Co."
    if not any("Transcript and source links" in sentence for sentence in base):
        base.append(suffix)
    return " ".join(base[:3])


def _metadata_files(project_root: Path) -> list[Path]:
    root = _audio_root(project_root)
    if not root.exists():
        return []
    rows: list[Path] = []
    for path in root.glob("*.json"):
        if path.name.endswith("flash-briefing.json"):
            continue
        rows.append(path)
    return sorted(rows, key=lambda p: p.name, reverse=True)


def _load_items(project_root: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in _metadata_files(project_root):
        try:
            payload = _read_json(path)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(payload, dict):
            continue
        edition_date = str(payload.get("edition_date") or "").strip()
        transcript_url = str(payload.get("transcript_url") or "").strip()
        if not edition_date or not transcript_url:
            continue
        items.append(payload)
    items.sort(key=lambda row: str(row.get("edition_date") or ""), reverse=True)
    return items[:MAX_ITEMS]


def _enclosure_tag(project_root: Path, row: dict[str, Any]) -> str:
    audio_file_raw = row.get("audio_file")
    if not isinstance(audio_file_raw, str) or not audio_file_raw.strip():
        return ""
    audio_file = audio_file_raw.strip()
    if "/" in audio_file or "\\" in audio_file:
        relative = audio_file.lstrip("/").replace("\\", "/")
    else:
        relative = f"gaza/audio/{audio_file}"
    site_path = project_root / "output" / "site" / Path(relative)
    if not site_path.exists():
        return ""
    length = site_path.stat().st_size
    mime = str(row.get("audio_mime_type") or "audio/mpeg")
    audio_url = str(row.get("audio_url") or "").strip()
    if audio_url:
        public_url = BASE_URL + audio_url if audio_url.startswith("/") else audio_url
    else:
        public_url = BASE_URL + "/" + relative.replace("\\", "/")
    return f'<enclosure url="{escape(public_url)}" length="{length}" type="{escape(mime)}" />'


def build_gaza_podcast_xml(project_root: Path) -> str:
    rows = _load_items(project_root)
    pub_date = format_datetime(datetime.now(timezone.utc))
    channel_items: list[str] = []
    for row in rows:
        edition_date = str(row.get("edition_date") or "").strip()
        transcript_url = str(row.get("transcript_url") or "").strip()
        if not edition_date or not transcript_url:
            continue
        desc = _episode_description(row, edition_date)
        title = _episode_title(edition_date)
        item_pub_date = format_datetime(_parse_date(edition_date))
        enclosure = _enclosure_tag(project_root, row)
        parts = [
            "  <item>",
            f"    <title>{escape(title)}</title>",
            f"    <link>{escape(transcript_url)}</link>",
            f"    <guid>{escape(transcript_url)}</guid>",
            f"    <pubDate>{item_pub_date}</pubDate>",
            f"    <description>{escape(desc)}</description>",
        ]
        if enclosure:
            parts.append(f"    {enclosure}")
        parts.append("  </item>")
        channel_items.append("\n".join(parts))
    items_blob = "\n".join(channel_items)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">\n'
        "<channel>\n"
        f"  <title>{escape(PODCAST_TITLE)}</title>\n"
        f"  <link>{escape(PODCAST_LINK)}</link>\n"
        f"  <description>{escape(PODCAST_DESCRIPTION)}</description>\n"
        f"  <language>{escape(PODCAST_LANGUAGE)}</language>\n"
        f"  <copyright>{escape(PODCAST_COPYRIGHT)}</copyright>\n"
        f"  <managingEditor>{escape(PODCAST_OWNER_EMAIL + ' (' + PODCAST_OWNER_NAME + ')')}</managingEditor>\n"
        f"  <webMaster>{escape(PODCAST_OWNER_EMAIL + ' (' + PODCAST_OWNER_NAME + ')')}</webMaster>\n"
        f"  <itunes:author>{escape(PODCAST_AUTHOR)}</itunes:author>\n"
        f"  <itunes:summary>{escape(PODCAST_DESCRIPTION)}</itunes:summary>\n"
        f"  <itunes:explicit>{escape(PODCAST_EXPLICIT)}</itunes:explicit>\n"
        f'  <itunes:category text="{escape(PODCAST_CATEGORY)}" />\n'
        "  <itunes:owner>\n"
        f"    <itunes:name>{escape(PODCAST_OWNER_NAME)}</itunes:name>\n"
        f"    <itunes:email>{escape(PODCAST_OWNER_EMAIL)}</itunes:email>\n"
        "  </itunes:owner>\n"
        f'  <itunes:image href="{escape(PODCAST_ARTWORK_URL)}" />\n'
        f"  <atom:link xmlns:atom=\"http://www.w3.org/2005/Atom\" href=\"{escape(PODCAST_FEED_URL)}\" rel=\"self\" type=\"application/rss+xml\" />\n"
        f"  <lastBuildDate>{pub_date}</lastBuildDate>\n"
        f"{items_blob}\n"
        "</channel>\n"
        "</rss>\n"
    )


def write_gaza_podcast_feed(*, project_root: Path, dry_run: bool = False) -> Path:
    gaza_root = _gaza_root(project_root)
    audio_root = _audio_root(project_root)
    path = gaza_root / "podcast.xml"
    mirrored_path = audio_root / "podcast.xml"
    _ = _write_podcast_artwork(project_root, dry_run=dry_run)
    xml = build_gaza_podcast_xml(project_root)
    if not dry_run:
        gaza_root.mkdir(parents=True, exist_ok=True)
        audio_root.mkdir(parents=True, exist_ok=True)
        path.write_text(xml, encoding="utf-8")
        mirrored_path.write_text(xml, encoding="utf-8")
    return path

