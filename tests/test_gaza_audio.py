import json
import wave
from io import BytesIO
from pathlib import Path

import pytest

from bluefern_dispatches.gaza_audio import build_gaza_audio_script, write_gaza_audio_outputs


def _write_edition(tmp_path: Path, edition_date: str, *, curation: list[dict], sources: list[dict]) -> None:
    root = tmp_path / "output" / "site" / "gaza" / "editions" / edition_date
    root.mkdir(parents=True, exist_ok=True)
    (root / "curation_manifest.json").write_text(json.dumps(curation, indent=2), encoding="utf-8")
    (root / "sources_manifest.json").write_text(json.dumps(sources, indent=2), encoding="utf-8")
    (root / "edition_manifest.json").write_text(json.dumps({"edition_date": edition_date}, indent=2), encoding="utf-8")
    (root / "index.html").write_text("<html><body><main><h2>At A Glance</h2></main></body></html>", encoding="utf-8")
    gaza_root = tmp_path / "output" / "site" / "gaza"
    gaza_root.mkdir(parents=True, exist_ok=True)
    (gaza_root / "index.html").write_text("<html><body><main><h1>Gaza</h1></main></body></html>", encoding="utf-8")
    (gaza_root / "archive.html").write_text("<html><body><main><h1>Archive</h1></main></body></html>", encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "gaza-logo.png").write_bytes(b"png")


def test_audio_script_includes_source_attribution():
    curation = [
        {
            "title": "Reported strike near central Gaza",
            "summary": "Medical sources reported injuries after a strike.",
            "source_record_ids": ["s1"],
            "included_in_public_summary": True,
        }
    ]
    sources_by_id = {
        "s1": {
            "source_record_id": "s1",
            "publisher": "Reuters",
            "url": "https://example.com/s1",
            "title": "Story 1",
        }
    }
    script, used = build_gaza_audio_script(edition_date="2026-05-31", curation_rows=curation, sources_by_id=sources_by_id)
    assert "reported by Reuters" in script
    assert len(used) == 1


def test_script_does_not_invent_sources_when_records_missing():
    curation = [{"title": "Update", "summary": "Context.", "source_record_ids": ["missing"], "included_in_public_summary": True}]
    script, used = build_gaza_audio_script(edition_date="2026-05-31", curation_rows=curation, sources_by_id={})
    assert "reported by public sources" in script
    assert used == []


def test_transcript_html_includes_source_links_and_flash_briefing_generated(tmp_path: Path):
    date = "2026-05-31"
    _write_edition(
        tmp_path,
        date,
        curation=[
            {
                "title": "Strike and aid updates",
                "summary": "Reports described strikes and aid pressure.",
                "source_record_ids": ["s1", "s2"],
                "included_in_public_summary": True,
            },
            {
                "title": "Detention update",
                "summary": "A report described detention without charge.",
                "source_record_ids": ["s3"],
                "included_in_public_summary": True,
            },
            {
                "title": "Satellite analysis",
                "summary": "Imagery showed changes on the ground.",
                "source_record_ids": ["s4"],
                "included_in_public_summary": True,
            },
        ],
        sources=[
            {"source_record_id": "s1", "publisher": "Reuters", "url": "https://example.com/s1", "title": "S1"},
            {"source_record_id": "s2", "publisher": "AP", "url": "https://example.com/s2", "title": "S2"},
            {"source_record_id": "s3", "publisher": "NPR", "url": "https://example.com/s3", "title": "S3"},
            {"source_record_id": "s4", "publisher": "Al Jazeera", "url": "https://example.com/s4", "title": "S4"},
        ],
    )
    result = write_gaza_audio_outputs(tmp_path, date, dry_run=False)
    transcript = result.transcript_path.read_text(encoding="utf-8")
    assert 'href="https://example.com/s1"' in transcript
    assert "Reuters" in transcript
    flash = json.loads(result.flash_briefing_path.read_text(encoding="utf-8"))
    assert isinstance(flash, list) and flash
    assert flash[0]["redirectionUrl"].endswith(f"/gaza/audio/{date}-transcript.html")


