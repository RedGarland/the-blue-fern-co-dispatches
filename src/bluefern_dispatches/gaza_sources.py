from __future__ import annotations

import email.utils
import gzip
import hashlib
import html
import json
import mimetypes
import os
import re
import subprocess
import string
import unicodedata
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit, unquote, quote_plus
from zoneinfo import ZoneInfo


REQUIRED_SOURCE_FIELDS = {
    "source_record_id",
    "title",
    "url",
    "publisher",
    "published_at",
    "retrieved_at",
    "summary_or_snippet",
    "source_type",
    "region_scope",
    "category_hint",
    "reliability_tier",
}
GAZA_TERMS = re.compile(r"\b(gaza|rafah|khan younis|deir al-balah|jabalia|palestinian territories|occupied palestinian territory)\b", re.I)
STRONG_GAZA_TERMS = re.compile(r"\b(gaza|palestin|unrwa|ocha|rafah|khan younis|deir al-balah|jabalia)\b", re.I)
PALESTINE_TERMS = re.compile(r"\b(palestin(e|ian|ians)?)\b", re.I)
PALESTINIAN_ANCHOR_TERMS = re.compile(
    r"\b(palestin(e|ian|ians)?|gaza|west bank|east jerusalem|unrwa|nakba|right of return|palestinian refugee(s)?)\b",
    re.I,
)
PALESTINIAN_DEVELOPMENT_TERMS = re.compile(
    r"\b(west bank|east jerusalem|palestinian refugee|refugee|unrwa|nakba|right of return|settler violence|detention|prisoner|civil rights|human rights|accountability)\b",
    re.I,
)
PALESTINIAN_POLICY_IMPACT_TERMS = re.compile(
    r"\b(settler violence|israeli policy|detention|prisoner|civil rights|human rights|accountability|legal|court|icc|icj|refugee|asylum|deport|refoulement)\b",
    re.I,
)
GAZA_CONTEXT_TERMS = re.compile(
    r"\b(gaza|israel|war|aid|humanitarian|unrwa|ocha|ceasefire|hostage|airstrike|hospital|famine|food|displacement|military)\b",
    re.I,
)
WEAK_ONLY_GAZA_PATTERNS = (
    "live",
    "live blog",
    "australia",
    "coal",
    "ev",
    "election",
    "budget",
    "domestic politics",
)
GAZA_LIVE_BLOG_MARKERS = ("live blog", "live updates", "as it happened")
GAZA_REGION_CONTEXT_TERMS = ("iran", "hormuz", "oman", "qatar", "lebanon", "syria", "yemen")
GAZA_IMPACT_TERMS = (
    "aid",
    "access",
    "food",
    "water",
    "fuel",
    "hospital",
    "clinic",
    "health",
    "crossing",
    "border",
    "displacement",
    "displaced",
    "reconstruction",
    "ceasefire",
    "truce",
    "hostage",
    "detainee",
    "prisoner",
    "evacuation",
    "shelter",
    "civilian",
    "funeral",
    "burial",
    "recovered",
    "remains",
    "bodies",
    "strike",
    "airstrike",
    "killed",
    "injured",
    "attack",
)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PLACEHOLDER_RE = re.compile(r"^(replace with|actual source|actual publisher|actual-source-url)", re.I)
WHITESPACE_RE = re.compile(r"\s+")
TAG_RE = re.compile(r"<[^>]+>")
PUNCT_TRANS = str.maketrans({char: " " for char in string.punctuation})
SMART_CHAR_TRANS = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\xa0": " ",
    }
)
TRACKING_QUERY_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "gclid",
    "fbclid",
    "ocid",
    "ref",
    "ref_src",
    "igshid",
}
TITLE_SOURCE_SUFFIX_HINTS = {
    "agency",
    "associated press",
    "bbc",
    "bbc news",
    "cnn",
    "english",
    "guardian",
    "haaretz",
    "jazeera",
    "jerusalem post",
    "monitor",
    "news",
    "pais",
    "post",
    "press",
    "reuters",
    "source",
    "times",
    "wire",
}

RANK_KEYWORDS = {
    "humanitarian_impact": ("humanitarian", "aid", "famine", "hunger", "shelter", "relief", "unrwa", "ocha", "unicef", "wfp", "who"),
    "aid_access": ("aid convoy", "crossing", "access", "corridor", "blockade", "entry", "distribution"),
    "ceasefire_diplomacy": ("ceasefire", "truce", "talks", "negotiation", "mediator", "diplom", "agreement"),
    "civilian_harm": ("civilian", "killed", "injured", "casualties", "strike", "bomb", "attack"),
    "displacement": ("displaced", "evacuation", "shelter", "camp", "refugee"),
    "health_infrastructure": ("hospital", "clinic", "water", "sanitation", "power", "electricity", "infrastructure", "disease"),
    "accountability_legal": ("icj", "icc", "un security council", "investigation", "legal", "court", "rights", "accountability"),
}

RELIABILITY_SCORES = {
    "official-humanitarian-source": 20,
    "official-public-source": 18,
    "reported-public-source": 14,
    "editorial-record": 10,
}
HIGH_RELEVANCE_KEYWORDS = (
    "gaza",
    "aid",
    "ceasefire",
    "negotiat",
    "hostage",
    "prisoner",
    "military",
    "civilian",
    "displace",
    "hospital",
    "famine",
    "food",
    "water",
    "fuel",
    "unrwa",
    "ocha",
    "who",
    "wfp",
    "unicef",
    "icc",
    "icj",
)
LOW_RELEVANCE_KEYWORDS = ("sports", "football", "soccer", "flag", "culture", "symbolic")
OPINION_COMMENTARY_URL_HINTS = (
    "/opinion/",
    "/opinions/",
    "/editorial/",
    "/editorials/",
    "/commentary/",
    "/commentaries/",
    "/column/",
    "/columns/",
    "/op-ed/",
    "/opeds/",
)
SOURCE_STATES = {"enabled", "diagnostics_only", "manual_only", "disabled"}
TLS_FAILURE_REASON = "tls_certificate_verification_failed"
LOS_ANGELES_TZ = ZoneInfo("America/Los_Angeles")
GOOGLE_NEWS_RSS_TEMPLATE = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"


@dataclass(frozen=True)
class SourceDefinition:
    source_id: str
    name: str
    url: str
    type: str
    enabled: bool
    publisher: str
    reliability_tier: str
    category_hint: str
    region_scope: str
    source_tier: str = "unspecified"
    source_group: str = "unspecified"
    discovery_role: str = "strong_ground_development"
    source_state: str = "enabled"
    disabled_reason: str = ""
    diagnostics_reason: str = ""
    query: str = ""


def has_palestinian_anchor_text(text: str) -> bool:
    return bool(PALESTINIAN_ANCHOR_TERMS.search(str(text or "")))


def is_palestinian_development_text(text: str) -> bool:
    haystack = str(text or "")
    if not has_palestinian_anchor_text(haystack):
        return False
    if any(token in haystack.lower() for token in LOW_RELEVANCE_KEYWORDS) and not PALESTINIAN_POLICY_IMPACT_TERMS.search(haystack):
        return False
    if PALESTINIAN_DEVELOPMENT_TERMS.search(haystack):
        return True
    if PALESTINIAN_POLICY_IMPACT_TERMS.search(haystack):
        return True
    return bool(re.search(r"\b(west bank|east jerusalem|unrwa|nakba|right of return)\b", haystack, re.I))


