from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.gaza_audio import gaza_audio_release_artifact_contract, write_gaza_audio_outputs

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_date(value: str) -> str:
    text = str(value or "").strip()
    if not DATE_RE.match(text):
        raise ValueError(f"date must use YYYY-MM-DD: {text}")
    return text


def _available_edition_dates(root: Path) -> list[str]:
    editions = root / "output" / "site" / "gaza" / "editions"
    if not editions.exists():
        return []
    out: list[str] = []
    for child in editions.iterdir():
        if child.is_dir() and DATE_RE.match(child.name):
            out.append(child.name)
    return sorted(out)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Gaza transcript/podcast/flash outputs from existing source-backed edition records.")
    parser.add_argument("--date", help="Edition date in YYYY-MM-DD format.")
    parser.add_argument("--latest", action="store_true", help="Use latest available Gaza edition date under output/site/gaza/editions.")
    parser.add_argument("--all", action="store_true", help="Generate audio outputs for all available Gaza edition dates.")
    parser.add_argument("--dry-run", action="store_true", help="Compute outputs without writing files.")
    parser.add_argument("--write", action="store_true", help="Explicit write flag (default behavior already writes unless --dry-run).")
    parser.add_argument("--tts-provider", choices=("none", "openai"), default=None, help="Optional TTS provider. Defaults to env GAZA_AUDIO_TTS_PROVIDER or none.")
    parser.add_argument("--voice", default=None, help="Optional TTS voice. Defaults to env GAZA_AUDIO_VOICE or alloy.")
    parser.add_argument("--voices", default=None, help="Comma-separated voices for alternating mode (example: alloy,verse).")
    parser.add_argument("--alternate-voices", action="store_true", help="Generate segmented TTS and alternate voices across story segments.")
    parser.add_argument("--segue-chime", choices=("none", "gentle"), default="none", help="Optional segue chime between story segments in alternating mode.")
    parser.add_argument("--model", default=None, help="Optional TTS model. Defaults to env GAZA_AUDIO_MODEL or gpt-4o-mini-tts.")
    parser.add_argument("--audio-format", default="mp3", choices=("mp3", "wav"), help="Audio format for generated speech.")
    parser.add_argument("--tts-price-per-1m-chars", type=float, default=None, help="Optional pricing input for estimated cost logging.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if sum([bool(args.date), bool(args.latest), bool(args.all)]) != 1:
        print(json.dumps({"ok": False, "error": "choose exactly one of --date, --latest, or --all"}, indent=2))
        return 2

    dates: list[str] = []
    available = _available_edition_dates(ROOT)
    if args.all:
        dates = available
    elif args.latest:
        if not available:
            print(json.dumps({"ok": False, "error": "no Gaza editions found under output/site/gaza/editions"}, indent=2))
            return 2
        dates = [available[-1]]
    else:
        dates = [_validate_date(str(args.date))]

    if not dates:
        print(json.dumps({"ok": False, "error": "no target dates to process"}, indent=2))
        return 2

    dry_run = bool(args.dry_run)
    tts_provider = str(args.tts_provider or os.getenv("GAZA_AUDIO_TTS_PROVIDER", "none")).strip().lower() or "none"
    tts_voice = str(args.voice or os.getenv("GAZA_AUDIO_VOICE", "alloy")).strip() or "alloy"
    tts_model = str(args.model or os.getenv("GAZA_AUDIO_MODEL", "gpt-4o-mini-tts")).strip() or "gpt-4o-mini-tts"
    audio_format = str(args.audio_format or "mp3").strip().lower() or "mp3"
    results: list[dict[str, object]] = []
    errors: list[str] = []
    tts_failures: list[str] = []
    for date_text in dates:
        try:
            result = write_gaza_audio_outputs(
                ROOT,
                date_text,
                dry_run=dry_run,
                tts_provider=tts_provider,
                tts_model=tts_model,
                tts_voice=tts_voice,
                audio_format=audio_format,
                alternate_voices=bool(args.alternate_voices),
                voices=str(args.voices or ""),
                segue_chime=str(args.segue_chime or "none"),
                tts_price_per_1m_chars=args.tts_price_per_1m_chars,
            )
            contract = gaza_audio_release_artifact_contract(ROOT, edition_date=date_text)
            results.append(
                {
                    "edition_date": result.edition_date,
                    "transcript_path": str(result.transcript_path),
                    "metadata_path": str(result.metadata_path),
                    "podcast_path": str(result.podcast_path),
                    "flash_briefing_path": str(result.flash_briefing_path),
                    "audio_status": result.audio_status,
                    "audio_file": result.audio_file,
                    "audio_url": result.audio_url,
                    "tts_provider": result.tts_provider,
                    "tts_model": result.tts_model,
                    "tts_voice": result.tts_voice,
                    "tts_error": result.tts_error,
                    "story_count": result.story_count,
                    "voice_mode": "alternating" if args.alternate_voices else "single",
                    "audio_format": audio_format,
                    "audio_expected": contract.get("audio_expected"),
                    "audio_present": contract.get("audio_present"),
                    "audio_publish_status": contract.get("audio_publish_status"),
                    "audio_files_in_copy_plan": contract.get("audio_files_in_copy_plan"),
                    "audio_index_entries": contract.get("audio_index_entries"),
                    "podcast_entries": contract.get("podcast_entries"),
                    "missing_audio_artifacts": contract.get("missing_audio_artifacts"),
                    "audio_follow_up_command": contract.get("audio_follow_up_command"),
                }
            )
            if tts_provider != "none" and result.audio_status != "audio_file_ready":
                tts_failures.append(f"{date_text}: {result.audio_status}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{date_text}: {exc}")

    payload = {
        "ok": len(errors) == 0 and len(tts_failures) == 0,
        "dry_run": dry_run,
        "tts_provider": tts_provider,
        "tts_model": tts_model if tts_provider != "none" else None,
        "tts_voice": tts_voice if tts_provider != "none" else None,
        "generated_count": len(results),
        "results": results,
        "errors": errors,
        "tts_failures": tts_failures,
        "suggested_follow_up": "After daily Gaza run, generate audio with: python scripts/run_gaza_audio.py --date YYYY-MM-DD",
    }
    print(json.dumps(payload, indent=2))
    return 0 if (not errors and not tts_failures) else 1


if __name__ == "__main__":
    raise SystemExit(main())