def test_missing_date_source_records_fails_safely(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        write_gaza_audio_outputs(tmp_path, "2026-05-31", dry_run=False)


def test_output_paths_stay_under_public_audio_root(tmp_path: Path):
    date = "2026-05-31"
    _write_edition(
        tmp_path,
        date,
        curation=[{"title": "Update", "summary": "Summary.", "source_record_ids": ["s1"], "included_in_public_summary": True}],
        sources=[{"source_record_id": "s1", "publisher": "Reuters", "url": "https://example.com/s1", "title": "S1"}],
    )
    result = write_gaza_audio_outputs(tmp_path, date, dry_run=False)
    audio_root = (tmp_path / "output" / "site" / "gaza" / "audio").resolve()
    assert str(result.transcript_path.resolve()).startswith(str(audio_root))
    assert str(result.metadata_path.resolve()).startswith(str(audio_root))
    assert "output\\detail" not in str(result.transcript_path).lower()
    assert "output\\paid" not in str(result.transcript_path).lower()
    index_body = (tmp_path / "output" / "site" / "gaza" / "audio" / "index.html").read_text(encoding="utf-8")
    assert f"{date}-transcript.html" in index_body
    assert 'href="/gaza/audio/podcast.xml"' in index_body
    transcript_body = result.transcript_path.read_text(encoding="utf-8")
    assert 'href="/gaza/audio/podcast.xml"' in transcript_body
    assert (tmp_path / "output" / "site" / "gaza" / "podcast.xml").exists()
    assert (tmp_path / "output" / "site" / "gaza" / "audio" / "podcast.xml").exists()
    assert (tmp_path / "output" / "site" / "gaza" / "audio" / "podcast-artwork.png").exists()


def test_provider_none_keeps_script_only_behavior(tmp_path: Path):
    date = "2026-05-31"
    _write_edition(
        tmp_path,
        date,
        curation=[{"title": "Update", "summary": "Summary.", "source_record_ids": ["s1"], "included_in_public_summary": True}],
        sources=[{"source_record_id": "s1", "publisher": "Reuters", "url": "https://example.com/s1", "title": "S1"}],
    )
    result = write_gaza_audio_outputs(tmp_path, date, dry_run=False, tts_provider="none")
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert metadata["audio_status"] == "script_ready_no_audio_file"
    assert metadata["audio_file"] is None
    assert metadata["tts_provider"] == "none"
    assert not (tmp_path / "output" / "site" / "gaza" / "audio" / f"{date}.mp3").exists()


def test_provider_none_refresh_preserves_existing_mp3_enclosure(tmp_path: Path):
    date = "2026-05-31"
    _write_edition(
        tmp_path,
        date,
        curation=[{"title": "Update", "summary": "Summary.", "source_record_ids": ["s1"], "included_in_public_summary": True}],
        sources=[{"source_record_id": "s1", "publisher": "Reuters", "url": "https://example.com/s1", "title": "S1"}],
    )
    existing_mp3 = tmp_path / "output" / "site" / "gaza" / "audio" / f"{date}.mp3"
    existing_mp3.parent.mkdir(parents=True, exist_ok=True)
    existing_mp3.write_bytes(b"existing-audio")
    result = write_gaza_audio_outputs(tmp_path, date, dry_run=False, tts_provider="none")
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert metadata["audio_file"] == f"{date}.mp3"
    assert metadata["audio_url"] == f"/gaza/audio/{date}.mp3"
    feed = (tmp_path / "output" / "site" / "gaza" / "audio" / "podcast.xml").read_text(encoding="utf-8")
    assert "<enclosure " in feed
    assert f'length="{len(b"existing-audio")}"' in feed


def test_openai_provider_without_api_key_fails_safely(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    date = "2026-05-31"
    _write_edition(
        tmp_path,
        date,
        curation=[{"title": "Update", "summary": "Summary.", "source_record_ids": ["s1"], "included_in_public_summary": True}],
        sources=[{"source_record_id": "s1", "publisher": "Reuters", "url": "https://example.com/s1", "title": "S1"}],
    )
    result = write_gaza_audio_outputs(tmp_path, date, dry_run=False, tts_provider="openai")
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert metadata["audio_file"] is None
    assert metadata["audio_status"] == "missing_openai_api_key"
    assert metadata["tts_error"] == "missing_openai_api_key"
    assert "OPENAI_API_KEY" not in result.metadata_path.read_text(encoding="utf-8")


def test_mocked_tts_success_writes_mp3_and_updates_metadata(tmp_path: Path, monkeypatch):
    from bluefern_dispatches.tts_provider import TTSResult

    monkeypatch.setattr(
        "bluefern_dispatches.tts_provider.synthesize_speech",
        lambda **_kwargs: TTSResult(
            ok=True,
            audio_bytes=b"mp3-bytes",
            provider="openai",
            model="gpt-4o-mini-tts",
            voice="alloy",
            fmt="mp3",
            error_reason=None,
        ),
    )
    date = "2026-05-31"
    _write_edition(
        tmp_path,
        date,
        curation=[{"title": "Update", "summary": "Summary.", "source_record_ids": ["s1"], "included_in_public_summary": True}],
        sources=[{"source_record_id": "s1", "publisher": "Reuters", "url": "https://example.com/s1", "title": "S1"}],
    )
    result = write_gaza_audio_outputs(tmp_path, date, dry_run=False, tts_provider="openai")
    mp3 = tmp_path / "output" / "site" / "gaza" / "audio" / f"{date}.mp3"
    assert mp3.exists()
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert metadata["audio_status"] == "audio_file_ready"
    assert metadata["audio_file"] == f"{date}.mp3"
    assert metadata["audio_url"] == f"/gaza/audio/{date}.mp3"
    transcript = result.transcript_path.read_text(encoding="utf-8")
    assert "<audio controls" in transcript
    flash = json.loads(result.flash_briefing_path.read_text(encoding="utf-8"))
    assert flash[0]["redirectionUrl"].endswith(f"/gaza/audio/{date}.mp3")


def _wav_bytes(duration_seconds: float = 0.1) -> bytes:
    buf = BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(22050)
        wav.writeframes(b"\x00\x00" * int(22050 * duration_seconds))
    return buf.getvalue()


def test_cost_metadata_defaults_to_null_estimate(tmp_path: Path):
    date = "2026-05-31"
    _write_edition(
        tmp_path,
        date,
        curation=[{"title": "Update", "summary": "Summary.", "source_record_ids": ["s1"], "included_in_public_summary": True}],
        sources=[{"source_record_id": "s1", "publisher": "Reuters", "url": "https://example.com/s1", "title": "S1"}],
    )
    result = write_gaza_audio_outputs(tmp_path, date, dry_run=False, tts_provider="none")
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert metadata["estimated_cost_usd"] is None
    assert metadata["tts_pricing_basis"] is None


def test_cost_metadata_uses_configured_price(tmp_path: Path, monkeypatch):
    from bluefern_dispatches.tts_provider import TTSResult

    monkeypatch.setattr(
        "bluefern_dispatches.tts_provider.synthesize_speech",
        lambda **_kwargs: TTSResult(True, b"mp3-bytes", "openai", "gpt-4o-mini-tts", "alloy", "mp3", None),
    )
    date = "2026-05-31"
    _write_edition(
        tmp_path,
        date,
        curation=[{"title": "Update", "summary": "Summary.", "source_record_ids": ["s1"], "included_in_public_summary": True}],
        sources=[{"source_record_id": "s1", "publisher": "Reuters", "url": "https://example.com/s1", "title": "S1"}],
    )
    result = write_gaza_audio_outputs(tmp_path, date, dry_run=False, tts_provider="openai", tts_price_per_1m_chars=20.0)
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert metadata["estimated_cost_usd"] is not None
    assert metadata["tts_pricing_basis"] == "price_per_1m_input_chars"


def test_alternate_voices_requires_two_voices(tmp_path: Path):
    date = "2026-05-31"
    _write_edition(
        tmp_path,
        date,
        curation=[{"title": "Update", "summary": "Summary.", "source_record_ids": ["s1"], "included_in_public_summary": True}],
        sources=[{"source_record_id": "s1", "publisher": "Reuters", "url": "https://example.com/s1", "title": "S1"}],
    )
    with pytest.raises(ValueError, match="at least two voices"):
        write_gaza_audio_outputs(
            tmp_path,
            date,
            dry_run=False,
            tts_provider="openai",
            alternate_voices=True,
            voices="alloy",
            audio_format="wav",
        )


def test_alternating_voice_wav_with_gentle_chime_sets_metadata(tmp_path: Path, monkeypatch):
    from bluefern_dispatches.tts_provider import TTSResult

    monkeypatch.setattr(
        "bluefern_dispatches.tts_provider.synthesize_speech",
        lambda **_kwargs: TTSResult(True, _wav_bytes(), "openai", "gpt-4o-mini-tts", str(_kwargs.get("voice")), "wav", None),
    )
    date = "2026-05-31"
    _write_edition(
        tmp_path,
        date,
        curation=[
            {"title": "Story One", "summary": "One.", "source_record_ids": ["s1"], "included_in_public_summary": True},
            {"title": "Story Two", "summary": "Two.", "source_record_ids": ["s2"], "included_in_public_summary": True},
            {"title": "Story Three", "summary": "Three.", "source_record_ids": ["s3"], "included_in_public_summary": True},
        ],
        sources=[
            {"source_record_id": "s1", "publisher": "Reuters", "url": "https://example.com/s1", "title": "S1"},
            {"source_record_id": "s2", "publisher": "AP", "url": "https://example.com/s2", "title": "S2"},
            {"source_record_id": "s3", "publisher": "NPR", "url": "https://example.com/s3", "title": "S3"},
        ],
    )
    result = write_gaza_audio_outputs(
        tmp_path,
        date,
        dry_run=False,
        tts_provider="openai",
        audio_format="wav",
        alternate_voices=True,
        voices="alloy,verse",
        segue_chime="gentle",
    )
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert metadata["voice_mode"] == "alternating"
    assert metadata["segue_chime"] == "gentle"
    assert metadata["segue_chime_count"] >= 1
    assert metadata["audio_duration_seconds"] is not None
