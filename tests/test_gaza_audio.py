import json
import wave
from io import BytesIO
from datetime import date, timedelta
from pathlib import Path

import pytest

from bluefern_dispatches.gaza_audio import (
    build_gaza_audio_script,
    refresh_gaza_audio_public_surfaces,
    select_gaza_audio_stories,
    write_audio_index,
    write_gaza_audio_outputs,
)


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


def _write_dispatch_only_edition(tmp_path: Path, edition_date: str, *, curation: list[dict], sources: list[dict]) -> None:
    root = tmp_path / "output" / "dispatches" / "gaza" / "editions" / edition_date
    root.mkdir(parents=True, exist_ok=True)
    (root / "curation_manifest.json").write_text(json.dumps(curation, indent=2), encoding="utf-8")
    (root / "sources_manifest.json").write_text(json.dumps(sources, indent=2), encoding="utf-8")
    (root / "edition_manifest.json").write_text(json.dumps({"edition_date": edition_date}, indent=2), encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "gaza-logo.png").write_bytes(b"png")


def _write_pages_audio_metadata(tmp_path: Path, edition_date: str, payload: dict) -> Path:
    root = tmp_path / "bluefern-dispatches-pages" / "gaza" / "audio"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{edition_date}.json"
    (root / f"{edition_date}-transcript.html").write_text("<html>Archived transcript</html>", encoding="utf-8")
    data = {
        "edition_date": edition_date,
        "transcript_url": f"https://dispatches.thebluefernco.com/gaza/audio/{edition_date}-transcript.html",
        "script_text": f"Archived Gaza audio summary for {edition_date}.",
        "audio_file": f"{edition_date}.mp3",
        "audio_url": f"/gaza/audio/{edition_date}.mp3",
        "audio_mime_type": "audio/mpeg",
    }
    data.update(payload)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def _write_source_audio_metadata(tmp_path: Path, edition_date: str, payload: dict | None = None) -> Path:
    root = tmp_path / "output" / "site" / "gaza" / "audio"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{edition_date}.json"
    (root / f"{edition_date}-transcript.html").write_text("<html>Current transcript</html>", encoding="utf-8")
    data = {
        "edition_date": edition_date,
        "transcript_url": f"https://dispatches.thebluefernco.com/gaza/audio/{edition_date}-transcript.html",
        "script_text": f"Current Gaza audio summary for {edition_date}.",
        "audio_file": f"{edition_date}.mp3",
        "audio_url": f"/gaza/audio/{edition_date}.mp3",
        "audio_mime_type": "audio/mpeg",
    }
    if payload:
        data.update(payload)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


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


def test_audio_script_smooths_repeated_sentences_and_sentence_boundaries():
    curation = [
        {
            "title": "Al Jazeera cameraman Ahmed Wishah killed in Israeli strike on Gaza",
            "summary": (
                "Wishah among at least 260 journalists killed since. "
                "Israel's war on Gaza began in October 2023 Qatar-based news network Al Jazeera has said one of its journalists was killed by an Israeli strike in Gaza on Saturday, becoming one of the at least 260 Palestinian journalists to have been killed since. "
                "Israel's war on Gaza began in October 2023. "
                "Ahmed Wishah, a cameraman for the network, was killed in a strike targeting a house in the Bureij refugee camp in central Gaza, the broadcaster said on its website."
            ),
            "source_record_ids": ["guardian", "aljazeera", "bbc"],
            "source_records": [
                {
                    "source_record_id": "guardian",
                    "summary_or_snippet": "Ahmed Wishah, a cameraman for Al Jazeera, was killed in a strike targeting a house in the Bureij refugee camp in central Gaza.",
                },
                {
                    "source_record_id": "aljazeera",
                    "summary_or_snippet": "Al Jazeera said its cameraman Ahmed Wishah was killed in an Israeli attack in Gaza.",
                },
                {
                    "source_record_id": "bbc",
                    "summary_or_snippet": "The Israeli military accused Ahmed Wishah of being a \"Hamas sniper operative\", without providing evidence.",
                },
            ],
            "included_in_public_summary": True,
        }
    ]
    sources_by_id = {
        "guardian": {"source_record_id": "guardian", "publisher": "The Guardian", "url": "https://example.com/g", "title": "Guardian"},
        "aljazeera": {"source_record_id": "aljazeera", "publisher": "Al Jazeera", "url": "https://example.com/a", "title": "Al Jazeera"},
        "bbc": {"source_record_id": "bbc", "publisher": "BBC News", "url": "https://example.com/b", "title": "BBC"},
    }

    script, used = build_gaza_audio_script(edition_date="2026-06-20", curation_rows=curation, sources_by_id=sources_by_id)

    assert ".." not in script
    assert "Wishah among at least 260 journalists killed since." not in script
    assert "260. Palestinian journalists" not in script
    assert "Qatar-based news network Al Jazeera has said one of its journalists" not in script
    assert "to have been killed since." not in script
    assert script.count("Israel's war on Gaza began in October 2023") <= 1
    assert script.count("journalists killed since") <= 1
    assert "Multiple outlets reported that Ahmed Wishah, a cameraman for Al Jazeera, was killed in an Israeli strike on a house in the Bureij refugee camp in central Gaza." in script
    assert "Al Jazeera said Wishah was among at least 260 Palestinian journalists killed since Israel's war on Gaza began in October 2023." in script
    attribution = script.split("This was reported by ", 1)[1].split(".", 1)[0]
    assert "The Guardian" in attribution
    assert "Al Jazeera" in attribution
    assert "BBC News" in attribution
    assert len(used) == 3


