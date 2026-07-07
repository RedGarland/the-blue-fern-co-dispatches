from __future__ import annotations

import argparse
from datetime import date as _date
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.gaza_audio import write_gaza_audio_outputs

DATE_RE = r"^\d{4}-\d{2}-\d{2}$"
DEFAULT_PAGES_BRANCH = "gh-pages"
DEFAULT_TTS_PROVIDER = "none"
DEFAULT_AUDIO_MODEL = "gpt-4o-mini-tts"
DEFAULT_AUDIO_VOICE = "alloy"
DEFAULT_AUDIO_FORMAT = "mp3"


@dataclass(frozen=True)
class RepoStatus:
    exists: bool
    is_git_repo: bool
    branch: str | None
    dirty_paths: tuple[str, ...]


def _validate_date(value: str) -> str:
    text = str(value or "").strip()
    if len(text) != 10 or text[4] != "-" or text[7] != "-":
        raise ValueError(f"date must use YYYY-MM-DD: {text}")
    try:
        _date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"date must use YYYY-MM-DD: {text}") from exc
    return text


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _run_git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=check,
    )


def _git_output(repo: Path, *args: str) -> str:
    result = _run_git(repo, *args, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def _git_status_paths(repo: Path) -> tuple[str, ...]:
    result = _run_git(repo, "status", "--short", "--untracked-files=all", check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git status failed")
    paths: list[str] = []
    for line in result.stdout.splitlines():
        text = line.strip()
        if not text:
            continue
        if len(text) > 3 and text[2] == " ":
            paths.append(text[3:].replace("\\", "/"))
        else:
            parts = text.split(maxsplit=1)
            paths.append((parts[1] if len(parts) > 1 else parts[0]).replace("\\", "/"))
    return tuple(paths)


def _repo_status(repo: Path) -> RepoStatus:
    exists = repo.exists()
    is_git_repo = exists and (repo / ".git").exists()
    branch = None
    dirty_paths: tuple[str, ...] = ()
    if is_git_repo:
        branch = _git_output(repo, "branch", "--show-current") or None
        dirty_paths = _git_status_paths(repo)
    return RepoStatus(exists=exists, is_git_repo=is_git_repo, branch=branch, dirty_paths=dirty_paths)


def _source_audio_root(root: Path) -> Path:
    return root / "output" / "site" / "gaza" / "audio"


def _pages_audio_root(pages_repo: Path) -> Path:
    return pages_repo / "gaza" / "audio"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _read_metadata(project_root: Path, edition_date: str) -> dict[str, Any]:
    path = _source_audio_root(project_root) / f"{edition_date}.json"
    if not path.exists():
        return {}
    payload = _load_json(path)
    return payload if isinstance(payload, dict) else {}


def _edition_audio_files(project_root: Path, edition_date: str) -> list[Path]:
    audio_root = _source_audio_root(project_root)
    metadata = _read_metadata(project_root, edition_date)
    audio_file = _clean_text(metadata.get("audio_file"))
    files = [
        audio_root / f"{edition_date}-transcript.html",
        audio_root / f"{edition_date}.json",
        audio_root / "index.html",
        audio_root / "podcast.xml",
        audio_root / "podcast-artwork.png",
        project_root / "output" / "site" / "gaza" / "podcast.xml",
    ]
    if audio_file:
        files.insert(2, audio_root / audio_file)
    else:
        for ext in ("mp3", "wav"):
            candidate = audio_root / f"{edition_date}.{ext}"
            if candidate.exists():
                files.insert(2, candidate)
                break
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in files:
        resolved = path.resolve(strict=False)
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)
    return unique


def _artifact_status(source: Path, target: Path | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {"path": str(source), "exists": source.exists()}
    if target is not None:
        data["target_path"] = str(target)
        data["target_exists"] = target.exists()
        data["matches_target"] = source.exists() and target.exists() and source.read_bytes() == target.read_bytes()
    return data


def _artifact_ready(item: dict[str, Any]) -> bool:
    return bool(item.get("exists"))


def _artifact_published(item: dict[str, Any]) -> bool:
    return bool(item.get("target_exists"))


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _build_source_report(project_root: Path, edition_date: str) -> dict[str, Any]:
    audio_root = _source_audio_root(project_root)
    metadata_path = audio_root / f"{edition_date}.json"
    metadata: dict[str, Any] = {}
    issues: list[str] = []
    if metadata_path.exists():
        try:
            raw_metadata = _load_json(metadata_path)
        except Exception as exc:  # noqa: BLE001
            issues.append(f"invalid metadata JSON: {metadata_path}: {exc}")
        else:
            if isinstance(raw_metadata, dict):
                metadata = raw_metadata
            else:
                issues.append(f"metadata JSON is not an object: {metadata_path}")
    else:
        issues.append(f"missing metadata JSON: {metadata_path}")

    transcript_path = audio_root / f"{edition_date}-transcript.html"
    index_path = audio_root / "index.html"
    podcast_audio_path = audio_root / "podcast.xml"
    podcast_root_path = project_root / "output" / "site" / "gaza" / "podcast.xml"
    artwork_path = audio_root / "podcast-artwork.png"

    audio_file = _clean_text(metadata.get("audio_file")) if metadata else ""
    audio_path = audio_root / audio_file if audio_file else None
    if not audio_file:
        for ext in ("mp3", "wav"):
            candidate = audio_root / f"{edition_date}.{ext}"
            if candidate.exists():
                audio_path = candidate
                audio_file = candidate.name
                break

    if not transcript_path.exists():
        issues.append(f"missing transcript: {transcript_path}")
    if audio_path is None or not audio_path.exists():
        issues.append(f"missing audio file: {audio_root / (audio_file or f'{edition_date}.mp3')}")
    if not index_path.exists():
        issues.append(f"missing audio index: {index_path}")
    if not podcast_audio_path.exists():
        issues.append(f"missing audio podcast feed: {podcast_audio_path}")
    if not podcast_root_path.exists():
        issues.append(f"missing root podcast feed: {podcast_root_path}")
    if not artwork_path.exists():
        issues.append(f"missing podcast artwork: {artwork_path}")

    pages_repo = project_root / "bluefern-dispatches-pages"
    pages_transcript = _pages_audio_root(pages_repo) / f"{edition_date}-transcript.html"
    pages_audio = _pages_audio_root(pages_repo) / (audio_file or f"{edition_date}.mp3")
    pages_metadata = _pages_audio_root(pages_repo) / f"{edition_date}.json"
    pages_index = _pages_audio_root(pages_repo) / "index.html"
    pages_podcast = _pages_audio_root(pages_repo) / "podcast.xml"
    pages_artwork = _pages_audio_root(pages_repo) / "podcast-artwork.png"
    pages_root_podcast = pages_repo / "gaza" / "podcast.xml"

    source_paths = {
        "transcript": _artifact_status(transcript_path, pages_transcript),
        "metadata": _artifact_status(metadata_path, pages_metadata),
        "audio_file": _artifact_status(audio_path or (audio_root / f"{edition_date}.mp3"), pages_audio),
        "audio_index": _artifact_status(index_path, pages_index),
        "podcast_feed": _artifact_status(podcast_audio_path, pages_podcast),
        "podcast_root_feed": _artifact_status(podcast_root_path, pages_root_podcast),
        "podcast_artwork": _artifact_status(artwork_path, pages_artwork),
    }
    status = "ready" if not issues else "repair needed"
    return {
        "path": str(audio_root),
        "metadata": metadata,
        "audio_file": audio_file,
        "files": source_paths,
        "issues": issues,
        "status": status,
        "ready": all(_artifact_ready(source_paths[key]) for key in ("transcript", "metadata", "audio_file", "audio_index", "podcast_feed", "podcast_root_feed")),
        "mp3_ready": _artifact_ready(source_paths["audio_file"]),
    }


def _build_pages_report(project_root: Path, edition_date: str) -> dict[str, Any]:
    pages_repo = project_root / "bluefern-dispatches-pages"
    status = _repo_status(pages_repo)
    source_root = _source_audio_root(project_root)
    metadata = _read_metadata(project_root, edition_date)
    audio_file = _clean_text(metadata.get("audio_file")) or f"{edition_date}.mp3"
    files = {
        "transcript": _artifact_status(source_root / f"{edition_date}-transcript.html", _pages_audio_root(pages_repo) / f"{edition_date}-transcript.html"),
        "metadata": _artifact_status(source_root / f"{edition_date}.json", _pages_audio_root(pages_repo) / f"{edition_date}.json"),
        "audio_file": _artifact_status(source_root / audio_file, _pages_audio_root(pages_repo) / audio_file),
        "audio_index": _artifact_status(source_root / "index.html", _pages_audio_root(pages_repo) / "index.html"),
        "podcast_feed": _artifact_status(source_root / "podcast.xml", _pages_audio_root(pages_repo) / "podcast.xml"),
        "podcast_root_feed": _artifact_status(project_root / "output" / "site" / "gaza" / "podcast.xml", pages_repo / "gaza" / "podcast.xml"),
        "podcast_artwork": _artifact_status(source_root / "podcast-artwork.png", _pages_audio_root(pages_repo) / "podcast-artwork.png"),
    }
    index_text = _read_text(_pages_audio_root(pages_repo) / "index.html")
    podcast_text = _read_text(_pages_audio_root(pages_repo) / "podcast.xml")
    root_podcast_text = _read_text(pages_repo / "gaza" / "podcast.xml")
    return {
        "path": str(pages_repo),
        "exists": status.exists,
        "is_git_repo": status.is_git_repo,
        "branch": status.branch,
        "dirty_paths": list(status.dirty_paths),
        "files": files,
        "ready": all(_artifact_published(files[key]) for key in ("transcript", "metadata", "audio_file", "audio_index", "podcast_feed", "podcast_root_feed")),
        "mp3_ready": _artifact_published(files["audio_file"]),
        "links_ready": bool(index_text) and edition_date in index_text and ".mp3" in index_text and bool(podcast_text) and ".mp3" in podcast_text and bool(root_podcast_text) and ".mp3" in root_podcast_text,
        "index_links_date": bool(index_text) and edition_date in index_text,
        "index_links_mp3": bool(index_text) and ".mp3" in index_text,
        "podcast_feed_includes_mp3": bool(podcast_text) and ".mp3" in podcast_text,
        "site_podcast_includes_mp3": bool(root_podcast_text) and ".mp3" in root_podcast_text,
    }


def _copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _copy_if_present(source: Path, target: Path) -> bool:
    if not source.exists():
        return False
    _copy_file(source, target)
    return True


def _restore_source_audio_from_pages(project_root: Path, edition_date: str, pages_repo: Path) -> list[str]:
    restored: list[str] = []
    source_audio_root = _source_audio_root(project_root)
    pages_audio_root = _pages_audio_root(pages_repo)
    pages_root_podcast = pages_repo / "gaza" / "podcast.xml"
    source_root_podcast = project_root / "output" / "site" / "gaza" / "podcast.xml"
    pages_metadata_path = pages_audio_root / f"{edition_date}.json"
    source_metadata_path = source_audio_root / f"{edition_date}.json"
    metadata: dict[str, Any] = {}
    if pages_metadata_path.exists():
        try:
            payload = _load_json(pages_metadata_path)
        except Exception:  # noqa: BLE001
            payload = {}
        if isinstance(payload, dict):
            metadata = payload

    file_pairs = [
        (pages_audio_root / f"{edition_date}-transcript.html", source_audio_root / f"{edition_date}-transcript.html", "transcript"),
        (pages_metadata_path, source_metadata_path, "metadata"),
        (pages_audio_root / "index.html", source_audio_root / "index.html", "audio index"),
        (pages_audio_root / "podcast.xml", source_audio_root / "podcast.xml", "audio podcast feed"),
        (pages_audio_root / "podcast-artwork.png", source_audio_root / "podcast-artwork.png", "podcast artwork"),
        (pages_root_podcast, source_root_podcast, "root podcast feed"),
    ]
    for source, target, label in file_pairs:
        if _copy_if_present(source, target):
            restored.append(f"restored {label} from Pages")

    audio_file = _clean_text(metadata.get("audio_file"))
    if audio_file:
        pages_audio_file = pages_audio_root / audio_file
        source_audio_file = source_audio_root / audio_file
        if _copy_if_present(pages_audio_file, source_audio_file):
            restored.append(f"restored audio file from Pages: {audio_file}")
    return restored


def _collect_publish_paths(project_root: Path, edition_date: str) -> list[tuple[Path, Path]]:
    pages_repo = project_root / "bluefern-dispatches-pages"
    audio_root = _source_audio_root(project_root)
    pairs: list[tuple[Path, Path]] = []
    for source in _edition_audio_files(project_root, edition_date):
        if not source.exists():
            continue
        if source.name == "podcast.xml":
            target = pages_repo / "gaza" / "podcast.xml" if source.parent == project_root / "output" / "site" / "gaza" else _pages_audio_root(pages_repo) / source.name
        else:
            target = _pages_audio_root(pages_repo) / source.name
        pairs.append((source, target))
    return pairs


def _publish_audio_artifacts(project_root: Path, edition_date: str, pages_repo: Path) -> dict[str, Any]:
    if not pages_repo.exists():
        raise FileNotFoundError(f"Pages repo does not exist: {pages_repo}")
    if not (pages_repo / ".git").exists():
        raise RuntimeError(f"Pages repo is not a git repository: {pages_repo}")
    branch = _git_output(pages_repo, "branch", "--show-current") or None
    if branch != DEFAULT_PAGES_BRANCH:
        raise RuntimeError(f"Pages repo must be on {DEFAULT_PAGES_BRANCH}: {pages_repo} (current: {branch or '<detached>'})")
    dirty_paths = _git_status_paths(pages_repo)
    if dirty_paths:
        raise RuntimeError(f"Pages repo must be clean before publishing:\n" + "\n".join(dirty_paths))

    copied: list[str] = []
    sources = _collect_publish_paths(project_root, edition_date)
    for source, target in sources:
        _copy_file(source, target)
        copied.append(str(target.relative_to(pages_repo)).replace("\\", "/"))

    dirty_after_copy = _git_status_paths(pages_repo)
    unexpected = [path for path in dirty_after_copy if path not in copied]
    if unexpected:
        raise RuntimeError(f"publish touched unexpected paths:\n" + "\n".join(unexpected))

    return {
        "pages_repo": str(pages_repo),
        "copied": copied,
        "copied_count": len(copied),
        "target_branch": branch,
    }


def _commit_pages_repo(pages_repo: Path, copied: Sequence[str], edition_date: str) -> str:
    if not copied:
        return ""
    _run_git(pages_repo, "add", "--", *copied)
    staged = _git_output(pages_repo, "diff", "--cached", "--name-only").splitlines()
    staged = [line.strip().replace("\\", "/") for line in staged if line.strip()]
    expected = sorted(path.replace("\\", "/") for path in copied)
    if sorted(staged) != expected:
        raise RuntimeError(f"staged files do not match copied files:\nexpected: {expected}\nstaged: {staged}")
    message = f"Republish Gaza audio for {edition_date}"
    commit = _run_git(pages_repo, "commit", "-m", message, check=False)
    if commit.returncode != 0:
        raise RuntimeError(commit.stderr.strip() or commit.stdout.strip() or "git commit failed")
    return message


def _push_pages_repo(pages_repo: Path, branch: str) -> None:
    push = _run_git(pages_repo, "push", "origin", branch, check=False)
    if push.returncode != 0:
        raise RuntimeError(push.stderr.strip() or push.stdout.strip() or f"git push origin {branch} failed")


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    edition_date = _validate_date(args.date)
    mode = "check" if args.check else "generate" if args.generate else "publish"
    source_report = _build_source_report(ROOT, edition_date)
    pages_report = _build_pages_report(ROOT, edition_date)
    issues: list[str] = []

    pages_repo = Path(args.pages_repo).resolve()
    pages_ready = pages_report["exists"] and pages_report["is_git_repo"] and pages_report["branch"] == args.pages_branch and not pages_report["dirty_paths"]
    if mode in {"check", "publish"} and not pages_ready:
        if not pages_report["exists"]:
            issues.append(f"Pages repo missing: {pages_repo}")
        elif not pages_report["is_git_repo"]:
            issues.append(f"Pages repo is not a git repository: {pages_repo}")
        elif pages_report["branch"] != args.pages_branch:
            issues.append(f"Pages repo must be on {args.pages_branch}: {pages_repo}")
        elif pages_report["dirty_paths"]:
            issues.append(f"Pages repo is dirty: {pages_repo}")

    source_missing_keys = [name for name, item in (source_report.get("files") or {}).items() if not _artifact_ready(item)]
    pages_missing_keys = [name for name, item in (pages_report.get("files") or {}).items() if not _artifact_published(item)]
    pages_has_complete_set = bool(pages_report.get("ready")) and bool(pages_report.get("links_ready"))
    source_has_complete_set = bool(source_report.get("ready"))
    source_has_mp3 = bool(source_report.get("mp3_ready"))
    pages_has_mp3 = bool(pages_report.get("mp3_ready"))

    report: dict[str, Any] = {
        "date": edition_date,
        "mode": mode,
        "source_repo": {
            "path": str(ROOT),
            "audio_root": source_report["path"],
        },
        "pages_repo": pages_report,
        "source_artifacts": source_report,
        "source_missing_keys": source_missing_keys,
        "pages_missing_keys": pages_missing_keys,
        "issues": issues,
        "ok": False,
        "next_action": "",
        "generation": None,
        "publish": None,
    }

    if mode == "check":
        if pages_has_complete_set:
            report["ok"] = True
            report["next_action"] = "No action needed."
            return report
        if not pages_has_mp3 and not source_has_mp3:
            issues.append("MP3 missing from both source and Pages; run --generate.")
        elif not pages_has_mp3 and source_has_mp3:
            issues.append("MP3 is present in source but missing from Pages; run --publish.")
        else:
            issues.append("Pages audio set is incomplete; run --publish after repairing the listed source audio files.")
        if not pages_report.get("links_ready"):
            issues.append("Pages audio index or podcast links are incomplete.")
        report["ok"] = False
        report["next_action"] = "Run --generate to create source audio outputs." if not source_has_mp3 else "Run --publish to refresh the Pages audio mirror."
        return report

    if mode == "generate":
        restored_from_pages = _restore_source_audio_from_pages(ROOT, edition_date, pages_repo)
        try:
            result = write_gaza_audio_outputs(
                ROOT,
                edition_date,
                dry_run=False,
                tts_provider=str(args.tts_provider or os.getenv("GAZA_AUDIO_TTS_PROVIDER", DEFAULT_TTS_PROVIDER)).strip().lower() or DEFAULT_TTS_PROVIDER,
                tts_model=str(args.audio_model or os.getenv("GAZA_AUDIO_MODEL", DEFAULT_AUDIO_MODEL)).strip() or DEFAULT_AUDIO_MODEL,
                tts_voice=str(args.audio_voice or os.getenv("GAZA_AUDIO_VOICE", DEFAULT_AUDIO_VOICE)).strip() or DEFAULT_AUDIO_VOICE,
                audio_format=str(args.audio_format or DEFAULT_AUDIO_FORMAT).strip().lower() or DEFAULT_AUDIO_FORMAT,
            )
        except Exception as exc:  # noqa: BLE001
            refreshed_source_report = _build_source_report(ROOT, edition_date)
            report["source_artifacts"] = refreshed_source_report
            report["source_missing_keys"] = [name for name, item in (refreshed_source_report.get("files") or {}).items() if not _artifact_ready(item)]
            report["generation"] = {"restored_from_pages": restored_from_pages}
            report["issues"].append(str(exc))
            if restored_from_pages:
                report["issues"].extend(restored_from_pages)
            report["next_action"] = "Fix the audio generation inputs and rerun --generate."
            return report
        refreshed_source_report = _build_source_report(ROOT, edition_date)
        report["source_artifacts"] = refreshed_source_report
        report["source_missing_keys"] = [name for name, item in (refreshed_source_report.get("files") or {}).items() if not _artifact_ready(item)]
        report["generation"] = {
            "transcript_path": str(result.transcript_path),
            "metadata_path": str(result.metadata_path),
            "podcast_path": str(result.podcast_path),
            "flash_briefing_path": str(result.flash_briefing_path),
            "audio_status": result.audio_status,
            "audio_file": result.audio_file,
            "audio_url": result.audio_url,
            "story_count": result.story_count,
            "tts_provider": result.tts_provider,
            "tts_model": result.tts_model,
            "tts_voice": result.tts_voice,
            "restored_from_pages": restored_from_pages,
        }
        if refreshed_source_report["ready"] and refreshed_source_report["mp3_ready"]:
            report["ok"] = True
            report["next_action"] = f'Run --publish to copy Gaza audio files into {pages_repo}.' if pages_ready else "Publish once the Pages repo is clean and on gh-pages."
        else:
            report["ok"] = False
            if not refreshed_source_report["mp3_ready"]:
                report["issues"].append(
                    f"MP3 is still missing from source: {source_report['files']['audio_file']['path']}"
                )
            missing = ", ".join(report["source_missing_keys"] or [])
            if missing:
                report["issues"].append(f"Source audio artifacts are still incomplete after generation: {missing}")
            report["next_action"] = "Run --generate with a TTS provider or restore the missing MP3 from a source-backed audio file."
        return report

    if source_report["issues"]:
        report["issues"].extend(source_report["issues"])
        report["ok"] = False
        report["next_action"] = "Repair the listed audio files before publishing."
        return report

    try:
        publish_result = _publish_audio_artifacts(ROOT, edition_date, pages_repo)
        report["publish"] = publish_result
        if args.commit:
            commit_message = _commit_pages_repo(pages_repo, publish_result["copied"], edition_date)
            report["publish"]["commit_message"] = commit_message
            if args.push:
                _push_pages_repo(pages_repo, args.pages_branch)
                report["publish"]["pushed"] = True
            else:
                report["publish"]["pushed"] = False
        elif args.push:
            raise RuntimeError("--push requires --commit")
        report["pages_repo"] = _build_pages_report(ROOT, edition_date)
        report["ok"] = True
        report["next_action"] = f'Run git status inside "{pages_repo}" to review the copied files.' if not args.commit else f'Run git status inside "{pages_repo}" before pushing.'
        return report
    except Exception as exc:  # noqa: BLE001
        report["issues"].append(str(exc))
        report["next_action"] = "Fix the listed publish issue and rerun --publish."
        return report


def render_text_report(report: dict[str, Any]) -> str:
    edition_date = str(report.get("date") or "")
    mode = str(report.get("mode") or "")
    source = report.get("source_artifacts") or {}
    pages = report.get("pages_repo") or {}
    lines = [
        f"GAZA AUDIO REPUBLISH - {edition_date}",
        "",
        f"Mode: {mode}",
        f"Source audio root: {source.get('path')}",
        f"Pages repo: {pages.get('path')}",
        f"Status: {'ready' if report.get('ok') else 'repair needed'}",
        "",
        "Source audio",
    ]
    files = source.get("files") or {}
    for key in ("transcript", "metadata", "audio_file", "audio_index", "podcast_feed", "podcast_root_feed", "podcast_artwork"):
        item = files.get(key) or {}
        status = "present" if item.get("exists") else "missing"
        if item.get("matches_target") is True:
            status = "in sync"
        lines.append(f"- {key.replace('_', ' ').title()}: {status}")
    lines.append("")
    lines.append("Pages audio")
    pages_files = pages.get("files") or {}
    for key in ("transcript", "metadata", "audio_file", "audio_index", "podcast_feed", "podcast_root_feed", "podcast_artwork"):
        item = pages_files.get(key) or {}
        status = "present" if item.get("target_exists") else "missing"
        if item.get("matches_target") is True:
            status = "in sync"
        lines.append(f"- {key.replace('_', ' ').title()}: {status}")
    if report.get("source_missing_keys"):
        lines.append("")
        lines.append("Source gaps")
        for key in report["source_missing_keys"]:
            lines.append(f"- {key.replace('_', ' ').title()}")
    if report.get("pages_missing_keys"):
        lines.append("")
        lines.append("Pages gaps")
        for key in report["pages_missing_keys"]:
            lines.append(f"- {key.replace('_', ' ').title()}")
    if report.get("generation"):
        generation = report["generation"]
        lines.append("")
        lines.append("Generation")
        lines.append(f"- Audio status: {generation.get('audio_status')}")
        if generation.get("audio_file"):
            lines.append(f"- Audio file: {generation.get('audio_file')}")
        lines.append(f"- Story count: {generation.get('story_count')}")
    if report.get("publish"):
        publish = report["publish"]
        lines.append("")
        lines.append("Publish")
        lines.append(f"- Files copied: {publish.get('copied_count')}")
        for copied in publish.get("copied", [])[:10]:
            lines.append(f"- {copied}")
        if publish.get("commit_message"):
            lines.append(f"- Commit: {publish.get('commit_message')}")
        if publish.get("pushed") is True:
            lines.append("- Push: yes")
    if report.get("issues"):
        lines.append("")
        lines.append("Issues")
        for issue in report["issues"]:
            lines.append(f"- {issue}")
    lines.append("")
    lines.append("Next action:")
    lines.append(str(report.get("next_action") or "No action needed."))
    return "\n".join(lines)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check, generate, or republish Gaza audio artifacts to the Pages repo.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Read-only check of Gaza audio files and Pages sync state.")
    mode.add_argument("--generate", action="store_true", help="Generate Gaza audio artifacts in the source repo only.")
    mode.add_argument("--publish", action="store_true", help="Generate and copy Gaza audio artifacts into the Pages repo.")
    parser.add_argument("--date", required=True, help="Edition date in YYYY-MM-DD format.")
    parser.add_argument("--pages-repo", default=str(ROOT / "bluefern-dispatches-pages"), help="Path to the local Pages repo.")
    parser.add_argument("--pages-branch", default=DEFAULT_PAGES_BRANCH, help="Pages branch to expect and optionally push to.")
    parser.add_argument("--tts-provider", choices=("none", "openai"), default=None, help="TTS provider used during generation.")
    parser.add_argument("--audio-model", default=None, help="Audio model used during generation.")
    parser.add_argument("--audio-voice", default=None, help="Audio voice used during generation.")
    parser.add_argument("--audio-format", choices=("mp3", "wav"), default=DEFAULT_AUDIO_FORMAT, help="Audio file format used during generation.")
    parser.add_argument("--commit", action="store_true", help="Commit copied Pages changes locally.")
    parser.add_argument("--push", action="store_true", help="Push the Pages commit to origin.")
    parser.add_argument("--no-live", action="store_true", help="Skip live HTTP verification. Accepted for operator safety.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        if args.push and not args.commit:
            raise ValueError("--push requires --commit")
        report = build_report(args)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(render_text_report(report))
        return 0 if report.get("ok") else 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
