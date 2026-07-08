from __future__ import annotations

import html
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bluefern_dispatches.public_prose import sanitize_public_prose

BASE_URL = "https://dispatches.thebluefernco.com"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_STORIES = 6
MIN_STORIES = 3
EXCLUDED_AUDIO_MARKERS = (
    "UK politics |",
    "Environment |",
    "Ukraine |",
    "England news |",
    "UK news |",
    "royal property",
    "social care system",
    "planning laws",
    "Andrew Mountbatten-Windsor",
    "Lebanon rises despite ceasefire",
)
GAZA_DIRECT_RE = re.compile(
    r"\b(gaza|rafah|khan younis|deir al-balah|jabalia|beit lahia|beit hanoun)\b",
    re.IGNORECASE,
)
PALESTINIAN_CONTEXT_RE = re.compile(r"\b(palestin\w*|west bank|occupied territories|occupation)\b", re.IGNORECASE)
DETENTION_CONTEXT_RE = re.compile(r"\b(detention|detained|detainee|prison|red cross|icrc)\b", re.IGNORECASE)
HISTORICAL_CONTEXT_RE = re.compile(r"\b(1967|expulsion|expulsions|killings?)\b", re.IGNORECASE)
GAZA_ADJACENT_AID_RE = re.compile(r"\b(gaza-bound|flotilla|maritime aid|aid convoy|crossing|border)\b", re.IGNORECASE)


@dataclass(frozen=True)
class GazaAudioResult:
    edition_date: str
    transcript_path: Path
    metadata_path: Path
    flash_briefing_path: Path
    podcast_path: Path
    audio_status: str
    audio_file: str | None
    audio_url: str | None
    tts_provider: str
    tts_model: str | None
    tts_voice: str | None
    tts_error: str | None
    story_count: int


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_date(edition_date: str) -> str:
    value = str(edition_date or "").strip()
    if not DATE_RE.match(value):
        raise ValueError(f"date must use YYYY-MM-DD: {value}")
    return value


def _edition_root(project_root: Path, edition_date: str) -> Path:
    return project_root / "output" / "site" / "gaza" / "editions" / edition_date


def _dispatch_edition_root(project_root: Path, edition_date: str) -> Path:
    return project_root / "output" / "dispatches" / "gaza" / "editions" / edition_date


def _audio_root(project_root: Path) -> Path:
    return project_root / "output" / "site" / "gaza" / "audio"


def _gaza_public_root(project_root: Path) -> Path:
    return project_root / "output" / "site" / "gaza"




def _discover_audio_entries(project_root: Path) -> list[dict[str, str]]:
    from bluefern_dispatches.podcast_feed import discover_gaza_audio_episode_rows

    rows: list[dict[str, str]] = []
    for payload in discover_gaza_audio_episode_rows(project_root):
        date_text = str(payload.get("edition_date") or "").strip()
        if not DATE_RE.match(date_text):
            continue
        transcript_url = str(payload.get("transcript_url") or "").strip()
        if not transcript_url:
            transcript_url = f"{BASE_URL}/gaza/audio/{date_text}-transcript.html"
        rows.append(
            {
                "edition_date": date_text,
                "transcript_url": transcript_url.replace(BASE_URL, "") if transcript_url.startswith(BASE_URL) else transcript_url,
                "audio_url": str(payload.get("audio_url") or "").strip(),
                "edition_url": f"/gaza/editions/{date_text}/",
            }
        )
    return rows


def write_audio_index(project_root: Path, *, dry_run: bool = False) -> Path:
    entries = _discover_audio_entries(project_root)
    audio_root = _audio_root(project_root)
    index_path = audio_root / "index.html"
    items: list[str] = []
    for row in entries:
        date_text = row["edition_date"]
        transcript_url = html.escape(row["transcript_url"])
        audio_url = str(row.get("audio_url") or "").strip()
        edition_url = html.escape(row["edition_url"])
        media_cell = (
            f'<a href="{html.escape(audio_url)}">MP3</a> '
            f'<audio controls preload="none" src="{html.escape(audio_url)}"></audio>'
            if audio_url
            else '<span class="gaza-audio-index-empty">No MP3 yet</span>'
        )
        items.append(
            "\n      <li class=\"gaza-audio-index-row\">"
            f'<span class="gaza-audio-index-date"><strong>{html.escape(date_text)}</strong></span>'
            f'<span class="gaza-audio-index-transcript"><a href="{transcript_url}">Transcript</a></span>'
            f'<span class="gaza-audio-index-media">{media_cell}</span>'
            f'<span class="gaza-audio-index-edition"><a href="{edition_url}">Full edition</a></span>'
            "</li>"
        )
    body = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '  <meta charset="utf-8" />',
        '  <meta name="viewport" content="width=device-width, initial-scale=1" />',
        "  <title>Gaza Audio Index</title>",
        '  <link rel="stylesheet" href="../assets/site.css" />',
        "</head>",
        "<body>",
        '  <main class="container">',
        "    <h1>Gaza Audio and Transcript Index</h1>",
        '    <p><a href="/gaza/">Back to Gaza dispatch home</a></p>',
        '    <p><a href="/gaza/audio/podcast.xml">Podcast feed</a></p>',
        '    <ul class="gaza-audio-index">',
        *items,
        "    </ul>",
        "  </main>",
        "</body>",
        "</html>",
    ]
    if not dry_run:
        audio_root.mkdir(parents=True, exist_ok=True)
        index_path.write_text("\n".join(body), encoding="utf-8")
    return index_path