def _gaza_relevance_profile(item: dict[str, str], source: SourceDefinition | None = None) -> dict[str, Any]:
    title = clean_feed_text(item.get("title", ""))
    summary = clean_feed_text(item.get("summary_or_snippet", ""))
    text = " ".join([title, summary]).strip()
    lowered = text.lower()
    gaza_anchor = bool(GAZA_TERMS.search(text))
    palestinian_anchor = bool(PALESTINE_TERMS.search(text) or has_palestinian_anchor_text(text))
    west_bank_anchor = bool(re.search(r"\b(west bank|east jerusalem|settler(?:s| violence)?|settlement(?:s)?)\b", text, re.I))
    regional_anchor = bool(re.search(r"\b(" + "|".join(GAZA_REGION_CONTEXT_TERMS) + r")\b", text, re.I))
    live_blog = any(marker in lowered for marker in GAZA_LIVE_BLOG_MARKERS) or "as it happened" in lowered
    impact_anchor = any(term in lowered for term in GAZA_IMPACT_TERMS)
    incidental_anchor = any(marker in lowered for marker in WEAK_ONLY_GAZA_PATTERNS if marker in lowered)
    explicit_gaza_consequence = gaza_anchor and (impact_anchor or palestinian_anchor)
    inherited_scope = bool(str(item.get("region_scope") or "").strip()) or (
        source is not None and bool(str(source.region_scope or "").strip())
    )
    if live_blog and (incidental_anchor or not explicit_gaza_consequence):
        return {
            "accepted": False,
            "reason": "live_blog_incidental_gaza_reference",
            "scope_provenance": "inherited_collection_scope" if inherited_scope else "uncertain",
            "nexus_type": "live_blog_incidental",
        }
    if not gaza_anchor:
        if west_bank_anchor:
            reason = "west_bank_without_gaza_impact"
        elif inherited_scope:
            reason = "inherited_scope_only"
        elif regional_anchor:
            reason = "regional_context_only"
        elif palestinian_anchor:
            reason = "no_demonstrated_gaza_nexus"
        elif incidental_anchor:
            reason = "no_demonstrated_gaza_nexus"
        else:
            reason = "no_demonstrated_gaza_nexus"
        return {
            "accepted": False,
            "reason": reason,
            "scope_provenance": "inherited_collection_scope" if inherited_scope else "uncertain",
            "nexus_type": "no_gaza_anchor",
        }
    if not explicit_gaza_consequence and live_blog:
        return {
            "accepted": False,
            "reason": "live_blog_incidental_gaza_reference",
            "scope_provenance": "article_evidence",
            "nexus_type": "live_blog_incidental",
        }
    if palestinian_anchor and not impact_anchor and not regional_anchor:
        reason = "palestinian_development_material"
        provenance = "article_evidence"
    elif regional_anchor and impact_anchor:
        reason = "regional_gaza_consequence"
        provenance = "article_evidence"
    elif impact_anchor:
        reason = "explicit_gaza_impact"
        provenance = "article_evidence"
    else:
        reason = "direct_gaza_development"
        provenance = "article_evidence"
    return {
        "accepted": True,
        "reason": reason,
        "scope_provenance": provenance,
        "nexus_type": "gaza_evidence",
    }


def _looks_like_google_news_wrapper(url: str) -> bool:
    text = str(url or "").lower()
    return "news.google.com" in text and "/rss/articles/" in text


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_feed_text(value: str) -> str:
    raw = str(value or "")
    decoded = html.unescape(raw).translate(SMART_CHAR_TRANS)
    stripped = TAG_RE.sub(" ", decoded)
    stripped = re.sub(r"https?://\S+", " ", stripped)
    stripped = stripped.replace("Continue reading...", " ")
    stripped = stripped.replace("Continue reading", " ")
    return WHITESPACE_RE.sub(" ", stripped).strip()


