from __future__ import annotations

import io
import math
import struct
import wave


def _wav_parts(audio_bytes: bytes) -> tuple[wave._wave_params, bytes]:
    with wave.open(io.BytesIO(audio_bytes), "rb") as wav:
        params = wav.getparams()
        frames = wav.readframes(wav.getnframes())
    return params, frames


def assemble_wav(parts: list[bytes]) -> bytes:
    if not parts:
        raise ValueError("no audio parts were provided")
    base_params: wave._wave_params | None = None
    frames_blob = bytearray()
    for part in parts:
        params, frames = _wav_parts(part)
        if base_params is None:
            base_params = params
        elif params[:3] != base_params[:3]:
            raise ValueError("wav segment format mismatch")
        frames_blob.extend(frames)
    if base_params is None:
        raise ValueError("no audio parts were provided")
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(base_params.nchannels)
        wav.setsampwidth(base_params.sampwidth)
        wav.setframerate(base_params.framerate)
        wav.writeframes(bytes(frames_blob))
    return out.getvalue()


def wav_duration_seconds(audio_bytes: bytes) -> float | None:
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as wav:
            frames = wav.getnframes()
            rate = wav.getframerate() or 0
        if rate <= 0:
            return None
        return frames / float(rate)
    except Exception:  # noqa: BLE001
        return None


def make_gentle_chime_wav(*, duration_seconds: float = 0.35, sample_rate: int = 22050, frequency_hz: float = 740.0) -> bytes:
    total = max(1, int(duration_seconds * sample_rate))
    frames = bytearray()
    for i in range(total):
        t = i / float(sample_rate)
        env = math.exp(-6.0 * t / duration_seconds)
        value = math.sin(2.0 * math.pi * frequency_hz * t) * env
        sample = int(max(-1.0, min(1.0, value)) * 11000)
        frames.extend(struct.pack("<h", sample))
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(bytes(frames))
    return out.getvalue()
