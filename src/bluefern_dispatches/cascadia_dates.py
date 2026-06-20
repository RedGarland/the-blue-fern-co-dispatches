from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import email.utils
import re
from typing import Any


try:
    PACIFIC_TZ = ZoneInfo("America/Los_Angeles")
except ZoneInfoNotFoundError:
    # Prefer real tzdata-backed ZoneInfo for correct DST transitions.
    # This fixed-offset fallback keeps canonical ISO behavior available when
    # timezone data is unavailable (common on some Windows setups).
    PACIFIC_TZ = timezone(timedelta(hours=-8), "America/Los_Angeles")


def _parse_datetime(value: Any) -> tuple[datetime | None, str | None]:
    text = str(value or "").strip()
    if not text:
        return None, "missing_date"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = email.utils.parsedate_to_datetime(text)
        except (TypeError, ValueError):
            m = re.search(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", text)
            if m:
                year, month, day = [int(n) for n in m.groups()]
                parsed = datetime(year, month, day, tzinfo=PACIFIC_TZ)
            else:
                return None, "unparseable_date"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=PACIFIC_TZ)
    return parsed.astimezone(PACIFIC_TZ), None


def to_iso_ts(value: Any) -> tuple[str | None, str | None]:
    dt, reason = _parse_datetime(value)
    if dt is None:
        return None, reason
    return dt.isoformat(timespec="seconds"), None


def to_event_date(value: Any) -> tuple[str | None, str | None]:
    dt, reason = _parse_datetime(value)
    if dt is None:
        return None, reason
    return dt.date().isoformat(), None


def coverage_week_for_date(value: Any) -> str | None:
    dt, _ = _parse_datetime(value)
    if dt is None:
        return None
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def canonical_date_fields(
    *,
    published_at: Any,
    retrieved_at: Any,
    coverage_start_date: str | None = None,
    coverage_end_date: str | None = None,
) -> dict[str, Any]:
    source_published_at, source_reason = to_iso_ts(published_at)
    event_ts = source_published_at
    event_date, event_reason = to_event_date(published_at)
    retrieved_iso, retrieved_reason = to_iso_ts(retrieved_at)
    date_quality_reasons = [reason for reason in (source_reason, event_reason, retrieved_reason) if reason]
    quality = "ok" if not date_quality_reasons else "partial"
    return {
        "event_date": event_date,
        "event_ts": event_ts,
        "source_published_at": source_published_at,
        "retrieved_at": retrieved_iso,
        "coverage_week": coverage_week_for_date(event_ts or coverage_end_date or coverage_start_date),
        "coverage_start_date": coverage_start_date,
        "coverage_end_date": coverage_end_date,
        "raw_date_text": str(published_at or "") or None,
        "date_quality": quality,
        "date_quality_reason": ",".join(date_quality_reasons) if date_quality_reasons else None,
    }
