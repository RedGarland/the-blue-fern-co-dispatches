from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


MEMORY_PATH = Path("data") / "records" / "story_memory.json"
STOPWORDS = {
    "about",
    "after",
    "again",
    "against",
    "amid",
    "from",
    "have",
    "into",
    "over",
    "says",
    "that",
    "their",
    "this",
    "through",
    "under",
    "with",
    "gaza",
    "cascadia",
    "briefing",
    "dispatches",
    "story",
    "update",
    "weekly",
    "daily",
}
GAZA_EVENT_STOPWORDS = STOPWORDS | {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "been",
    "but",
    "by",
    "city",
    "count",
    "deaths",
    "for",
    "has",
    "have",
    "he",
    "her",
    "him",
    "his",
    "in",
    "is",
    "israel",
    "israeli",
    "it",
    "its",
    "least",
    "more",
    "most",
    "new",
    "of",
    "on",
    "people",
    "palestinian",
    "palestinians",
    "reported",
    "reports",
    "says",
    "said",
    "since",
    "so",
    "than",
    "the",
    "their",
    "there",
    "this",
    "those",
    "through",
    "to",
    "under",
    "was",
    "were",
    "while",
    "with",
    "would",
}
TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid", "igshid", "ref", "source"}
UPDATE_TERMS = {
    "opens",
    "opened",
    "closes",
    "closed",
    "expands",
    "expanded",
    "declares",
    "declared",
    "approves",
    "approved",
    "rejects",
    "rejected",
    "files",
    "filed",
    "passes",
    "passed",
    "signs",
    "signed",
    "evacuates",
    "evacuated",
    "restores",
    "restored",
    "cancels",
    "cancelled",
    "canceled",
    "announces",
    "announced",
    "reports",
    "reported",
    "advisory",
    "warning",
    "order",
    "orders",
    "ordered",
}
OFFICIAL_TERMS = {
    "agency",
    "authority",
    "bureau",
    "city",
    "county",
    "department",
    "district",
    "federal",
    "gov",
    "government",
    "ministry",
    "municipal",
    "office",
    "official",
    "state",
    "tribal",
}
GAZA_EVENT_ROLE_PREFIXES = (
    "cameraman",
    "camera operator",
    "correspondent",
    "doctor",
    "editor",
    "journalist",
    "media worker",
    "nurse",
    "photographer",
    "reporter",
    "teacher",
    "worker",
)
GAZA_EVENT_LOCATION_TERMS = ("gaza", "gaza strip", "central gaza", "southern gaza", "northern gaza")
GAZA_EVENT_CASUALTY_TERMS = ("killed", "kill", "dead", "death", "injured", "injure", "wounded", "wound")
GAZA_EVENT_ORG_TERMS = ("al jazeera", "guardian", "reuters", "ap ", "bbc", "afp", "washington post", "new york times")


@dataclass
class DedupeResult:
    stories: list[dict[str, Any]]
    report: dict[str, Any]
    memory: list[dict[str, Any]]
    decisions: list[dict[str, Any]]


def read_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return payload if isinstance(payload, list) else []


def write_json(path: Path, payload: Any, dry_run: bool, written: list[str]) -> None:
    written.append(str(path))
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def normalize_text(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())
    return " ".join(text.split())


def normalize_title(value: Any) -> str:
    text = normalize_text(value)
    for prefix in ("breaking ", "update "):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    return text


def normalize_url(value: Any) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url.lower().rstrip("/")
    query = [
        (key, val)
        for key, val in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_QUERY_KEYS and not key.lower().startswith(TRACKING_QUERY_PREFIXES)
    ]
    path = re.sub(r"/+$", "", parts.path or "")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower().removeprefix("www."), path, urlencode(query), ""))


def similarity(left: Any, right: Any) -> float:
    return SequenceMatcher(None, normalize_text(left), normalize_text(right)).ratio()


