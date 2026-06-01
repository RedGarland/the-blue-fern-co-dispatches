import json
from pathlib import Path

from bluefern_dispatches.podcast_feed import build_gaza_podcast_xml, write_gaza_podcast_feed


def _write_audio_metadata(tmp_path: Path, edition_date: str, payload: dict) -> Path:
    root = tmp_path / "output" / "site" / "gaza" / "audio"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{edition_date}.json"
    data = {
        "edition_date": edition_date,
        "transcript_url": f"https://dispatches.thebluefernco.com/gaza/audio/{edition_date}-transcript.html",
        "script_text": "Short transcript summary.",
        "audio_file": None,
        "audio_url": None,
        "audio_mime_type": None,
    }
    data.update(payload)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def test_podcast_feed_omits_nonexistent_mp3_enclosures(tmp_path: Path):
    _write_audio_metadata(tmp_path, "2026-05-31", {"audio_file": "2026-05-31.mp3", "audio_url": "/gaza/audio/2026-05-31.mp3", "audio_mime_type": "audio/mpeg"})
    assets = tmp_path / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "gaza-logo.png").write_bytes(b"png")
    xml = build_gaza_podcast_xml(tmp_path)
    assert "<item>" in xml
    assert "<enclosure " not in xml


def test_podcast_feed_can_include_existing_audio_enclosure(tmp_path: Path):
    audio_file = tmp_path / "output" / "site" / "gaza" / "audio" / "2026-05-31.mp3"
    audio_file.parent.mkdir(parents=True, exist_ok=True)
    audio_file.write_bytes(b"fake-audio")
    _write_audio_metadata(tmp_path, "2026-05-31", {"audio_file": "2026-05-31.mp3", "audio_url": "/gaza/audio/2026-05-31.mp3", "audio_mime_type": "audio/mpeg"})
    assets = tmp_path / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "gaza-logo.png").write_bytes(b"png")
    xml = build_gaza_podcast_xml(tmp_path)
    assert "<enclosure " in xml
    assert "2026-05-31.mp3" in xml
    assert 'length="10"' in xml


def test_podcast_episode_title_uses_human_date_format(tmp_path: Path):
    _write_audio_metadata(
        tmp_path,
        "2026-06-01",
        {"script_text": "Today source-backed updates are summarized with transcript and sources."},
    )
    assets = tmp_path / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "gaza-logo.png").write_bytes(b"png")
    xml = build_gaza_podcast_xml(tmp_path)
    assert "<title>Gaza Briefing for June 1, 2026</title>" in xml


def test_podcast_episode_description_is_episode_specific(tmp_path: Path):
    _write_audio_metadata(
        tmp_path,
        "2026-05-31",
        {
            "script_text": "Today's Gaza Dispatch follows aid access pressures and displacement reporting. Source-backed updates remain the focus.",
        },
    )
    assets = tmp_path / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "gaza-logo.png").write_bytes(b"png")
    xml = build_gaza_podcast_xml(tmp_path)
    assert "aid access pressures" in xml
    assert "Transcript and source links are available from The Blue Fern Co." in xml
    assert "Text-first Gaza audio briefing transcripts derived from source-backed daily editions." not in xml.split("<item>", 1)[1]


def test_podcast_description_prefers_story_sentences_over_script_boilerplate(tmp_path: Path):
    _write_audio_metadata(
        tmp_path,
        "2026-05-31",
        {
            "script_text": (
                "This is the Gaza Dispatch audio briefing for May 31, 2026. "
                "Here are the key source-backed developments from today's edition. "
                "Reports described new aid access pressures and worsening conditions for displaced families. "
                "For the full source-backed dispatch, read the May 31, 2026 Gaza edition at dispatches.thebluefernco.com."
            ),
        },
    )
    assets = tmp_path / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "gaza-logo.png").write_bytes(b"png")
    xml = build_gaza_podcast_xml(tmp_path)
    item_block = xml.split("<item>", 1)[1]
    assert "aid access pressures" in item_block
    assert "This is the Gaza Dispatch audio briefing for May 31, 2026." not in item_block
    assert "Here are the key source-backed developments from today's edition." not in item_block


