from __future__ import annotations

import argparse
import json
import re
import ssl
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "data" / "source_registry" / "american_pressure_sources.json"
OUT_SUMMARY = ROOT / "output" / "site" / "american-pressure" / "backfill_summary.json"
OUT_FETCH_DIAGNOSTICS = ROOT / "output" / "site" / "american-pressure" / "backfill_fetch_diagnostics.json"
OUT_SOURCES_ROOT = ROOT / "data" / "dispatches" / "american-pressure" / "sources"
VALIDATION_REPORT_PATH = ROOT / "output" / "site" / "american-pressure" / "source_feed_validation_report.json"

PILLAR_MAP = {
    "food_grocery_pressure": "food_pressure",
    "housing_utility_pressure": "housing_household_cost_pressure",
    "health_care_access_pressure": "health_access_pressure",
    "jobs_paycheck_pressure": "labor_income_pressure",
    "debt_bankruptcy_pressure": "financial_distress_pressure",
    "public_services_under_strain": "local_system_strain",
    "disaster_insurance_recovery_pressure": "environmental_pressure",
    "benefits_policy_delivery_pressure": "policy_implementation",
    "transportation_daily_access_pressure": "transportation_daily_access_pressure",
    "childcare_schools_family_pressure": "childcare_school_family_pressure",
}

KEYWORDS = {
    "food_pressure": ["food bank", "snap", "food assistance", "grocery", "food insecurity", "pantry", "school meals"],
    "housing_household_cost_pressure": ["rent", "eviction", "homeless", "utility shutoff", "utility rate", "energy burden"],
    "health_access_pressure": ["hospital closure", "clinic closure", "medicaid", "rural health", "health access", "er closure"],
    "labor_income_pressure": ["layoff", "wage cut", "strike", "unemployment", "plant closure", "warn notice"],
    "financial_distress_pressure": ["bankruptcy", "fiscal stress", "school deficit", "debt distress", "foreclosure"],
    "local_system_strain": ["school cuts", "service cuts", "transportation cuts", "budget cuts", "public service cuts"],
    "environmental_pressure": ["disaster recovery", "insurance", "fema", "flood", "wildfire", "storm"],
    "childcare_school_family_pressure": ["childcare", "school meals", "family services", "after-school"],
    "policy_implementation": ["benefits delay", "program access", "eligibility delay", "application backlog"],
    "transportation_daily_access_pressure": ["transit cuts", "bus service cuts", "route cuts", "transportation access"],
}

EDITOR_STAFF_TERMS = [
    "editor's note", "editors note", "editor note", "signs off", "sign-off", "staff announcement", "newsroom update",
    "masthead", "publisher's note", "publishers note", "personnel update", "farewell", "welcome our new"
]

NON_PRESSURE_TERMS = [
    "opinion", "editorial", "sports", "entertainment", "celebrity", "box office", "crime blotter", "game recap",
    "event listing", "things to do", "calendar listing", "obituary"
]

CITY_STATE_RE = re.compile(r"\b([A-Z][A-Za-z.'-]*(?:\s+[A-Z][A-Za-z.'-]*){0,3}),\s*([A-Z]{2})\b")
COUNTY_STATE_RE = re.compile(r"\b([A-Z][A-Za-z.'-]*(?:\s+[A-Z][A-Za-z.'-]*){0,3}\s+County),\s*([A-Z]{2})\b")
STATE_NAME_RE = re.compile(
    r"\b(Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|District of Columbia|Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming)\b"
)
STATE_ABBR = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO",
    "Connecticut": "CT", "Delaware": "DE", "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI",
    "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA",
    "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ",
    "New Mexico": "NM", "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD",
    "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _date_only(value: str) -> str:
    text = (value or "").strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except Exception:
            pass
    if len(text) >= 10:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").date().isoformat()
        except Exception:
            return ""
    return ""


def _canonical_url(url: str) -> str:
    u = urlsplit((url or "").strip())
    if not (u.scheme and u.netloc):
        return ""
    clean_q = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=False) if not k.lower().startswith("utm_")]
    return urlunsplit((u.scheme.lower(), u.netloc.lower(), u.path.rstrip("/"), urlencode(clean_q), ""))


def _text(*parts: str) -> str:
    return " ".join((p or "").strip() for p in parts if (p or "").strip())


