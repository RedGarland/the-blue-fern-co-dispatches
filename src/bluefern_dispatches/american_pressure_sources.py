from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
import urllib.error
import urllib.request


REGISTRY_PATH = Path("data/dispatches/american-pressure/source_registry.yml")
REQUIRED_FIELDS = {
    "source_id",
    "name",
    "url",
    "publisher",
    "pillar",
    "geography",
    "source_type",
    "reliability_tier",
    "update_frequency",
    "enabled",
    "notes",
}
ALLOWED_SOURCE_STATES = {
    "enabled",
    "manual_only",
    "diagnostics_only",
    "disabled",
}
ALLOWED_PILLARS = {
    "food_pressure",
    "health_access_pressure",
    "household_cost_pressure",
    "environmental_pressure",
    "local_system_strain",
    "policy_implementation",
    "financial_distress_pressure",
}
ALLOWED_RELIABILITY_TIERS = {
    "official_primary",
    "institutional",
    "reputable_reporting",
    "context_only",
}
DEFAULT_TIMEOUT_SECONDS = 8
DEFAULT_USER_AGENT = "BlueFernDispatches/0.1 american-pressure-source-check"


class RegistryValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SourceCheckResult:
    source_id: str
    url: str
    pillar: str
    geography: str
    source_type: str
    reliability_tier: str
    enabled: bool
    validation_ok: bool
    fetch_attempted: bool
    fetch_success: bool | None
    status_code: int | None
    failure_reason: str | None
    checked_at: str
    source_state: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_scalar(value: str) -> Any:
    raw = value.strip()
    if not raw:
        return ""
    if raw[0] == raw[-1] and raw[0] in {'"', "'"} and len(raw) >= 2:
        raw = raw[1:-1]
    lowered = raw.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    return raw


def _parse_simple_yaml(text: str) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or stripped == "sources:":
            continue
        if stripped.startswith("- "):
            if current is not None:
                sources.append(current)
            current = {}
            inline = stripped[2:].strip()
            if inline and ":" in inline:
                key, value = inline.split(":", 1)
                current[key.strip()] = _parse_scalar(value)
            continue
        if current is None:
            continue
        if ":" not in stripped:
            raise RegistryValidationError(f"Unsupported YAML line: {raw_line}")
        key, value = stripped.split(":", 1)
        current[key.strip()] = _parse_scalar(value)
    if current is not None:
        sources.append(current)
    return sources


def load_source_registry(root: Path, path: Path | None = None) -> list[dict[str, Any]]:
    registry_path = root / (path or REGISTRY_PATH)
    if not registry_path.exists():
        raise FileNotFoundError(f"American Pressure source registry does not exist: {registry_path}")
    text = registry_path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        payload = yaml.safe_load(text)
        raw = payload.get("sources", []) if isinstance(payload, dict) else payload
        if not isinstance(raw, list):
            raise RegistryValidationError("source_registry.yml must contain a top-level list or a 'sources' list")
        return [item for item in raw if isinstance(item, dict)]
    except Exception:
        return _parse_simple_yaml(text)


def _is_valid_url(url: str) -> bool:
    try:
        parsed = urlsplit(url.strip())
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def validate_registry_sources(sources: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    seen_ids: set[str] = set()
    for index, source in enumerate(sources, start=1):
        source_id = str(source.get("source_id") or "").strip()
        enabled = source.get("enabled")
        prefix = f"source {index} ({source_id or 'missing-source-id'})"
        if isinstance(enabled, bool) and enabled:
            missing = [field for field in sorted(REQUIRED_FIELDS) if field not in source or str(source.get(field)).strip() == ""]
            if missing:
                errors.append(f"{prefix} missing required fields: {', '.join(missing)}")
        if source_id:
            if source_id in seen_ids:
                errors.append(f"{prefix} has duplicate source_id: {source_id}")
            seen_ids.add(source_id)
        else:
            errors.append(f"{prefix} has empty source_id")
        if not isinstance(enabled, bool):
            errors.append(f"{prefix} has non-boolean enabled value: {enabled!r}")
        pillar = str(source.get("pillar") or "").strip()
        if pillar not in ALLOWED_PILLARS:
            errors.append(f"{prefix} has invalid pillar: {pillar!r}")
        reliability_tier = str(source.get("reliability_tier") or "").strip()
        if reliability_tier not in ALLOWED_RELIABILITY_TIERS:
            errors.append(f"{prefix} has invalid reliability_tier: {reliability_tier!r}")
        source_type = str(source.get("source_type") or "").strip()
        if not source_type:
            errors.append(f"{prefix} has empty source_type")
        source_state = str(source.get("source_state") or ("enabled" if enabled else "disabled")).strip()
        if source_state not in ALLOWED_SOURCE_STATES:
            errors.append(f"{prefix} has invalid source_state: {source_state!r}")
        if source_state == "enabled" and enabled is not True:
            errors.append(f"{prefix} source_state is enabled but enabled is not true")
        if source_state in {"manual_only", "diagnostics_only", "disabled"} and enabled is True:
            errors.append(f"{prefix} source_state is {source_state} but enabled is true")
        url = str(source.get("url") or "").strip()
        if not _is_valid_url(url):
            errors.append(f"{prefix} has malformed URL: {url!r}")
    return errors


def _fetch_status(url: str, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS, user_agent: str = DEFAULT_USER_AGENT) -> tuple[bool, int | None, str | None]:
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status = int(getattr(response, "status", 0) or 0)
            return 200 <= status < 400, status, None
    except urllib.error.HTTPError as exc:
        return False, int(exc.code), f"HTTPError: {exc.code}"
    except Exception as exc:
        try:
            fallback = urllib.request.Request(url, method="GET", headers={"User-Agent": user_agent})
            with urllib.request.urlopen(fallback, timeout=timeout_seconds) as response:
                status = int(getattr(response, "status", 0) or 0)
                return 200 <= status < 400, status, None
        except urllib.error.HTTPError as get_exc:
            return False, int(get_exc.code), f"HTTPError: {get_exc.code}"
        except Exception as get_exc:
            return False, None, f"{type(exc).__name__}: {exc}; GET fallback {type(get_exc).__name__}: {get_exc}"


def build_source_health_report(
    sources: list[dict[str, Any]],
    *,
    fetch_check: bool = False,
    checked_at: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    checked = checked_at or _utc_now()
    report: list[dict[str, Any]] = []
    for source in sources:
        enabled = bool(source.get("enabled"))
        fetch_attempted = bool(fetch_check and enabled)
        fetch_success: bool | None = None
        status_code: int | None = None
        failure_reason: str | None = None
        if fetch_attempted:
            fetch_success, status_code, failure_reason = _fetch_status(str(source.get("url") or ""), timeout_seconds=timeout_seconds)
        row = SourceCheckResult(
            source_id=str(source.get("source_id") or ""),
            url=str(source.get("url") or ""),
            pillar=str(source.get("pillar") or ""),
            geography=str(source.get("geography") or ""),
            source_type=str(source.get("source_type") or ""),
            reliability_tier=str(source.get("reliability_tier") or ""),
            enabled=enabled,
            validation_ok=True,
            fetch_attempted=fetch_attempted,
            fetch_success=fetch_success,
            status_code=status_code,
            failure_reason=failure_reason,
            checked_at=checked,
            source_state=str(source.get("source_state") or ("enabled" if enabled else "disabled")),
        )
        report.append(row.__dict__)
    return report


def write_source_health_report(root: Path, report: list[dict[str, Any]], as_of_date: str) -> Path:
    path = root / "output" / "dispatches" / "american-pressure" / "source_health" / f"{as_of_date}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path