def story_keywords(story: dict[str, Any]) -> set[str]:
    text = normalize_text(f"{story.get('title', '')} {story.get('summary', '')} {story.get('category', '')}")
    return {word for word in text.split() if len(word) > 3 and word not in STOPWORDS}


def topic_fingerprint(story: dict[str, Any]) -> str:
    category = normalize_text(story.get("category"))
    keywords = sorted(story_keywords(story))[:12]
    raw = "|".join([category, *keywords])
    return sha256(raw.encode("utf-8")).hexdigest()[:16]


def story_source_urls(story: dict[str, Any]) -> list[str]:
    urls = list(story.get("source_urls") or [])
    for record in story.get("source_records") or []:
        if not isinstance(record, dict):
            continue
        url = record.get("canonical_url") or record.get("source_url") or record.get("url")
        if url:
            urls.append(str(url))
    return unique_strings(urls)


def story_canonical_urls(story: dict[str, Any]) -> list[str]:
    urls = []
    for record in story.get("source_records") or []:
        if isinstance(record, dict) and record.get("canonical_url"):
            urls.append(str(record["canonical_url"]))
    urls.extend(story.get("canonical_urls") or [])
    return unique_strings(urls)


def story_publishers(story: dict[str, Any]) -> list[str]:
    publishers = list(story.get("publisher_names") or [])
    for record in story.get("source_records") or []:
        if not isinstance(record, dict):
            continue
        publisher = record.get("publisher") or record.get("source_name")
        if publisher:
            publishers.append(str(publisher))
    return unique_strings(publishers)


def story_geographies(story: dict[str, Any]) -> list[str]:
    values = []
    for field in ("state_hint", "region_scope", "geography", "county", "city"):
        if story.get(field):
            values.append(story.get(field))
    for record in story.get("source_records") or []:
        if not isinstance(record, dict):
            continue
        for field in ("state_hint", "region_scope", "geographic_scope", "county", "city"):
            if record.get(field):
                values.append(record.get(field))
    return unique_strings([normalize_text(value).upper() if len(str(value).strip()) <= 3 else value for value in values])


def source_dates(story: dict[str, Any]) -> list[str]:
    dates = []
    for record in story.get("source_records") or []:
        if not isinstance(record, dict):
            continue
        for field in ("published_at", "retrieved_at"):
            if record.get(field):
                dates.append(str(record[field]))
    return sorted(unique_strings(dates))


def source_family(value: Any) -> str:
    text = normalize_text(value)
    return re.sub(r"\b(news|media|daily|times|post|press|wire|agency|public|radio|tv|television)\b", "", text).strip()


def has_update_terms(story: dict[str, Any]) -> bool:
    text = normalize_text(f"{story.get('title', '')} {story.get('summary', '')} " + " ".join(str(record.get("summary_or_snippet") or record.get("text") or record.get("title") or "") for record in story.get("source_records") or [] if isinstance(record, dict)))
    words = set(text.split())
    if words & UPDATE_TERMS:
        return True
    if re.search(r"\bnew\s+(advisory|warning|order|numbers?|figures?|total|cases?|deaths?|closures?|route|schedule)\b", text):
        return True
    if re.search(r"\breports?\s+new\s+\d", text):
        return True
    return False


def has_new_numbers(story: dict[str, Any]) -> bool:
    text = normalize_text(f"{story.get('title', '')} {story.get('summary', '')}")
    return bool(re.search(r"\b(new|now|reports?|reported)\b.*\b\d+[\d,]*(?:\.\d+)?\b", text))