def _infer_pillar(text: str) -> str:
    low = text.lower()
    for pillar, terms in KEYWORDS.items():
        if any(t in low for t in terms):
            return pillar
    return ""


def _reject_reason(text: str) -> str:
    low = text.lower()
    if any(term in low for term in EDITOR_STAFF_TERMS):
        return "editor_note_or_staff_update"
    if any(term in low for term in NON_PRESSURE_TERMS):
        return "non_pressure_topic"
    if "election" in low or "campaign" in low or "polling" in low:
        return "national_politics_without_local_impact"
    return ""


def _extract_location(text: str, fallback_state: str) -> tuple[str, str, str]:
    m_county = COUNTY_STATE_RE.search(text)
    if m_county:
        label = f"{m_county.group(1)}, {m_county.group(2)}"
        return label, m_county.group(2), "county_state"
    m_city = CITY_STATE_RE.search(text)
    if m_city:
        label = f"{m_city.group(1)}, {m_city.group(2)}"
        return label, m_city.group(2), "city_state"
    m_state = STATE_NAME_RE.search(text)
    if m_state:
        state = STATE_ABBR.get(m_state.group(1), fallback_state)
        return state, state, "state_level"
    if fallback_state:
        return fallback_state, fallback_state, "state_level"
    return "", "", ""


def _is_ap_relevant(title: str, snippet: str, source_pillars: list[str]) -> tuple[bool, str, str]:
    combined = _text(title, snippet)
    rej = _reject_reason(combined)
    if rej:
        return False, "", rej
    pillar = _infer_pillar(combined)
    if not pillar:
        mapped_source_pillars = [PILLAR_MAP.get(raw, "") for raw in source_pillars]
        if any(mapped_source_pillars):
            return False, "", "no_household_or_system_strain"
        return False, "", "not_ap_relevant"
    return True, pillar, ""


def _validated_feed_url_map() -> dict[str, tuple[str, str]]:
    if not VALIDATION_REPORT_PATH.exists():
        return {}
    payload = json.loads(VALIDATION_REPORT_PATH.read_text(encoding="utf-8"))
    results = payload.get("results") if isinstance(payload, dict) else []
    out: dict[str, tuple[str, str]] = {}
    if not isinstance(results, list):
        return out
    for row in results:
        if not isinstance(row, dict):
            continue
        if str(row.get("validation_status") or "") != "live_validated":
            continue
        source_id = str(row.get("source_id") or "").strip()
        url = str(row.get("feed_url") or "").strip()
        feed_type = str(row.get("feed_type") or "").strip().lower()
        if not source_id or not url:
            continue
        if feed_type == "json_feed":
            feed_type = "json"
        if feed_type not in {"rss", "atom", "json"}:
            continue
        out[source_id] = (url, feed_type)
    return out


def _feed_url(source: dict[str, Any], validated_map: dict[str, tuple[str, str]]) -> tuple[str, str, str]:
    source_id = str(source.get("source_id") or "").strip()
    if source_id and source_id in validated_map:
        v_url, v_kind = validated_map[source_id]
        return v_url, v_kind, "validation_report.feed_url"
    for field, kind in (("rss_url", "rss"), ("atom_url", "atom"), ("json_feed_url", "json"), ("feed_url", "rss"), ("discovered_feed_url", "rss")):
        v = (source.get(field) or "").strip()
        if v:
            return v, kind, field
    return "", "", ""


def _ssl_context(allow_insecure_ssl: bool) -> tuple[ssl.SSLContext, str, bool, str]:
    if allow_insecure_ssl:
        ctx = ssl._create_unverified_context()  # noqa: SLF001
        return ctx, "insecure", True, "WARNING: insecure SSL mode enabled via --allow-insecure-ssl (debug only)."
    try:
        import certifi  # type: ignore

        ctx = ssl.create_default_context(cafile=certifi.where())
        return ctx, "certifi", False, ""
    except Exception:
        ctx = ssl.create_default_context()
        return ctx, "default", False, "certifi is not installed; using default SSL trust store. Consider: pip install certifi"


def _fetch(url: str, context: ssl.SSLContext) -> tuple[bytes, int | None, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "BlueFernDispatches/0.1"})
    with urllib.request.urlopen(req, timeout=12, context=context) as res:  # noqa: S310
        status = int(getattr(res, "status", 0) or 0)
        content_type = str(res.headers.get("Content-Type") or "")
        return res.read(), status, content_type


