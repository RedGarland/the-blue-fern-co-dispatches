from __future__ import annotations

import json
import os
import re
import ssl
import time
from dataclasses import dataclass
from pathlib import Path
from urllib import error, request

OPENAI_TTS_URL = "https://api.openai.com/v1/audio/speech"


@dataclass(frozen=True)
class TTSResult:
    ok: bool
    audio_bytes: bytes | None
    provider: str
    model: str | None
    voice: str | None
    fmt: str | None
    error_reason: str | None


@dataclass(frozen=True)
class TTSDiagnostics:
    provider: str
    model_requested: str | None
    voice_requested: str | None
    narration_char_count: int
    output_path_attempted: str | None
    api_key_present: bool
    output_dir_exists: bool
    partial_mp3_exists: bool
    elapsed_seconds: float
    exception_type: str | None
    exception_message_sanitized: str | None
    timeout_seconds: float | None
    audio_format: str | None
    tls_verify: bool = True
    ca_file_used: str | None = None
    ca_source: str | None = None
    truststore_requested: bool = False
    truststore_available: bool = False
    ssl_cert_file_env: str | None = None
    requests_ca_bundle_env: str | None = None
    bluefern_tts_ca_file_env: str | None = None
    tls_workaround_warning: str | None = None


def _env_text(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _sanitize_exception_message(message: str) -> str:
    text = str(message or "")
    text = re.sub(r"sk-[A-Za-z0-9]{10,}", "[redacted-api-key]", text)
    text = re.sub(r"Bearer\s+[A-Za-z0-9._-]+", "Bearer [redacted]", text, flags=re.IGNORECASE)
    text = re.sub(r"\b[A-Za-z0-9]{20,}\b", lambda match: "[redacted]" if "sk-" in match.group(0).lower() else match.group(0), text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:500]


def _sanitize_http_error_body(exc: error.HTTPError) -> str | None:
    try:
        body = exc.read()
    except Exception:  # noqa: BLE001
        body = b""
    if not body:
        return None
    if isinstance(body, bytes):
        text = body.decode("utf-8", errors="replace")
    else:
        text = str(body)
    cleaned = _sanitize_exception_message(text)
    return cleaned or None


def _resolve_ca_bundle_path(env_name: str) -> tuple[str | None, str | None]:
    value = _env_text(env_name)
    if not value:
        return None, None
    path = Path(value).expanduser()
    if not path.is_file():
        raise RuntimeError(
            f"{env_name} is set to {value!r} but that file does not exist or is not a file. "
            "Set it to a valid PEM bundle or unset it."
        )
    return str(path.resolve()), value


def _build_tls_context() -> tuple[ssl.SSLContext, dict[str, object | None]]:
    bluefern_tts_ca_file, bluefern_tts_ca_file_env = _resolve_ca_bundle_path("BLUEFERN_TTS_CA_FILE")
    ssl_cert_file, ssl_cert_file_env = _resolve_ca_bundle_path("SSL_CERT_FILE")
    requests_ca_bundle, requests_ca_bundle_env = _resolve_ca_bundle_path("REQUESTS_CA_BUNDLE")
    truststore_requested = _env_text("BLUEFERN_TTS_USE_TRUSTSTORE") == "1"
    ca_source_preference = (_env_text("BLUEFERN_TTS_CA_SOURCE") or "auto").strip().lower()
    if ca_source_preference not in {"auto", "certifi"}:
        ca_source_preference = "auto"
    meta: dict[str, object | None] = {
        "tls_verify": True,
        "ca_file_used": None,
        "ca_source": "system_default",
        "truststore_requested": truststore_requested,
        "truststore_available": False,
        "ssl_cert_file_env": ssl_cert_file_env,
        "requests_ca_bundle_env": requests_ca_bundle_env,
        "bluefern_tts_ca_file_env": bluefern_tts_ca_file_env,
        "tls_workaround_warning": None,
    }

    if bluefern_tts_ca_file:
        meta["ca_file_used"] = bluefern_tts_ca_file
        meta["ca_source"] = "bluefern_tts_ca_file"
        return ssl.create_default_context(cafile=bluefern_tts_ca_file), meta

    if ssl_cert_file:
        meta["ca_file_used"] = ssl_cert_file
        meta["ca_source"] = "SSL_CERT_FILE"
        return ssl.create_default_context(cafile=ssl_cert_file), meta

    if requests_ca_bundle:
        meta["ca_file_used"] = requests_ca_bundle
        meta["ca_source"] = "REQUESTS_CA_BUNDLE"
        return ssl.create_default_context(cafile=requests_ca_bundle), meta

    if ca_source_preference == "certifi":
        try:
            import certifi  # type: ignore

            certifi_path = certifi.where()
            meta["ca_file_used"] = certifi_path
            meta["ca_source"] = "certifi"
            return ssl.create_default_context(cafile=certifi_path), meta
        except Exception:  # noqa: BLE001
            meta["ca_source"] = "certifi_unavailable"
            meta["tls_workaround_warning"] = "BLUEFERN_TTS_CA_SOURCE=certifi was requested, but certifi is unavailable; using the system default trust store."

    if truststore_requested:
        try:
            import truststore  # type: ignore

            meta["truststore_available"] = True
            meta["ca_source"] = "truststore"
            return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT), meta
        except Exception:  # noqa: BLE001
            meta["ca_source"] = "truststore_unavailable"
            meta["tls_workaround_warning"] = "BLUEFERN_TTS_USE_TRUSTSTORE=1 was requested, but truststore is unavailable; using the system default trust store."

    return ssl.create_default_context(), meta