def is_official_source(story: dict[str, Any]) -> bool:
    for record in story.get("source_records") or []:
        if not isinstance(record, dict):
            continue
        publisher_text = normalize_text(" ".join(str(record.get(field) or "") for field in ("publisher", "source_name", "provider_name")))
        source_type = normalize_text(record.get("source_type"))
        reliability = normalize_text(record.get("reliability_tier"))
        url = normalize_url(record.get("canonical_url") or record.get("source_url") or record.get("url"))
        domain = urlsplit(url).netloc.lower() if url else ""
        if domain.endswith(".gov") or ".gov." in domain:
            return True
        if source_type.startswith("official") or reliability in {"official public", "official-public"}:
            return True
        if OFFICIAL_TERMS & set(publisher_text.split()):
            return True
    return False


def unique_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = " ".join(str(value or "").split())
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _normalize_event_token(token: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "", str(token or "").lower())
    if not text:
        return ""
    if text in {"attack", "attacks", "attacked", "attacking", "strike", "strikes", "struck", "airstrike", "airstrikes"}:
        return "strike"
    if text.endswith("ies") and len(text) > 4:
        text = f"{text[:-3]}y"
    elif text.endswith("ing") and len(text) > 5:
        text = text[:-3]
    elif text.endswith("ed") and len(text) > 4:
        text = text[:-2]
    elif text.endswith("es") and len(text) > 4:
        text = text[:-2]
    elif text.endswith("s") and len(text) > 4 and not text.endswith(("ss", "us")):
        text = text[:-1]
    return text


def _gaza_event_tokens(story: dict[str, Any]) -> set[str]:
    text = normalize_text(
        " ".join(
            [
                str(story.get("title") or ""),
                str(story.get("summary") or ""),
                " ".join(
                    str(record.get("title") or "")
                    for record in story.get("source_records") or []
                    if isinstance(record, dict)
                ),
            ]
        )
    )
    tokens = {
        normalized
        for normalized in (_normalize_event_token(part) for part in text.split())
        if normalized and normalized not in GAZA_EVENT_STOPWORDS
    }
    return tokens


def _gaza_named_casualty_event_key(story: dict[str, Any]) -> str:
    text = " ".join(
        [
            str(story.get("title") or ""),
            str(story.get("summary") or ""),
            " ".join(
                str(record.get("title") or "")
                for record in story.get("source_records") or []
                if isinstance(record, dict)
            ),
        ]
    )
    lowered = normalize_text(text)
    if "gaza" not in lowered:
        return ""
    if not any(term in lowered for term in GAZA_EVENT_CASUALTY_TERMS):
        return ""
    person_match = None
    for prefix in GAZA_EVENT_ROLE_PREFIXES:
        pattern = rf"\b{re.escape(prefix)}\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b"
        person_match = re.search(pattern, text)
        if person_match:
            break
    if person_match is None:
        return ""
    person = normalize_text(person_match.group(1))
    if not person:
        return ""
    org = ""
    for term in GAZA_EVENT_ORG_TERMS:
        if term in lowered:
            org = normalize_text(term)
            break
    location = ""
    for term in GAZA_EVENT_LOCATION_TERMS:
        if term in lowered:
            location = normalize_text(term)
            break
    parts = ["gaza_named_casualty", person.replace(" ", "_")]
    if org:
        parts.append(org.replace(" ", "_"))
    if location:
        parts.append(location.replace(" ", "_"))
    return "_".join(parts)