def _parse_rss_or_atom(data: bytes) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    root = ET.fromstring(data)
    # RSS
    for item in root.findall(".//item"):
        out.append({
            "title": (item.findtext("title") or "").strip(),
            "url": (item.findtext("link") or "").strip(),
            "snippet": (item.findtext("description") or "").strip(),
            "published_at": (item.findtext("pubDate") or "").strip(),
        })
    # Atom
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for entry in root.findall(".//a:entry", ns):
        link = ""
        link_el = entry.find("a:link", ns)
        if link_el is not None:
            link = (link_el.attrib.get("href") or "").strip()
        out.append({
            "title": (entry.findtext("a:title", default="", namespaces=ns) or "").strip(),
            "url": link,
            "snippet": (entry.findtext("a:summary", default="", namespaces=ns) or entry.findtext("a:content", default="", namespaces=ns) or "").strip(),
            "published_at": (entry.findtext("a:published", default="", namespaces=ns) or entry.findtext("a:updated", default="", namespaces=ns) or "").strip(),
        })
    return out


def _parse_json_feed(data: bytes) -> list[dict[str, str]]:
    payload = json.loads(data.decode("utf-8", errors="ignore"))
    out: list[dict[str, str]] = []
    for item in payload.get("items", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        out.append({
            "title": (item.get("title") or "").strip(),
            "url": (item.get("url") or item.get("external_url") or "").strip(),
            "snippet": (item.get("summary") or item.get("content_text") or "").strip(),
            "published_at": (item.get("date_published") or item.get("date_modified") or "").strip(),
        })
    return out


def _parse_feed(data: bytes, preferred_kind: str, content_type: str) -> tuple[list[dict[str, str]], str]:
    low_ct = (content_type or "").lower()
    text_head = data[:256].decode("utf-8", errors="ignore").lstrip().lower()
    if preferred_kind == "json" or "application/feed+json" in low_ct or text_head.startswith("{"):
        return _parse_json_feed(data), "json"
    if "<feed" in text_head:
        return _parse_rss_or_atom(data), "atom"
    if "<rss" in text_head or "<rdf" in text_head or "xml" in low_ct:
        return _parse_rss_or_atom(data), "rss"
    # last-ditch parse attempts
    try:
        return _parse_rss_or_atom(data), "rss"
    except Exception:
        return _parse_json_feed(data), "json"


def _load_registry() -> list[dict[str, Any]]:
    rows = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]