def _diagnostics(
    *,
    provider: str,
    model: str | None,
    voice: str | None,
    text: str,
    output_path: Path | None,
    timeout: float | None,
    audio_format: str | None,
    tls_meta: dict[str, object | None],
    elapsed_seconds: float = 0.0,
    exception: BaseException | None = None,
) -> TTSDiagnostics:
    output_dir = output_path.parent if output_path is not None else None
    message: str | None = None
    if exception is not None:
        message = _sanitize_exception_message(str(exception))
        if isinstance(exception, error.HTTPError):
            body = _sanitize_http_error_body(exception)
            if body:
                message = f"{message}; response_body={body}" if message else f"response_body={body}"
    return TTSDiagnostics(
        provider=provider,
        model_requested=model,
        voice_requested=voice,
        narration_char_count=len(str(text or "")),
        output_path_attempted=str(output_path) if output_path is not None else None,
        api_key_present=bool(str(os.getenv("OPENAI_API_KEY", "")).strip()),
        output_dir_exists=bool(output_dir and output_dir.exists()),
        partial_mp3_exists=bool(output_path and output_path.exists()),
        elapsed_seconds=elapsed_seconds,
        exception_type=exception.__class__.__name__ if exception is not None else None,
        exception_message_sanitized=message,
        timeout_seconds=timeout,
        audio_format=audio_format,
        tls_verify=bool(tls_meta.get("tls_verify", True)),
        ca_file_used=tls_meta.get("ca_file_used"),
        ca_source=tls_meta.get("ca_source"),
        truststore_requested=bool(tls_meta.get("truststore_requested", False)),
        truststore_available=bool(tls_meta.get("truststore_available", False)),
        ssl_cert_file_env=tls_meta.get("ssl_cert_file_env"),
        requests_ca_bundle_env=tls_meta.get("requests_ca_bundle_env"),
        bluefern_tts_ca_file_env=tls_meta.get("bluefern_tts_ca_file_env"),
        tls_workaround_warning=tls_meta.get("tls_workaround_warning"),
    )


def _none_result() -> TTSResult:
    return TTSResult(
        ok=False,
        audio_bytes=None,
        provider="none",
        model=None,
        voice=None,
        fmt=None,
        error_reason="tts_provider_none",
    )