def test_podcast_description_strips_leading_list_prefixes(tmp_path: Path):
    _write_audio_metadata(
        tmp_path,
        "2026-05-31",
        {
            "script_text": (
                "1. Israeli forces kill Palestinian who allegedly carried out car-ramming attack in occupied West Bank. "
                "- Aid convoys faced delays at crossings. "
                "• Hospitals reported urgent supply shortages."
            ),
        },
    )
    assets = tmp_path / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "gaza-logo.png").write_bytes(b"png")
    xml = build_gaza_podcast_xml(tmp_path)
    item_block = xml.split("<item>", 1)[1]
    assert "Israeli forces kill Palestinian who allegedly carried out car-ramming attack in occupied West Bank." in item_block
    assert "1. Israeli forces kill Palestinian" not in item_block
    assert "<description>- " not in item_block
    assert "<description>• " not in item_block


def test_podcast_episode_description_fallback_is_safe_when_story_content_unavailable(tmp_path: Path):
    _write_audio_metadata(
        tmp_path,
        "2026-05-31",
        {
            "script_text": "",
            "sources": [],
        },
    )
    assets = tmp_path / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "gaza-logo.png").write_bytes(b"png")
    xml = build_gaza_podcast_xml(tmp_path)
    assert "Source-backed Gaza Dispatch audio briefing for May 31, 2026." in xml
    assert "Transcript and source links are available from The Blue Fern Co." in xml


def test_existing_mp3_still_produces_enclosure_without_new_tts(tmp_path: Path):
    audio_file = tmp_path / "output" / "site" / "gaza" / "audio" / "2026-05-31.mp3"
    audio_file.parent.mkdir(parents=True, exist_ok=True)
    audio_file.write_bytes(b"existing-mp3-data")
    _write_audio_metadata(
        tmp_path,
        "2026-05-31",
        {
            "audio_status": "script_ready_no_audio_file",
            "tts_provider": "none",
            "audio_file": "2026-05-31.mp3",
            "audio_url": "/gaza/audio/2026-05-31.mp3",
            "audio_mime_type": "audio/mpeg",
        },
    )
    assets = tmp_path / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "gaza-logo.png").write_bytes(b"png")
    xml = build_gaza_podcast_xml(tmp_path)
    assert "<enclosure " in xml
    assert f'length="{len(b"existing-mp3-data")}"' in xml


def test_write_gaza_podcast_feed_outputs_file(tmp_path: Path):
    assets = tmp_path / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "gaza-logo.png").write_bytes(b"png")
    _write_audio_metadata(tmp_path, "2026-05-31", {})
    path = write_gaza_podcast_feed(project_root=tmp_path, dry_run=False)
    assert path.exists()
    body = path.read_text(encoding="utf-8")
    assert "xmlns:itunes=" in body
    assert "<itunes:owner>" in body
    assert "<itunes:email>bluefernco@thebluefernco.com</itunes:email>" in body
    assert "itunes:image href=\"https://dispatches.thebluefernco.com/gaza/audio/podcast-artwork.png\"" in body
    assert "itunes:category text=\"News\"" in body
    assert "<language>en-us</language>" in body
    assert "<itunes:explicit>false</itunes:explicit>" in body
    mirrored = tmp_path / "output" / "site" / "gaza" / "audio" / "podcast.xml"
    assert mirrored.exists()
    assert mirrored.read_text(encoding="utf-8") == body
    artwork = tmp_path / "output" / "site" / "gaza" / "audio" / "podcast-artwork.png"
    assert artwork.exists()
    assert "output\\detail" not in str(artwork).lower()
    assert "output\\paid" not in str(artwork).lower()

