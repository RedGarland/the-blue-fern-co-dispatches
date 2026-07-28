"""Private, deterministic findings exchanged between agents and Food Line intake."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .food_line_sources import canonical_url

TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source"}


def normalize_source_url(value: str) -> str:
    raw = str(value or "").strip()
    parts = urlsplit(raw)
    if parts.scheme.lower() != "https" or not parts.netloc:
        return ""
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in TRACKING_KEYS]
    return canonical_url(urlunsplit(("https", parts.netloc.lower(), parts.path, urlencode(query), "")))


def _text(value: Any) -> str:
    return str(value or "").strip()


def _slug(value: str) -> str:
    return re.sub(r"\s+", " ", _text(value).lower())


def duplicate_key_for(*, canonical_source_url: str, title: str, publisher: str) -> str:
    """Article identity; discovery time and agent run are deliberately excluded."""
    material = "|".join((_text(canonical_source_url), _slug(title), _slug(publisher)))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FoodLineAgentFinding:
    finding_id: str
    agent_name: str
    agent_run_id: str
    discovered_at: str
    source_url: str
    canonical_source_url: str
    publisher: str
    source_published_at: str
    title: str
    exact_supporting_passage: str
    summary: str
    location_name: str
    state: str
    location_scope: str
    affected_groups: list[str] = field(default_factory=list)
    pressure_type: str = ""
    confidence: str = ""
    source_role: str = ""
    evidence_level: str = ""
    agent_query_context: dict[str, Any] = field(default_factory=dict)
    duplicate_key: str = ""
    review_status: str = "pending_review"
    exclusion_reason: str = ""
    raw_agent_payload: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def finding_from_payload(payload: dict[str, Any], *, agent_name: str, agent_run_id: str, discovered_at: str) -> FoodLineAgentFinding:
    source = _text(payload.get("canonical_source_url") or payload.get("canonical_url") or payload.get("source_url") or payload.get("url"))
    canonical = normalize_source_url(source)
    title = _text(payload.get("title") or payload.get("headline"))
    publisher = _text(payload.get("publisher") or payload.get("source_name"))
    duplicate_key = duplicate_key_for(canonical_source_url=canonical, title=title, publisher=publisher)
    identity = "|".join((agent_name, agent_run_id, duplicate_key, _slug(payload.get("exact_supporting_passage") or payload.get("passage"))))
    finding_id = "finding_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    reason = "" if canonical and _text(payload.get("exact_supporting_passage") or payload.get("passage")) else "missing_traceable_source_or_supporting_passage"
    return FoodLineAgentFinding(
        finding_id=finding_id, agent_name=agent_name, agent_run_id=agent_run_id,
        discovered_at=discovered_at, source_url=_text(payload.get("source_url") or source),
        canonical_source_url=canonical, publisher=publisher,
        source_published_at=_text(payload.get("source_published_at") or payload.get("published_at") or payload.get("publication_date")),
        title=title, exact_supporting_passage=_text(payload.get("exact_supporting_passage") or payload.get("passage")),
        summary=_text(payload.get("summary") or payload.get("summary_or_snippet")),
        location_name=_text(payload.get("location_name") or payload.get("location")),
        state=_text(payload.get("state")).upper(), location_scope=_text(payload.get("location_scope")),
        affected_groups=[_text(v) for v in payload.get("affected_groups") or [] if _text(v)],
        pressure_type=_text(payload.get("pressure_type")), confidence=_text(payload.get("confidence")),
        source_role=_text(payload.get("source_role")), evidence_level=_text(payload.get("evidence_level")),
        agent_query_context=dict(payload.get("agent_query_context") or payload.get("query_context") or {}),
        duplicate_key=duplicate_key, review_status="pending_review", exclusion_reason=reason,
        raw_agent_payload=dict(payload),
    )


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