def synthesize_speech(
    *,
    text: str,
    provider: str,
    model: str = "gpt-4o-mini-tts",
    voice: str = "alloy",
    audio_format: str = "mp3",
    timeout: float = 30.0,
) -> TTSResult:
    result, _ = synthesize_speech_with_diagnostics(
        text=text,
        provider=provider,
        model=model,
        voice=voice,
        audio_format=audio_format,
        timeout=timeout,
    )
    return result


def synthesize_speech_with_diagnostics(
    *,
    text: str,
    provider: str,
    model: str = "gpt-4o-mini-tts",
    voice: str = "alloy",
    audio_format: str = "mp3",
    timeout: float = 30.0,
    output_path: Path | None = None,
) -> tuple[TTSResult, TTSDiagnostics]:
    chosen = str(provider or "none").strip().lower()
    tls_context, tls_meta = _build_tls_context()
    if chosen in {"", "none"}:
        diagnostics = _diagnostics(
            provider="none",
            model=None,
            voice=None,
            text=text,
            output_path=output_path,
            timeout=timeout,
            audio_format=audio_format,
            tls_meta=tls_meta,
        )
        return _none_result(), diagnostics
    if chosen != "openai":
        diagnostics = _diagnostics(
            provider=chosen,
            model=model,
            voice=voice,
            text=text,
            output_path=output_path,
            timeout=timeout,
            audio_format=audio_format,
            tls_meta=tls_meta,
        )
        return TTSResult(False, None, chosen, model, voice, audio_format, "unsupported_tts_provider"), diagnostics
    api_key = str(os.getenv("OPENAI_API_KEY", "")).strip()
    if not api_key:
        diagnostics = _diagnostics(
            provider="openai",
            model=model,
            voice=voice,
            text=text,
            output_path=output_path,
            timeout=timeout,
            audio_format=audio_format,
            tls_meta=tls_meta,
        )
        return TTSResult(False, None, "openai", model, voice, audio_format, "missing_openai_api_key"), diagnostics
    payload = {
        "model": model,
        "voice": voice,
        "input": str(text or ""),
        "response_format": audio_format,
    }
    req = request.Request(
        OPENAI_TTS_URL,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    start = time.monotonic()
    try:
        with request.urlopen(req, timeout=timeout, context=tls_context) as resp:
            audio = resp.read()
    except error.HTTPError as exc:
        diagnostics = _diagnostics(
            provider="openai",
            model=model,
            voice=voice,
            text=text,
            output_path=output_path,
            timeout=timeout,
            audio_format=audio_format,
            tls_meta=tls_meta,
            elapsed_seconds=round(time.monotonic() - start, 6),
            exception=exc,
        )
        return TTSResult(False, None, "openai", model, voice, audio_format, f"openai_http_{getattr(exc, 'code', 'error')}"), diagnostics
    except Exception as exc:  # noqa: BLE001
        diagnostics = _diagnostics(
            provider="openai",
            model=model,
            voice=voice,
            text=text,
            output_path=output_path,
            timeout=timeout,
            audio_format=audio_format,
            tls_meta=tls_meta,
            elapsed_seconds=round(time.monotonic() - start, 6),
            exception=exc,
        )
        return TTSResult(False, None, "openai", model, voice, audio_format, "openai_tts_request_failed"), diagnostics
    if not audio:
        diagnostics = _diagnostics(
            provider="openai",
            model=model,
            voice=voice,
            text=text,
            output_path=output_path,
            timeout=timeout,
            audio_format=audio_format,
            tls_meta=tls_meta,
            elapsed_seconds=round(time.monotonic() - start, 6),
        )
        return TTSResult(False, None, "openai", model, voice, audio_format, "openai_tts_empty_audio"), diagnostics
    diagnostics = _diagnostics(
        provider="openai",
        model=model,
        voice=voice,
        text=text,
        output_path=output_path,
        timeout=timeout,
        audio_format=audio_format,
        tls_meta=tls_meta,
        elapsed_seconds=round(time.monotonic() - start, 6),
    )
    return TTSResult(True, audio, "openai", model, voice, audio_format, None), diagnostics