def merge_story(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key in ("source_record_ids", "source_ids", "source_urls", "publisher_names", "canonical_urls"):
        merged[key] = unique_strings(list(base.get(key) or []) + list(incoming.get(key) or []))
    if incoming.get("source_records"):
        by_id = {
            str(record.get("source_record_id") or record.get("canonical_url") or record.get("source_url") or record.get("url")): record
            for record in base.get("source_records") or []
            if isinstance(record, dict)
        }
        for record in incoming.get("source_records") or []:
            if not isinstance(record, dict):
                continue
            key = str(record.get("source_record_id") or record.get("canonical_url") or record.get("source_url") or record.get("url"))
            by_id[key] = record
        merged["source_records"] = list(by_id.values())
    if len(str(incoming.get("summary") or "")) > len(str(base.get("summary") or "")):
        merged["summary"] = incoming.get("summary")
    merged["score"] = max(int(base.get("score") or 0), int(incoming.get("score") or 0))
    reasons = list(base.get("scoring_reasons") or []) + list(incoming.get("scoring_reasons") or [])
    reasons.append(f"merged_duplicate_story={incoming.get('story_id')}")
    merged["scoring_reasons"] = unique_strings(reasons)
    return merged


def _is_preferred_story(candidate: dict[str, Any], current: dict[str, Any]) -> bool:
    candidate_score = int(candidate.get("score") or 0)
    current_score = int(current.get("score") or 0)
    if candidate_score != current_score:
        return candidate_score > current_score
    candidate_sources = len(story_source_urls(candidate))
    current_sources = len(story_source_urls(current))
    if candidate_sources != current_sources:
        return candidate_sources > current_sources
    return len(str(candidate.get("summary") or "")) > len(str(current.get("summary") or ""))


def _gaza_event_key(story: dict[str, Any]) -> str:
    text = normalize_text(
        " ".join(
            [
                str(story.get("title") or ""),
                str(story.get("summary") or ""),
                " ".join(
                    str(record.get("title") or "")
                    for record in story.get("source_records") or []
                    if isinstance(record, dict)
                ),
            ]
        )
    )
    tokens = _gaza_event_tokens(story)
    actor = bool(re.search(r"\b(israeli forces?|israel|commandos?)\b", text))
    action = bool(re.search(r"\b(board|boarding|intercept|intercepting|storm|seize|seizing)\b", text))
    obj = bool(re.search(r"\b(gaza bound aid flotilla|gaza bound flotilla|global sumud flotilla|aid flotilla|flotilla|vessels?|boats?)\b", text))
    location = bool(re.search(r"\b(near cyprus|off cyprus|cyprus|international waters|maritime blockade)\b", text))
    if actor and action and obj and location:
        return "gaza_flotilla_interception_israeli_forces_cyprus"
    named_casualty = _gaza_named_casualty_event_key(story)
    if named_casualty:
        return named_casualty
    casualty_terms = {"kill", "death", "dead", "casualty", "casualti", "injur", "injury", "wound"}
    ceasefire_terms = {"ceasefire", "ceasefir", "truce"}
    if ceasefire_terms & tokens and tokens & casualty_terms:
        return "gaza_ceasefire_casualty"
    return ""


def _same_event_cluster_key(story: dict[str, Any]) -> str:
    return _gaza_event_key(story)


def _same_event_duplicate_reason(event_key: str) -> str:
    if event_key == "gaza_flotilla_interception_israeli_forces_cyprus":
        return "same_event_flotilla_interception"
    if event_key.startswith("gaza_named_casualty_"):
        return "same_event_named_casualty"
    return event_key or "within_edition_duplicate"


def has_same_topic(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if normalize_text(left.get("category")) and normalize_text(left.get("category")) != normalize_text(right.get("category")):
        return False
    if similarity(left.get("title"), right.get("title")) >= 0.82:
        return True
    left_keywords = story_keywords(left)
    right_keywords = story_keywords(right)
    if not left_keywords or not right_keywords:
        return False
    overlap = len(left_keywords & right_keywords) / max(1, min(len(left_keywords), len(right_keywords)))
    return overlap >= 0.5


def has_new_detail(candidate: dict[str, Any], prior: dict[str, Any]) -> bool:
    if similarity(candidate.get("summary"), prior.get("summary")) < 0.88:
        return True
    candidate_urls = {normalize_url(url) for url in story_source_urls(candidate)}
    prior_urls = {normalize_url(url) for url in prior.get("source_urls", []) + prior.get("canonical_urls", [])}
    return bool(candidate_urls - prior_urls)


def is_major_update(candidate: dict[str, Any], prior: dict[str, Any]) -> bool:
    if not has_same_topic(candidate, prior):
        return False
    candidate_text = normalize_text(f"{candidate.get('title', '')} {candidate.get('summary', '')}")
    markers = {"announces", "announced", "approves", "approved", "orders", "ordered", "resumes", "ends", "major"}
    if markers & set(candidate_text.split()) and similarity(candidate.get("summary"), prior.get("summary")) < 0.82:
        return has_new_detail(candidate, prior)
    return similarity(candidate.get("summary"), prior.get("summary")) < 0.55


def material_update(candidate: dict[str, Any], prior: dict[str, Any] | None) -> tuple[bool, list[str]]:
    if prior is None:
        return True, ["new_story"]
    reasons: list[str] = []
    candidate_urls = {normalize_url(url) for url in story_source_urls(candidate) + story_canonical_urls(candidate) if normalize_url(url)}
    prior_urls = {normalize_url(url) for url in prior.get("source_urls", []) + prior.get("canonical_urls", []) if normalize_url(url)}
    candidate_publishers = {normalize_text(publisher) for publisher in story_publishers(candidate)}
    prior_publishers = {normalize_text(publisher) for publisher in prior.get("publisher_names", [])}
    candidate_families = {source_family(publisher) for publisher in candidate_publishers if source_family(publisher)}
    prior_families = {source_family(publisher) for publisher in prior_publishers if source_family(publisher)}
    if candidate_urls - prior_urls and candidate_publishers and not (candidate_publishers & prior_publishers) and not (candidate_families & prior_families):
        reasons.append("new_source_url_different_publisher")
    if candidate_urls - prior_urls and is_official_source(candidate):
        reasons.append("new_official_or_public_agency_source")
    candidate_geos = {normalize_text(value) for value in story_geographies(candidate)}
    prior_geos = {normalize_text(value) for value in prior.get("geographies", [])}
    if candidate_geos and candidate_geos - prior_geos:
        reasons.append("new_geography")
    prior_category = normalize_text(prior.get("category"))
    if normalize_text(candidate.get("category")) and prior_category and normalize_text(candidate.get("category")) != prior_category:
        reasons.append("new_category_or_public_system_dimension")
    if has_update_terms(candidate):
        reasons.append("update_term_in_title_or_snippet")
    if has_new_numbers(candidate):
        reasons.append("reports_new_numbers")
    candidate_dates = source_dates(candidate)
    prior_dates = list(prior.get("source_dates") or [])
    if candidate_dates and prior_dates and max(candidate_dates) > max(prior_dates) and (has_update_terms(candidate) or has_new_numbers(candidate)):
        reasons.append("newer_source_date_with_status_change")
    return bool(reasons), unique_strings(reasons)


def classify_against_prior(candidate: dict[str, Any], prior_rows: list[dict[str, Any]]) -> tuple[str, list[str], dict[str, Any] | None]:
    candidate_urls = {url for url in (normalize_url(url) for url in story_source_urls(candidate) + story_canonical_urls(candidate)) if url}
    candidate_title = normalize_title(candidate.get("title"))
    candidate_publishers = {normalize_text(publisher) for publisher in story_publishers(candidate)}
    reasons: list[str] = []
    for prior in prior_rows:
        prior_urls = {url for url in (normalize_url(url) for url in prior.get("source_urls", []) + prior.get("canonical_urls", [])) if url}
        prior_title = normalize_title(prior.get("title"))
        prior_publishers = {normalize_text(publisher) for publisher in prior.get("publisher_names", [])}
        exact_url = bool(candidate_urls & prior_urls)
        title_match = bool(candidate_title and candidate_title == prior_title)
        publisher_title = bool(title_match and candidate_publishers and candidate_publishers & prior_publishers)
        fuzzy_title = similarity(candidate_title, prior_title) >= 0.86
        topic_match = has_same_topic(candidate, prior)
        if exact_url and not has_new_detail(candidate, prior):
            return "duplicate_skip", ["exact_or_normalized_source_url", "no_new_summary_detail"], prior
        if title_match and not has_new_detail(candidate, prior):
            return "duplicate_skip", ["normalized_title", "no_new_summary_detail"], prior
        if publisher_title and not has_new_detail(candidate, prior):
            return "duplicate_skip", ["publisher_title", "no_new_summary_detail"], prior
        if is_major_update(candidate, prior):
            return "major_update", ["same_topic", "material_new_development"], prior
        if exact_url or fuzzy_title or topic_match:
            if has_new_detail(candidate, prior):
                reasons = ["same_topic", "new_source_or_summary_detail"]
                if exact_url:
                    reasons.append("source_url_seen_before")
                elif fuzzy_title:
                    reasons.append("fuzzy_title_similarity")
                return "continuing_development", reasons, prior
    return "new", ["different_topic_or_source"], None


def memory_row(dispatch_slug: str, edition_date: str, story: dict[str, Any], prior: dict[str, Any] | None = None) -> dict[str, Any]:
    source_urls = unique_strings(list(prior.get("source_urls", []) if prior else []) + story_source_urls(story))
    canonical_urls = unique_strings(list(prior.get("canonical_urls", []) if prior else []) + (story_canonical_urls(story) or story_source_urls(story)))
    first_seen = prior.get("first_seen_date") if prior else None
    update_count = int(prior.get("update_count") or 0) + 1 if prior else 0
    return {
        "dispatch_slug": dispatch_slug,
        "edition_date": edition_date,
        "story_id": story.get("story_id"),
        "title": story.get("title"),
        "normalized_title": normalize_title(story.get("title")),
        "summary": story.get("summary"),
        "source_urls": source_urls,
        "canonical_urls": unique_strings(canonical_urls),
        "publisher_names": story_publishers(story),
        "category": story.get("category"),
        "geographies": unique_strings(list(prior.get("geographies", []) if prior else []) + story_geographies(story)),
        "source_dates": unique_strings(list(prior.get("source_dates", []) if prior else []) + source_dates(story)),
        "topic_fingerprint": topic_fingerprint(story),
        "first_seen_date": first_seen or edition_date,
        "last_seen_date": edition_date,
        "update_count": update_count,
        "latest_classification": story.get("dedupe_classification") or "new",
    }


def decision_record(
    story: dict[str, Any],
    classification: str,
    material: bool,
    material_reasons: list[str],
    duplicate_reasons: list[str],
    prior: dict[str, Any] | None,
    include: bool,
) -> dict[str, Any]:
    return {
        "story_id": story.get("story_id"),
        "title": story.get("title"),
        "classification": classification,
        "material_update": material,
        "material_update_reasons": material_reasons,
        "duplicate_reasons": duplicate_reasons,
        "prior_story_matched": prior.get("story_id") if prior else None,
        "prior_edition_date": prior.get("edition_date") if prior else None,
        "include_decision": "include" if include else "skip",
        "public_rendered": bool(include),
    }


def prior_memory_from_outputs(root: Path, dispatch_slug: str, edition_date: str) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    checked: list[str] = []
    editions_root = root / "output" / "site" / dispatch_slug / "editions"
    if not editions_root.exists():
        return rows, checked
    for path in sorted(editions_root.iterdir(), reverse=True):
        if not path.is_dir() or path.name >= edition_date:
            continue
        if dispatch_slug == "cascadia" and not is_weekly_cascadia_edition(path, path.name):
            continue
        curation_path = path / "curation_manifest.json"
        if not curation_path.exists():
            continue
        checked.append(path.name)
        for story in read_json_list(curation_path):
            if story.get("included_in_public_summary") is False:
                continue
            rows.append(memory_row(dispatch_slug, path.name, story))
    return rows, checked


def is_sunday(value: str) -> bool:
    try:
        return date.fromisoformat(value).weekday() == 6
    except ValueError:
        return False


def is_weekly_cascadia_edition(edition_dir: Path, edition_date: str) -> bool:
    if not is_sunday(edition_date):
        return False
    manifest_path = edition_dir / "edition_manifest.json"
    if not manifest_path.exists():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return manifest.get("briefing_type") == "weekly" and manifest.get("coverage_end") == edition_date


def collapse_within_edition(stories: list[dict[str, Any]], dispatch_slug: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    included: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    for story in stories:
        match_index: int | None = None
        match_reasons: list[str] = []
        for index, existing in enumerate(included):
            candidate_urls = {normalize_url(url) for url in story_source_urls(story)}
            existing_urls = {normalize_url(url) for url in story_source_urls(existing)}
            duplicate_reason = ""
            normalized_event_key = ""
            if dispatch_slug == "gaza":
                existing_event_key = _same_event_cluster_key(existing)
                candidate_event_key = _same_event_cluster_key(story)
                if existing_event_key and candidate_event_key and existing_event_key == candidate_event_key:
                    match_index = index
                    duplicate_reason = _same_event_duplicate_reason(existing_event_key)
                    match_reasons = [duplicate_reason]
                    normalized_event_key = existing_event_key
                    break
            if candidate_urls and candidate_urls & existing_urls:
                match_index = index
                match_reasons = ["within_edition_source_url"]
                break
            if normalize_title(story.get("title")) and normalize_title(story.get("title")) == normalize_title(existing.get("title")):
                match_index = index
                match_reasons = ["within_edition_normalized_title"]
                break
            if dispatch_slug == "gaza":
                summary_similarity = similarity(story.get("summary"), existing.get("summary"))
                title_similarity = similarity(story.get("title"), existing.get("title"))
                if has_same_topic(story, existing) and title_similarity >= 0.9 and summary_similarity >= 0.8:
                    match_index = index
                    match_reasons = ["within_edition_near_duplicate"]
                    break
            elif has_same_topic(story, existing) and similarity(story.get("title"), existing.get("title")) >= 0.78:
                match_index = index
                match_reasons = ["within_edition_same_topic"]
                break
        if match_index is None:
            included.append(story)
            continue
        target = included[match_index]
        preferred = story if _is_preferred_story(story, target) else target
        secondary = target if preferred is story else story
        merged_story = merge_story(preferred, secondary)
        included[match_index] = merged_story
        skipped.append(
            {
                "story_id": secondary.get("story_id"),
                "title": secondary.get("title"),
                "classification": "duplicate_merged",
                "reasons": match_reasons,
                "kept_story_id": preferred.get("story_id"),
            }
        )
        group = {
            "kept_story_id": preferred.get("story_id"),
            "merged_story_ids": [secondary.get("story_id")],
            "merged_source_ids": unique_strings(list(secondary.get("source_record_ids") or []) + list(secondary.get("source_ids") or [])),
            "duplicate_reason": duplicate_reason or "within_edition_duplicate",
            "normalized_event_key": _same_event_cluster_key(preferred) or _same_event_cluster_key(secondary) or "",
            "reasons": match_reasons,
        }
        groups.append(group)
    return included, skipped, groups


def dedupe_public_stories(
    root: Path,
    dispatch_slug: str,
    edition_date: str,
    stories: list[dict[str, Any]],
    dry_run: bool = False,
    written: list[str] | None = None,
) -> DedupeResult:
    written = written if written is not None else []
    records_root = root / "data" / "records"
    memory_path = records_root / "story_memory.json"
    existing_memory = read_json_list(memory_path)
    output_memory, checked = prior_memory_from_outputs(root, dispatch_slug, edition_date)
    prior_rows_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in existing_memory + output_memory:
        if row.get("dispatch_slug") != dispatch_slug or str(row.get("edition_date") or "") >= edition_date:
            continue
        if dispatch_slug == "cascadia" and not is_sunday(str(row.get("edition_date") or "")):
            continue
        key = (str(row.get("dispatch_slug")), str(row.get("story_id")), str(row.get("edition_date")))
        prior_rows_by_key[key] = row
    prior_rows = sorted(prior_rows_by_key.values(), key=lambda row: str(row.get("edition_date", "")), reverse=True)
    prior_dates = sorted({str(row.get("edition_date")) for row in prior_rows if row.get("edition_date")} | set(checked), reverse=True)
    collapsed, merged_skips, duplicate_groups = collapse_within_edition(stories, dispatch_slug=dispatch_slug)
    included: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    continuing: list[dict[str, Any]] = []
    major: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    new_memory_rows: list[dict[str, Any]] = []
    for merged in merged_skips:
        merged_decision = {
            "story_id": merged.get("story_id"),
            "title": merged.get("title"),
            "classification": "duplicate_merged",
            "material_update": False,
            "material_update_reasons": [],
            "duplicate_reasons": list(merged.get("reasons") or []),
            "prior_story_matched": merged.get("kept_story_id"),
            "prior_edition_date": edition_date,
            "include_decision": "merge_into_existing",
            "public_rendered": False,
        }
        skipped.append(merged_decision)
        decisions.append(merged_decision)
    for story in collapsed:
        classification, why, prior = classify_against_prior(story, prior_rows)
        material, material_reasons = material_update(story, prior)
        include = classification in {"new", "major_update"} or (classification == "continuing_development" and material)
        final_classification = "duplicate_skip" if classification == "continuing_development" and not material else classification
        duplicate_reasons = [] if include else unique_strings(why + (["non_material_continuation"] if classification == "continuing_development" else []))
        annotated = dict(story)
        annotated["dedupe_classification"] = final_classification if not include else classification
        annotated["dedupe_reasons"] = why
        annotated["material_update"] = material
        annotated["material_update_reasons"] = material_reasons
        if prior:
            annotated["continuation_of_story_id"] = prior.get("story_id")
            annotated["first_seen_date"] = prior.get("first_seen_date") or prior.get("edition_date")
        decision = decision_record(story, final_classification, material, material_reasons, duplicate_reasons, prior, include)
        decisions.append(decision)
        if not include:
            skipped.append(decision)
            continue
        included.append(annotated)
        if classification == "continuing_development":
            continuing.append(decision)
        if classification == "major_update":
            major.append(decision)
        new_memory_rows.append(memory_row(dispatch_slug, edition_date, annotated, prior))
    memory = [
        row
        for row in existing_memory
        if not (row.get("dispatch_slug") == dispatch_slug and row.get("edition_date") == edition_date)
    ]
    memory.extend(new_memory_rows)
    memory = sorted(memory, key=lambda row: (str(row.get("dispatch_slug")), str(row.get("edition_date")), str(row.get("story_id"))))
    report = {
        "dispatch_slug": dispatch_slug,
        "edition_date": edition_date,
        "candidates_seen": len(stories),
        "included_stories": [
            {
                "story_id": story.get("story_id"),
                "title": story.get("title"),
                "classification": story.get("dedupe_classification"),
                "material_update": story.get("material_update"),
                "material_update_reasons": story.get("material_update_reasons") or [],
                "include_decision": "include",
                "public_rendered": True,
            }
            for story in included
        ],
        "duplicate_skipped": skipped,
        "continuing_developments": continuing,
        "major_updates": major,
        "merged_sources": sum(max(0, len(story_source_urls(story)) - 1) for story in included),
        "duplicate_groups": duplicate_groups,
        "reasons": decisions,
        "prior_editions_checked": prior_dates,
    }
    write_json(memory_path, memory, dry_run, written)
    report_path = root / "output" / "dispatches" / dispatch_slug / "editions" / edition_date / "dedupe_report.json"
    write_json(report_path, report, dry_run, written)
    return DedupeResult(stories=included, report=report, memory=memory, decisions=decisions)