def run_backfill(start_date: str, end_date: str, max_per_source: int, write: bool, *, allow_insecure_ssl: bool = False) -> dict[str, Any]:
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    if end < start:
        raise ValueError("--end-date must be on or after --start-date")

    registry = _load_registry()
    sources = [r for r in registry if r.get("active") is True and (bool(r.get("feed_validated_live")) or bool(r.get("ingest_ready")))]
    validated_map = _validated_feed_url_map()
    ssl_ctx, ssl_mode, insecure_ssl_used, ssl_message = _ssl_context(allow_insecure_ssl=allow_insecure_ssl)
    if ssl_message:
        print(ssl_message)

    per_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    fetch_diag_rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    scanned_entries = 0
    accepted = 0
    rejected = 0
    duplicate_count = 0
    mappable_count = 0
    unmapped_count = 0
    rej_reasons: Counter[str] = Counter()
    by_state: Counter[str] = Counter()
    by_pillar: Counter[str] = Counter()

    for source in sources:
        source_id = str(source.get("source_id") or "").strip()
        source_name = str(source.get("source_name") or "").strip()
        url, kind, url_field_used = _feed_url(source, validated_map)
        if not url:
            fetch_diag_rows.append(
                {
                    "source_id": source_id,
                    "source_name": source_name,
                    "feed_url_attempted": "",
                    "field_used": "",
                    "fetch_status": "no_feed_url",
                    "http_status": None,
                    "content_type": "",
                    "bytes_read": 0,
                    "parser_used": "",
                    "entries_found": 0,
                    "error_message": "No feed URL present in validation report or registry fields.",
                    "ssl_mode": ssl_mode,
                    "insecure_ssl_used": insecure_ssl_used,
                }
            )
            continue
        try:
            raw, http_status, content_type = _fetch(url, context=ssl_ctx)
            entries, parser_used = _parse_feed(raw, kind, content_type)
        except Exception as exc:
            rej_reasons["feed_fetch_or_parse_failed"] += 1
            fetch_diag_rows.append(
                {
                    "source_id": source_id,
                    "source_name": source_name,
                    "feed_url_attempted": url,
                    "field_used": url_field_used,
                    "fetch_status": "feed_fetch_or_parse_failed",
                    "http_status": None,
                    "content_type": "",
                    "bytes_read": 0,
                    "parser_used": "",
                    "entries_found": 0,
                    "error_message": f"{type(exc).__name__}: {exc}",
                    "ssl_mode": ssl_mode,
                    "insecure_ssl_used": insecure_ssl_used,
                }
            )
            continue
        if not entries:
            rej_reasons["feed_ok_no_entries_in_window"] += 1
            fetch_diag_rows.append(
                {
                    "source_id": source_id,
                    "source_name": source_name,
                    "feed_url_attempted": url,
                    "field_used": url_field_used,
                    "fetch_status": "feed_ok_no_entries_in_window",
                    "http_status": http_status,
                    "content_type": content_type,
                    "bytes_read": len(raw),
                    "parser_used": parser_used,
                    "entries_found": 0,
                    "error_message": "",
                    "ssl_mode": ssl_mode,
                    "insecure_ssl_used": insecure_ssl_used,
                }
            )
            continue

        in_window_entries = 0

        for entry in entries[: max(0, max_per_source)]:
            scanned_entries += 1
            title = (entry.get("title") or "").strip()
            link = _canonical_url(entry.get("url") or "")
            snippet = (entry.get("snippet") or "").strip()
            published_raw = (entry.get("published_at") or "").strip()
            published_date = _date_only(published_raw) or start_date
            if not title or not link or not published_date:
                rejected += 1
                rej_reasons["missing_required_fields"] += 1
                continue
            day = datetime.strptime(published_date, "%Y-%m-%d").date()
            if day < start or day > end:
                rejected += 1
                rej_reasons["outside_date_window"] += 1
                continue
            in_window_entries += 1

            ok, pillar, rej = _is_ap_relevant(title, snippet, [str(p) for p in (source.get("pressure_pillars") or [])])
            if not ok:
                rejected += 1
                rej_reasons[rej or "not_ap_relevant"] += 1
                continue

            loc_text = _text(title, snippet)
            fallback_state = (source.get("state") or "").strip().upper()
            location_label, state, precision = _extract_location(loc_text, fallback_state)
            if not state:
                rejected += 1
                rej_reasons["no_mappable_or_state_context"] += 1
                continue

            dkey = (link, pillar)
            if dkey in seen:
                duplicate_count += 1
                rejected += 1
                rej_reasons["duplicate_syndicated"] += 1
                continue
            seen.add(dkey)

            rec_id = f"feed-backfill-{published_date}-{source.get('source_id')}-{len(per_day[published_date]) + 1:03d}"
            record = {
                "source_record_id": rec_id,
                "source_id": str(source.get("source_id") or "").strip(),
                "title": title,
                "url": link,
                "publisher": str(source.get("source_name") or "").strip(),
                "published_at": published_raw or f"{published_date}T00:00:00Z",
                "retrieved_at": _utc_now(),
                "summary_or_snippet": snippet,
                "source_type": "news_report",
                "region_scope": str(source.get("coverage_scope") or "statewide"),
                "category_hint": pillar,
                "pillar": pillar,
                "reliability_tier": "reputable_reporting",
                "source_state": "feed_backfill",
                "state": state,
                "location": location_label,
                "location_precision": precision,
                "manual_source_role": "human_story",
                "map_collection_source": "feed_backfill",
            }
            per_day[published_date].append(record)
            accepted += 1
            by_state[state] += 1
            by_pillar[pillar] += 1
            if precision:
                mappable_count += 1
            else:
                unmapped_count += 1
        if in_window_entries == 0:
            rej_reasons["feed_ok_no_entries_in_window"] += 1
            status_label = "feed_ok_no_entries_in_window"
        else:
            status_label = "feed_ok_entries_found"
        fetch_diag_rows.append(
            {
                "source_id": source_id,
                "source_name": source_name,
                "feed_url_attempted": url,
                "field_used": url_field_used,
                "fetch_status": status_label,
                "http_status": http_status,
                "content_type": content_type,
                "bytes_read": len(raw),
                "parser_used": parser_used,
                "entries_found": in_window_entries,
                "error_message": "",
                "ssl_mode": ssl_mode,
                "insecure_ssl_used": insecure_ssl_used,
            }
        )

    written_files: list[str] = []
    if write:
        day_cursor = start
        while day_cursor <= end:
            day_key = day_cursor.isoformat()
            out = OUT_SOURCES_ROOT / day_key / "feed_backfill_sources.json"
            rows = list(per_day.get(day_key, []))
            existing_rows: list[dict[str, Any]] = []
            if out.exists():
                try:
                    parsed = json.loads(out.read_text(encoding="utf-8"))
                    if isinstance(parsed, list):
                        existing_rows = [r for r in parsed if isinstance(r, dict)]
                except Exception:
                    existing_rows = []
            combined_rows = existing_rows + rows
            cleaned_rows: list[dict[str, Any]] = []
            seen_rows: set[tuple[str, str]] = set()
            for row in combined_rows:
                title = str(row.get("title") or "").strip()
                snippet = str(row.get("summary_or_snippet") or "").strip()
                url = _canonical_url(str(row.get("url") or ""))
                source_pillars = [str(row.get("pillar") or "").strip()] if row.get("pillar") else []
                ok, inferred_pillar, rej = _is_ap_relevant(title, snippet, source_pillars)
                if not ok:
                    rej_reasons[rej or "not_ap_relevant"] += 1
                    rejected += 1
                    continue
                if not url:
                    rej_reasons["missing_required_fields"] += 1
                    rejected += 1
                    continue
                state = str(row.get("state") or "").strip().upper()
                location = str(row.get("location") or "").strip()
                precision = str(row.get("location_precision") or "").strip()
                if not state:
                    combined_loc = _text(title, snippet)
                    fallback_state = state or ""
                    location, state, precision = _extract_location(combined_loc, fallback_state)
                    if not state:
                        rej_reasons["no_mappable_or_state_context"] += 1
                        rejected += 1
                        continue
                dedupe_key = (url, inferred_pillar or str(row.get("pillar") or "").strip())
                if dedupe_key in seen_rows:
                    rej_reasons["duplicate_syndicated"] += 1
                    rejected += 1
                    duplicate_count += 1
                    continue
                seen_rows.add(dedupe_key)
                row["url"] = url
                row["state"] = state
                row["location"] = location or state
                row["location_precision"] = precision or "state_level"
                row["pillar"] = inferred_pillar or str(row.get("pillar") or "").strip()
                cleaned_rows.append(row)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(cleaned_rows, indent=2), encoding="utf-8")
            written_files.append(str(out))
            day_cursor += timedelta(days=1)

    summary = {
        "generated_at": _utc_now(),
        "start_date": start_date,
        "end_date": end_date,
        "sources_scanned": len(sources),
        "feed_entries_scanned": scanned_entries,
        "accepted_ap_records": accepted,
        "rejected_entries": rejected,
        "accepted_by_state": dict(sorted(by_state.items())),
        "accepted_by_pillar": dict(sorted(by_pillar.items())),
        "mappable_count": mappable_count,
        "unmapped_count": unmapped_count,
        "duplicate_count": duplicate_count,
        "ssl_mode": ssl_mode,
        "insecure_ssl_used": insecure_ssl_used,
        "top_rejection_reasons": dict(rej_reasons.most_common(10)),
        "written_files": written_files,
    }
    OUT_SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    OUT_SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    OUT_FETCH_DIAGNOSTICS.parent.mkdir(parents=True, exist_ok=True)
    OUT_FETCH_DIAGNOSTICS.write_text(json.dumps(fetch_diag_rows, indent=2), encoding="utf-8")
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backfill AP feed-derived source records for map coverage.")
    p.add_argument("--start-date", required=True)
    p.add_argument("--end-date", required=True)
    p.add_argument("--max-per-source", type=int, default=10)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--write", action="store_true")
    p.add_argument("--allow-insecure-ssl", action="store_true", help="Disable TLS verification for local debugging only.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    datetime.strptime(args.start_date, "%Y-%m-%d")
    datetime.strptime(args.end_date, "%Y-%m-%d")
    if args.dry_run and args.write:
        raise ValueError("use either --dry-run or --write")
    summary = run_backfill(
        args.start_date,
        args.end_date,
        args.max_per_source,
        write=bool(args.write and not args.dry_run),
        allow_insecure_ssl=bool(args.allow_insecure_ssl),
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