def refresh_gaza_audio_public_surfaces(project_root: Path) -> tuple[Path, Path]:
    # Preview and no-audio runs still need to keep the public history surfaces in sync.
    index_path = write_audio_index(project_root, dry_run=False)

    from bluefern_dispatches.podcast_feed import write_gaza_podcast_feed

    podcast_path = write_gaza_podcast_feed(project_root=project_root, dry_run=False)
    return index_path, podcast_path


def _clean_public_text(value: str) -> str:
    return sanitize_public_prose(str(value or ""))


def _count_words(value: str) -> int:
    return len(re.findall(r"\S+", str(value or "")))


def _story_rows(curation_payload: Any) -> list[dict[str, Any]]:
    if not isinstance(curation_payload, list):
        return []
    rows: list[dict[str, Any]] = []
    for row in curation_payload:
        if not isinstance(row, dict):
            continue
        if row.get("included_in_public_summary") is False:
            continue
        if row.get("public_rendered") is False:
            continue
        rows.append(row)
    return rows


def _story_text(story: dict[str, Any]) -> str:
    parts = [
        str(story.get("title") or ""),
        str(story.get("summary") or ""),
        str(story.get("public_summary") or ""),
        str(story.get("social_summary") or ""),
        str(story.get("summary_or_snippet") or ""),
        str(story.get("category") or ""),
        str(story.get("story_scope") or ""),
        str(story.get("region_scope") or ""),
        " ".join(str(item) for item in story.get("relevance_terms_matched") or []),
    ]
    return " ".join(parts)


def _story_excluded_markers(story: dict[str, Any]) -> list[str]:
    text = _story_text(story).lower()
    return [marker for marker in EXCLUDED_AUDIO_MARKERS if marker.lower() in text]


def _audio_story_eligibility(story: dict[str, Any]) -> tuple[bool, str | None]:
    text = _story_text(story)
    lowered = text.lower()
    markers = _story_excluded_markers(story)
    if markers:
        return False, f"excluded marker {markers[0]!r}"
    if "newsletter" in lowered and "gaza" not in lowered:
        return False, "multi-topic newsletter without Gaza-specific summary"
    if "newsletter" in lowered and any(marker.lower() in lowered for marker in EXCLUDED_AUDIO_MARKERS):
        return False, "multi-topic newsletter with unrelated sidebar markers"
    if "lebanon rises despite ceasefire" in lowered:
        return False, "Lebanon-only displacement story"
    if "lebanon" in lowered and not (
        GAZA_DIRECT_RE.search(text)
        or (PALESTINIAN_CONTEXT_RE.search(text) and (DETENTION_CONTEXT_RE.search(text) or HISTORICAL_CONTEXT_RE.search(text)))
    ):
        return False, "Lebanon-only or broad regional story"
    if any(term in lowered for term in ("ukraine", "uk politics", "england news", "royal property", "social care system", "planning laws")):
        return False, "unrelated non-Gaza topic"
    if GAZA_DIRECT_RE.search(text):
        return True, None
    if PALESTINIAN_CONTEXT_RE.search(text) and (DETENTION_CONTEXT_RE.search(text) or HISTORICAL_CONTEXT_RE.search(text)):
        return True, None
    if GAZA_ADJACENT_AID_RE.search(text) and ("gaza" in lowered or "palestin" in lowered):
        return True, None
    return False, "not clearly Gaza-focused or Palestinian-context audio material"


