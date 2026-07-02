from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from bluefern_dispatches.generator import discover_public_edition_dates

BASE_URL = "https://dispatches.thebluefernco.com"
MAX_ITEMS = 20
PODCAST_TITLE = "Dispatches from Gaza by The Blue Fern Co."
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
FOOD_LINE_PODCAST_TITLE = "Food Line Dispatch"
FOOD_LINE_PODCAST_LINK = f"{BASE_URL}/food-line/audio/"
FOOD_LINE_PODCAST_FEED_URL = f"{BASE_URL}/food-line/audio/podcast.xml"
FOOD_LINE_PODCAST_ARTWORK_PATH = "food-line/audio/podcast-artwork.png"
FOOD_LINE_PODCAST_ARTWORK_URL = f"{BASE_URL}/{FOOD_LINE_PODCAST_ARTWORK_PATH}"
FOOD_LINE_PODCAST_DESCRIPTION = "Daily source-backed public briefing on U.S. food insecurity signals."


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


def _pages_gaza_audio_root(project_root: Path) -> Path:
    return project_root / "bluefern-dispatches-pages" / "gaza" / "audio"


def _food_line_public_cutoff(max_edition_date: str | None = None) -> str | None:
    if max_edition_date == "":
        return None
    if max_edition_date:
        return max_edition_date
    override = str(os.getenv("BLUEFERN_FOOD_LINE_CURRENT_DATE", "")).strip()
    if override:
        return override
    return "2026-06-05"


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