def test_audio_script_preserves_year_phrases_and_repairs_adjacent_complete_clauses():
    sources_by_id = {
        "guardian": {
            "source_record_id": "guardian",
            "publisher": "The Guardian",
            "url": "https://example.com/guardian",
            "title": "Guardian report",
        },
        "bbc": {
            "source_record_id": "bbc",
            "publisher": "BBC News",
            "url": "https://example.com/bbc",
            "title": "BBC report",
        },
    }
    for title in (
        "Mass funeral held in Gaza for victims of 2023 Israeli strike",
        "Gaza families assess the 2024 Ceasefire agreement",
        "Gaza groups challenge the 2025 Aid restrictions",
    ):
        script, used = build_gaza_audio_script(
            edition_date="2026-08-05",
            curation_rows=[
                {
                    "title": title,
                    "summary": (
                        "Remains were recovered more than two years after the residential block was "
                        "destroyed Mourners gathered in Gaza for a mass funeral."
                    ),
                    "source_record_ids": ["guardian", "bbc"],
                    "publisher_names": ["The Guardian", "BBC News"],
                    "included_in_public_summary": True,
                }
            ],
            sources_by_id=sources_by_id,
        )

        assert f"{title}." in script
        assert "destroyed Mourners" not in script
        assert "after the residential block was destroyed." in script
        assert "reported by The Guardian and BBC News" in script
        assert len(used) == 2


def test_august_five_transcript_only_surfaces_use_complete_clean_script(tmp_path: Path):
    edition_date = "2026-08-05"
    full_title = "Mass funeral held in Gaza for victims of 2023 Israeli strike"
    malformed_title = "Mass funeral held in Gaza for victims of 2023."
    _write_edition(
        tmp_path,
        edition_date,
        curation=[
            {
                "title": full_title,
                "summary": (
                    "Remains of 112 victims, including 40 children, recovered from rubble more than two years "
                    "after residential block was destroyed Mourners gathered in central Gaza on Tuesday for a "
                    "mass funeral for 112 people, including 40 children, who were killed in 2023 in one of the "
                    "deadliest Israeli strikes of the Gaza war."
                ),
                "source_record_ids": ["guardian", "bbc"],
                "publisher_names": ["The Guardian", "BBC News"],
                "included_in_public_summary": True,
            }
        ],
        sources=[
            {
                "source_record_id": "guardian",
                "publisher": "The Guardian",
                "url": "https://example.com/guardian",
                "title": full_title,
            },
            {
                "source_record_id": "bbc",
                "publisher": "BBC News",
                "url": "https://example.com/bbc",
                "title": "Mass funeral in Gaza for 112 Palestinians killed in 2023 Israeli strike",
            },
        ],
    )

    result = write_gaza_audio_outputs(tmp_path, edition_date, dry_run=False, tts_provider="none")
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    flash = json.loads(result.flash_briefing_path.read_text(encoding="utf-8"))[0]
    surfaces = {
        "transcript": result.transcript_path.read_text(encoding="utf-8"),
        "script_text": metadata["script_text"],
        "audio_podcast": (tmp_path / "output/site/gaza/audio/podcast.xml").read_text(encoding="utf-8"),
        "general_podcast": (tmp_path / "output/site/gaza/podcast.xml").read_text(encoding="utf-8"),
        "flash": flash["mainText"],
    }

    for value in surfaces.values():
        assert full_title in value
        assert malformed_title not in value
        assert "destroyed Mourners" not in value
        assert "Hormuz" not in value
        assert "Smotrich" not in value
        assert "2026-08-06" not in value
        assert "C:\\" not in value
    assert "The Guardian and BBC News" in metadata["script_text"]
    assert "after the residential block was destroyed." in metadata["script_text"]
    assert "The Guardian" in surfaces["transcript"]
    assert "BBC News" in surfaces["transcript"]
    assert metadata["audio_file"] is None
    assert metadata["audio_url"] is None
    assert result.audio_file is None
    assert not (tmp_path / f"output/site/gaza/audio/{edition_date}.mp3").exists()
    assert "<enclosure " not in surfaces["audio_podcast"]
    assert "<enclosure " not in surfaces["general_podcast"]