def select_gaza_audio_stories(curation_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for story in curation_rows:
        eligible, _reason = _audio_story_eligibility(story)
        if eligible:
            selected.append(story)
        if len(selected) >= MAX_STORIES:
            break
    return selected


def _source_map(sources_payload: Any) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(sources_payload, list):
        return out
    for row in sources_payload:
        if not isinstance(row, dict):
            continue
        key = str(row.get("source_record_id") or row.get("source_id") or "").strip()
        if key and key not in out:
            out[key] = row
    return out


def _story_source_ids(story: dict[str, Any]) -> list[str]:
    for key in ("source_record_ids", "source_ids"):
        raw = story.get(key)
        if isinstance(raw, list):
            vals = [str(item).strip() for item in raw if str(item).strip()]
            if vals:
                return vals
    return []


def _story_publishers(story: dict[str, Any], source_rows: list[dict[str, Any]]) -> list[str]:
    story_publishers = story.get("publisher_names")
    if isinstance(story_publishers, list):
        cleaned = [str(item).strip() for item in story_publishers if str(item).strip()]
        if cleaned:
            return cleaned
    pubs: list[str] = []
    seen: set[str] = set()
    for row in source_rows:
        pub = str(row.get("publisher") or "").strip()
        if not pub:
            continue
        key = pub.lower()
        if key in seen:
            continue
        seen.add(key)
        pubs.append(pub)
    return pubs


def _join_words(items: list[str]) -> str:
    if not items:
        return "public sources"
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return f"{', '.join(items[:-1])}, and {items[-1]}"


def _format_date_human(edition_date: str) -> str:
    dt = datetime.strptime(edition_date, "%Y-%m-%d")
    return f"{dt.strftime('%B')} {dt.day}, {dt.year}"


_AUDIO_SENTENCE_BOUNDARY_AFTER_YEAR_RE = re.compile(r"(?<=\b(?:19|20)\d{2})\s+(?=[A-Z])")
_AUDIO_WHITESPACE_RE = re.compile(r"\s+")
_AUDIO_FRAGMENT_SENTENCE_RE = re.compile(
    r"^[A-Z][A-Za-z0-9'’.-]*(?:\s+[A-Z][A-Za-z0-9'’.-]*){0,4}\s+among at least \d+ journalists killed since\.?$"
)
_GAZA_NAMED_CASUALTY_TITLE_RE = re.compile(
    r"^(?P<org>[A-Z][A-Za-z&'’.-]*(?:\s+[A-Z][A-Za-z&'’.-]*)*)\s+"
    r"(?P<role>cameraman|journalist|reporter|photographer)\s+"
    r"(?P<person>[A-Z][A-Za-z'’.-]+(?:\s+[A-Z][A-Za-z'’.-]+)+)\s+killed in\s+(?P<event>.+)$",
    re.IGNORECASE,
)
_GAZA_AUDIO_FIELD_PRIORITY = ("summary", "public_summary", "description", "dek", "social_summary", "summary_or_snippet")
_GAZA_AUDIO_SOURCEY_BOILERPLATE_RE = re.compile(
    r"(qatar-based news network|has said one of its journalists was killed|to have been killed since|reported on its website)",
    re.IGNORECASE,
)


def _normalize_audio_sentence_text(text: str) -> str:
    cleaned = _clean_public_text(str(text or "").strip())
    if not cleaned:
        return ""
    cleaned = html.unescape(cleaned)
    cleaned = cleaned.replace("..", ".")
    cleaned = re.sub(
        r"\b(after|before|while|when|during)\.\s+([A-Z][A-Za-z0-9'/-]*)",
        r"\1 \2",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = _AUDIO_SENTENCE_BOUNDARY_AFTER_YEAR_RE.sub(". ", cleaned)
    cleaned = _AUDIO_WHITESPACE_RE.sub(" ", cleaned).strip()
    return cleaned


def _audio_sentence_list(text: str) -> list[str]:
    cleaned = _normalize_audio_sentence_text(text)
    if not cleaned:
        return []
    sentences: list[str] = []
    seen: set[str] = set()
    for chunk in re.split(r"(?<=[.!?])\s+", cleaned):
        sentence = chunk.strip()
        if not sentence:
            continue
        sentence = sentence.replace("..", ".")
        if sentence[-1] not in ".!?":
            sentence = f"{sentence}."
        if _AUDIO_FRAGMENT_SENTENCE_RE.match(sentence):
            continue
        normalized = re.sub(r"[.!?]+$", "", sentence).strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        sentences.append(sentence)
    return sentences


def _story_audio_named_casualty_summary(story: dict[str, Any]) -> str:
    title = str(story.get("title") or "").strip()
    match = _GAZA_NAMED_CASUALTY_TITLE_RE.match(title)
    if not match:
        return ""
    person = match.group("person").strip()
    org = match.group("org").strip()
    role = match.group("role").strip()
    source_records = [record for record in (story.get("source_records") or []) if isinstance(record, dict)]
    record_text = " ".join(
        str(
            record.get("summary_or_snippet")
            or record.get("summary")
            or record.get("text")
            or record.get("title")
            or ""
        )
        for record in source_records
    )
    text = f"{_story_text(story)} {record_text}".lower()
    if not person or not org or not role:
        return ""
    surname = person.split()[-1]
    event_sentence = f"Multiple outlets reported that {person}, a {role} for {org}, was killed in an Israeli strike"
    if "bureij refugee camp" in text:
        event_sentence += " on a house in the Bureij refugee camp in central Gaza"
    elif "gaza" in text:
        event_sentence += " in Gaza"
    event_sentence += "."
    context_sentence = ""
    if "260" in text and "palestinian journalists" in text and "israel's war on gaza began in october 2023" in text:
        context_sentence = f"{org} said {surname} was among at least 260 Palestinian journalists killed since Israel's war on Gaza began in October 2023."
    return " ".join(part for part in (event_sentence, context_sentence) if part)


def _score_audio_summary_candidate(text: str, title: str) -> tuple[int, int, int]:
    sentences = _audio_sentence_list(text)
    if not sentences:
        return (-10_000, 0, 0)
    joined = " ".join(sentences)
    lowered = joined.lower()
    title_terms = {term for term in re.findall(r"[A-Za-z]{4,}", title.lower()) if term not in {"the", "with", "from", "this", "that", "have", "been", "into", "onto"}}
    title_hits = sum(1 for term in title_terms if term in lowered)
    boilerplate_hits = 1 if _GAZA_AUDIO_SOURCEY_BOILERPLATE_RE.search(joined) else 0
    length = len(joined)
    sentence_count = len(sentences)
    score = 0
    if sentence_count <= 2:
        score += 20
    else:
        score -= 8 * (sentence_count - 2)
    if length <= 260:
        score += 12
    elif length <= 360:
        score += 6
    else:
        score -= min(12, (length - 360) // 40 + 1)
    score += min(10, title_hits * 2)
    score -= boilerplate_hits * 18
    if re.search(r"\b(killed|strike|attack|cameraman|journalist|gaza|bureij|palestinian journalists)\b", lowered):
        score += 5
    if re.search(r"\b(\d{3,})\b", joined):
        score += 2
    return score, -sentence_count, -length


def _story_audio_summary_text(story: dict[str, Any]) -> str:
    named_casualty = _story_audio_named_casualty_summary(story)
    if named_casualty:
        return named_casualty
    title = str(story.get("title") or "").strip()
    best_text = ""
    best_score = (-10_000, 0, 0)
    for field in _GAZA_AUDIO_FIELD_PRIORITY:
        raw = str(story.get(field) or "").strip()
        if not raw:
            continue
        score = _score_audio_summary_candidate(raw, title)
        if score > best_score:
            best_score = score
            best_text = raw
    if not best_text:
        return ""
    sentences = _audio_sentence_list(best_text)
    if not sentences:
        return ""
    return sentences[0]


def build_gaza_audio_script(
    *,
    edition_date: str,
    curation_rows: list[dict[str, Any]],
    sources_by_id: dict[str, dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    selected = select_gaza_audio_stories(curation_rows)
    if not selected:
        raise ValueError(f"no Gaza-audio-eligible stories found for {edition_date}")
    script_sections: list[str] = []
    script_sections.append(f"This is the Gaza Dispatch audio briefing for {_format_date_human(edition_date)}.")
    script_sections.append("Here are the key source-backed developments from today's edition.")
    used_sources: dict[str, dict[str, Any]] = {}

    for idx, story in enumerate(selected, start=1):
        title = str(story.get("title") or "Untitled update").strip()
        summary = _story_audio_summary_text(story)
        source_ids = _story_source_ids(story)
        source_rows = [sources_by_id[sid] for sid in source_ids if sid in sources_by_id]
        for sid in source_ids:
            if sid in sources_by_id and sid not in used_sources:
                used_sources[sid] = sources_by_id[sid]
        publishers = _story_publishers(story, source_rows)
        attribution = f"reported by {_join_words(publishers)}"
        if str(story.get("attribution_mode") or "").strip() == "military_claim_reported":
            attribution = f"according to {_join_words(publishers)} reporting on an IDF statement"
        narrative_sentences: list[str] = []
        title_sentence = _audio_sentence_list(title)
        if title_sentence:
            narrative_sentences.extend(title_sentence[:1])
        else:
            narrative_sentences.append("Untitled update.")
        for sentence in _audio_sentence_list(summary):
            if title_sentence and sentence == title_sentence[0]:
                continue
            narrative_sentences.append(sentence)
        narrative_sentences.append(f"This was {attribution}.")
        story_text = " ".join(sentence.strip() for sentence in narrative_sentences if sentence.strip())
        story_text = _normalize_audio_sentence_text(story_text)
        script_sections.append(f"{idx}. {story_text}")

    script_sections.append(
        f"For the full source-backed dispatch, read the {_format_date_human(edition_date)} Gaza edition at dispatches.thebluefernco.com."
    )
    script = "\n\n".join(script_sections).strip()
    return script, list(used_sources.values())


def _validate_gaza_audio_transcript(*, stories: list[dict[str, Any]], transcript_html: str) -> None:
    transcript_text = html.unescape(str(transcript_html or ""))
    for story in stories:
        markers = _story_excluded_markers(story)
        if markers:
            title = str(story.get("title") or "Untitled update").strip()
            raise ValueError(f"Gaza audio blocked by excluded marker {markers[0]!r} in story: {title}")
    lowered = transcript_text.lower()
    for marker in EXCLUDED_AUDIO_MARKERS:
        if marker.lower() not in lowered:
            continue
        for story in stories:
            if marker.lower() in _story_text(story).lower():
                title = str(story.get("title") or "Untitled update").strip()
                raise ValueError(f"Gaza audio blocked by transcript validator for marker {marker!r} in story: {title}")
        raise ValueError(f"Gaza audio blocked by transcript validator for marker {marker!r}")


def _script_segments(script_text: str, story_count: int) -> list[dict[str, str]]:
    parts = [segment.strip() for segment in str(script_text or "").split("\n\n") if segment.strip()]
    intro_count = 2
    if len(parts) <= intro_count:
        return [{"type": "full", "text": script_text.strip()}] if script_text.strip() else []
    segments: list[dict[str, str]] = []
    intro = " ".join(parts[:intro_count]).strip()
    if intro:
        segments.append({"type": "intro", "text": intro})
    story_end = min(intro_count + max(0, story_count), len(parts))
    for idx, chunk in enumerate(parts[intro_count:story_end], start=1):
        segments.append({"type": f"story_{idx}", "text": chunk})
    closing = " ".join(parts[story_end:]).strip()
    if closing:
        segments.append({"type": "closing", "text": closing})
    return segments


def _parse_voice_list(text: str) -> list[str]:
    return [part.strip() for part in str(text or "").split(",") if part.strip()]


def render_gaza_transcript_html(
    *,
    edition_date: str,
    script_text: str,
    sources: list[dict[str, Any]],
    audio_url: str | None = None,
) -> str:
    title = f"Gaza Audio Briefing Transcript - {_format_date_human(edition_date)}"
    parts = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '  <meta charset="utf-8" />',
        '  <meta name="viewport" content="width=device-width, initial-scale=1" />',
        f"  <title>{html.escape(title)}</title>",
        '  <link rel="stylesheet" href="../assets/site.css" />',
        "</head>",
        "<body>",
        '  <main class="container">',
        f"    <h1>{html.escape(title)}</h1>",
        f"    <p><strong>Date:</strong> {html.escape(_format_date_human(edition_date))}</p>",
        f'    <p><a href="/gaza/editions/{html.escape(edition_date)}/">Back to full Gaza edition</a></p>',
        '    <p><a href="/gaza/audio/podcast.xml">Podcast feed</a></p>',
    ]
    if audio_url:
        parts.extend(
            [
                "    <h2>Audio</h2>",
                f'    <audio controls preload="none" src="{html.escape(audio_url)}"></audio>',
            ]
        )
    parts.extend(
        [
        "    <h2>Transcript</h2>",
        ]
    )
    for para in script_text.split("\n\n"):
        clean = _clean_public_text(para)
        if clean:
            parts.append(f"    <p>{html.escape(clean)}</p>")
    parts.extend(["    <h2>Sources</h2>", "    <ul>"])
    for src in sources:
        pub = str(src.get("publisher") or "Unknown publisher").strip()
        title_text = str(src.get("title") or "Source").strip()
        url = str(src.get("url") or "").strip()
        if not url:
            continue
        parts.append(
            f'      <li><a href="{html.escape(url)}" target="_blank" rel="noopener noreferrer">{html.escape(title_text)}</a> - {html.escape(pub)}</li>'
        )
    parts.extend(["    </ul>", "  </main>", "</body>", "</html>"])
    return "\n".join(parts)


def build_audio_metadata(
    *,
    edition_date: str,
    script_text: str,
    used_sources: list[dict[str, Any]],
    audio_status: str = "script_ready_no_audio_file",
    audio_file: str | None = None,
    audio_url: str | None = None,
    tts_provider: str = "none",
    tts_model: str | None = None,
    tts_voice: str | None = None,
    tts_error: str | None = None,
    audio_format: str = "mp3",
    voice_mode: str = "single",
    tts_voices_used: list[str] | None = None,
    segment_voice_map: list[dict[str, Any]] | None = None,
    tts_input_character_count: int | None = None,
    tts_input_word_count: int | None = None,
    tts_story_count: int | None = None,
    tts_segment_count: int | None = None,
    audio_duration_seconds: float | None = None,
    audio_file_size_bytes: int | None = None,
    estimated_cost_usd: float | None = None,
    estimated_cost_note: str | None = None,
    tts_pricing_basis: str | None = None,
    tts_pricing_model: str | None = None,
    cost_logged_at: str | None = None,
    segue_chime: str = "none",
    segue_chime_count: int = 0,
    segue_chime_asset: str | None = None,
) -> dict[str, Any]:
    edition_url = f"{BASE_URL}/gaza/editions/{edition_date}/"
    transcript_url = f"{BASE_URL}/gaza/audio/{edition_date}-transcript.html"
    source_rows: list[dict[str, Any]] = []
    for src in used_sources:
        source_rows.append(
            {
                "source_record_id": str(src.get("source_record_id") or src.get("source_id") or "").strip(),
                "publisher": str(src.get("publisher") or "").strip(),
                "url": str(src.get("url") or "").strip(),
                "title": str(src.get("title") or "").strip(),
            }
        )
    return {
        "dispatch_slug": "gaza",
        "edition_date": edition_date,
        "generated_at": _iso_now(),
        "audio_status": audio_status,
        "audio_file": audio_file,
        "audio_url": audio_url,
        "audio_mime_type": ("audio/wav" if audio_format == "wav" else "audio/mpeg") if audio_file else None,
        "tts_provider": tts_provider,
        "tts_model": tts_model,
        "tts_voice": tts_voice,
        "tts_error": tts_error,
        "voice_mode": voice_mode,
        "tts_voices_used": tts_voices_used or ([] if tts_provider != "none" else []),
        "segment_voice_map": segment_voice_map or [],
        "tts_input_character_count": tts_input_character_count,
        "tts_input_word_count": tts_input_word_count,
        "tts_story_count": tts_story_count,
        "tts_segment_count": tts_segment_count,
        "audio_duration_seconds": audio_duration_seconds,
        "audio_file_size_bytes": audio_file_size_bytes,
        "estimated_cost_usd": estimated_cost_usd,
        "estimated_cost_note": estimated_cost_note,
        "tts_pricing_basis": tts_pricing_basis,
        "tts_pricing_model": tts_pricing_model,
        "cost_logged_at": cost_logged_at,
        "segue_chime": segue_chime,
        "segue_chime_count": segue_chime_count,
        "segue_chime_asset": segue_chime_asset,
        "transcript_url": transcript_url,
        "edition_url": edition_url,
        "script_text": script_text,
        "source_count": len(source_rows),
        "sources": source_rows,
    }


def build_flash_briefing_item(metadata: dict[str, Any]) -> dict[str, Any]:
    edition_date = str(metadata.get("edition_date") or "")
    script_text = _clean_public_text(str(metadata.get("script_text") or ""))
    short = script_text[:1100].strip()
    if len(script_text) > 1100 and " " in short:
        short = short.rsplit(" ", 1)[0].rstrip(" ,;:-")
    if short and short[-1] not in ".!?":
        short = f"{short}."
    audio_url = str(metadata.get("audio_url") or "").strip()
    redirect = audio_url if audio_url else str(metadata.get("transcript_url") or f"{BASE_URL}/gaza/editions/{edition_date}/")
    return {
        "uid": f"gaza-{edition_date}",
        "updateDate": str(metadata.get("generated_at") or _iso_now()),
        "titleText": f"Gaza Dispatch {_format_date_human(edition_date)}",
        "mainText": short,
        "redirectionUrl": redirect,
    }


def load_gaza_audio_inputs(project_root: Path, edition_date: str) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    edition_dir = _edition_root(project_root, edition_date)
    curation_path = edition_dir / "curation_manifest.json"
    sources_path = edition_dir / "sources_manifest.json"
    if not curation_path.exists() or not sources_path.exists():
        dispatch_dir = _dispatch_edition_root(project_root, edition_date)
        dispatch_curation = dispatch_dir / "curation_manifest.json"
        dispatch_sources = dispatch_dir / "sources_manifest.json"
        if dispatch_curation.exists() and dispatch_sources.exists():
            edition_dir = dispatch_dir
            curation_path = dispatch_curation
            sources_path = dispatch_sources
    missing = [str(p) for p in (curation_path, sources_path) if not p.exists()]
    if missing:
        raise FileNotFoundError(f"required Gaza edition artifacts missing for {edition_date}: {', '.join(missing)}")
    curation_rows = _story_rows(_read_json(curation_path))
    if not curation_rows:
        raise ValueError(f"no public Gaza stories found in curation manifest for {edition_date}")
    sources = _source_map(_read_json(sources_path))
    if not sources:
        raise ValueError(f"no Gaza sources found in sources manifest for {edition_date}")
    return curation_rows, sources


def write_gaza_audio_outputs(
    project_root: Path,
    edition_date: str,
    *,
    dry_run: bool = False,
    tts_provider: str = "none",
    tts_model: str = "gpt-4o-mini-tts",
    tts_voice: str = "alloy",
    audio_format: str = "mp3",
    alternate_voices: bool = False,
    voices: str | None = None,
    segue_chime: str = "none",
    tts_price_per_1m_chars: float | None = None,
) -> GazaAudioResult:
    date_text = _validate_date(edition_date)
    curation_rows, sources_by_id = load_gaza_audio_inputs(project_root, date_text)
    selected_stories = select_gaza_audio_stories(curation_rows)
    if not selected_stories:
        raise ValueError(f"no Gaza-audio-eligible stories found for {date_text}")
    script_text, used_sources = build_gaza_audio_script(
        edition_date=date_text,
        curation_rows=curation_rows,
        sources_by_id=sources_by_id,
    )
    segments = _script_segments(script_text, len(selected_stories))
    audio_root = _audio_root(project_root)
    gaza_root = _gaza_public_root(project_root)
    audio_filename = f"{date_text}.{audio_format}"
    audio_file_path = audio_root / audio_filename
    audio_file_value: str | None = None
    audio_url_value: str | None = None
    audio_status = "script_ready_no_audio_file"
    tts_error: str | None = None
    chosen_provider = str(tts_provider or "none").strip().lower()
    chosen_model = tts_model if chosen_provider != "none" else None
    chosen_voice = tts_voice if chosen_provider != "none" else None
    voice_mode = "single"
    segment_voice_map: list[dict[str, Any]] = []
    tts_voices_used: list[str] = [tts_voice] if chosen_provider != "none" else []
    segue_chime_mode = str(segue_chime or "none").strip().lower() or "none"
    if segue_chime_mode not in {"none", "gentle"}:
        raise ValueError("segue_chime must be none or gentle")
    configured_price = tts_price_per_1m_chars
    if configured_price is None:
        raw_price = str(os.getenv("GAZA_AUDIO_TTS_PRICE_PER_1M_CHARS", "")).strip()
        if raw_price:
            configured_price = float(raw_price)
    estimated_cost_usd: float | None = None
    estimated_cost_note: str | None = "pricing input not configured"
    tts_pricing_basis: str | None = None
    tts_pricing_model: str | None = None
    if configured_price is not None:
        tts_pricing_basis = "price_per_1m_input_chars"
        tts_pricing_model = "configured_manual_estimate"

    if chosen_provider != "none":
        from bluefern_dispatches.tts_provider import synthesize_speech

        if alternate_voices:
            voice_mode = "alternating"
            voice_list = _parse_voice_list(voices or "")
            if len(voice_list) < 2:
                raise ValueError("alternate voice mode requires at least two voices via --voices")
            if audio_format != "wav":
                raise ValueError("alternate voice mode currently requires --audio-format wav")
            tts_voices_used = list(dict.fromkeys(voice_list))
            from bluefern_dispatches.audio_assembly import assemble_wav, make_gentle_chime_wav

            part_bytes: list[bytes] = []
            story_counter = 0
            chime_count = 0
            chime_asset: str | None = None
            chime_bytes = make_gentle_chime_wav() if segue_chime_mode == "gentle" else None
            for idx, segment in enumerate(segments):
                kind = str(segment.get("type") or "")
                if kind.startswith("story_"):
                    voice = voice_list[story_counter % len(voice_list)]
                    story_counter += 1
                else:
                    voice = voice_list[0]
                segment_voice_map.append({"segment_index": idx, "segment_type": kind, "voice": voice})
                segment_result = synthesize_speech(
                    text=str(segment.get("text") or ""),
                    provider=chosen_provider,
                    model=tts_model,
                    voice=voice,
                    audio_format=audio_format,
                )
                if not segment_result.ok or not segment_result.audio_bytes:
                    audio_status = segment_result.error_reason or "tts_generation_failed"
                    tts_error = audio_status
                    break
                part_bytes.append(segment_result.audio_bytes)
                if chime_bytes and idx < len(segments) - 2 and kind.startswith("story_"):
                    part_bytes.append(chime_bytes)
                    chime_count += 1
                    chime_asset = "generated_gentle_chime_wav"
            if tts_error is None:
                final_bytes = assemble_wav(part_bytes)
                audio_status = "audio_file_ready"
                audio_file_value = audio_filename
                audio_url_value = f"/gaza/audio/{audio_filename}"
                if not dry_run:
                    audio_root.mkdir(parents=True, exist_ok=True)
                    audio_file_path.write_bytes(final_bytes)
            chosen_voice = voice_list[0]
            segue_chime_count = chime_count if tts_error is None else 0
            segue_chime_asset = chime_asset if tts_error is None else None
        else:
            tts_result = synthesize_speech(
                text=script_text,
                provider=chosen_provider,
                model=tts_model,
                voice=tts_voice,
                audio_format=audio_format,
            )
            chosen_provider = tts_result.provider
            chosen_model = tts_result.model
            chosen_voice = tts_result.voice
            tts_voices_used = [tts_result.voice] if tts_result.voice else []
            segment_voice_map = [{"segment_index": 0, "segment_type": "full", "voice": tts_result.voice}]
            segue_chime_count = 0
            segue_chime_asset = None
            if tts_result.ok and tts_result.audio_bytes:
                audio_status = "audio_file_ready"
                audio_file_value = audio_filename
                audio_url_value = f"/gaza/audio/{audio_filename}"
                if not dry_run:
                    audio_root.mkdir(parents=True, exist_ok=True)
                    audio_file_path.write_bytes(tts_result.audio_bytes)
            else:
                audio_status = tts_result.error_reason or "tts_generation_failed"
                tts_error = tts_result.error_reason or "tts_generation_failed"
    else:
        segue_chime_count = 0
        segue_chime_asset = None
        # Keep feed/transcript wiring stable when an existing dated audio file is already present.
        if audio_file_path.exists():
            audio_status = "audio_file_ready_existing"
            audio_file_value = audio_filename
            audio_url_value = f"/gaza/audio/{audio_filename}"

    tts_input_character_count = len(script_text) if chosen_provider != "none" else 0
    tts_input_word_count = _count_words(script_text) if chosen_provider != "none" else 0
    tts_story_count = len(selected_stories)
    tts_segment_count = len(segments) if alternate_voices else (1 if chosen_provider != "none" else 0)
    audio_size: int | None = None
    audio_duration_seconds: float | None = None
    if not dry_run and audio_file_value:
        if audio_file_path.exists():
            audio_size = audio_file_path.stat().st_size
            if audio_format == "wav":
                from bluefern_dispatches.audio_assembly import wav_duration_seconds

                audio_duration_seconds = wav_duration_seconds(audio_file_path.read_bytes())
    if configured_price is not None and chosen_provider != "none":
        estimated_cost_usd = round((tts_input_character_count / 1_000_000.0) * configured_price, 6)
        estimated_cost_note = "estimated from configured input-char pricing"

    metadata = build_audio_metadata(
        edition_date=date_text,
        script_text=script_text,
        used_sources=used_sources,
        audio_status=audio_status,
        audio_file=audio_file_value,
        audio_url=audio_url_value,
        tts_provider=chosen_provider,
        tts_model=chosen_model,
        tts_voice=chosen_voice,
        tts_error=tts_error,
        audio_format=audio_format,
        voice_mode=voice_mode,
        tts_voices_used=tts_voices_used,
        segment_voice_map=segment_voice_map,
        tts_input_character_count=tts_input_character_count,
        tts_input_word_count=tts_input_word_count,
        tts_story_count=tts_story_count,
        tts_segment_count=tts_segment_count,
        audio_duration_seconds=audio_duration_seconds,
        audio_file_size_bytes=audio_size,
        estimated_cost_usd=estimated_cost_usd,
        estimated_cost_note=estimated_cost_note,
        tts_pricing_basis=tts_pricing_basis,
        tts_pricing_model=tts_pricing_model,
        cost_logged_at=_iso_now(),
        segue_chime=segue_chime_mode,
        segue_chime_count=segue_chime_count,
        segue_chime_asset=segue_chime_asset,
    )
    transcript_html = render_gaza_transcript_html(
        edition_date=date_text,
        script_text=script_text,
        sources=used_sources,
        audio_url=audio_url_value,
    )
    _validate_gaza_audio_transcript(stories=selected_stories, transcript_html=transcript_html)
    flash_item = build_flash_briefing_item(metadata)

    transcript_path = audio_root / f"{date_text}-transcript.html"
    metadata_path = audio_root / f"{date_text}.json"
    flash_path = gaza_root / "flash-briefing.json"

    if not dry_run:
        audio_root.mkdir(parents=True, exist_ok=True)
        transcript_path.write_text(transcript_html, encoding="utf-8")
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        flash_path.write_text(json.dumps([flash_item], indent=2), encoding="utf-8")

    # The public audio listing and podcast feed are preview-safe surfaces:
    # they preserve existing Pages history and let dry-runs validate shrink guards.
    write_audio_index(project_root, dry_run=False)

    from bluefern_dispatches.podcast_feed import write_gaza_podcast_feed

    podcast_path = write_gaza_podcast_feed(project_root=project_root, dry_run=False)
    return GazaAudioResult(
        edition_date=date_text,
        transcript_path=transcript_path,
        metadata_path=metadata_path,
        flash_briefing_path=flash_path,
        podcast_path=podcast_path,
        audio_status=audio_status,
        audio_file=audio_file_value,
        audio_url=audio_url_value,
        tts_provider=chosen_provider,
        tts_model=chosen_model,
        tts_voice=chosen_voice,
        tts_error=tts_error,
        story_count=len(selected_stories),
    )
