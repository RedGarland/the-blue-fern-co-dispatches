from __future__ import annotations

import json
import shutil
import subprocess
import types
import uuid
from pathlib import Path

import pytest

import scripts.gaza_audio_republish as republish


def _run_git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, encoding="utf-8")


def _init_pages_repo(root: Path) -> Path:
    pages = root / "bluefern-dispatches-pages"
    pages.mkdir(parents=True, exist_ok=True)
    _run_git(pages, "init", "-b", "gh-pages")
    _run_git(pages, "config", "user.email", "codex@example.com")
    _run_git(pages, "config", "user.name", "Codex")
    (pages / "gaza").mkdir(parents=True, exist_ok=True)
    (pages / "gaza" / "keep.txt").write_text("keep\n", encoding="utf-8")
    _run_git(pages, "add", "gaza/keep.txt")
    _run_git(pages, "commit", "-m", "initial")
    return pages


def _make_root(repo: Path) -> Path:
    root = repo / "output" / "test-runs" / uuid.uuid4().hex / "gaza-audio-republish"
    root.mkdir(parents=True)
    return root


def _write_audio_fixture(root: Path, edition_date: str, *, audio_file: str = "2026-07-05.mp3") -> None:
    audio_root = root / "output" / "site" / "gaza" / "audio"
    audio_root.mkdir(parents=True, exist_ok=True)
    (root / "output" / "site" / "gaza").mkdir(parents=True, exist_ok=True)
    metadata = {
        "edition_date": edition_date,
        "audio_file": audio_file,
        "audio_url": f"/gaza/audio/{audio_file}",
        "audio_status": "audio_file_ready",
        "transcript_url": f"https://dispatches.thebluefernco.com/gaza/audio/{edition_date}-transcript.html",
    }
    (audio_root / f"{edition_date}-transcript.html").write_text("<p>Transcript</p>", encoding="utf-8")
    (audio_root / f"{edition_date}.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    (audio_root / audio_file).write_bytes(b"audio-bytes")
    (audio_root / "index.html").write_text("<a href=\"2026-07-05.mp3\">MP3</a>", encoding="utf-8")
    (audio_root / "podcast.xml").write_text("<enclosure url=\"/gaza/audio/2026-07-05.mp3\" />", encoding="utf-8")
    (audio_root / "podcast-artwork.png").write_bytes(b"artwork")
    (root / "output" / "site" / "gaza" / "podcast.xml").write_text("<enclosure url=\"/gaza/audio/2026-07-05.mp3\" />", encoding="utf-8")


def _mirror_audio_fixture_to_pages(root: Path, pages: Path, edition_date: str, *, audio_file: str = "2026-07-05.mp3") -> None:
    source_audio = root / "output" / "site" / "gaza" / "audio"
    pages_audio = pages / "gaza" / "audio"
    pages_audio.mkdir(parents=True, exist_ok=True)
    for name in (
        f"{edition_date}-transcript.html",
        f"{edition_date}.json",
        audio_file,
        "index.html",
        "podcast.xml",
        "podcast-artwork.png",
    ):
        source = source_audio / name
        target = pages_audio / name
        if source.suffix == ".json":
            target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        elif source.suffix in {".html", ".xml"}:
            target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            target.write_bytes(source.read_bytes())
    (pages / "gaza" / "podcast.xml").write_text((source_audio / "podcast.xml").read_text(encoding="utf-8"), encoding="utf-8")


@pytest.fixture()
def isolated(monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = Path(__file__).resolve().parents[1]
    root = _make_root(repo)
    monkeypatch.setattr(republish, "ROOT", root)
    try:
        yield root
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)


def test_check_is_healthy_when_pages_are_complete_even_if_source_audio_was_cleaned(isolated: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_audio_fixture(isolated, "2026-07-05")
    pages = _init_pages_repo(isolated)
    _mirror_audio_fixture_to_pages(isolated, pages, "2026-07-05")
    shutil.rmtree(isolated / "output" / "site" / "gaza" / "audio")

    code = republish.main(["--date", "2026-07-05", "--check", "--no-live"])

    output = capsys.readouterr().out
    assert code == 0
    assert "Status: ready" in output
    assert "Source audio" in output
    assert "Source gaps" in output
    assert "Pages audio" in output
    assert "Next action:\nNo action needed." in output


def test_check_suggests_generate_when_source_and_pages_mp3_are_missing(isolated: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_audio_fixture(isolated, "2026-07-05")
    pages = _init_pages_repo(isolated)
    _mirror_audio_fixture_to_pages(isolated, pages, "2026-07-05")
    (isolated / "output" / "site" / "gaza" / "audio" / "2026-07-05.mp3").unlink()
    (pages / "gaza" / "audio" / "2026-07-05.mp3").unlink()

    code = republish.main(["--date", "2026-07-05", "--check", "--no-live"])

    output = capsys.readouterr().out
    assert code == 1
    assert "repair needed" in output.lower()
    assert "--generate" in output
    assert "MP3 missing from both source and Pages" in output


def test_check_suggests_publish_when_source_has_mp3_but_pages_is_missing_it(isolated: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_audio_fixture(isolated, "2026-07-05")
    pages = _init_pages_repo(isolated)
    _mirror_audio_fixture_to_pages(isolated, pages, "2026-07-05")
    (pages / "gaza" / "audio" / "2026-07-05.mp3").unlink()
    (pages / "gaza" / "audio" / "index.html").write_text("<a href=\"2026-07-05-transcript.html\">Transcript</a>", encoding="utf-8")

    code = republish.main(["--date", "2026-07-05", "--check", "--no-live"])

    output = capsys.readouterr().out
    assert code == 1
    assert "--publish" in output
    assert "MP3 is present in source but missing from Pages" in output


def test_generate_forwards_audio_settings_and_writes_source_outputs(isolated: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _write_audio_fixture(isolated, "2026-07-05")
    _init_pages_repo(isolated)
    calls: list[dict[str, str]] = []

    def fake_write(root: Path, edition_date: str, **kwargs: object) -> types.SimpleNamespace:
        calls.append({"root": str(root), "edition_date": edition_date, **{k: str(v) for k, v in kwargs.items()}})
        return types.SimpleNamespace(
            transcript_path=root / "output" / "site" / "gaza" / "audio" / f"{edition_date}-transcript.html",
            metadata_path=root / "output" / "site" / "gaza" / "audio" / f"{edition_date}.json",
            podcast_path=root / "output" / "site" / "gaza" / "podcast.xml",
            flash_briefing_path=root / "output" / "site" / "gaza" / "flash-briefing.json",
            audio_status="audio_file_ready",
            audio_file="2026-07-05.mp3",
            audio_url="/gaza/audio/2026-07-05.mp3",
            story_count=4,
            tts_provider=kwargs["tts_provider"],
            tts_model=kwargs["tts_model"],
            tts_voice=kwargs["tts_voice"],
        )

    monkeypatch.setattr(republish, "write_gaza_audio_outputs", fake_write)

    code = republish.main(
        [
            "--date",
            "2026-07-05",
            "--generate",
            "--tts-provider",
            "none",
            "--audio-model",
            "test-model",
            "--audio-voice",
            "test-voice",
            "--audio-format",
            "mp3",
        ]
    )

    output = capsys.readouterr().out
    assert code == 0
    assert calls and calls[0]["edition_date"] == "2026-07-05"
    assert calls[0]["tts_provider"] == "none"
    assert calls[0]["tts_model"] == "test-model"
    assert calls[0]["tts_voice"] == "test-voice"
    assert "Generation" in output
    assert "Audio status: audio_file_ready" in output


def test_generate_restores_source_audio_from_pages_but_fails_without_mp3(isolated: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _init_pages_repo(isolated)
    _write_audio_fixture(isolated, "2026-07-05")
    pages = isolated / "bluefern-dispatches-pages"
    _mirror_audio_fixture_to_pages(isolated, pages, "2026-07-05")
    shutil.rmtree(isolated / "output" / "site" / "gaza" / "audio")
    (pages / "gaza" / "audio" / "2026-07-05.mp3").unlink()

    def fake_write(root: Path, edition_date: str, **kwargs: object) -> types.SimpleNamespace:
        _ = kwargs
        audio_root = root / "output" / "site" / "gaza" / "audio"
        assert (audio_root / f"{edition_date}-transcript.html").exists()
        assert (audio_root / f"{edition_date}.json").exists()
        assert (audio_root / "index.html").exists()
        assert (audio_root / "podcast.xml").exists()
        assert (root / "output" / "site" / "gaza" / "podcast.xml").exists()
        return types.SimpleNamespace(
            transcript_path=audio_root / f"{edition_date}-transcript.html",
            metadata_path=audio_root / f"{edition_date}.json",
            podcast_path=root / "output" / "site" / "gaza" / "podcast.xml",
            flash_briefing_path=root / "output" / "site" / "gaza" / "flash-briefing.json",
            audio_status="script_ready_no_audio_file",
            audio_file=None,
            audio_url=None,
            story_count=4,
            tts_provider="none",
            tts_model=None,
            tts_voice=None,
        )

    monkeypatch.setattr(republish, "write_gaza_audio_outputs", fake_write)

    code = republish.main(["--date", "2026-07-05", "--generate", "--no-live"])

    output = capsys.readouterr().out
    assert code == 1
    assert "repair needed" in output.lower()
    assert "No action needed." not in output
    assert (isolated / "output" / "site" / "gaza" / "audio" / "2026-07-05-transcript.html").exists()
    assert (isolated / "output" / "site" / "gaza" / "audio" / "2026-07-05.json").exists()
    assert not (isolated / "output" / "site" / "gaza" / "audio" / "2026-07-05.mp3").exists()
    assert "run --generate with a tts provider" in output.lower()


def test_publish_copies_audio_and_feed_files_without_touching_unrelated_pages_files(isolated: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _write_audio_fixture(isolated, "2026-07-05")
    pages = _init_pages_repo(isolated)

    monkeypatch.setattr(
        republish,
        "write_gaza_audio_outputs",
        lambda *args, **kwargs: types.SimpleNamespace(
            transcript_path=isolated / "output" / "site" / "gaza" / "audio" / "2026-07-05-transcript.html",
            metadata_path=isolated / "output" / "site" / "gaza" / "audio" / "2026-07-05.json",
            podcast_path=isolated / "output" / "site" / "gaza" / "podcast.xml",
            flash_briefing_path=isolated / "output" / "site" / "gaza" / "flash-briefing.json",
            audio_status="audio_file_ready",
            audio_file="2026-07-05.mp3",
            audio_url="/gaza/audio/2026-07-05.mp3",
            story_count=4,
            tts_provider="none",
            tts_model=None,
            tts_voice=None,
        ),
    )

    code = republish.main(["--date", "2026-07-05", "--publish", "--no-live"])

    output = capsys.readouterr().out
    assert code == 0
    assert "Publish" in output
    assert (pages / "gaza" / "audio" / "2026-07-05-transcript.html").read_text(encoding="utf-8") == "<p>Transcript</p>"
    assert (pages / "gaza" / "audio" / "2026-07-05.json").exists()
    assert (pages / "gaza" / "audio" / "2026-07-05.mp3").read_bytes() == b"audio-bytes"
    assert (pages / "gaza" / "audio" / "index.html").exists()
    assert (pages / "gaza" / "audio" / "podcast.xml").exists()
    assert (pages / "gaza" / "podcast.xml").exists()
    assert (pages / "gaza" / "keep.txt").read_text(encoding="utf-8") == "keep\n"


def test_publish_refuses_when_mp3_is_missing_even_if_other_source_audio_exists(isolated: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_audio_fixture(isolated, "2026-07-05")
    pages = _init_pages_repo(isolated)
    _mirror_audio_fixture_to_pages(isolated, pages, "2026-07-05")
    (isolated / "output" / "site" / "gaza" / "audio" / "2026-07-05.mp3").unlink()
    (pages / "gaza" / "audio" / "2026-07-05.mp3").unlink()

    code = republish.main(["--date", "2026-07-05", "--publish", "--no-live"])

    output = capsys.readouterr().out
    assert code == 1
    assert "missing audio file" in output.lower()
    assert "Repair the listed audio files before publishing." in output


def test_publish_requires_source_side_artifacts(isolated: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_audio_fixture(isolated, "2026-07-05")
    pages = _init_pages_repo(isolated)
    _mirror_audio_fixture_to_pages(isolated, pages, "2026-07-05")
    shutil.rmtree(isolated / "output" / "site" / "gaza" / "audio")

    code = republish.main(["--date", "2026-07-05", "--publish", "--no-live"])

    output = capsys.readouterr().out
    assert code == 1
    assert "Repair the listed audio files before publishing." in output
