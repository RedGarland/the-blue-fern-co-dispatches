from __future__ import annotations

import argparse
import json
import sys
from datetime import date as date_class
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.tts_provider import synthesize_speech_with_diagnostics  # noqa: E402


def _default_output_dir(date: str) -> Path:
    return ROOT / "output" / "review" / "food-line" / date


def _read_audio_text(date: str) -> str:
    metadata_path = ROOT / "output" / "site" / "food-line" / "audio" / f"{date}.json"
    if not metadata_path.exists():
        return ""
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return ""
    if not isinstance(payload, dict):
        return ""
    for key in ("script_text", "episode_summary"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def _sample_text(date: str, sample_text: str | None) -> str:
    if sample_text and sample_text.strip():
        return sample_text.strip()
    loaded = _read_audio_text(date)
    if loaded:
        return loaded
    return "This is a Food Line Dispatch audio smoke test."


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a Food Line TTS smoke test.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/test_food_line_tts.py --sample-text \"This is a Food Line Dispatch audio smoke test.\"\n"
            "  $env:BLUEFERN_TTS_CA_SOURCE='certifi'; python scripts/test_food_line_tts.py\n"
            "  $env:BLUEFERN_TTS_CA_FILE='C:\\path\\to\\corp-ca.pem'; python scripts/test_food_line_tts.py\n"
            "  $env:BLUEFERN_TTS_USE_TRUSTSTORE='1'; python scripts/test_food_line_tts.py\n"
        ),
    )
    parser.add_argument("--date", default=date_class.today().isoformat(), help="Edition date YYYY-MM-DD. Defaults to today's run date.")
    parser.add_argument("--sample-text", default="", help="Override the narration text with a safe sample.")
    parser.add_argument("--output", default="", help="Output directory for smoke-test artifacts.")
    parser.add_argument("--model", default="gpt-4o-mini-tts", help="OpenAI TTS model.")
    parser.add_argument("--voice", default="alloy", help="OpenAI TTS voice.")
    parser.add_argument("--provider", default="openai", choices=("openai", "none"), help="TTS provider to use for the smoke test.")
    parser.add_argument("--audio-timeout-seconds", type=float, default=90.0, help="Timeout for the TTS request.")
    return parser.parse_args(argv)


def run_food_line_tts_smoke(
    *,
    date: str,
    sample_text: str | None = None,
    output: Path | None = None,
    model: str = "gpt-4o-mini-tts",
    voice: str = "alloy",
    provider: str = "openai",
    audio_timeout_seconds: float = 90.0,
) -> dict[str, Any]:
    text = _sample_text(date, sample_text)
    out_dir = Path(output) if output else _default_output_dir(date)
    out_dir.mkdir(parents=True, exist_ok=True)
    mp3_path = out_dir / "tts_smoke-test.mp3"
    result, diagnostics = synthesize_speech_with_diagnostics(
        text=text,
        provider=provider,
        model=model,
        voice=voice,
        audio_format="mp3",
        timeout=audio_timeout_seconds,
        output_path=mp3_path,
    )
    payload: dict[str, Any] = {
        "ok": bool(result.ok and result.audio_bytes),
        "date": date,
        "provider": diagnostics.provider,
        "model": diagnostics.model_requested,
        "voice": diagnostics.voice_requested,
        "api_key_present": diagnostics.api_key_present,
        "error_type": diagnostics.exception_type or result.error_reason,
        "error_message_sanitized": diagnostics.exception_message_sanitized or result.error_reason,
        "tts_error_type": diagnostics.exception_type or result.error_reason,
        "tts_error_message_sanitized": diagnostics.exception_message_sanitized or result.error_reason,
        "mp3_path": str(mp3_path),
        "mp3_size_bytes": 0,
        "sample_text": text,
        "tts_timeout_seconds": diagnostics.timeout_seconds,
        "tls_verify": diagnostics.tls_verify,
        "ca_file_used": diagnostics.ca_file_used,
        "ca_source": diagnostics.ca_source,
        "truststore_requested": diagnostics.truststore_requested,
        "truststore_available": diagnostics.truststore_available,
        "ssl_cert_file_env": diagnostics.ssl_cert_file_env,
        "requests_ca_bundle_env": diagnostics.requests_ca_bundle_env,
        "bluefern_tts_ca_file_env": diagnostics.bluefern_tts_ca_file_env,
        "tls_workaround_warning": diagnostics.tls_workaround_warning,
        "tts_diagnostics": {
            "provider": diagnostics.provider,
            "model_requested": diagnostics.model_requested,
            "voice_requested": diagnostics.voice_requested,
            "narration_char_count": diagnostics.narration_char_count,
            "output_path_attempted": diagnostics.output_path_attempted,
            "api_key_present": diagnostics.api_key_present,
            "output_dir_exists": diagnostics.output_dir_exists,
            "partial_mp3_exists": diagnostics.partial_mp3_exists,
            "elapsed_seconds": diagnostics.elapsed_seconds,
            "exception_type": diagnostics.exception_type,
            "exception_message_sanitized": diagnostics.exception_message_sanitized,
            "timeout_seconds": diagnostics.timeout_seconds,
            "audio_format": diagnostics.audio_format,
            "tls_verify": diagnostics.tls_verify,
            "ca_file_used": diagnostics.ca_file_used,
            "ca_source": diagnostics.ca_source,
            "truststore_requested": diagnostics.truststore_requested,
            "truststore_available": diagnostics.truststore_available,
            "ssl_cert_file_env": diagnostics.ssl_cert_file_env,
            "requests_ca_bundle_env": diagnostics.requests_ca_bundle_env,
            "bluefern_tts_ca_file_env": diagnostics.bluefern_tts_ca_file_env,
            "tls_workaround_warning": diagnostics.tls_workaround_warning,
        },
    }
    if result.ok and result.audio_bytes:
        try:
            mp3_path.write_bytes(result.audio_bytes)
            payload["mp3_size_bytes"] = mp3_path.stat().st_size
            payload["ok"] = True
        except Exception as exc:  # noqa: BLE001
            payload["ok"] = False
            payload["error_type"] = exc.__class__.__name__
            payload["error_message_sanitized"] = str(exc).replace("\n", " ").strip()[:500]
    smoke_json = out_dir / "tts_smoke_test.json"
    smoke_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    payload["json_path"] = str(smoke_json)
    return payload


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    date = str(args.date).strip()
    output = Path(args.output) if args.output else None
    result = run_food_line_tts_smoke(
        date=date,
        sample_text=args.sample_text,
        output=output,
        model=args.model,
        voice=args.voice,
        provider=args.provider,
        audio_timeout_seconds=float(args.audio_timeout_seconds or 90.0),
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