def _fold_diacritics(value: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFKD", str(value or "")) if not unicodedata.combining(char))


def _normalize_fingerprint_text(value: str) -> str:
    folded = _fold_diacritics(html.unescape(str(value or "")).translate(SMART_CHAR_TRANS)).lower()
    folded = folded.translate(PUNCT_TRANS)
    return WHITESPACE_RE.sub(" ", folded).strip()


def _looks_like_source_suffix(value: str) -> bool:
    normalized = _normalize_fingerprint_text(value)
    if not normalized:
        return False
    tokens = normalized.split()
    if len(tokens) > 5:
        return False
    joined = " ".join(tokens)
    return joined in TITLE_SOURCE_SUFFIX_HINTS or any(token in TITLE_SOURCE_SUFFIX_HINTS for token in tokens)


def _strip_trailing_source_suffix(title: str, publisher: str = "") -> str:
    value = html.unescape(str(title or "")).translate(SMART_CHAR_TRANS).strip()
    if not value:
        return ""
    publisher_norm = _normalize_fingerprint_text(publisher)
    for separator in (" | ", " - "):
        head, matched, tail = value.rpartition(separator)
        if not matched:
            continue
        tail_norm = _normalize_fingerprint_text(tail)
        if not tail_norm:
            continue
        if publisher_norm and (tail_norm == publisher_norm or tail_norm in publisher_norm or publisher_norm in tail_norm):
            return head.strip()
        if _looks_like_source_suffix(tail_norm):
            return head.strip()
    return value


def normalize_title(title: str, publisher: str = "") -> str:
    value = _normalize_fingerprint_text(_strip_trailing_source_suffix(title, publisher=publisher))
    value = value.translate(PUNCT_TRANS)
    return WHITESPACE_RE.sub(" ", value).strip()


def normalize_publisher(publisher: str) -> str:
    value = _normalize_fingerprint_text(publisher)
    value = re.sub(r"\b(news|media|wire|agency|press|the)\b", " ", value)
    return WHITESPACE_RE.sub(" ", value).strip()


def canonicalize_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw.lower()
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    path = re.sub(r"/{2,}", "/", parts.path or "/").rstrip("/") or "/"
    query_pairs = parse_qsl(parts.query, keep_blank_values=False)
    filtered = [
        (key, value)
        for key, value in query_pairs
        if key.lower() not in TRACKING_QUERY_KEYS and not key.lower().startswith("utm_")
    ]
    query = urlencode(filtered)
    return urlunsplit((scheme, netloc, path, query, ""))


def build_google_news_rss_url(query: str) -> str:
    text = str(query or "").strip()
    if not text:
        return ""
    return GOOGLE_NEWS_RSS_TEMPLATE.format(query=quote_plus(text))


def extract_canonical_from_google_wrapper(url: str) -> tuple[str, str]:
    raw = str(url or "").strip()
    if not _looks_like_google_news_wrapper(raw):
        return "", "not_wrapper"
    try:
        parts = urlsplit(raw)
    except ValueError:
        return "", "invalid_wrapper_url"
    query_pairs = dict(parse_qsl(parts.query, keep_blank_values=False))
    candidate = ""
    for key in ("url", "u", "q"):
        if key in query_pairs:
            candidate = unquote(str(query_pairs.get(key) or "")).strip()
            break
    if not candidate.startswith(("http://", "https://")):
        return "", "wrapper_without_extractable_canonical"
    return canonicalize_url(candidate), "resolved_from_query"


def canonical_source_key(source: dict[str, Any]) -> dict[str, str]:
    title = normalize_title(str(source.get("title") or ""), publisher=str(source.get("publisher") or ""))
    publisher = normalize_publisher(str(source.get("publisher") or ""))
    original_url = str(source.get("url") or "")
    explicit_canonical = str(source.get("canonical_url") or "").strip()
    extracted_canonical, _status = extract_canonical_from_google_wrapper(original_url)
    unresolved_google_wrapper = _looks_like_google_news_wrapper(original_url) and not extracted_canonical and not explicit_canonical
    canonical_candidate = explicit_canonical or extracted_canonical or ("" if unresolved_google_wrapper else original_url)
    canonical_url = canonicalize_url(canonical_candidate)
    normalized_url = canonicalize_url(original_url)
    if unresolved_google_wrapper:
        normalized_url = ""
    publisher_title = f"{publisher}|{title}" if publisher and title else ""
    duplicate_fingerprint = story_claim_fingerprint(
        {
            **source,
            "title": title,
            "publisher": publisher,
            "canonical_url": canonical_url,
        }
    )
    return {
        "canonical_url": canonical_url,
        "normalized_url": normalized_url,
        "publisher_title": publisher_title,
        "title_fingerprint": title,
        "duplicate_fingerprint": duplicate_fingerprint,
    }


def story_claim_fingerprint(source_or_story: dict[str, Any]) -> str:
    title = normalize_title(str(source_or_story.get("title") or ""), publisher=str(source_or_story.get("publisher") or ""))
    publisher = normalize_publisher(str(source_or_story.get("publisher") or ""))
    category = normalize_title(str(source_or_story.get("category_hint") or source_or_story.get("category") or ""))
    dispatch = normalize_title(str(source_or_story.get("dispatch_slug") or source_or_story.get("dispatch") or "gaza"))
    published_day = ""
    published_at = _safe_parse_dt(str(source_or_story.get("published_at") or ""))
    if published_at is not None:
        published_day = published_at.date().isoformat()
    return "|".join(part for part in (dispatch, publisher, title, category, published_day) if part)


def is_labeled_context_source(item: dict[str, Any], source: SourceDefinition | None = None) -> bool:
    attribution_mode = str(item.get("attribution_mode") or "").strip().lower()
    claim_status = str(item.get("claim_status") or "").strip().lower()
    if attribution_mode == "gaza_adjacent_context" or claim_status == "gaza_adjacent_context":
        return True
    if source is not None:
        source_state = str(source.source_state or "").strip().lower()
        if source_state == "manual_only" and str(source.diagnostics_reason or "").strip().lower() == "context_only":
            return True
    return False


def is_opinion_editorial_commentary_url(item: dict[str, Any]) -> bool:
    url = str(item.get("url") or "").strip().lower()
    if not url:
        return False
    return any(hint in url for hint in OPINION_COMMENTARY_URL_HINTS)


def gaza_story_selection_exclusion_reason(item: dict[str, Any], source: SourceDefinition | None = None) -> str | None:
    if is_opinion_editorial_commentary_url(item):
        if is_labeled_context_source(item, source):
            return "opinion/editorial/commentary source retained as labeled context"
        return "opinion/editorial/commentary source excluded from Gaza story selection"
    return None


def _safe_parse_dt(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    candidate = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_google_news_rss(url: str) -> bool:
    text = str(url or "").lower()
    return "news.google.com" in text and "/rss/" in text


def _iter_prior_source_manifests(root: Path, edition_date: str, lookback_days: int) -> list[tuple[str, Path]]:
    target = date.fromisoformat(edition_date)
    manifests: list[tuple[str, Path]] = []
    for days_back in range(1, lookback_days + 1):
        prior = (target - timedelta(days=days_back)).isoformat()
        for manifest in (
            root / "output" / "dispatches" / "gaza" / "editions" / prior / "sources_manifest.json",
            root / "output" / "site" / "gaza" / "editions" / prior / "sources_manifest.json",
            root / "bluefern-dispatches-pages" / "gaza" / "editions" / prior / "sources_manifest.json",
        ):
            if manifest.exists():
                manifests.append((prior, manifest))
    return manifests


def _recent_duplicate_override(source: dict[str, Any]) -> bool:
    return any(
        bool(source.get(field))
        for field in ("allow_recent_duplicate_story", "materially_new_reporting", "material_update_override")
    )


def filter_recent_duplicate_sources(
    root: Path,
    edition_date: str,
    candidates: list[dict[str, Any]],
    lookback_days: int = 7,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    seen_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    checked_editions: set[str] = set()
    for prior_date, manifest_path in _iter_prior_source_manifests(root, edition_date, lookback_days):
        checked_editions.add(prior_date)
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            continue
        for prior in payload:
            if not isinstance(prior, dict):
                continue
            keys = canonical_source_key(prior)
            claim = story_claim_fingerprint(prior)
            for key_type in ("canonical_url", "normalized_url", "publisher_title", "title_fingerprint", "duplicate_fingerprint"):
                value = keys.get(key_type) or ""
                if value:
                    seen_by_key.setdefault((key_type, value), {"edition_date": prior_date, "source": prior, "key_type": key_type})
            if claim:
                seen_by_key.setdefault(("claim_fingerprint", claim), {"edition_date": prior_date, "source": prior, "key_type": "claim_fingerprint"})

    annotated: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    stale_risk: list[dict[str, Any]] = []
    for source in candidates:
        keys = canonical_source_key(source)
        claim = story_claim_fingerprint(source)
        source["dedupe_key"] = keys.get("duplicate_fingerprint") or keys.get("publisher_title") or keys.get("canonical_url") or keys.get("title_fingerprint") or ""
        source["title_fingerprint"] = keys.get("title_fingerprint") or ""
        source["claim_fingerprint"] = claim
        if keys.get("canonical_url"):
            source["canonical_url"] = keys.get("canonical_url") or ""
        if _looks_like_google_news_wrapper(str(source.get("url") or "")):
            source["wrapper_url"] = str(source.get("wrapper_url") or source.get("url") or "")

        matches: list[tuple[str, str, dict[str, Any]]] = []
        for key_type in ("canonical_url", "normalized_url", "publisher_title", "title_fingerprint", "duplicate_fingerprint"):
            value = keys.get(key_type) or ""
            if value and (key_type, value) in seen_by_key:
                matches.append((key_type, value, seen_by_key[(key_type, value)]))
        if claim and ("claim_fingerprint", claim) in seen_by_key:
            matches.append(("claim_fingerprint", claim, seen_by_key[("claim_fingerprint", claim)]))

        if not matches:
            annotated.append(source)
            continue

        published_at = _safe_parse_dt(str(source.get("published_at") or ""))
        suppress = True
        reason = "matched recent prior edition"
        matched = matches[0]
        prior_src = matched[2]["source"]
        prior_published_at = _safe_parse_dt(str(prior_src.get("published_at") or ""))
        if _recent_duplicate_override(source):
            suppress = False
            reason = "explicit material update override"
        elif published_at and prior_published_at and published_at > prior_published_at and matched[0] in {"canonical_url", "normalized_url", "duplicate_fingerprint", "claim_fingerprint"}:
            suppress = False
            reason = "newer publication timestamp than prior url match"
        if str(source.get("published_at") or "").strip() == "":
            stale_risk.append({"title": source.get("title"), "publisher": source.get("publisher"), "url": source.get("url")})
        if suppress:
            source["repeated_from_edition_date"] = matched[2]["edition_date"]
            source["story_selection_excluded_reason"] = "duplicate_recent_story"
            source["prior_duplicate_edition_date"] = matched[2]["edition_date"]
            source["prior_duplicate_source_record_id"] = str(prior_src.get("source_record_id") or "").strip()
            suppressed.append(
                {
                    "source_record_id": source.get("source_record_id"),
                    "title": source.get("title"),
                    "publisher": source.get("publisher"),
                    "url": source.get("url"),
                    "published_at": source.get("published_at"),
                    "retrieved_at": source.get("retrieved_at"),
                    "matched_prior_edition": matched[2]["edition_date"],
                    "matched_prior_source_record_id": str(prior_src.get("source_record_id") or "").strip(),
                    "matched_key_type": matched[0],
                    "matched_prior_title": prior_src.get("title"),
                    "matched_prior_url": prior_src.get("url"),
                    "reason": reason,
                    "story_selection_excluded_reason": "duplicate_recent_story",
                }
            )
        annotated.append(source)

    report = {
        "edition_date": edition_date,
        "lookback_days": lookback_days,
        "prior_editions_checked": sorted(checked_editions),
        "input_candidate_count": len(candidates),
        "kept_candidate_count": sum(1 for source in annotated if not str(source.get("story_selection_excluded_reason") or "").strip()),
        "suppressed_candidate_count": len(suppressed),
        "suppressed_candidates": suppressed,
        "stale_risk_candidates": stale_risk,
        "warnings": [],
        "google_wrapper_count": sum(
            1
            for source in candidates
            if bool(source.get("wrapper_url")) or _is_google_news_rss(str(source.get("url") or ""))
        ),
        "canonical_publisher_url_count": sum(
            1
            for source in annotated
            if str(source.get("canonical_url") or "").strip() and not str(source.get("story_selection_excluded_reason") or "").strip()
        ),
    }
    if candidates and not report["kept_candidate_count"]:
        report["warnings"].append("all candidates were suppressed as repeated or stale-risk")
    return annotated, report


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _parse_scalar(value: str) -> Any:
    value = _strip_quotes(value)
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    return value


def _minimal_yaml_load(text: str) -> dict[str, Any]:
    """Load the small list-of-maps schema used by data/dispatches/gaza/sources.yml."""
    result: dict[str, Any] = {}
    current_key: str | None = None
    current_tier: str | None = None
    current_item: dict[str, Any] | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" ") and line.endswith(":"):
            current_key = line[:-1].strip()
            result[current_key] = [] if current_key != "tiers" else {}
            current_tier = None
            current_item = None
            continue
        if current_key == "tiers" and line.startswith("  ") and line.strip().endswith(":") and not line.strip().startswith("- "):
            current_tier = line.strip()[:-1].strip()
            tiers = result.setdefault("tiers", {})
            if isinstance(tiers, dict):
                tiers[current_tier] = []
            current_item = None
            continue
        stripped = line.strip()
        if stripped.startswith("- "):
            if current_key is None:
                raise ValueError("YAML item found before a list key")
            current_item = {}
            if current_key == "tiers":
                tiers = result.setdefault("tiers", {})
                if not isinstance(tiers, dict) or not current_tier:
                    raise ValueError("tiers entry found before tier key")
                tier_rows = tiers.setdefault(current_tier, [])
                if not isinstance(tier_rows, list):
                    raise ValueError("tiers row is not a list")
                tier_rows.append(current_item)
            else:
                result[current_key].append(current_item)
            rest = stripped[2:].strip()
            if rest:
                key, value = rest.split(":", 1)
                current_item[key.strip()] = _parse_scalar(value.strip())
            continue
        if current_item is not None and ":" in stripped:
            key, value = stripped.split(":", 1)
            current_item[key.strip()] = _parse_scalar(value.strip())
            continue
        raise ValueError(f"Unsupported YAML line: {raw_line}")
    return result


def load_sources_config(path: Path) -> list[SourceDefinition]:
    if not path.exists():
        raise FileNotFoundError(f"Gaza sources config does not exist: {path}")
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        payload = yaml.safe_load(text)
    except Exception:
        payload = _minimal_yaml_load(text)
    raw_sources = payload.get("sources") if isinstance(payload, dict) else None
    if isinstance(payload, dict) and isinstance(payload.get("tiers"), dict):
        expanded: list[dict[str, Any]] = []
        for tier_name, entries in payload["tiers"].items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                item = dict(entry)
                item.setdefault("source_tier", str(tier_name))
                expanded.append(item)
        raw_sources = list(raw_sources or []) + expanded
    if not isinstance(raw_sources, list):
        raise ValueError("sources.yml must contain a sources list")
    definitions: list[SourceDefinition] = []
    for index, item in enumerate(raw_sources, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"sources.yml item {index} is not an object")
        source_type = str(item.get("type") or "").strip().lower()
        if source_type == "rss_or_page":
            source_type = "rss"
        source_state = str(item.get("source_state") or "").strip().lower()
        if not source_state:
            source_state = "enabled" if bool(item.get("enabled")) else "disabled"
        if source_state not in SOURCE_STATES:
            raise ValueError(f"sources.yml item {index} has unsupported source_state: {source_state}")
        enabled = source_state == "enabled"
        definitions.append(
            SourceDefinition(
                source_id=str(item.get("source_id") or item.get("id") or "").strip(),
                name=str(item.get("name") or "").strip(),
                url=str(item.get("url") or "").strip(),
                query=str(item.get("query") or "").strip(),
                type=source_type,
                enabled=enabled,
                publisher=str(item.get("publisher") or item.get("name") or "").strip(),
                reliability_tier=str(item.get("reliability_tier") or "").strip(),
                category_hint=str(item.get("category_hint") or "").strip(),
                region_scope=str(item.get("region_scope") or "").strip(),
                source_tier=str(item.get("source_tier") or item.get("tier") or "unspecified").strip(),
                source_group=str(item.get("source_group") or item.get("group") or "unspecified").strip(),
                discovery_role=str(item.get("discovery_role") or "strong_ground_development").strip() or "strong_ground_development",
                source_state=source_state,
                disabled_reason=str(item.get("disabled_reason") or "").strip(),
                diagnostics_reason=str(item.get("diagnostics_reason") or "").strip(),
            )
        )
    return definitions


def parse_feed_date(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(value[: len(fmt)], fmt).replace(tzinfo=timezone.utc)
            return parsed.isoformat()
        except Exception:
            continue
    return value


def _text(element: ET.Element, child_name: str) -> str:
    child = element.find(child_name)
    if child is not None and child.text:
        return child.text.strip()
    for item in element:
        if item.tag.lower().endswith(child_name.lower()) and item.text:
            return item.text.strip()
    return ""


def _is_tls_error(exc_text: str) -> bool:
    lowered = str(exc_text or "").lower()
    return (
        "certificate_verify_failed" in lowered
        or "ssl" in lowered
        or "tls" in lowered
        or "certificate verification" in lowered
        or "schannel" in lowered
        or "sec_e_" in lowered
        or "acquirecredentialshandle failed" in lowered
    )


def _curl_fetch(url: str, timeout: int, allow_no_revoke: bool) -> tuple[bytes, str]:
    cmd = ["curl.exe", "--silent", "--show-error", "--location", "--max-time", str(timeout), "--fail", url]
    if allow_no_revoke:
        cmd.insert(1, "--ssl-no-revoke")
    proc = subprocess.run(cmd, capture_output=True, text=False, check=False)
    if proc.returncode != 0:
        stderr_text = (proc.stderr or b"").decode("utf-8", errors="replace")
        raise RuntimeError(f"curl_failed(rc={proc.returncode}): {stderr_text.strip()}")
    return proc.stdout or b"", "curl"


def fetch_feed_payload(source_id: str, url: str, timeout: int = 20) -> dict[str, Any]:
    backend_pref = str(os.getenv("GAZA_FETCH_BACKEND", "auto") or "auto").strip().lower()
    allow_no_revoke = str(os.getenv("GAZA_ALLOW_CURL_NO_REVOKE", "")).strip() == "1"

    def _python_fetch() -> dict[str, Any]:
        request = urllib.request.Request(url, headers={"User-Agent": "BlueFernDispatches/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return {
                    "ok": True,
                    "source_id": source_id,
                    "url": url,
                    "status_code": int(getattr(response, "status", 200) or 200),
                    "failure_reason": None,
                    "exception_type": None,
                    "tls_error": False,
                    "backend_used": "python",
                    "content_type": str(response.headers.get("Content-Type") or ""),
                    "content_encoding": str(response.headers.get("Content-Encoding") or ""),
                    "content_bytes": response.read(),
                    "content_text": None,
                }
        except urllib.error.HTTPError as exc:
            return {
                "ok": False,
                "source_id": source_id,
                "url": url,
                "status_code": int(exc.code),
                "failure_reason": f"HTTPError: {exc}",
                "exception_type": type(exc).__name__,
                "tls_error": False,
                "backend_used": "python",
                "content_bytes": None,
                "content_text": None,
            }
        except Exception as exc:  # noqa: BLE001
            tls_error = _is_tls_error(str(exc))
            return {
                "ok": False,
                "source_id": source_id,
                "url": url,
                "status_code": None,
                "failure_reason": TLS_FAILURE_REASON if tls_error else f"{type(exc).__name__}: {exc}",
                "exception_type": type(exc).__name__,
                "tls_error": tls_error,
                "backend_used": "python",
                "content_bytes": None,
                "content_text": None,
            }

    if backend_pref not in {"auto", "python", "curl"}:
        backend_pref = "auto"

    result = _python_fetch() if backend_pref in {"auto", "python"} else None
    if result is not None and result["ok"]:
        return result
    if backend_pref == "python":
        return result or {}

    if backend_pref == "curl" or (backend_pref == "auto" and bool(result and result.get("tls_error"))):
        try:
            body, backend = _curl_fetch(url, timeout=timeout, allow_no_revoke=allow_no_revoke)
            return {
                "ok": True,
                "source_id": source_id,
                "url": url,
                "status_code": 200,
                "failure_reason": None,
                "exception_type": None,
                "tls_error": False,
                "backend_used": backend,
                "content_type": "",
                "content_encoding": "",
                "content_bytes": body,
                "content_text": None,
            }
        except Exception as exc:  # noqa: BLE001
            failure = str(exc)
            tls_error = _is_tls_error(failure)
            return {
                "ok": False,
                "source_id": source_id,
                "url": url,
                "status_code": None,
                "failure_reason": TLS_FAILURE_REASON if tls_error else failure,
                "exception_type": type(exc).__name__,
                "tls_error": tls_error,
                "backend_used": "curl",
                "content_bytes": None,
                "content_text": None,
            }

    return result or {
        "ok": False,
        "source_id": source_id,
        "url": url,
        "status_code": None,
        "failure_reason": "unknown_fetch_failure",
        "exception_type": "RuntimeError",
        "tls_error": False,
        "backend_used": "python",
        "content_bytes": None,
        "content_text": None,
    }


def parse_rss_items(content: bytes, content_type: str = "", content_encoding: str = "") -> list[dict[str, str]]:
    data = content
    ctype = str(content_type or "").lower()
    cenc = str(content_encoding or "").lower()
    if cenc == "gzip" or data.startswith(b"\x1f\x8b"):
        data = gzip.decompress(data)
    stripped = data.lstrip(b"\xef\xbb\xbf\r\n\t ")
    if not stripped:
        raise ValueError("empty feed response")
    if not stripped.startswith(b"<"):
        guessed = mimetypes.guess_extension(ctype.split(";", 1)[0].strip()) if ctype else None
        detail = f"content-type={ctype}" if ctype else "response does not start with XML"
        if guessed:
            detail = f"{detail}, guessed_extension={guessed}"
        raise ValueError(f"non-XML feed response ({detail})")
    root = ET.fromstring(data)
    items = root.findall(".//item")
    if not items:
        items = root.findall(".//{http://www.w3.org/2005/Atom}entry")
    records: list[dict[str, str]] = []
    for item in items:
        title = _text(item, "title")
        link = _text(item, "link")
        if not link:
            link_el = item.find("{http://www.w3.org/2005/Atom}link")
            if link_el is not None:
                link = str(link_el.attrib.get("href") or "").strip()
        records.append(
            {
                "title": title,
                "url": link,
                "published_at": parse_feed_date(_text(item, "pubDate") or _text(item, "published") or _text(item, "updated")),
                "summary_or_snippet": _text(item, "description") or _text(item, "summary"),
            }
        )
    return records


def fetch_rss_items(url: str, timeout: int = 20) -> list[dict[str, str]]:
    payload = fetch_feed_payload("unknown", url, timeout=timeout)
    if not payload.get("ok"):
        raise RuntimeError(str(payload.get("failure_reason") or "feed_fetch_failed"))
    return parse_rss_items(
        payload.get("content_bytes") or b"",
        content_type=str(payload.get("content_type") or ""),
        content_encoding=str(payload.get("content_encoding") or ""),
    )


def gaza_relevance_decision(item: dict[str, str], source: SourceDefinition | None = None) -> tuple[bool, str]:
    profile = _gaza_relevance_profile(item, source)
    return bool(profile["accepted"]), str(profile["reason"])


def gaza_relevance_profile(item: dict[str, str], source: SourceDefinition | None = None) -> dict[str, Any]:
    return _gaza_relevance_profile(item, source)


def _parse_metadata_dt(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
    candidate = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _format_local(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(LOS_ANGELES_TZ).isoformat(timespec="seconds")


def _scheduled_run_local_dt(edition_date: str) -> datetime:
    target = date.fromisoformat(edition_date)
    return datetime.combine(target, time(6, 0), tzinfo=LOS_ANGELES_TZ)


def build_gaza_collection_timing_metadata(
    sources: list[dict[str, Any]],
    edition_date: str,
    actual_run_utc: str | None = None,
) -> dict[str, Any]:
    scheduled_run_local_dt = _scheduled_run_local_dt(edition_date)
    actual_run_dt = _parse_metadata_dt(actual_run_utc) or datetime.now(timezone.utc)
    published_datetimes = [dt for dt in (_parse_metadata_dt(str(source.get("published_at") or "")) for source in sources) if dt is not None]
    retrieved_datetimes = [dt for dt in (_parse_metadata_dt(str(source.get("retrieved_at") or "")) for source in sources) if dt is not None]
    retrieval_batches: list[dict[str, Any]] = []
    later_same_day_batches: list[dict[str, Any]] = []
    post_edition_date_batches: list[dict[str, Any]] = []
    same_day_baseline_local_dt: datetime | None = None
    batches: dict[str, list[str]] = {}
    for source in sources:
        batch_key = str(source.get("retrieved_at") or "").strip()
        if not batch_key:
            continue
        batches.setdefault(batch_key, []).append(str(source.get("source_record_id") or ""))
    for batch_key, source_ids in sorted(
        batches.items(),
        key=lambda item: (_parse_metadata_dt(item[0]) or datetime.max.replace(tzinfo=timezone.utc), item[0]),
    ):
        batch_time = _parse_metadata_dt(batch_key)
        batch_time_local = batch_time.astimezone(LOS_ANGELES_TZ) if batch_time is not None else None
        batch_date_local = batch_time_local.date().isoformat() if batch_time_local is not None else None
        if batch_time_local is not None and batch_date_local == edition_date:
            if same_day_baseline_local_dt is None or batch_time_local < same_day_baseline_local_dt:
                same_day_baseline_local_dt = batch_time_local
        if batch_time_local is not None and batch_time_local.date() > scheduled_run_local_dt.date():
            classification = "post_edition_date_update"
        elif (
            batch_time_local is not None
            and batch_time_local.date() == scheduled_run_local_dt.date()
            and same_day_baseline_local_dt is not None
            and batch_time_local > same_day_baseline_local_dt
        ):
            classification = "later_same_day_update"
        else:
            classification = "scheduled_or_pre_scheduled_batch"
        retrieval_batches.append(
            {
                "retrieved_at": _format_utc(batch_time) or batch_key,
                "retrieved_at_local": _format_local(batch_time),
                "retrieved_local_date": batch_date_local,
                "batch_classification": classification,
                "source_record_ids": [source_id for source_id in source_ids if source_id],
                "source_count": len([source_id for source_id in source_ids if source_id]),
            }
        )
        if classification == "later_same_day_update":
            later_same_day_batches.append(retrieval_batches[-1])
        elif classification == "post_edition_date_update":
            post_edition_date_batches.append(retrieval_batches[-1])
    first_retrieved = min(retrieved_datetimes) if retrieved_datetimes else None
    last_retrieved = max(retrieved_datetimes) if retrieved_datetimes else None
    source_window_start = min(published_datetimes) if published_datetimes else None
    source_window_end = max(published_datetimes) if published_datetimes else None
    later_same_day_update_batch_count = len(later_same_day_batches)
    later_same_day_update_source_count = sum(int(batch.get("source_count") or 0) for batch in later_same_day_batches)
    post_edition_date_update_batch_count = len(post_edition_date_batches)
    post_edition_date_update_source_count = sum(int(batch.get("source_count") or 0) for batch in post_edition_date_batches)
    return {
        "scheduled_run_local_time": _format_local(scheduled_run_local_dt),
        "actual_run_local_time": _format_local(actual_run_dt),
        "source_window_start_utc": _format_utc(source_window_start),
        "source_window_end_utc": _format_utc(source_window_end),
        "first_source_retrieved_at": _format_utc(first_retrieved),
        "last_source_retrieved_at": _format_utc(last_retrieved),
        "contains_later_same_day_update": later_same_day_update_source_count > 0,
        "later_same_day_update_count": later_same_day_update_source_count,
        "later_same_day_update_batch_count": later_same_day_update_batch_count,
        "later_same_day_update_source_count": later_same_day_update_source_count,
        "contains_post_edition_date_update": post_edition_date_update_source_count > 0,
        "post_edition_date_update_count": post_edition_date_update_source_count,
        "post_edition_date_update_batch_count": post_edition_date_update_batch_count,
        "post_edition_date_update_source_count": post_edition_date_update_source_count,
        "post_edition_date_retrieval_batches": post_edition_date_batches,
        "retrieval_batches": retrieval_batches,
    }


def _matched_terms(item: dict[str, str]) -> list[str]:
    text = " ".join(
        [
            clean_feed_text(item.get("title", "")),
            clean_feed_text(item.get("summary_or_snippet", "")),
            str(item.get("url") or ""),
        ]
    ).lower()
    terms = [
        "gaza",
        "palestine",
        "unrwa",
        "ocha",
        "ceasefire",
        "aid",
        "hospital",
        "famine",
        "food",
        "displacement",
        "hostage",
        "airstrike",
        "military",
    ]
    return [term for term in terms if term in text][:8]


def _date_basis(published_at: str, start_date: date, end_date: date) -> str:
    text = str(published_at or "").strip()
    if not text:
        return "missing_published_at"
    match = re.match(r"^(\d{4}-\d{2}-\d{2})", text)
    if not match:
        return "weak_date_basis"
    try:
        parsed = date.fromisoformat(match.group(1))
    except ValueError:
        return "weak_date_basis"
    if start_date <= parsed <= end_date:
        return "in_window"
    return "out_of_window"


def is_gaza_relevant(item: dict[str, str], source: SourceDefinition | None = None) -> bool:
    accepted, _reason = gaza_relevance_decision(item, source)
    return accepted


def is_on_requested_date(published_at: str, edition_date: str) -> bool:
    if not published_at:
        return True
    match = re.match(r"^(\d{4}-\d{2}-\d{2})", published_at)
    if not match:
        return True
    return match.group(1) == edition_date


def _date_in_window(published_at: str, start_date: date, end_date: date) -> bool:
    text = str(published_at or "").strip()
    if not text:
        return False
    match = re.match(r"^(\d{4}-\d{2}-\d{2})", text)
    if not match:
        return False
    try:
        published = date.fromisoformat(match.group(1))
    except ValueError:
        return False
    return start_date <= published <= end_date


def source_record_id(source_id: str, title: str, url: str, edition_date: str) -> str:
    digest = hashlib.sha1(f"{source_id}|{title}|{url}".encode("utf-8")).hexdigest()[:12]
    return f"gaza-{edition_date}-{source_id}-{digest}"


def _governance_traceability_note(
    *,
    publisher: str,
    published_at: str,
    url: str,
    canonical_url: str = "",
    wrapper_url: str = "",
) -> tuple[str, str | None]:
    if not url:
        return "", "source record missing a source URL"
    if not url.startswith(("http://", "https://")):
        return "", f"source record has invalid URL: {url}"
    publisher_name = str(publisher or "").strip() or "the source"
    if wrapper_url:
        if not canonical_url:
            return "", "source record uses an unresolved Google News wrapper URL"
        note = f"Traceable to {publisher_name} via a Google News RSS wrapper and resolved canonical publisher URL {canonical_url}"
        if published_at:
            note = f"{note} dated {published_at}"
        return f"{note}; title, publisher, URL, canonical_url, and published_at are preserved in the record.", None
    note = f"Traceable to {publisher_name} via a direct publisher URL"
    if published_at:
        note = f"{note} dated {published_at}"
    return f"{note}; title, publisher, URL, and published_at are preserved in the record.", None


def _governance_attribution_mode(source: SourceDefinition, url: str) -> str:
    reliability = str(source.reliability_tier or "").strip().lower()
    source_group = str(source.source_group or "").strip().lower()
    source_tier = str(source.source_tier or "").strip().lower()
    publisher = str(source.publisher or "").strip().lower()
    if "official-humanitarian-source" in reliability or source_group == "institutional" or source_tier == "official_humanitarian":
        return "official_humanitarian"
    if "jerusalem post" in publisher or "jpost" in publisher:
        return "gaza_adjacent_context"
    if "news.google.com" in str(url or "").lower():
        return "reported_public_source"
    return "reported_public_source"


def normalize_rss_item(item: dict[str, str], source: SourceDefinition, edition_date: str, retrieved_at: str) -> dict[str, Any] | None:
    title = clean_feed_text(item.get("title", ""))
    url = (item.get("url") or "").strip()
    if not title or not url or not url.startswith(("http://", "https://")):
        return None
    published_at = (item.get("published_at") or "").strip()
    relevance_profile = _gaza_relevance_profile(
        {"title": title, "summary_or_snippet": clean_feed_text(item.get("summary_or_snippet", "")), "url": url},
        source,
    )
    if not bool(relevance_profile.get("accepted")):
        return None
    canonical_url, canonical_status = extract_canonical_from_google_wrapper(url)
    wrapper_url = url if _looks_like_google_news_wrapper(url) else ""
    if not canonical_url:
        canonical_url = canonicalize_url(url)
    if wrapper_url and canonical_status == "not_wrapper":
        canonical_status = "wrapper_unresolved"
    if not wrapper_url:
        canonical_status = "direct_url"
    summary = clean_feed_text(item.get("summary_or_snippet", ""))
    traceability_note, traceability_error = _governance_traceability_note(
        publisher=source.publisher,
        published_at=published_at,
        url=url,
        canonical_url=canonical_url,
        wrapper_url=wrapper_url,
    )
    if traceability_error:
        return None
    attribution_mode = _governance_attribution_mode(source, url)
    return {
        "source_record_id": source_record_id(source.source_id, title, url, edition_date),
        "source_id": source.source_id,
        "title": title,
        "url": url,
        "publisher": source.publisher,
        "published_at": published_at,
        "retrieved_at": retrieved_at,
        "summary_or_snippet": summary,
        "source_type": "rss",
        "provider_id": source.source_id,
        "collector_source_type": source.type,
        "region_scope": source.region_scope,
        "category_hint": source.category_hint,
        "reliability_tier": source.reliability_tier,
        "source_tier": source.source_tier,
        "source_group": source.source_group,
        "discovery_role": source.discovery_role,
        "canonical_url_attempted": bool(wrapper_url),
        "canonical_url": canonical_url,
        "canonicalization_status": canonical_status,
        "wrapper_url": wrapper_url or None,
        "published_at_missing": published_at == "",
        "traceability_note": traceability_note,
        "attribution_mode": attribution_mode,
        "claim_status": attribution_mode,
        "scope_provenance": str(relevance_profile.get("scope_provenance") or "uncertain"),
        "relevance_decision": "qualified",
        "relevance_reason": str(relevance_profile.get("reason") or "direct_gaza_development"),
        "nexus_type": str(relevance_profile.get("nexus_type") or "gaza_evidence"),
        "story_selection_excluded_reason": gaza_story_selection_exclusion_reason(
            {
                "title": title,
                "summary_or_snippet": summary,
                "url": url,
                "attribution_mode": attribution_mode,
                "claim_status": attribution_mode,
            },
            source,
        ),
    }



def _keyword_points(text: str, keywords: tuple[str, ...], points: int) -> int:
    lowered = text.lower()
    return points if any(keyword in lowered for keyword in keywords) else 0


def rank_gaza_candidate(record: dict[str, Any], edition_date: str) -> dict[str, Any]:
    text = " ".join(
        [
            str(record.get("title") or ""),
            str(record.get("summary_or_snippet") or ""),
            str(record.get("category_hint") or ""),
            str(record.get("publisher") or ""),
        ]
    )
    score = 0
    breakdown: dict[str, int] = {}

    for key, keywords in RANK_KEYWORDS.items():
        points = _keyword_points(text, keywords, 12 if key in {"humanitarian_impact", "aid_access", "civilian_harm"} else 8)
        breakdown[key] = points
        score += points

    reliability_tier = str(record.get("reliability_tier") or "").strip().lower()
    reliability_points = RELIABILITY_SCORES.get(reliability_tier, 8)
    breakdown["source_reliability"] = reliability_points
    score += reliability_points

    published_at = str(record.get("published_at") or "").strip()
    date_confidence = 0
    if published_at:
        if published_at.startswith(edition_date):
            date_confidence = 18
        else:
            date_confidence = 10
    breakdown["date_confidence"] = date_confidence
    score += date_confidence

    ranked = dict(record)
    lowered = text.lower()
    high_hits = sum(1 for token in HIGH_RELEVANCE_KEYWORDS if token in lowered)
    low_hits = sum(1 for token in LOW_RELEVANCE_KEYWORDS if token in lowered)
    relevance_band = "core" if high_hits > 0 else "peripheral"
    if low_hits > 0 and high_hits == 0:
        relevance_band = "low"
        score = max(0, score - 20)
    ranked["candidate_score"] = int(score)
    ranked["candidate_score_breakdown"] = breakdown
    ranked["ranking_reasons"] = [k for k, v in breakdown.items() if v > 0]
    ranked["relevance_band"] = relevance_band
    return ranked


def rank_gaza_candidates(records: list[dict[str, Any]], edition_date: str) -> list[dict[str, Any]]:
    return [rank_gaza_candidate(record, edition_date) for record in records]


def validate_source_records(records: list[dict[str, Any]], min_sources: int = 1) -> list[str]:
    errors: list[str] = []
    if len(records) < min_sources:
        errors.append(f"source count {len(records)} is below minimum {min_sources}")
    for index, record in enumerate(records, start=1):
        missing = sorted(field for field in REQUIRED_SOURCE_FIELDS if not str(record.get(field) or "").strip())
        if missing:
            errors.append(f"source record {index} missing required fields: {', '.join(missing)}")
        url = str(record.get("url") or "").strip()
        if url and not url.startswith(("http://", "https://")):
            errors.append(f"source record {index} has invalid URL: {url}")
        if "example.com" in url.lower():
            errors.append(f"source record {index} uses placeholder URL: {url}")
        if PLACEHOLDER_RE.search(url.replace("https://", "").replace("http://", "")):
            errors.append(f"source record {index} uses placeholder URL: {url}")
        if str(record.get("title") or "").strip().lower().startswith("replace with"):
            errors.append(f"source record {index} uses placeholder title")
        if PLACEHOLDER_RE.search(str(record.get("title") or "").strip()):
            errors.append(f"source record {index} uses placeholder title")
        if str(record.get("publisher") or "").strip().lower().startswith("replace with"):
            errors.append(f"source record {index} uses placeholder publisher")
        if PLACEHOLDER_RE.search(str(record.get("publisher") or "").strip()):
            errors.append(f"source record {index} uses placeholder publisher")
    return errors


def manual_source_path(root: Path, edition_date: str) -> Path:
    if not DATE_RE.match(edition_date):
        raise ValueError(f"date must use YYYY-MM-DD: {edition_date}")
    return root / "data" / "dispatches" / "gaza" / "sources" / edition_date / "manual_sources.json"


def load_manual_source_records(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("sources") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise ValueError(f"{path.name} must be a list or an object with a sources list")
    return [record for record in records if isinstance(record, dict)]


def write_source_records(root: Path, edition_date: str, records: list[dict[str, Any]], filename: str = "manual_sources.json") -> Path:
    if not DATE_RE.match(edition_date):
        raise ValueError(f"date must use YYYY-MM-DD: {edition_date}")
    path = root / "data" / "dispatches" / "gaza" / "sources" / edition_date / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    return path


def collect_gaza_sources(
    root: Path,
    edition_date: str,
    max_sources: int = 12,
    min_sources: int = 1,
    output_filename: str = "manual_sources.json",
    prefer_manual: bool = True,
    write_output: bool = True,
) -> dict[str, Any]:
    retrieved_at = utc_now()
    warnings: list[str] = []
    errors: list[str] = []
    failed_source_ids: list[dict[str, str]] = []
    manual_path = manual_source_path(root, edition_date)
    if prefer_manual and manual_path.exists():
        try:
            manual_records = load_manual_source_records(manual_path)
            manual_errors = validate_source_records(manual_records, min_sources=min_sources)
        except Exception as exc:
            manual_records = []
            manual_errors = [str(exc)]
        if not manual_errors:
            return {
                "ok": True,
                "date": edition_date,
                "source_file": str(manual_path),
                "source_count": len(manual_records),
                "sources": manual_records[:max_sources],
                "warnings": warnings,
                "errors": errors,
                "failed_source_ids": failed_source_ids,
                "source_mode_used": "manual",
            }
        warnings.append(f"manual_sources.json was present but invalid: {'; '.join(manual_errors)}")

    config_path = root / "data" / "dispatches" / "gaza" / "sources.yml"
    definitions = load_sources_config(config_path)
    records: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    lookback_start = date.fromisoformat(edition_date) - timedelta(days=2)
    lookback_end = date.fromisoformat(edition_date)
    provider_diagnostics: list[dict[str, Any]] = []
    skipped_providers: list[dict[str, Any]] = []
    working_providers: list[str] = []
    rejected_by_reason: dict[str, int] = {}
    review_candidates: list[dict[str, Any]] = []
    enabled_provider_count = sum(1 for item in definitions if item.source_state == "enabled" and item.type == "rss")
    stage_counts: dict[str, int] = {
        "registry_sources": len(definitions),
        "enabled_providers_configured": enabled_provider_count,
        "providers_attempted": 0,
        "providers_successful": 0,
        "raw_candidates": 0,
        "normalized_candidates": 0,
        "accepted_before_rank": 0,
    }

    def reject(reason: str) -> None:
        rejected_by_reason[reason] = int(rejected_by_reason.get(reason, 0)) + 1

    def _maybe_queue_review(diag: dict[str, Any], reason: str, item: dict[str, str], *, relevance_band: str, date_basis: str) -> None:
        # Review queue holds rejected-but-possibly-relevant items for operator inspection.
        # It is diagnostics-only and never auto-published.
        text = " ".join([clean_feed_text(item.get("title", "")), clean_feed_text(item.get("summary_or_snippet", "")), str(item.get("url") or "")])
        has_terms = bool(GAZA_TERMS.search(text) or PALESTINE_TERMS.search(text))
        if not has_terms:
            return
        if reason not in {
            "rejected_low_relevance",
            "rejected_off_topic",
            "rejected_missing_published_at",
            "rejected_weak_date_basis",
            "no_demonstrated_gaza_nexus",
            "inherited_scope_only",
            "regional_context_only",
            "west_bank_without_gaza_impact",
            "live_blog_incidental_gaza_reference",
            "manual_scope_review_required",
        }:
            return
        if len(review_candidates) >= 40:
            return
        review_candidates.append(
            {
                "source_id": str(diag.get("source_id") or ""),
                "publisher": str(diag.get("publisher") or ""),
                "title": clean_feed_text(item.get("title", ""))[:220],
                "url": str(item.get("url") or "")[:500],
                "published_at": str(item.get("published_at") or ""),
                "rejection_reason": reason,
                "matched_terms": _matched_terms(item),
                "relevance_band": relevance_band,
                "date_basis": date_basis,
                "summary_or_snippet": clean_feed_text(item.get("summary_or_snippet", ""))[:400],
            }
        )

    def _provider_reject(diag: dict[str, Any], reason: str, item: dict[str, str], *, relevance_band: str = "n/a", date_basis: str = "n/a") -> None:
        by_reason = diag.setdefault("rejected_counts", {})
        by_reason[reason] = int(by_reason.get(reason, 0)) + 1
        reject(reason)
        examples: list[dict[str, Any]] = diag.setdefault("top_rejected_examples", [])
        if len(examples) < 8:
            examples.append(
                {
                    "source_id": diag.get("source_id"),
                    "title": clean_feed_text(item.get("title", ""))[:200],
                    "url": str(item.get("url") or "")[:500],
                    "published_at": str(item.get("published_at") or ""),
                    "rejection_reason": reason,
                    "matched_terms": _matched_terms(item),
                    "relevance_band": relevance_band,
                    "date_basis": date_basis,
                }
            )
        _maybe_queue_review(diag, reason, item, relevance_band=relevance_band, date_basis=date_basis)

    for source in definitions:
        if len(records) >= max_sources:
            break
        if source.source_state != "enabled":
            skip_reason = source.source_state
            if source.source_state == "disabled" and source.disabled_reason:
                skip_reason = f"disabled:{source.disabled_reason}"
            elif source.source_state == "diagnostics_only" and source.diagnostics_reason:
                skip_reason = f"diagnostics_only:{source.diagnostics_reason}"
            skipped = {
                "source_id": source.source_id,
                "source_state": source.source_state,
                "status": "skipped",
                "reason": skip_reason,
                "source_tier": source.source_tier,
                "url": source.url,
            }
            provider_diagnostics.append(skipped)
            skipped_providers.append(skipped)
            continue
        if source.type not in {"rss", "google_news_rss"}:
            skipped = {
                "source_id": source.source_id,
                "source_state": source.source_state,
                "status": "skipped",
                "reason": f"unsupported_type:{source.type}",
                "source_tier": source.source_tier,
                "url": source.url,
            }
            provider_diagnostics.append(skipped)
            skipped_providers.append(skipped)
            continue
        stage_counts["providers_attempted"] += 1
        source_record_start = len(records)
        fetch_url = source.url
        if source.type == "google_news_rss":
            fetch_url = build_google_news_rss_url(source.query or source.url)
        if not str(fetch_url or "").strip():
            warnings.append(f"{source.source_id}: missing_fetch_url")
            failed_source_ids.append({"source_id": source.source_id, "reason": "missing_fetch_url"})
            provider_diagnostics.append(
                {
                    "source_id": source.source_id,
                    "publisher": source.publisher,
                    "url": source.url,
                    "query": source.query,
                    "status": "failed",
                    "error": "missing_fetch_url",
                    "source_tier": source.source_tier,
                    "source_state": source.source_state,
                    "backend_used": "python",
                    "tls_error": False,
                }
            )
            continue
        diag: dict[str, Any] = {
            "source_id": source.source_id,
            "publisher": source.publisher,
            "url": fetch_url,
            "configured_url": source.url,
            "query": source.query,
            "status": "ok",
            "raw_items": 0,
            "accepted": 0,
            "source_tier": source.source_tier,
            "source_state": source.source_state,
            "items_with_gaza_terms": 0,
            "items_with_palestine_terms": 0,
            "items_in_date_window": 0,
            "accepted": 0,
            "rejected_counts": {
                "rejected_off_topic": 0,
                "rejected_date_out_of_window": 0,
                "rejected_missing_url": 0,
                "rejected_missing_title": 0,
                "rejected_missing_published_at": 0,
                "rejected_weak_date_basis": 0,
                "rejected_low_relevance": 0,
                "rejected_no_palestinian_anchor": 0,
                "rejected_parse_error": 0,
            },
            "top_rejected_examples": [],
        }
        fetch = fetch_feed_payload(source.source_id, fetch_url)
        diag["backend_used"] = str(fetch.get("backend_used") or "python")
        diag["tls_error"] = bool(fetch.get("tls_error"))
        if not fetch.get("ok"):
            reason = str(fetch.get("failure_reason") or "feed_fetch_failed")
            if diag["tls_error"] and reason == TLS_FAILURE_REASON:
                reason = "tls_certificate_verification_failed (environment-sensitive)"
            warnings.append(f"{source.source_id}: {reason}")
            failed_source_ids.append(
                {
                    "source_id": source.source_id,
                    "reason": reason,
                    "status_code": fetch.get("status_code"),
                    "tls_error": bool(fetch.get("tls_error")),
                    "backend_used": str(fetch.get("backend_used") or "python"),
                    "exception_type": fetch.get("exception_type"),
                }
            )
            diag["status"] = "failed"
            diag["error"] = reason
            diag["status_code"] = fetch.get("status_code")
            diag["exception_type"] = fetch.get("exception_type")
            provider_diagnostics.append(diag)
            continue
        try:
            items = parse_rss_items(
                fetch.get("content_bytes") or b"",
                content_type=str(fetch.get("content_type") or ""),
                content_encoding=str(fetch.get("content_encoding") or ""),
            )
            diag["raw_items"] = len(items)
            stage_counts["raw_candidates"] += len(items)
        except (TimeoutError, ET.ParseError, OSError, ValueError) as exc:
            reason = f"{type(exc).__name__}: {exc}"
            warnings.append(f"{source.source_id}: {reason}")
            failed_source_ids.append(
                {
                    "source_id": source.source_id,
                    "reason": reason,
                    "status_code": fetch.get("status_code"),
                    "tls_error": False,
                    "backend_used": str(fetch.get("backend_used") or "python"),
                    "exception_type": type(exc).__name__,
                }
            )
            diag["status"] = "failed"
            diag["error"] = reason
            diag["status_code"] = fetch.get("status_code")
            diag["exception_type"] = type(exc).__name__
            diag["rejected_counts"]["rejected_parse_error"] = int(diag["rejected_counts"].get("rejected_parse_error", 0)) + 1
            provider_diagnostics.append(diag)
            continue
        for item in items:
            if len(records) >= max_sources:
                break
            # Filtering gates order (pre-dedupe):
            # 1) missing title/url
            # 2) topical relevance (off-topic / low relevance)
            # 3) published_at basis (missing/weak/out-of-window)
            # 4) normalization integrity
            # 5) in-run URL dedupe
            title = clean_feed_text(item.get("title", ""))
            url = str(item.get("url") or "").strip()
            summary = clean_feed_text(item.get("summary_or_snippet", ""))
            item_text = " ".join([title, summary, url])
            if "gaza" in item_text.lower():
                diag["items_with_gaza_terms"] = int(diag.get("items_with_gaza_terms") or 0) + 1
            if PALESTINE_TERMS.search(item_text):
                diag["items_with_palestine_terms"] = int(diag.get("items_with_palestine_terms") or 0) + 1
            if not title:
                _provider_reject(diag, "rejected_missing_title", item, date_basis="unknown")
                continue
            if not url:
                _provider_reject(diag, "rejected_missing_url", item, date_basis="unknown")
                continue
            relevant, relevance_reason = gaza_relevance_decision(item, source)
            if not relevant:
                low_relevance = "low" if any(token in item_text.lower() for token in LOW_RELEVANCE_KEYWORDS) else "peripheral"
                reason = relevance_reason or "no_demonstrated_gaza_nexus"
                if reason in {"live_blog_incidental_gaza_reference", "weak_liveblog_unrelated_topic"}:
                    _provider_reject(diag, reason, item, relevance_band=low_relevance)
                elif reason in {
                    "no_demonstrated_gaza_nexus",
                    "regional_context_only",
                    "west_bank_without_gaza_impact",
                    "inherited_scope_only",
                    "manual_scope_review_required",
                }:
                    _provider_reject(diag, reason, item, relevance_band="off_topic")
                else:
                    _provider_reject(diag, "rejected_off_topic", item, relevance_band="off_topic")
                continue
            basis = _date_basis(item.get("published_at", ""), lookback_start, lookback_end)
            if basis == "missing_published_at":
                _provider_reject(diag, "rejected_missing_published_at", item, date_basis=basis)
                continue
            if basis == "weak_date_basis":
                _provider_reject(diag, "rejected_weak_date_basis", item, date_basis=basis)
                continue
            if basis == "out_of_window":
                _provider_reject(diag, "rejected_date_out_of_window", item, date_basis=basis)
                continue
            diag["items_in_date_window"] = int(diag.get("items_in_date_window") or 0) + 1
            record = normalize_rss_item(item, source, edition_date, retrieved_at)
            if record is None:
                if not title:
                    _provider_reject(diag, "rejected_missing_title", item, date_basis=basis)
                elif not url:
                    _provider_reject(diag, "rejected_missing_url", item, date_basis=basis)
                else:
                    _provider_reject(diag, "rejected_parse_error", item, date_basis=basis)
                continue
            url_key = canonicalize_url(str(record.get("canonical_url") or record["url"])).lower()
            if url_key in seen_urls:
                _provider_reject(diag, "duplicate_url_in_collection", item, relevance_band=str(record.get("relevance_band") or "core"), date_basis=basis)
                continue
            seen_urls.add(url_key)
            records.append(record)
            stage_counts["normalized_candidates"] += 1
            diag["accepted"] = int(diag.get("accepted", 0)) + 1
        if len(records) > source_record_start:
            stage_counts["providers_successful"] += 1
            working_providers.append(source.source_id)
        if len(records) == source_record_start:
            failed_source_ids.append({"source_id": source.source_id, "reason": f"no matching Gaza items for {edition_date}"})
            if diag.get("status") == "ok":
                diag["status"] = "no_matches"
        rejected_counts = dict(diag.get("rejected_counts") or {})
        diag["most_common_rejection_reasons"] = [
            {"reason": reason, "count": count}
            for reason, count in sorted(rejected_counts.items(), key=lambda kv: kv[1], reverse=True)
            if int(count) > 0
        ][:5]
        provider_diagnostics.append(diag)

    stage_counts["accepted_before_rank"] = len(records)
    records = rank_gaza_candidates(records, edition_date)
    validation_errors = validate_source_records(records, min_sources=min_sources)
    errors.extend(validation_errors)
    source_file = None
    if not errors:
        if write_output:
            source_file = write_source_records(root, edition_date, records, output_filename)
        else:
            source_file = manual_path
    return {
        "ok": not errors,
        "date": edition_date,
        "source_file": str(source_file) if source_file else None,
        "source_count": len(records),
        "sources": records,
        "warnings": warnings,
        "errors": errors,
        "failed_source_ids": failed_source_ids,
        "source_mode_used": "auto",
        "stage_counts": stage_counts,
        "lookback_start_date": lookback_start.isoformat(),
        "lookback_end_date": lookback_end.isoformat(),
        "provider_diagnostics": provider_diagnostics,
        "rejected_by_reason": rejected_by_reason,
        "providers_configured": [item.source_id for item in definitions if item.source_state == "enabled" and item.type == "rss"],
        "providers_attempted": [row.get("source_id") for row in provider_diagnostics if row.get("status") != "skipped"],
        "providers_successful": sorted(set(working_providers)),
        "skipped_providers": skipped_providers,
        "working_providers": sorted(set(working_providers)),
        "top_rejected_examples": [
            example
            for diag in provider_diagnostics
            if isinstance(diag, dict)
            for example in list(diag.get("top_rejected_examples") or [])
        ][:25],
        "review_candidates": review_candidates[:25],
    }