def test_audio_script_uses_concise_story_level_summary_for_related_mourning_story():
    curation = [
        {
            "title": "Mother of Al Jazeera's Ahmed Wishah mourns his killing",
            "summary": "This is the moment the mother of Al Jazeera cameraman Ahmed Wishah first saw his body after. Israel killed him in Gaza.",
            "source_record_ids": ["aljazeera"],
            "source_records": [
                {
                    "source_record_id": "aljazeera",
                    "summary_or_snippet": "This is the moment the mother of Al Jazeera cameraman Ahmed Wishah first saw his body after. Israel killed him in Gaza.",
                }
            ],
            "included_in_public_summary": True,
        }
    ]
    sources_by_id = {
        "aljazeera": {"source_record_id": "aljazeera", "publisher": "Al Jazeera", "url": "https://example.com/a", "title": "Al Jazeera"},
    }

    script, used = build_gaza_audio_script(edition_date="2026-06-21", curation_rows=curation, sources_by_id=sources_by_id)

    assert "after. Israel killed him in Gaza" not in script
    assert "This is the moment the mother of Al Jazeera cameraman Ahmed Wishah first saw his body after Israel killed him in Gaza." in script
    assert "This was reported by Al Jazeera." in script
    assert len(used) == 1


def test_script_does_not_invent_sources_when_records_missing():
    curation = [{"title": "Gaza update", "summary": "Context from Gaza.", "source_record_ids": ["missing"], "included_in_public_summary": True}]
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
                    "title": "Strike and aid updates in Gaza",
                    "summary": "Reports described strikes in Gaza and aid pressure.",
                    "source_record_ids": ["s1", "s2"],
                    "included_in_public_summary": True,
                },
                {
                    "title": "Palestinian detainee update",
                    "summary": "A report described Palestinian detainees held without charge.",
                    "source_record_ids": ["s3"],
                    "included_in_public_summary": True,
                },
                {
                    "title": "Satellite analysis of Gaza damage",
                    "summary": "Imagery showed changes on the ground in Gaza.",
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


def test_gaza_audio_filters_newsletter_sidebar_and_lebanon_only_regressions(tmp_path: Path):
    date = "2026-06-05"
    guardian_summary = (
        "In today's newsletter: Gaza is mentioned early. "
        "UK politics | social care system update. "
        "Environment | broad climate item. "
        "Ukraine | negotiation item. "
        "England news | planning laws debate. "
        "UK news | Andrew Mountbatten-Windsor and royal property."
    )
    curation = [
        {
            "title": "Friday briefing: How Gaza, Lebanon and Iran have found themselves caught in an escalation without end",
            "summary": guardian_summary,
            "source_record_ids": ["s1"],
            "included_in_public_summary": True,
        },
        {
            "title": "UN agency says displacement in Lebanon rises despite ceasefire",
            "summary": "More than 2,100 people sheltering in UNRWA facilities as hostilities continue despite truce, agency says.",
            "source_record_ids": ["s2"],
            "included_in_public_summary": True,
        },
        {
            "title": "Israeli strikes kill 11 people in Gaza City, medics say",
            "summary": "Medics reported casualties after strikes in Gaza City.",
            "source_record_ids": ["s3"],
            "included_in_public_summary": True,
        },
        {
            "title": "Israel Supreme Court strikes down ban on Red Cross prison visits",
            "summary": "The ICRC said it was ready to resume visits to Palestinian detainees.",
            "source_record_ids": ["s4"],
            "included_in_public_summary": True,
        },
        {
            "title": "Newly disclosed Israeli testimonies detail expulsions, killings during 1967 war: Report",
            "summary": "Archival material documents expulsions and killings of Palestinians in 1967.",
            "source_record_ids": ["s5"],
            "included_in_public_summary": True,
        },
    ]
    _write_edition(
        tmp_path,
        date,
        curation=curation,
        sources=[
            {"source_record_id": "s1", "publisher": "The Guardian", "url": "https://example.com/s1", "title": "S1"},
            {"source_record_id": "s2", "publisher": "Anadolu Agency", "url": "https://example.com/s2", "title": "S2"},
            {"source_record_id": "s3", "publisher": "BBC News", "url": "https://example.com/s3", "title": "S3"},
            {"source_record_id": "s4", "publisher": "The New Arab", "url": "https://example.com/s4", "title": "S4"},
            {"source_record_id": "s5", "publisher": "Anadolu Agency", "url": "https://example.com/s5", "title": "S5"},
        ],
    )

    selected = select_gaza_audio_stories(curation)
    titles = [row["title"] for row in selected]
    assert "Friday briefing: How Gaza, Lebanon and Iran have found themselves caught in an escalation without end" not in titles
    assert "UN agency says displacement in Lebanon rises despite ceasefire" not in titles
    assert "Israeli strikes kill 11 people in Gaza City, medics say" in titles
    assert "Israel Supreme Court strikes down ban on Red Cross prison visits" in titles
    assert "Newly disclosed Israeli testimonies detail expulsions, killings during 1967 war: Report" in titles

    result = write_gaza_audio_outputs(tmp_path, date, dry_run=False, tts_provider="none")
    transcript = result.transcript_path.read_text(encoding="utf-8")
    assert "UK politics" not in transcript
    assert "Environment" not in transcript
    assert "Ukraine" not in transcript
    assert "England news" not in transcript
    assert "Andrew Mountbatten-Windsor" not in transcript
    assert "Lebanon rises despite ceasefire" not in transcript
    assert "social care system" not in transcript
    assert "planning laws" not in transcript
    assert "Israeli strikes kill 11 people in Gaza City, medics say" in transcript
    assert "Israel Supreme Court strikes down ban on Red Cross prison visits" in transcript
    assert transcript.count("<li><a href=") == 3


def test_gaza_audio_validator_fails_on_excluded_marker_in_selected_story(tmp_path: Path):
    date = "2026-06-05"
    _write_edition(
        tmp_path,
        date,
        curation=[
            {
                "title": "Gaza overview",
                "summary": "UK politics | unrelated sidebar text should block the audio build.",
                "source_record_ids": ["s1"],
                "included_in_public_summary": True,
            }
        ],
        sources=[{"source_record_id": "s1", "publisher": "Example", "url": "https://example.com/s1", "title": "S1"}],
    )
    with pytest.raises(ValueError, match="no Gaza-audio-eligible stories found"):
        write_gaza_audio_outputs(tmp_path, date, dry_run=False, tts_provider="none")


def test_missing_date_source_records_fails_safely(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        write_gaza_audio_outputs(tmp_path, "2026-05-31", dry_run=False)


def test_audio_generation_can_use_dispatch_manifests_when_public_site_manifests_are_absent(tmp_path: Path):
    date = "2026-07-01"
    _write_dispatch_only_edition(
        tmp_path,
        date,
        curation=[{"title": "Gaza update", "summary": "Summary from Gaza.", "source_record_ids": ["s1"], "included_in_public_summary": True}],
        sources=[{"source_record_id": "s1", "publisher": "Reuters", "url": "https://example.com/s1", "title": "S1"}],
    )
    result = write_gaza_audio_outputs(tmp_path, date, dry_run=False, tts_provider="none")
    assert result.transcript_path.exists()
    assert result.metadata_path.exists()
    assert result.podcast_path.exists()


def test_output_paths_stay_under_public_audio_root(tmp_path: Path):
    date = "2026-05-31"
    _write_edition(
        tmp_path,
        date,
        curation=[{"title": "Gaza update", "summary": "Summary from Gaza.", "source_record_ids": ["s1"], "included_in_public_summary": True}],
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


def test_refresh_public_audio_surfaces_uses_explicit_pages_repo_history(tmp_path: Path):
    _write_source_audio_metadata(tmp_path, "2026-08-07", payload={"audio_file": None, "audio_url": None, "audio_mime_type": None})
    _write_pages_audio_metadata(
        tmp_path,
        "2026-08-05",
        {"audio_file": None, "audio_url": None, "audio_mime_type": None},
    )
    _write_pages_audio_metadata(
        tmp_path,
        "2026-08-04",
        {"audio_file": None, "audio_url": None, "audio_mime_type": None},
    )
    assets = tmp_path / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "gaza-logo.png").write_bytes(b"png")

    index_path, podcast_path = refresh_gaza_audio_public_surfaces(
        tmp_path,
        pages_repo=tmp_path / "bluefern-dispatches-pages",
    )

    index_text = index_path.read_text(encoding="utf-8")
    podcast_text = podcast_path.read_text(encoding="utf-8")
    assert "2026-08-07" in index_text
    assert "2026-08-05" in index_text
    assert "2026-08-04" in index_text
    assert "2026-08-07" in podcast_text
    assert "2026-08-05" in podcast_text
    assert "2026-08-04" in podcast_text
    assert "2026-08-03" not in index_text
    assert "2026-08-03" not in podcast_text


def test_provider_none_keeps_script_only_behavior(tmp_path: Path):
    date = "2026-05-31"
    _write_edition(
        tmp_path,
        date,
        curation=[{"title": "Gaza update", "summary": "Summary from Gaza.", "source_record_ids": ["s1"], "included_in_public_summary": True}],
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
        curation=[{"title": "Gaza update", "summary": "Summary from Gaza.", "source_record_ids": ["s1"], "included_in_public_summary": True}],
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


def test_audio_index_includes_archived_pages_episode_and_skips_incomplete_rows(tmp_path: Path):
    date = "2026-07-01"
    _write_edition(
        tmp_path,
        date,
        curation=[{"title": "Gaza update", "summary": "Summary from Gaza.", "source_record_ids": ["s1"], "included_in_public_summary": True}],
        sources=[{"source_record_id": "s1", "publisher": "Reuters", "url": "https://example.com/s1", "title": "S1"}],
    )
    existing_mp3 = tmp_path / "output" / "site" / "gaza" / "audio" / f"{date}.mp3"
    existing_mp3.parent.mkdir(parents=True, exist_ok=True)
    existing_mp3.write_bytes(b"latest-audio")
    write_gaza_audio_outputs(tmp_path, date, dry_run=False, tts_provider="none")

    pages_audio_root = tmp_path / "bluefern-dispatches-pages" / "gaza" / "audio"
    pages_audio_root.mkdir(parents=True, exist_ok=True)
    (pages_audio_root / "2026-06-30.mp3").write_bytes(b"archived-audio")
    (pages_audio_root / "2026-06-30-transcript.html").write_text("<html>archived transcript</html>", encoding="utf-8")
    (pages_audio_root / "2026-06-30.json").write_text(
        json.dumps(
            {
                "edition_date": "2026-06-30",
                "transcript_url": "https://dispatches.thebluefernco.com/gaza/audio/2026-06-30-transcript.html",
                "audio_file": "2026-06-30.mp3",
                "audio_url": "/gaza/audio/2026-06-30.mp3",
                "audio_mime_type": "audio/mpeg",
                "script_text": "Archived Gaza episode.",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (pages_audio_root / "2026-06-29.json").write_text(
        json.dumps(
            {
                "edition_date": "2026-06-29",
                "transcript_url": "https://dispatches.thebluefernco.com/gaza/audio/2026-06-29-transcript.html",
                "audio_file": "2026-06-29.mp3",
                "audio_url": "/gaza/audio/2026-06-29.mp3",
                "audio_mime_type": "audio/mpeg",
                "script_text": "Incomplete archived Gaza episode.",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    result = write_gaza_audio_outputs(tmp_path, date, dry_run=False, tts_provider="none")
    index_body = (tmp_path / "output" / "site" / "gaza" / "audio" / "index.html").read_text(encoding="utf-8")
    podcast_body = result.podcast_path.read_text(encoding="utf-8")

    assert "2026-07-01" in index_body
    assert "2026-06-30" in index_body
    assert "2026-06-29" not in index_body
    assert "2026-07-01.mp3" in podcast_body
    assert "2026-06-30.mp3" in podcast_body
    assert "2026-06-29" not in podcast_body


def test_audio_index_keeps_mp3_column_aligned_when_some_rows_have_no_file(tmp_path: Path):
    local_date = "2026-07-01"
    _write_edition(
        tmp_path,
        local_date,
        curation=[{"title": "Gaza update", "summary": "Summary from Gaza.", "source_record_ids": ["s1"], "included_in_public_summary": True}],
        sources=[{"source_record_id": "s1", "publisher": "Reuters", "url": "https://example.com/s1", "title": "S1"}],
    )
    local_mp3 = tmp_path / "output" / "site" / "gaza" / "audio" / f"{local_date}.mp3"
    local_mp3.parent.mkdir(parents=True, exist_ok=True)
    local_mp3.write_bytes(b"local-audio")

    pages_audio_root = tmp_path / "bluefern-dispatches-pages" / "gaza" / "audio"
    pages_audio_root.mkdir(parents=True, exist_ok=True)
    pages_date = "2026-06-30"
    (pages_audio_root / f"{pages_date}-transcript.html").write_text("<html>archived transcript</html>", encoding="utf-8")
    (pages_audio_root / f"{pages_date}.json").write_text(
        json.dumps(
            {
                "edition_date": pages_date,
                "transcript_url": f"https://dispatches.thebluefernco.com/gaza/audio/{pages_date}-transcript.html",
                "audio_status": "script_ready_no_audio_file",
                "script_text": "Archived Gaza episode.",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    write_gaza_audio_outputs(tmp_path, local_date, dry_run=False, tts_provider="none")

    index_body = (tmp_path / "output" / "site" / "gaza" / "audio" / "index.html").read_text(encoding="utf-8")
    css_text = (Path(__file__).resolve().parents[1] / "assets" / "site.css").read_text(encoding="utf-8")

    assert index_body.count("gaza-audio-index-row") == 2
    assert index_body.count("gaza-audio-index-media") == 2
    assert "No MP3 yet" in index_body
    assert f"/gaza/audio/{local_date}.mp3" in index_body
    assert f"/gaza/audio/{pages_date}.mp3" not in index_body
    assert "grid-template-columns: minmax(7.5rem, 8.5rem) minmax(8.5rem, 10rem) minmax(16rem, 1.2fr) minmax(8rem, 9rem);" in css_text


def test_dry_run_audio_outputs_preserve_pages_history_surface_rows(tmp_path: Path):
    current_date = "2026-07-06"
    _write_edition(
        tmp_path,
        current_date,
        curation=[{"title": "Gaza update", "summary": "Summary from Gaza.", "source_record_ids": ["s1"], "included_in_public_summary": True}],
        sources=[{"source_record_id": "s1", "publisher": "Reuters", "url": "https://example.com/s1", "title": "S1"}],
    )

    pages_audio_root = tmp_path / "bluefern-dispatches-pages" / "gaza" / "audio"
    pages_audio_root.mkdir(parents=True, exist_ok=True)
    start = date.fromisoformat(current_date)
    preserved_dates = []
    for offset in range(1, 36):
        edition_date = (start - timedelta(days=offset)).isoformat()
        preserved_dates.append(edition_date)
        _write_pages_audio_metadata(
            tmp_path,
            edition_date,
            {
                "audio_file": None,
                "audio_url": None,
                "audio_mime_type": None,
                "script_text": f"Archived Gaza audio summary for {edition_date}.",
            },
        )

    assets = tmp_path / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "gaza-logo.png").write_bytes(b"png")

    result = write_gaza_audio_outputs(tmp_path, current_date, dry_run=True, tts_provider="none")

    index_body = (tmp_path / "output" / "site" / "gaza" / "audio" / "index.html").read_text(encoding="utf-8")
    podcast_body = (tmp_path / "output" / "site" / "gaza" / "audio" / "podcast.xml").read_text(encoding="utf-8")

    assert not result.transcript_path.exists()
    assert not result.metadata_path.exists()
    assert index_body.count("gaza-audio-index-row") == 35
    assert podcast_body.count("<item>") == 35
    assert preserved_dates[0] in index_body
    assert preserved_dates[-1] in index_body
    assert preserved_dates[0] in podcast_body
    assert preserved_dates[-1] in podcast_body


def test_openai_provider_without_api_key_fails_safely(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    date = "2026-05-31"
    _write_edition(
        tmp_path,
        date,
        curation=[{"title": "Gaza update", "summary": "Summary from Gaza.", "source_record_ids": ["s1"], "included_in_public_summary": True}],
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
        curation=[{"title": "Gaza update", "summary": "Summary from Gaza.", "source_record_ids": ["s1"], "included_in_public_summary": True}],
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
        curation=[{"title": "Gaza update", "summary": "Summary from Gaza.", "source_record_ids": ["s1"], "included_in_public_summary": True}],
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
        curation=[{"title": "Gaza update", "summary": "Summary from Gaza.", "source_record_ids": ["s1"], "included_in_public_summary": True}],
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
        curation=[{"title": "Gaza update", "summary": "Summary from Gaza.", "source_record_ids": ["s1"], "included_in_public_summary": True}],
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
            {"title": "Story One in Gaza", "summary": "One in Gaza.", "source_record_ids": ["s1"], "included_in_public_summary": True},
            {"title": "Palestinian detainee story", "summary": "Two on Palestinian detainees.", "source_record_ids": ["s2"], "included_in_public_summary": True},
            {"title": "1967 documentation story", "summary": "Three on Palestinians in 1967.", "source_record_ids": ["s3"], "included_in_public_summary": True},
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


def test_audio_script_repairs_sentence_stitching_and_terminal_punctuation():
    curation = [
        {
            "title": "Gaza legal review under clause",
            "summary": "under. Israel's 'unlawful combatant' law remained in force.",
            "source_record_ids": ["s1"],
            "included_in_public_summary": True,
        },
        {
            "title": "Gaza response while clause",
            "summary": "while. Israel violently lashes out at critics of the ceasefire.",
            "source_record_ids": ["s2"],
            "included_in_public_summary": True,
        },
        {
            "title": "Gaza talks between clause",
            "summary": "between. Israel and the militant group Hamas remained divided on access terms.",
            "source_record_ids": ["s3"],
            "included_in_public_summary": True,
        },
    ]
    sources_by_id = {
        "s1": {"source_record_id": "s1", "publisher": "Reuters", "url": "https://example.com/s1", "title": "S1"},
        "s2": {"source_record_id": "s2", "publisher": "The New Arab", "url": "https://example.com/s2", "title": "S2"},
        "s3": {"source_record_id": "s3", "publisher": "Al Jazeera", "url": "https://example.com/s3", "title": "S3"},
    }

    script, used = build_gaza_audio_script(edition_date="2026-06-16", curation_rows=curation, sources_by_id=sources_by_id)

    assert "under. Israel" not in script
    assert "while. Israel" not in script
    assert "between. Israel" not in script
    assert "under Israel's 'unlawful combatant' law" in script
    assert "while Israel violently lashes out" in script
    assert "between Israel and the militant group Hamas" in script
    assert script.endswith(".")
    assert len(used) == 3


def test_audio_index_keeps_archived_pages_episode_when_local_site_is_sparse(tmp_path: Path):
    local_audio_root = tmp_path / "output" / "site" / "gaza" / "audio"
    local_audio_root.mkdir(parents=True, exist_ok=True)
    (local_audio_root / "2026-07-04-transcript.html").write_text("<html>Local transcript</html>", encoding="utf-8")
    (local_audio_root / "2026-07-04.json").write_text(
        json.dumps(
            {
                "edition_date": "2026-07-04",
                "transcript_url": "https://dispatches.thebluefernco.com/gaza/audio/2026-07-04-transcript.html",
                "audio_url": "/gaza/audio/2026-07-04.mp3",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    pages_audio_root = tmp_path / "bluefern-dispatches-pages" / "gaza" / "audio"
    pages_audio_root.mkdir(parents=True, exist_ok=True)
    (pages_audio_root / "2026-07-03-transcript.html").write_text("<html>Archived transcript</html>", encoding="utf-8")
    (pages_audio_root / "2026-07-03.json").write_text(
        json.dumps(
            {
                "edition_date": "2026-07-03",
                "transcript_url": "https://dispatches.thebluefernco.com/gaza/audio/2026-07-03-transcript.html",
                "audio_url": "/gaza/audio/2026-07-03.mp3",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    (tmp_path / "assets").mkdir(parents=True, exist_ok=True)
    (tmp_path / "assets" / "gaza-logo.png").write_bytes(b"png")

    index_path = write_audio_index(tmp_path, dry_run=False)
    body = index_path.read_text(encoding="utf-8")

    assert "2026-07-04" in body
    assert "2026-07-03" in body
    assert body.index("2026-07-04") < body.index("2026-07-03")