def _food_line_podcast_artwork_source(project_root: Path) -> Path | None:
    candidates = [
        project_root / "assets" / "food-line-logo.png",
        project_root / "assets" / "american-pressure-logo.png",
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


def _write_food_line_podcast_artwork(project_root: Path, *, dry_run: bool = False) -> Path:
    target = _site_root(project_root) / FOOD_LINE_PODCAST_ARTWORK_PATH
    source = _food_line_podcast_artwork_source(project_root)
    if source is None:
        raise FileNotFoundError("no suitable Food Line podcast artwork source found under assets/")
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    return target


def _parse_date(edition_date: str) -> datetime:
    return datetime.strptime(edition_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _format_human_date(edition_date: str) -> str:
    dt = datetime.strptime(edition_date, "%Y-%m-%d")
    return f"{dt.strftime('%B')} {dt.day}, {dt.year}"


_INVISIBLE_RSS_TEXT_RE = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff\u202a-\u202e\u2066-\u2069]")


def _repair_mojibake(text: str) -> str:
    if not text:
        return text
    if not any(marker in text for marker in ("Â", "Ã", "â", "�")):
        return text
    try:
        repaired = text.encode("latin-1").decode("utf-8")
    except UnicodeError:
        return text
    return repaired


def _sanitize_rss_text(value: str) -> str:
    text = _normalize_spaces(str(value or ""))
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    text = _INVISIBLE_RSS_TEXT_RE.sub("", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]", "", text)
    text = _repair_mojibake(text)
    text = text.replace("\ufffd", "")
    text = text.replace("Â©", "©").replace("â€", "")
    text = text.replace("Â", "").replace("â", "")
    text = _normalize_spaces(text)
    return text.strip()


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
        chunk = re.sub(r"^\s*(?:[-•]\s+|\d+[\.\)]\s+)", "", chunk).strip()
        if not chunk:
            continue
        if re.fullmatch(r"\d+[\.\)]?", chunk):
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
    return f"Today's Dispatches from Gaza covers {joined}."


def _episode_description(row: dict[str, Any], edition_date: str) -> str:
    preferred = _normalize_spaces(str(row.get("episode_summary") or row.get("summary") or ""))
    is_food_line = str(row.get("dispatch_slug") or "").strip() == "food-line"
    if preferred:
        sentence_limit = 5 if is_food_line else 2
        base = _sentence_chunks(preferred)[:sentence_limit]
    else:
        script_text = _normalize_spaces(str(row.get("script_text") or ""))
        if script_text:
            script_sentences = _sentence_chunks(script_text)
            filtered: list[str] = []
            for sentence in script_sentences:
                lower = sentence.lower()
                if lower.startswith("this is the gaza dispatch audio briefing for "):
                    continue
                if lower.startswith("here are the key source-backed developments from today's edition."):
                    continue
                if lower.startswith("for the full source-backed dispatch, read the "):
                    continue
                filtered.append(sentence)
            base = (filtered or script_sentences)[:2]
        else:
            fallback = _headline_fallback(row)
            base = _sentence_chunks(fallback)[:1] if fallback else []
    if not base:
        base = [f"Source-backed Gaza Dispatch audio briefing for {_format_human_date(edition_date)}."]
    suffix = "Transcript and source links are available from The Blue Fern Co."
    if not is_food_line and not any("Transcript and source links" in sentence for sentence in base):
        base.append(suffix)
    return " ".join(base[:5] if is_food_line else base[:3])


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


def _candidate_gaza_audio_roots(project_root: Path) -> list[Path]:
    roots: list[Path] = []
    seen: set[Path] = set()
    for candidate in (_audio_root(project_root), _pages_gaza_audio_root(project_root)):
        normalized = candidate.resolve(strict=False)
        if normalized in seen:
            continue
        seen.add(normalized)
        roots.append(candidate)
    return roots


def _audio_relative_path(audio_file: str) -> Path:
    normalized = str(audio_file or "").strip().lstrip("/").replace("\\", "/")
    if not normalized:
        return Path()
    if "/" not in normalized:
        normalized = f"gaza/audio/{normalized}"
    return Path(normalized)


def _discover_existing_audio_path(project_root: Path, audio_root: Path, edition_date: str, audio_file: str) -> Path | None:
    relative_audio_path = _audio_relative_path(audio_file)
    if not relative_audio_path.parts:
        return None
    candidates = [
        (project_root / "output" / "site" / relative_audio_path).resolve(strict=False),
        (audio_root / relative_audio_path.name).resolve(strict=False),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def discover_gaza_audio_episode_rows(project_root: Path) -> list[dict[str, Any]]:
    episodes: dict[str, dict[str, Any]] = {}
    for audio_root in _candidate_gaza_audio_roots(project_root):
        if not audio_root.exists():
            continue
        for path in sorted(audio_root.glob("*.json"), key=lambda item: item.name, reverse=True):
            if path.name.endswith("flash-briefing.json"):
                continue
            try:
                payload = _read_json(path)
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(payload, dict):
                continue
            edition_date = str(payload.get("edition_date") or "").strip()
            if not edition_date:
                continue
            transcript_url = str(payload.get("transcript_url") or "").strip()
            if not transcript_url:
                transcript_url = f"{BASE_URL}/gaza/audio/{edition_date}-transcript.html"
            transcript_path = audio_root / f"{edition_date}-transcript.html"
            if not transcript_path.exists():
                continue
            audio_file = str(payload.get("audio_file") or "").strip()
            row = dict(payload)
            row["edition_date"] = edition_date
            row["transcript_url"] = transcript_url
            row["_transcript_path"] = str(transcript_path)
            row["_audio_root"] = str(audio_root)
            row["_audio_path"] = ""
            if not audio_file:
                inferred_name = f"{edition_date}.mp3"
                inferred_path = _discover_existing_audio_path(project_root, audio_root, edition_date, inferred_name)
                if inferred_path is not None:
                    audio_file = inferred_name
                    row["audio_file"] = inferred_name
                    row["audio_url"] = f"/gaza/audio/{inferred_name}"
                    row["audio_mime_type"] = "audio/mpeg"
                    row["_audio_path"] = str(inferred_path)
            if audio_file:
                audio_path = _discover_existing_audio_path(project_root, audio_root, edition_date, audio_file)
                if audio_path is not None:
                    row["_audio_path"] = str(audio_path)
                else:
                    row["audio_file"] = None
                    row["audio_url"] = None
                    row["audio_mime_type"] = None
            existing = episodes.get(edition_date)
            if existing is None:
                episodes[edition_date] = row
                continue
            if not str(existing.get("_audio_path") or "").strip() and str(row.get("_audio_path") or "").strip():
                existing["audio_file"] = row.get("audio_file")
                existing["audio_url"] = row.get("audio_url")
                existing["audio_mime_type"] = row.get("audio_mime_type")
                existing["_audio_path"] = row.get("_audio_path")
    return [episodes[key] for key in sorted(episodes.keys(), reverse=True)]


def _load_items(project_root: Path) -> list[dict[str, Any]]:
    return discover_gaza_audio_episode_rows(project_root)


def _enclosure_tag(project_root: Path, row: dict[str, Any]) -> str:
    audio_path_raw = str(row.get("_audio_path") or "").strip()
    if not audio_path_raw:
        return ""
    site_path = Path(audio_path_raw)
    if not site_path.exists():
        return ""
    length = site_path.stat().st_size
    mime = str(row.get("audio_mime_type") or "audio/mpeg")
    audio_url = str(row.get("audio_url") or "").strip()
    if audio_url:
        public_url = BASE_URL + audio_url if audio_url.startswith("/") else audio_url
    else:
        audio_file = str(row.get("audio_file") or "").strip()
        if not audio_file:
            return ""
        relative = _audio_relative_path(audio_file).as_posix()
        public_url = BASE_URL + "/" + relative
    return f'<enclosure url="{escape(public_url)}" length="{length}" type="{escape(mime)}" />'


def _enclosure_tag_for_slug(project_root: Path, row: dict[str, Any], slug: str) -> str:
    audio_file_raw = row.get("audio_file")
    if not isinstance(audio_file_raw, str) or not audio_file_raw.strip():
        return ""
    audio_file = audio_file_raw.strip()
    if "/" in audio_file or "\\" in audio_file:
        relative = audio_file.lstrip("/").replace("\\", "/")
    else:
        relative = f"{slug}/audio/{audio_file}"
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
        desc = _sanitize_rss_text(_episode_description(row, edition_date))
        title = _sanitize_rss_text(_episode_title(edition_date))
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
        f"  <title>{escape(_sanitize_rss_text(PODCAST_TITLE))}</title>\n"
        f"  <link>{escape(PODCAST_LINK)}</link>\n"
        f"  <description>{escape(_sanitize_rss_text(PODCAST_DESCRIPTION))}</description>\n"
        f"  <language>{escape(PODCAST_LANGUAGE)}</language>\n"
        f"  <copyright>{escape(_sanitize_rss_text(PODCAST_COPYRIGHT))}</copyright>\n"
        f"  <managingEditor>{escape(_sanitize_rss_text(PODCAST_OWNER_EMAIL + ' (' + PODCAST_OWNER_NAME + ')'))}</managingEditor>\n"
        f"  <webMaster>{escape(_sanitize_rss_text(PODCAST_OWNER_EMAIL + ' (' + PODCAST_OWNER_NAME + ')'))}</webMaster>\n"
        f"  <itunes:author>{escape(_sanitize_rss_text(PODCAST_AUTHOR))}</itunes:author>\n"
        f"  <itunes:summary>{escape(_sanitize_rss_text(PODCAST_DESCRIPTION))}</itunes:summary>\n"
        f"  <itunes:explicit>{escape(PODCAST_EXPLICIT)}</itunes:explicit>\n"
        f'  <itunes:category text="{escape(PODCAST_CATEGORY)}" />\n'
        "  <itunes:owner>\n"
        f"    <itunes:name>{escape(_sanitize_rss_text(PODCAST_OWNER_NAME))}</itunes:name>\n"
        f"    <itunes:email>{escape(_sanitize_rss_text(PODCAST_OWNER_EMAIL))}</itunes:email>\n"
        "  </itunes:owner>\n"
        f'  <itunes:image href="{escape(_sanitize_rss_text(PODCAST_ARTWORK_URL))}" />\n'
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


def build_food_line_podcast_xml(project_root: Path, *, max_edition_date: str | None = None) -> str:
    site_root = project_root / "output" / "site"
    audio_root = site_root / "food-line" / "audio"
    public_dates = discover_public_edition_dates(site_root, "food-line", max_edition_date=_food_line_public_cutoff(max_edition_date))
    rows: list[dict[str, Any]] = []
    for edition_date in public_dates:
        path = audio_root / f"{edition_date}.json"
        if not path.exists():
            continue
        try:
            payload = _read_json(path)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(payload, dict) and str(payload.get("edition_date") or "").strip() == edition_date:
            rows.append(payload)
    pub_date = format_datetime(datetime.now(timezone.utc))
    items: list[str] = []
    for row in rows[:MAX_ITEMS]:
        edition_date = str(row.get("edition_date") or "").strip()
        transcript_url = str(row.get("transcript_url") or "").strip()
        if not transcript_url:
            transcript_url = f"{BASE_URL}/food-line/audio/{edition_date}-transcript.html"
        title = _sanitize_rss_text(str(row.get("episode_title") or f"Food Line Dispatch - {_format_human_date(edition_date)}"))
        desc = _sanitize_rss_text(_episode_description(row, edition_date))
        enclosure = _enclosure_tag_for_slug(project_root, row, "food-line")
        parts = [
            "  <item>",
            f"    <title>{escape(title)}</title>",
            f"    <link>{escape(transcript_url)}</link>",
            f"    <guid>{escape(transcript_url)}</guid>",
            f"    <pubDate>{format_datetime(_parse_date(edition_date))}</pubDate>",
            f"    <description>{escape(desc)}</description>",
        ]
        if enclosure:
            parts.append(f"    {enclosure}")
        parts.append("  </item>")
        items.append("\n".join(parts))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">\n'
        "<channel>\n"
        f"  <title>{escape(FOOD_LINE_PODCAST_TITLE)}</title>\n"
        f"  <link>{escape(FOOD_LINE_PODCAST_LINK)}</link>\n"
        f"  <description>{escape(FOOD_LINE_PODCAST_DESCRIPTION)}</description>\n"
        f"  <language>{escape(PODCAST_LANGUAGE)}</language>\n"
        f'  <itunes:image href="{escape(FOOD_LINE_PODCAST_ARTWORK_URL)}" />\n'
        f"  <atom:link xmlns:atom=\"http://www.w3.org/2005/Atom\" href=\"{escape(FOOD_LINE_PODCAST_FEED_URL)}\" rel=\"self\" type=\"application/rss+xml\" />\n"
        f"  <lastBuildDate>{pub_date}</lastBuildDate>\n"
        + "\n".join(items)
        + "\n</channel>\n</rss>\n"
    )


def write_food_line_podcast_feed(*, project_root: Path, dry_run: bool = False, max_edition_date: str | None = None) -> Path:
    food_root = project_root / "output" / "site" / "food-line"
    audio_root = food_root / "audio"
    path = food_root / "podcast.xml"
    mirrored_path = audio_root / "podcast.xml"
    _ = _write_food_line_podcast_artwork(project_root, dry_run=dry_run)
    xml = build_food_line_podcast_xml(project_root, max_edition_date=max_edition_date)
    if not dry_run:
        food_root.mkdir(parents=True, exist_ok=True)
        audio_root.mkdir(parents=True, exist_ok=True)
        path.write_text(xml, encoding="utf-8")
        mirrored_path.write_text(xml, encoding="utf-8")
    return path
