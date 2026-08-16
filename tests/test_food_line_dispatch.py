import csv
import base64
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import textwrap
import types
import urllib.error
from datetime import date as dt_date, datetime, timezone
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

import scripts.check_food_line_blue_fern_compliance as food_line_compliance
import scripts.discover_food_line_sources as food_line_discovery
import scripts.publish_food_line_review_only as food_line_review_publish
import scripts.run_food_line_dispatch as food_line
import scripts.update_food_line_archive_for_review_only as food_line_archive_update
import bluefern_dispatches.bluesky_post as bluesky_post
import bluefern_dispatches.food_line_bluesky_approval as food_line_bluesky_approval
import scripts.test_food_line_tts as food_line_tts
import bluefern_dispatches.tts_provider as tts_provider
from bluefern_dispatches.generator import public_edition_is_listable
from scripts.discover_food_line_sources import discover_food_line_sources, load_food_line_source_discovery_queries
from scripts.run_food_line_dispatch import run_food_line_dispatch
from scripts.test_food_line_candidate_sources import cleanup_food_line_candidates, import_food_line_candidate_intake, test_food_line_candidate_sources as run_food_line_candidate_sources
from bluefern_dispatches.food_line_sources import GENERIC_PRESSURE_SUMMARIES, load_food_line_candidate_registry, load_food_line_registry, validate_food_line_source_freshness
from bluefern_dispatches.tts_provider import TTSResult, TTSDiagnostics


@pytest.fixture(autouse=True)
def _food_line_suite_today(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(food_line, "_food_line_local_today", lambda: dt_date(2026, 6, 30))
    monkeypatch.setattr(food_line, "FOOD_LINE_FRESHNESS_WINDOW_DAYS", 14)


def _ensure_assets(root: Path) -> None:
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    repo_assets = Path(__file__).parent.parent / "assets"
    for asset_name in (
        "bluefern.png",
        "food-line-logo.png",
        "food-line-dispatch-social.png",
        "site.css",
        "favicon.ico",
        "favicon-16x16.png",
        "favicon-32x32.png",
        "apple-touch-icon.png",
    ):
        source = repo_assets / asset_name
        if source.exists():
            (assets / asset_name).write_bytes(source.read_bytes())
    if not (assets / "bluefern.png").exists():
        png_bytes = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9W2jN9kAAAAASUVORK5CYII="
        )
        (assets / "bluefern.png").write_bytes(png_bytes)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _manual_path(root: Path, date: str) -> Path:
    return root / "data" / "dispatches" / "food-line" / "sources" / date / "manual_sources.json"


def _write_food_line_bluesky_preview_fixture(root: Path, edition_date: str, *, public_url: str, public_summary: str) -> None:
    manifest_path = root / "output" / "site" / "food-line" / "editions" / edition_date / "edition_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "edition_date": edition_date,
                "public_url": public_url,
                "public_rendered": True,
                "public_signal_count": 2,
                "edition_mode": "current_update",
                "validation_status": "ok",
                "public_summary": public_summary,
                "bluesky_post_ready": False,
                "bluesky_post_text": None,
            }
        ),
        encoding="utf-8",
    )
    review_path = root / "data" / "dispatches" / "food-line" / "review" / "proposed-editions" / f"{edition_date}.json"
    review_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.write_text(
        json.dumps(
            {
                "layout": {
                    "todays_read": [{"summary": public_summary}],
                    "core_food_pressure_signals": [{"summary": public_summary}],
                },
                "items": [{"review_item_id": f"{edition_date}-001"}],
            }
        ),
        encoding="utf-8",
    )


def _food_line_regression_fixture_path() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "food_line" / "regression_2026-06-08_sources.json"


def _load_food_line_regression_fixture() -> list[dict]:
    return json.loads(_food_line_regression_fixture_path().read_text(encoding="utf-8"))


def _food_line_june_11_rows() -> tuple[dict, dict]:
    wsls = {
        "source_record_id": "wsls-roanoke-st-francis-house-food-shortage-20260610",
        "title": "Why Roanoke's St. Francis House is facing its tightest food shortage ever this summer",
        "url": "https://www.wsls.com/news/local/2026/06/10/why-roanokes-st-francis-house-is-facing-its-tightest-food-shortage-ever-this-summer/",
        "publisher": "WSLS",
        "published_at": "2026-06-10T06:24:00",
        "page_metadata_date": "2026-06-10T09:57:00",
        "retrieved_at": "2026-06-11T00:00:00Z",
        "summary_or_snippet": "St. Francis House had empty shelves in May. The June USDA delivery was smaller than May's, and the pantry is down 64% compared with January. Summer school-meal gaps and SNAP/USDA pressure are adding strain.",
        "evidence_text": (
            "ROANOKE, Va. - Roanoke City's St. Francis House Food Pantry faced completely empty shelves in May. "
            "Now in June, the pantry is facing an even tighter situation heading into summer, and the people who run it say the situation is only getting harder. "
            "St. Francis House received a new USDA food shipment for June, but the entire delivery is expected to last through the end of the month, and they received even less food than they had in May. "
            "In May, the pantry ran out of food in just two weeks. The June delivery was even smaller than May's. "
            "Enge said the shortfall is significant and is causing them to hand out less food. "
            "Summer is one of the busiest seasons for food pantries, as children who typically receive free or reduced-price lunches during the school year lose access to those daily meals. "
            "At the same time, cuts to SNAP and other USDA programs are leaving more families with fewer options."
        ),
        "evidence_text_basis": "page_text_excerpt",
        "source_type": "page",
        "source_family": "local_news",
        "state": "VA",
        "location_name": "Roanoke, VA",
        "location_scope": "local",
        "country": "US",
        "source_purpose": "current_news",
        "primary_source_url": "https://www.wsls.com/news/local/2026/06/10/why-roanokes-st-francis-house-is-facing-its-tightest-food-shortage-ever-this-summer/",
        "source_traceability_role": "article_url",
        "issue_tags": ["food shortage", "pantry capacity", "SNAP", "school meals"],
        "map_category": "acute strain / service disruption",
        "positive_keywords": ["food shortage", "empty shelves", "USDA", "SNAP", "school meals", "pantry"],
        "negative_keywords": ["recipe", "restaurant review", "menu", "cooking tips", "chef", "grocery sale"],
        "affected_group_keywords": ["pantry clients", "SNAP households", "families", "children"],
    }
    kold = {
        "source_record_id": "kold-tucson-food-bank-sees-surge-visitors-inflation-rises-20260610",
        "title": "Tucson food bank sees surge in visitors as inflation rises",
        "url": "https://www.kold.com/2026/06/11/tucson-food-bank-sees-surge-visitors-inflation-rises/",
        "publisher": "KOLD / 13 News",
        "published_at": "2026-06-10T18:53:00-07:00",
        "page_metadata_date": "2026-06-10T18:53:00-07:00",
        "retrieved_at": "2026-06-11T00:00:00Z",
        "summary_or_snippet": "Catholic Community Services' Tucson food bank saw rising demand from first-time visitors. Supplies were running out more regularly, and some visitors could not get food because lines were too long or supplies were nearly gone.",
        "evidence_text": (
            "Catholic Community Services' food bank has seen rising demand from first-time visitors and to their clothing donation center. "
            "Many of the people who are now coming in haven't consistently used community food resources in the past. "
            "Vanessa Rodriquez said she's begun utilizing food banks for the very first time in her life as her grocery bills have gotten too high. "
            "Tim Kromer with Catholic Community Services said he's seen that need increase on a daily basis at the food bank. "
            "With rising demand, supplies are dwindling quicker than usual. "
            "Rodriquez said her most recent trip was unsuccessful because the line was so long and the bank was already giving out the last of what it had. "
            "Because it's summer, families with children are seeing a large increase in need."
        ),
        "evidence_text_basis": "page_text_excerpt",
        "source_type": "page",
        "source_family": "local_news",
        "state": "AZ",
        "location_name": "Tucson, AZ",
        "location_scope": "local",
        "country": "US",
        "source_purpose": "current_news",
        "primary_source_url": "https://www.kold.com/2026/06/11/tucson-food-bank-sees-surge-visitors-inflation-rises/",
        "source_traceability_role": "article_url",
        "issue_tags": ["food bank demand", "pantry capacity", "SNAP", "food assistance"],
        "map_category": "demand strain",
        "positive_keywords": ["food bank", "food assistance", "first-time", "supplies are dwindling", "running out", "SNAP", "inflation", "families with children"],
        "negative_keywords": ["recipe", "restaurant review", "menu", "cooking tips", "chef", "grocery sale"],
        "affected_group_keywords": ["first-time food bank users", "families with children", "SNAP households", "low-income households"],
    }
    return kold, wsls


def _ktal_manual_source() -> dict:
    return {
        "source_record_id": "ktal-food-bank-summer-feeding-20260610",
        "title": "Food Bank of Northwest Louisiana says summer feeding need is tightening inventory",
        "url": "https://www.ktalnews.com/news/food-bank-summer-feeding/",
        "publisher": "KTAL / KMSS",
        "published_at": "2026-06-10T12:00:00Z",
        "published_date_basis": "published_at",
        "page_metadata_date": "",
        "date_provenance_warning": "",
        "retrieved_at": "2026-06-10T12:00:00Z",
        "summary_or_snippet": "Food Bank of Northwest Louisiana is providing summer meals and groceries to children and families. Meeting need has become increasingly difficult. Rising food costs and lower donations have left the pantry with one of its lowest inventory levels in years, and inventory is about 31% lower than at the same time last year.",
        "evidence_text": (
            "Food Bank of Northwest Louisiana is providing summer meals and groceries to children and families. "
            "Meeting need has become increasingly difficult. Rising food costs and lower donations have left the pantry with one of its lowest inventory levels in years, and inventory is about 31% lower than at the same time last year. "
            "The food bank says it has capacity to feed more children but needs partners."
        ),
        "evidence_text_basis": "manual_review",
        "source_type": "manual",
        "source_family": "local_news",
        "state": "LA",
        "issue_tags": [],
        "map_category": "summer meal / child nutrition",
        "location_name": "Northwest Louisiana",
        "country": "",
        "source_purpose": "current_news",
        "primary_source_url": "https://www.ktalnews.com/news/food-bank-summer-feeding/",
        "source_traceability_role": "article_url",
        "pressure_signal": True,
        "pressure_type": "service reduction",
        "pressure_reason": "matched service reduction",
        "pressure_summary": "KTAL / KMSS reported reduced distribution hours in Northwest Louisiana, affecting children, low-income households.",
        "affected_groups": ["children", "low-income households"],
        "evidence_level": "news report",
        "freshness_role": "fresh_daily_signal",
        "freshness_status": "fresh_daily_signal",
        "freshness_disqualification_reason": "",
        "source_published_date": "2026-06-10",
        "collected_date": "2026-06-10",
        "source_role": "local_signal",
        "location_scope": "state_local",
        "supported_product_geography": True,
        "source_role_allowed": "context_only",
        "pressure_required": False,
        "positive_keywords": ["food bank", "pantry", "hunger", "food insecurity", "SNAP", "meal site", "grocery closure"],
        "negative_keywords": ["recipe", "restaurant", "menu", "chef", "sale", "festival", "gala", "volunteer", "donation drive"],
        "affected_group_keywords": [
            "SNAP households",
            "WIC households",
            "adults 25-44",
            "children",
            "disaster-affected households",
            "low-income households",
            "non-white adults",
            "rural residents",
            "seniors",
            "working-age adults without a college degree",
        ],
        "max_age_days": 14,
        "current_or_evergreen": "current",
        "promotable": True,
        "non_promotable_reason": "",
        "rejected": False,
        "rejection_reason": "",
        "extraction_quality": "medium",
        "expected_text_basis": "page_text",
        "pressure_verification_required": True,
        "date_basis": "published_at",
        "map_eligible": True,
        "coordinate_basis": "",
        "source_freshness_status": "fresh_daily_signal",
        "source_freshness_disqualification_reason": "",
        "source_freshness_window_days": 3,
        "source_published_date_basis": "published_at",
        "source_url_date": "",
        "source_url_date_basis": "",
        "source_freshness_date_basis": "published_at",
        "source_public_story_eligible": True,
        "primary_eligible": True,
        "primary_disqualification_reason": "",
        "claim_supported": "Food Bank of Northwest Louisiana is providing summer meals and groceries to children and families. Meeting need has become increasingly difficult. Rising food costs and lower donations have left the pantry with one of its lowest inventory levels in years, and inventory is about 31% lower than at the same time last year. The food bank says it has capacity to feed more children but needs partners, as school-meal gaps add strain.",
        "limitations": "The source documents service strain in Northwest Louisiana, but it does not measure total unmet need across the full service area.",
        "included": True,
        "exclusion_reason": "",
    }


def _wpde_manual_source() -> dict:
    return {
        "source_record_id": "wpde-grand-strand-food-insecurity-20260612",
        "title": "Grand Strand food providers say inflation is driving more families to pantries",
        "url": "https://wpde.com/news/local/new-data-show-food-insecurity-higher-than-during-covid-19-with-horry-county-at-14",
        "publisher": "WPDE / ABC 15",
        "published_at": "2026-06-12T21:58:00Z",
        "retrieved_at": "2026-06-12T21:58:00Z",
        "summary_or_snippet": "Food insecurity in Horry County is about 14 percent and about 20 percent of children are food insecure, while the Lowcountry Food Bank said some Conway distributions usually serving about 100 families had 185 and demand is climbing at pantries and mobile distributions.",
        "evidence_text": (
            "Grand Strand food providers say inflation is driving more families to pantries WPDE — Food insecurity is rising above levels seen during the COVID-19 pandemic. "
            "In Horry County, Feeding America’s most recent Map the Meal Gap report shows about 14 percent of residents are food insecure and about 20 percent of Horry County’s children are considered food insecure. "
            "The Lowcountry Food Bank said some mobile distributions in Conway that usually served about 100 families had 185. "
            "Inflation and higher costs are making it harder for families to afford food and for food banks to source it. "
            "ABC 15 is teaming up with Feeding America for Sinclair Cares: Summer Hunger Relief, encouraging donations to help provide food for kids during the summer. "
            "More information and donations are available at sinclaircares.com."
        ),
        "evidence_text_basis": "manual_review",
        "source_type": "manual",
        "source_family": "local_news",
        "state": "SC",
        "location_name": "Horry County",
        "location_scope": "state_local",
        "source_purpose": "current_news",
        "primary_source_url": "https://wpde.com/news/local/new-data-show-food-insecurity-higher-than-during-covid-19-with-horry-county-at-14",
        "source_traceability_role": "article_url",
        "pressure_signal": True,
        "pressure_type": "demand strain",
        "pressure_reason": "Matched demand strain; the article reports higher food insecurity, rising pantry demand, and mobile distributions serving 185 families.",
        "pressure_summary": "Food insecurity in Horry County is about 14 percent and about 20 percent of children are food insecure, while the Lowcountry Food Bank said some Conway distributions usually serving about 100 families had 185 and inflation was making food harder to afford and source.",
        "affected_groups": ["children", "low-income households", "pantry clients"],
        "evidence_level": "news report",
        "freshness_role": "fresh_daily_signal",
        "source_role": "local_signal",
        "map_category": "elevated demand",
        "map_eligible": True,
        "pressure_verification_status": "source_text_verified",
    }


def _tulsa_manual_source() -> dict:
    return {
        "source_record_id": "tulsa-flyer-food-bank-fuel-costs-20260612",
        "title": "Tulsa Flyer Food Bank Fuel Costs",
        "url": "https://tulsaflyer.org/2026/06/12/your-money/post/food-bank-fuel-costs/",
        "publisher": "Tulsa Flyer",
        "published_at": "2026-06-12T12:00:00Z",
        "retrieved_at": "2026-06-12T12:00:00Z",
        "summary_or_snippet": "Food Bank of Eastern Oklahoma reported diesel costs of about $24,000–$26,000 per month, compared with about $12,000–$14,000 in typical summer months, reducing meal capacity for children, summer meal recipients, and food-bank clients.",
        "evidence_text": "Food Bank of Eastern Oklahoma reported diesel costs of about $24,000–$26,000 per month, compared with about $12,000–$14,000 in typical summer months, reducing meal capacity for children, summer meal recipients, and food-bank clients.",
        "evidence_text_basis": "manual_review",
        "source_type": "manual",
        "source_family": "local_news",
        "state": "OK",
        "location_name": "Tulsa, OK",
        "location_scope": "local",
        "source_purpose": "current_news",
        "primary_source_url": "https://tulsaflyer.org/2026/06/12/your-money/post/food-bank-fuel-costs/",
        "source_traceability_role": "article_url",
        "pressure_signal": True,
        "pressure_type": "fuel cost strain",
        "pressure_reason": "Matched fuel cost strain; the article reports diesel costs of about $24,000–$26,000 per month versus about $12,000–$14,000 in typical summer months, reducing meal capacity.",
        "pressure_summary": "Food Bank of Eastern Oklahoma reported diesel costs of about $24,000–$26,000 per month, compared with about $12,000–$14,000 in typical summer months, reducing meal capacity for children, summer meal recipients, and food-bank clients.",
        "affected_groups": ["children", "summer meal recipients", "food-bank clients"],
        "evidence_level": "news report",
        "freshness_role": "fresh_daily_signal",
        "source_role": "provider_signal",
        "map_category": "acute strain / service disruption",
        "map_eligible": True,
        "pressure_verification_status": "source_text_verified",
    }


def _wkrn_policy_access_source() -> dict:
    return {
        "source_record_id": "wkrn-tennessee-snap-enrollment-drop-20260612",
        "title": "Tennessee SNAP enrollment dropped by more than 100,000",
        "url": "https://www.wkrn.com/news/tennessee-politics/tennessee-snap-enrollment-drops-more-than-100000/",
        "publisher": "WKRN",
        "published_at": "2026-06-12T22:15:07Z",
        "retrieved_at": "2026-06-12T22:15:07Z",
        "summary_or_snippet": "Tennessee SNAP enrollment fell by more than 100,000 people in less than a year, with state administrative data tracking individuals, households, and benefit allotment.",
        "evidence_text": "Tennessee SNAP enrollment fell by more than 100,000 people in less than a year, with state administrative data tracking individuals, households, and benefit allotment.",
        "evidence_text_basis": "manual_review",
        "source_type": "manual",
        "source_family": "local_news",
        "state": "TN",
        "location_name": "Tennessee",
        "location_scope": "state",
        "source_purpose": "current_news",
        "primary_source_url": "https://www.wkrn.com/news/tennessee-politics/tennessee-snap-enrollment-drops-more-than-100000/",
        "secondary_source_url": "https://www.tn.gov/humanservices/for-families/supplemental-nutrition-assistance-program-snap/snap-statistical-information.html",
        "source_traceability_role": "article_url",
        "pressure_signal": True,
        "pressure_type": "benefit access decline",
        "pressure_reason": "Matched benefit access decline; the article reports Tennessee SNAP enrollment fell by more than 100,000 people in less than a year.",
        "pressure_summary": "Tennessee SNAP enrollment fell by more than 100,000 people in less than a year, pointing to SNAP access pressure.",
        "affected_groups": ["SNAP households", "low-income households"],
        "evidence_level": "news report with state administrative data",
        "freshness_role": "fresh_daily_signal",
        "source_role": "policy_context",
        "map_category": "benefit disruption",
        "map_eligible": True,
        "pressure_verification_status": "source_text_verified",
        "claim_supported": "Tennessee SNAP enrollment fell by more than 100,000 people in less than a year, pointing to SNAP access pressure.",
        "limitations": "SNAP enrollment decline does not by itself prove reduced food need; it may reflect eligibility changes, recertification churn, administrative barriers, employment or income changes, or policy effects unless the source isolates causes.",
    }


def _review_only_candidate(
    *,
    title: str,
    publisher: str,
    source_url: str,
    source_published_date: str = "2026-06-12",
    pressure_summary: str = "",
    pressure_type: str = "",
    affected_groups: list[str] | None = None,
    evidence_level: str = "background context",
    freshness_role: str = "fresh_daily_signal",
    source_role: str = "policy_analysis",
    public_claim_eligible: bool = True,
    public_claim_blockers: list[str] | None = None,
    candidate_review_status: str = "approved",
    location_scope: str = "national",
    pressure_signal_hint: str = "",
    classification_status: str = "qualified_pressure_signal",
) -> dict:
    return {
        "title": title,
        "publisher": publisher,
        "source_url": source_url,
        "source_published_date": source_published_date,
        "pressure_summary": pressure_summary,
        "pressure_type": pressure_type,
        "affected_groups": list(affected_groups or []),
        "evidence_level": evidence_level,
        "freshness_role": freshness_role,
        "source_role": source_role,
        "public_claim_eligible": public_claim_eligible,
        "public_claim_blockers": list(public_claim_blockers or []),
        "candidate_review_status": candidate_review_status,
        "traceability_status": "traceable",
        "location_scope": location_scope,
        "pressure_signal_hint": pressure_signal_hint,
        "classification_status": classification_status,
    }


def _write_review_only_candidate_review(root: Path, date: str, candidates: list[dict]) -> Path:
    review_path = root / "output" / "review" / "food-line" / date / "candidate_review.json"
    review_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": "2026-06-29T00:00:00Z",
        "edition_date": date,
        "public_claim_eligible_count": sum(1 for row in candidates if bool(row.get("public_claim_eligible"))),
        "candidate_count_total": len(candidates),
        "candidates": candidates,
    }
    review_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return review_path


def _build_review_only_render_dir(root: Path, date: str, candidates: list[dict]) -> Path:
    review_path = _write_review_only_candidate_review(root, date, candidates)
    food_line.render_food_line_review_only(
        root,
        date=date,
        candidate_review_path=review_path,
        public_eligible_only=True,
    )
    return root / "output" / "site-review-only" / "food-line" / "editions" / date


def _food_line_archive_html_fixture() -> str:
    return """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Food Line Dispatch Archive</title></head>
<body>
  <main class="home food-line-shell">
    <section class="food-line-panel">
      <h2>Latest edition</h2>
      <p><a href="editions/2026-06-20/">2026-06-20 — No current update</a></p>
      <h2>Archive</h2>
      <ul>
        <li><a href="editions/2026-06-20/">2026-06-20 — No current update</a></li>
        <li><a href="editions/2026-06-19/">2026-06-19 — Charlotte summer meal strain</a></li>
        <li><a href="editions/2026-06-18/">2026-06-18 — No current update</a></li>
        <li><a href="editions/2026-06-17/">2026-06-17 — United States food insecurity and United States food-pressure</a></li>
        <li><a href="editions/2026-06-16/">2026-06-16 — St. Lawrence County pantry demand</a></li>
        <li><a href="editions/2026-06-14/">2026-06-14 — No current update</a></li>
        <li><a href="editions/2026-06-13/">2026-06-13 — No current update</a></li>
        <li><a href="editions/2026-06-09/">2026-06-09 — No current update</a></li>
        <li><a href="editions/2026-06-07/">2026-06-07 — No current update</a></li>
        <li><a href="editions/2026-06-06/">2026-06-06 — No current update</a></li>
      </ul>
    </section>
  </main>
</body>
</html>
"""


def _setup_food_line_archive_review_only_pages_fixture(tmp_path: Path, *, include_frac_url: bool = True) -> tuple[Path, Path, Path]:
    pages_repo = tmp_path / "bluefern-dispatches-pages"
    archive_path = pages_repo / "food-line" / "archive.html"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_text(_food_line_archive_html_fixture(), encoding="utf-8")
    (pages_repo / "food-line" / "index.html").write_text("<html>Home</html>", encoding="utf-8")
    (pages_repo / "food-line" / "rss.xml").write_text("<rss></rss>", encoding="utf-8")
    (pages_repo / "food-line" / "podcast.xml").write_text("<rss></rss>", encoding="utf-8")
    (pages_repo / "food-line" / "audio" / "index.html").parent.mkdir(parents=True, exist_ok=True)
    (pages_repo / "food-line" / "audio" / "index.html").write_text("<html>Audio</html>", encoding="utf-8")
    (pages_repo / "food-line" / "map" / "index.html").parent.mkdir(parents=True, exist_ok=True)
    (pages_repo / "food-line" / "map" / "index.html").write_text("<html>Map</html>", encoding="utf-8")
    edition_path = pages_repo / "food-line" / "editions" / "2026-06-12" / "index.html"
    edition_path.parent.mkdir(parents=True, exist_ok=True)
    edition_html = "<html><body>FRAC edition</body></html>"
    if include_frac_url:
        edition_html = (
            "<html><body>"
            "FRAC edition "
            "https://frac.org/blog/usda-proposal-to-end-broad-based-categorical-eligibility-for-snap-would-increase-hunger-for-families-and-children"
            "</body></html>"
        )
    edition_path.write_text(edition_html, encoding="utf-8")
    return pages_repo, archive_path, edition_path


def test_food_line_manual_source_file_includes_wkrn_policy_access_signal():
    manual_sources = json.loads(_manual_path(Path.cwd(), "2026-06-12").read_text(encoding="utf-8"))
    row = next(item for item in manual_sources if item["source_record_id"] == "wkrn-tennessee-snap-enrollment-drop-20260612")
    assert row["source_role"] == "policy_context"
    assert row["pressure_type"] == "benefit access decline"
    assert row["source_purpose"] == "current_news"
    assert row["location_name"] == "Tennessee"
    assert row["location_scope"] == "state"
    assert row["pressure_summary"].startswith("Tennessee SNAP enrollment fell by more than 100,000 people in less than a year")
    assert row["claim_supported"].startswith("Tennessee SNAP enrollment fell by more than 100,000 people in less than a year")
    assert row["limitations"].startswith("SNAP enrollment decline does not by itself prove reduced food need")


def test_food_line_wkrn_policy_access_signal_stays_non_lead_and_carries_limitation(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-12"
    manual_path = _manual_path(tmp_path, date)
    manual_path.parent.mkdir(parents=True, exist_ok=True)
    manual_path.write_text(
        json.dumps([_wpde_manual_source(), _tulsa_manual_source(), _wkrn_policy_access_source()], indent=2),
        encoding="utf-8",
    )

    result = run_food_line_dispatch(tmp_path, date, generate_audio=False)
    edition_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "index.html").read_text(encoding="utf-8")
    source_table_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "source_table.html").read_text(encoding="utf-8")
    claim_ledger_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "claim_ledger.html").read_text(encoding="utf-8")

    assert result["ok"] is True
    assert result["public_signal_count"] == 3
    assert result["lead_source_record_id"] == "wpde-grand-strand-food-insecurity-20260612"
    assert "WKRN" in edition_html
    assert "WKRN" in source_table_html
    assert "WKRN" in claim_ledger_html
    assert "SNAP access pressure" in claim_ledger_html
    assert "does not by itself prove reduced food need" in claim_ledger_html
    assert "more than 100,000" in claim_ledger_html
    assert "Tennessee" in claim_ledger_html
    assert "WPDE / ABC 15" in edition_html
    assert "Tulsa Flyer" in edition_html


def _clear_food_line_registries(root: Path) -> None:
    registry_dir = root / "data" / "dispatches" / "food-line"
    registry_dir.mkdir(parents=True, exist_ok=True)
    (registry_dir / "source_registry.json").write_text("[]", encoding="utf-8")
    (registry_dir / "pressure_source_registry.json").write_text("[]", encoding="utf-8")


def _row(
    i: int,
    family: str = "federal_official",
    state: str = "CA",
    *,
    title: str | None = None,
    summary: str | None = None,
    source_type: str = "manual",
    publisher: str = "Example News",
) -> dict:
    return {
        "source_record_id": f"food-line-src-{i:03d}",
        "title": title or f"Source {i}",
        "url": f"https://example.com/{i}",
        "publisher": publisher,
        "published_at": "2026-06-01T12:00:00Z",
        "retrieved_at": "2026-06-01T13:00:00Z",
        "summary_or_snippet": summary or "Local pantry reports elevated demand.",
        "source_type": source_type,
        "source_family": family,
        "state": state,
        "issue_tags": ["food banks / pantry capacity", "household hardship signal"],
        "map_category": "elevated demand",
        "location_name": "Sacramento",
    }


def _pressure_row(
    i: int,
    title: str,
    summary: str,
    *,
    family: str,
    state: str = "US",
    source_type: str = "rss",
    publisher: str = "Example News",
) -> dict:
    row = _row(i, family=family, state=state, title=title, summary=summary, source_type=source_type, publisher=publisher)
    row["issue_tags"] = []
    row["map_category"] = "context / monitoring only"
    row["extraction_quality"] = "high"
    row["expected_text_basis"] = "rss_summary"
    row["pressure_verification_required"] = True
    return row


def _write_pressure_registry(root: Path, payload: list[dict]) -> Path:
    registry_dir = root / "data" / "dispatches" / "food-line"
    registry_dir.mkdir(parents=True, exist_ok=True)
    source_registry_path = registry_dir / "source_registry.json"
    if not source_registry_path.exists():
        source_registry_path.write_text("[]", encoding="utf-8")
    path = registry_dir / "pressure_source_registry.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _write_source_registry(root: Path, payload: list[dict]) -> Path:
    registry_dir = root / "data" / "dispatches" / "food-line"
    registry_dir.mkdir(parents=True, exist_ok=True)
    path = registry_dir / "source_registry.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _write_candidate_registry(root: Path, payload: list[dict]) -> Path:
    registry_dir = root / "data" / "dispatches" / "food-line"
    registry_dir.mkdir(parents=True, exist_ok=True)
    path = registry_dir / "candidate_source_registry.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _write_food_line_discovery_gap_report(
    root: Path,
    date: str,
    candidates: list[dict],
    *,
    likely_qualifying_count: int | None = None,
    blocking_likely_qualifying_count: int | None = None,
    unresolved_likely_qualifying_count: int | None = None,
    manual_review_only_count: int | None = None,
    likely_resource_only_count: int | None = None,
    duplicate_or_known_count: int | None = None,
    needs_review_count: int | None = None,
) -> tuple[Path, Path]:
    report_dir = root / "data" / "dispatches" / "food-line" / "discovery_gap" / date
    report_dir.mkdir(parents=True, exist_ok=True)
    likely_qualifying = [row for row in candidates if row.get("classification") == "likely_qualifying"]
    likely_resource_only = [row for row in candidates if row.get("classification") == "likely_resource_only"]
    duplicate_or_known = [row for row in candidates if row.get("classification") == "duplicate_or_known"]
    needs_review = [row for row in candidates if row.get("classification") == "needs_review"]
    report = {
        "date": date,
        "generated_at": f"{date}T12:00:00Z",
        "query_source": "google_news_rss",
        "query_count": 1,
        "queries": ["food insecurity"],
        "exclude_domains": ["facebook.com"],
        "candidate_count": len(candidates),
        "likely_qualifying_count": likely_qualifying_count if likely_qualifying_count is not None else len(likely_qualifying),
        "blocking_likely_qualifying_count": blocking_likely_qualifying_count if blocking_likely_qualifying_count is not None else sum(1 for row in likely_qualifying if row.get("publication_blocking_candidate", True)),
        "unresolved_likely_qualifying_count": unresolved_likely_qualifying_count if unresolved_likely_qualifying_count is not None else sum(1 for row in likely_qualifying if not row.get("publication_blocking_candidate", True)),
        "manual_review_only_count": manual_review_only_count if manual_review_only_count is not None else len(needs_review),
        "needs_review_count": needs_review_count if needs_review_count is not None else len(needs_review),
        "likely_resource_only_count": likely_resource_only_count if likely_resource_only_count is not None else len(likely_resource_only),
        "duplicate_or_known_count": duplicate_or_known_count if duplicate_or_known_count is not None else len(duplicate_or_known),
        "query_errors": [],
        "candidates": candidates,
        "summary": {
            "candidates_reviewed": len(candidates),
            "likely_qualifying": likely_qualifying_count if likely_qualifying_count is not None else len(likely_qualifying),
            "blocking_likely_qualifying": blocking_likely_qualifying_count if blocking_likely_qualifying_count is not None else sum(1 for row in likely_qualifying if row.get("publication_blocking_candidate", True)),
            "unresolved_likely_qualifying": unresolved_likely_qualifying_count if unresolved_likely_qualifying_count is not None else sum(1 for row in likely_qualifying if not row.get("publication_blocking_candidate", True)),
            "manual_review_only": manual_review_only_count if manual_review_only_count is not None else len(needs_review),
            "needs_review": needs_review_count if needs_review_count is not None else len(needs_review),
            "already_known": duplicate_or_known_count if duplicate_or_known_count is not None else len(duplicate_or_known),
            "likely_resource_only": likely_resource_only_count if likely_resource_only_count is not None else len(likely_resource_only),
        },
    }
    report_path = report_dir / "discovery_gap_report.json"
    report_markdown_path = report_dir / "discovery_gap_report.md"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    report_markdown_path.write_text("# Discovery gap report\n", encoding="utf-8")
    return report_path, report_markdown_path


def _write_source_collection_gold_set(root: Path, date: str, payload: list[dict]) -> Path:
    path = root / "data" / "dispatches" / "food-line" / "source_collection_gold_sets" / f"{date}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _http_urls(text: str) -> list[str]:
    urls = {
        url
        for url in re.findall(r'href="(https?://[^"]+)"', text)
        if "dispatches.thebluefernco.com" not in url and "thebluefernco.com" not in url
    }
    return sorted(urls)


def _freshen_food_line_payload_for_publication(rows: list[dict], date: str) -> list[dict]:
    fresh_rows: list[dict] = []
    for row in rows:
        fresh = dict(row)
        fresh["published_at"] = f"{date}T12:00:00Z"
        fresh["retrieved_at"] = f"{date}T13:00:00Z"
        fresh_url = str(fresh.get("url") or "")
        if re.search(r"/20\d{2}/\d{2}", fresh_url):
            fresh["url"] = f"https://example.com/food-line/{fresh.get('source_record_id') or fresh.get('title') or 'fresh'}"
        fresh_rows.append(fresh)
    return fresh_rows


def _write_intake_csv(root: Path, rows: list[dict[str, str]]) -> Path:
    path = root / "data" / "dispatches" / "food-line" / "candidate_source_intake_template.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "source_id",
        "source_name",
        "publisher",
        "candidate_url",
        "source_family",
        "source_type",
        "state",
        "location_name",
        "location_scope",
        "candidate_reason",
        "expected_text_basis",
        "extraction_quality_guess",
        "pressure_topics_expected",
        "status",
        "notes",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def _mock_food_line_tts(monkeypatch: pytest.MonkeyPatch, *, audio_bytes: bytes = b"fake-mp3-data") -> None:
    monkeypatch.setattr(
        food_line,
        "synthesize_speech_with_diagnostics",
        lambda **kwargs: (
            TTSResult(True, audio_bytes, "openai", kwargs.get("model"), kwargs.get("voice"), kwargs.get("audio_format"), None),
            TTSDiagnostics(
                provider="openai",
                model_requested=kwargs.get("model"),
                voice_requested=kwargs.get("voice"),
                narration_char_count=len(str(kwargs.get("text") or "")),
                output_path_attempted=str(kwargs.get("output_path") or ""),
                api_key_present=True,
                output_dir_exists=True,
                partial_mp3_exists=False,
                elapsed_seconds=0.01,
                exception_type=None,
                exception_message_sanitized=None,
                timeout_seconds=float(kwargs.get("timeout") or 90.0),
                audio_format=kwargs.get("audio_format"),
                tls_verify=True,
                ca_file_used=None,
                ca_source="system_default",
                truststore_requested=False,
                truststore_available=False,
                ssl_cert_file_env=None,
                requests_ca_bundle_env=None,
                bluefern_tts_ca_file_env=None,
                tls_workaround_warning=None,
            ),
        ),
    )


def _mock_food_line_tts_failure(monkeypatch: pytest.MonkeyPatch, *, exc: BaseException | None = None) -> None:
    exc = exc or TimeoutError("openai request timed out for sk-test-123")

    def fake_tts(**kwargs):
        diag = TTSDiagnostics(
            provider="openai",
            model_requested=kwargs.get("model"),
            voice_requested=kwargs.get("voice"),
            narration_char_count=len(str(kwargs.get("text") or "")),
            output_path_attempted=str(kwargs.get("output_path") or ""),
            api_key_present=True,
            output_dir_exists=True,
            partial_mp3_exists=False,
            elapsed_seconds=1.23,
            exception_type=exc.__class__.__name__,
            exception_message_sanitized="openai request timed out for [redacted-api-key]",
            timeout_seconds=float(kwargs.get("timeout") or 90.0),
            audio_format=kwargs.get("audio_format"),
            tls_verify=True,
            ca_file_used=None,
            ca_source="system_default",
            truststore_requested=False,
            truststore_available=False,
            ssl_cert_file_env=None,
            requests_ca_bundle_env=None,
            bluefern_tts_ca_file_env=None,
            tls_workaround_warning=None,
        )
        return TTSResult(False, None, "openai", kwargs.get("model"), kwargs.get("voice"), kwargs.get("audio_format"), "openai_tts_request_failed"), diag

    monkeypatch.setattr(food_line, "synthesize_speech_with_diagnostics", fake_tts)


def _seed_existing_food_line_audio(tmp_path: Path, date: str, data: bytes = b"existing-mp3-data") -> Path:
    audio_path = tmp_path / "output" / "site" / "food-line" / "audio" / f"{date}.mp3"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(data)
    return audio_path


def _rss_payload(items: list[dict[str, str]]) -> bytes:
    body = []
    for item in items:
        body.append(
            "<item>"
            f"<title>{item['title']}</title>"
            f"<link>{item['link']}</link>"
            f"<pubDate>{item.get('pubDate', 'Mon, 03 Jun 2026 12:00:00 GMT')}</pubDate>"
            f"<description>{item['description']}</description>"
            "</item>"
        )
    return ("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<rss version=\"2.0\"><channel>"
            + "".join(body)
            + "</channel></rss>").encode("utf-8")


def test_food_line_raw_list_loads_and_generates_sources(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-01"
    path = _manual_path(tmp_path, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([_row(1), _row(2, "state_official", "OR"), _row(3, "food_bank_provider", "WA")], indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    assert result["ok"] is True
    assert result["source_count"] > 0


def test_food_line_sources_wrapper_loads_and_generates_sources(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-01"
    path = _manual_path(tmp_path, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sources": [_row(1), _row(2, "state_official", "OR"), _row(3, "policy_research", "WA")]}, indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    assert result["ok"] is True
    assert result["source_count"] > 0


def test_food_line_utf8_bom_manual_sources_parse(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-01"
    path = _manual_path(tmp_path, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"sources": [_row(1), _row(2, "state_official", "OR"), _row(3, "policy_research", "WA")]}, indent=2)
    path.write_text(payload, encoding="utf-8-sig")
    result = run_food_line_dispatch(tmp_path, date)
    assert result["ok"] is True
    assert result["source_count"] == 3


def test_food_line_malformed_json_fails_clearly(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-01"
    path = _manual_path(tmp_path, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{bad json", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid JSON"):
        run_food_line_dispatch(tmp_path, date)


def test_food_line_rejected_records_report_useful_reasons(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-01"
    path = _manual_path(tmp_path, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    good = _row(1)
    bad = {"id": "x", "title": "Missing url and more"}
    path.write_text(json.dumps({"sources": [good, bad]}, indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    assert result["ok"] is True
    assert result["source_count"] == 1
    assert result["rejected_source_records"]
    reasons = " | ".join(result["rejected_source_records"][0]["reasons"])
    assert "missing required field" in reasons or "invalid field type" in reasons


def test_food_line_alias_fields_load_map_and_source_table(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-01"
    path = _manual_path(tmp_path, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    alias_rows = [
        {
            "id": "a1",
            "title": "Alias Source One",
            "source_url": "https://example.com/alias-one",
            "publisher": "Alias Pub",
            "published_date": "2026-06-01",
            "summary": "Food bank demand increased and pantry lines grew.",
            "family": "federal_official",
            "state": "US",
            "tags": ["SNAP / benefits"],
            "signal_category": "benefit disruption",
            "location": "United States",
        },
        {
            "id": "a2",
            "title": "Alias Source Two",
            "source_url": "https://example.com/alias-two",
            "publisher": "Alias Pub",
            "published_date": "2026-06-01",
            "text": "Food bank demand increased and pantry lines grew.",
            "family": "food_bank_provider",
            "state": "US",
            "tags": ["food banks / pantry capacity"],
            "category": "elevated demand",
            "location": "United States",
        },
    ]
    path.write_text(json.dumps(alias_rows, indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    assert result["ok"] is True
    assert result["source_count"] == 2
    map_payload = json.loads((tmp_path / "output" / "site" / "food-line" / "map" / "map_data.json").read_text(encoding="utf-8"))
    assert len(map_payload["markers"]) == 2
    source_table = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "source_table.html").read_text(encoding="utf-8")
    assert "Alias Source One" in source_table
    assert "Alias Source Two" in source_table
    assert "Current secondary item" in source_table
    assert "https://example.com/alias-two" in source_table
    assert "https://example.com/alias-one" in source_table


def test_food_line_map_page_is_interactive_and_not_placeholder(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-01"
    path = _manual_path(tmp_path, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "sources": [
                    _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX"),
                    _row(2, "state_official", "OR"),
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    run_food_line_dispatch(tmp_path, date)
    map_html = (tmp_path / "output" / "site" / "food-line" / "map" / "index.html").read_text(encoding="utf-8")
    assert "Food Line Pressure Map" in map_html
    assert "leaflet" in map_html.lower()
    assert "map_data.json" in map_html
    assert "Latest mapped signals for 2026-06-01" not in map_html
    assert "<strong>What happened:</strong>" in map_html
    assert "<strong>Included in briefing:</strong>" in map_html
    assert "<div><strong>Category:</strong>" not in map_html
    assert "<div><strong>Issue tags:</strong>" not in map_html
    assert "<strong>How it was used:</strong>" in map_html
    assert "<strong>Source:</strong>" in map_html
    assert "<strong>Dispatch date:</strong>" in map_html
    assert "<strong>Coordinate basis:</strong>" in map_html


def test_food_line_map_legend_includes_required_categories(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-01"
    path = _manual_path(tmp_path, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"sources": [_pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")]},
            indent=2,
        ),
        encoding="utf-8",
    )
    run_food_line_dispatch(tmp_path, date)
    map_html = (tmp_path / "output" / "site" / "food-line" / "map" / "index.html").read_text(encoding="utf-8")
    for label in (
        "acute strain / service disruption",
        "elevated demand",
        "summer meal / child nutrition",
        "senior hunger",
        "rural access",
        "benefit disruption",
        "context / monitoring only",
    ):
        assert label in map_html


def test_food_line_unknown_state_marker_is_skipped_with_diagnostics(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-01"
    path = _manual_path(tmp_path, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    bad = _row(1)
    bad["state"] = "XX"
    bad["location_name"] = "Unknown Place"
    bad["summary_or_snippet"] = "Pantry reduced hours and demand increase reported."
    bad["issue_tags"] = ["food banks", "pantry capacity", "service access"]
    path.write_text(json.dumps({"sources": [bad]}, indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)
    map_data = json.loads((tmp_path / "output" / "site" / "food-line" / "map" / "map_data.json").read_text(encoding="utf-8"))
    diagnostics = map_data.get("diagnostics") or {}
    assert diagnostics.get("marker_count") == 1
    assert diagnostics.get("plotted_marker_count") == 0
    assert diagnostics.get("skipped_marker_count") == 1
    skipped = diagnostics.get("skipped_markers") or []
    assert skipped and skipped[0].get("reason") == "missing_coordinates_and_no_supported_state_fallback"


def test_food_line_collect_writes_auto_sources(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-01"
    _manual_path(tmp_path, date).parent.mkdir(parents=True, exist_ok=True)
    _manual_path(tmp_path, date).write_text("[]", encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date, collect=True)
    assert result["ok"] is True
    auto_path = tmp_path / "data" / "dispatches" / "food-line" / "sources" / date / "auto_sources.json"
    assert auto_path.exists()
    payload = json.loads(auto_path.read_text(encoding="utf-8"))
    assert isinstance(payload, list)


def test_food_line_collect_stays_local_when_repo_registry_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    date = "2026-06-01"
    called = False

    def fail_urlopen(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("network should not be reached in test mode")

    monkeypatch.setattr("bluefern_dispatches.food_line_sources.urllib.request.urlopen", fail_urlopen)

    result = food_line.collect_food_line_auto_sources(tmp_path, date)

    assert result["ok"] is True
    assert result["source_count"] == 0
    assert called is False
    auto_path = tmp_path / "data" / "dispatches" / "food-line" / "sources" / date / "auto_sources.json"
    assert auto_path.exists()
    assert json.loads(auto_path.read_text(encoding="utf-8")) == []


def test_food_line_merges_auto_and_manual_sources(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-01"
    base = tmp_path / "data" / "dispatches" / "food-line" / "sources" / date
    base.mkdir(parents=True, exist_ok=True)
    (base / "manual_sources.json").write_text(json.dumps([_row(1)], indent=2), encoding="utf-8")
    auto = _row(2, family="economic_data", state="US")
    auto["source_type"] = "auto"
    (base / "auto_sources.json").write_text(json.dumps([auto], indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    assert result["source_count"] == 2


def test_food_line_manual_wins_on_duplicate_url_or_title(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-01"
    base = tmp_path / "data" / "dispatches" / "food-line" / "sources" / date
    base.mkdir(parents=True, exist_ok=True)
    manual = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    auto = dict(_pressure_row(2, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX"))
    auto["source_type"] = "auto"
    auto["url"] = manual["url"]
    auto["title"] = manual["title"]
    auto["summary_or_snippet"] = "AUTO"
    (base / "manual_sources.json").write_text(json.dumps([manual], indent=2), encoding="utf-8")
    (base / "auto_sources.json").write_text(json.dumps([auto], indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    assert result["source_count"] == 1
    manifest = json.loads((tmp_path / "output" / "site" / "food-line" / "editions" / date / "sources_manifest.json").read_text(encoding="utf-8"))
    assert manifest[0]["summary_or_snippet"] != "AUTO"


def test_food_line_collector_failure_does_not_crash(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-01"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps([_row(1)], indent=2), encoding="utf-8")

    def _boom(*_args, **_kwargs):
        raise RuntimeError("network unavailable")

    monkeypatch.setattr(food_line, "collect_food_line_auto_sources", _boom)
    result = run_food_line_dispatch(tmp_path, date, collect=True)
    assert result["ok"] is True
    assert result["collector_result"]["ok"] is False
    assert "network unavailable" in result["collector_result"]["failed_sources"][0]["reason"]


def test_food_line_collect_classification_and_national_default(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-01"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("[]", encoding="utf-8")
    _write_source_registry(
        tmp_path,
        [
            {
                "source_id": "collect-source-1",
                "source_name": "Collect Source One",
                "publisher": "Example Pub",
                "url": "https://example.com/food-line-feed.rss",
                "source_family": "local_news",
                "source_type": "rss",
                "state": "US",
                "location_name": "United States",
                "location_scope": "national",
                "enabled": True,
            }
        ],
    )
    _write_pressure_registry(tmp_path, [])
    rss_payload = b"""<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><item><title>Food bank sees rising demand</title><link>https://example.com/story</link><description>Food bank demand is rising and pantry lines grew.</description></item></channel></rss>"""

    result = run_food_line_dispatch(
        tmp_path,
        date,
        collect=True,
        collect_fetcher=lambda _url, timeout=15: rss_payload,
    )
    assert result["ok"] is True
    payload = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "sources" / date / "auto_sources.json").read_text(encoding="utf-8"))
    assert payload
    row = payload[0]
    assert isinstance(row.get("issue_tags"), list) and row["issue_tags"]
    assert row.get("map_category")
    assert row.get("location_name") == "United States"
    assert row.get("state") == "US"


def test_food_line_source_table_and_map_include_auto_sources(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-01"
    base = tmp_path / "data" / "dispatches" / "food-line" / "sources" / date
    base.mkdir(parents=True, exist_ok=True)
    (base / "manual_sources.json").write_text("[]", encoding="utf-8")
    (base / "auto_sources.json").write_text(
        json.dumps(
            [
                {
                    "source_record_id": "auto-1",
                    "title": "Auto Source Title",
                    "url": "https://example.com/auto-1",
                    "publisher": "Auto Pub",
                    "published_at": "2026-06-01T00:00:00Z",
                    "retrieved_at": "2026-06-01T00:00:00Z",
                    "summary_or_snippet": "Food bank demand increased and pantry lines grew.",
                    "source_type": "auto",
                    "source_family": "food_bank_provider",
                    "state": "CA",
                    "issue_tags": ["food banks"],
                    "map_category": "elevated demand",
                    "location_name": "Los Angeles",
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    result = run_food_line_dispatch(tmp_path, date)
    assert result["ok"] is True
    table = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "source_table.html").read_text(encoding="utf-8")
    assert "Auto Source Title" in table
    assert "https://example.com/auto-1" in table
    assert "How it was used" in table
    map_data = json.loads((tmp_path / "output" / "site" / "food-line" / "map" / "map_data.json").read_text(encoding="utf-8"))
    assert any(marker.get("source_title") == "Auto Source Title" for marker in map_data.get("markers") or [])


def test_food_line_lead_prefers_concrete_daily_signal_over_background(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-02"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    background = _row(1, family="economic_data", state="US")
    background["map_category"] = "context / monitoring only"
    background["title"] = "USDA context page"
    background["summary_or_snippet"] = "National context statistics."
    concrete = _row(2, family="food_bank_provider", state="CA")
    concrete["map_category"] = "elevated demand"
    concrete["summary_or_snippet"] = "Pantry wait times increased and one site reduced hours."
    p.write_text(json.dumps([background, concrete], indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    assert result["lead_source_record_id"] == concrete["source_record_id"]


def test_food_line_background_only_becomes_monitoring_context(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-02"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for i in range(1, 4):
        row = _row(i, family="economic_data", state="US")
        row["map_category"] = "context / monitoring only"
        row["issue_tags"] = ["household food insecurity"]
        rows.append(row)
    p.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    assert result["editorial_status"] == "monitoring/context"
    assert result["source_adequacy"]["label"] == "Monitoring/context edition"


def test_food_line_what_changed_does_not_overstate_novelty_on_context_day(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-02"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _row(1, family="economic_data", state="US")
    row["map_category"] = "context / monitoring only"
    p.write_text(json.dumps([row, dict(row, source_record_id="food-line-src-002", url="https://example.com/2"), dict(row, source_record_id="food-line-src-003", url="https://example.com/3")], indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    site_edition = tmp_path / "output" / "site" / "food-line" / "editions" / date
    review_manifest = tmp_path / "output" / "review" / "food-line" / date / "run_manifest.json"
    data_manifest = tmp_path / "data" / "dispatches" / "food-line" / "editions" / date / "run_manifest.json"
    assert result["public_rendered"] is False
    assert result["skip_reason"] == "No new primary food-access signal qualified for public Food Line publication."
    assert not site_edition.exists()
    assert review_manifest.exists()
    assert data_manifest.exists()
    manifest = json.loads(data_manifest.read_text(encoding="utf-8"))
    assert manifest["public_rendered"] is False
    assert manifest["qualified_primary_count"] == 0
    assert manifest["skip_reason"] == "No new primary food-access signal qualified for public Food Line publication."
    return
    run_food_line_dispatch(tmp_path, date)
    html_text = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "index.html").read_text(encoding="utf-8")
    assert "Today’s Read" in html_text
    assert "No new primary pressure signal qualified today." in html_text
    assert "What changed today" not in html_text


def test_food_line_2026_06_13_kltv_excerpt_is_cleaned_before_rendering():
    payload_path = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "food-line" / "sources" / "2026-06-13" / "auto_sources.json"
    rows = json.loads(payload_path.read_text(encoding="utf-8"))
    lead = next(row for row in rows if row.get("pressure_signal"))
    excerpt = food_line.clean_food_line_public_evidence_excerpt(str(lead.get("evidence_text") or ""), title=str(lead.get("title") or ""))

    assert excerpt != food_line.FOOD_LINE_PUBLIC_EVIDENCE_FALLBACK
    assert "Skip to content" not in excerpt
    assert "Advertise With Us" not in excerpt
    assert "Watch Live" not in excerpt
    assert "Weather Extra" not in excerpt
    assert "Reception Issues" not in excerpt
    assert "Pet Project" not in excerpt
    assert "empty shelves" in excerpt.lower()
    assert "1 in 4 Washingtonians" in excerpt


def test_food_line_2026_06_05_publishes_new_primary_and_records_freshness_diagnostics(tmp_path: Path):
    _ensure_assets(tmp_path)
    _clear_food_line_registries(tmp_path)
    payload_path = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "food-line" / "sources" / "2026-06-05" / "auto_sources.json"
    date = "2026-06-05"
    payload = _freshen_food_line_payload_for_publication(json.loads(payload_path.read_text(encoding="utf-8")), date)
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)

    review_path = tmp_path / "output" / "review" / "food-line" / date / "pressure_review.csv"
    review_rows = list(csv.DictReader(review_path.open(encoding="utf-8")))
    kltv = next(row for row in review_rows if row["source_record_id"] == "food-line-auto-c531de22a923a8d8")
    lead = next(row for row in review_rows if row["source_record_id"] == "food-line-auto-d746124a0786b5f9")
    edition_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "index.html").read_text(encoding="utf-8")
    archive_html = (tmp_path / "output" / "site" / "food-line" / "archive.html").read_text(encoding="utf-8")
    source_table_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "source_table.html").read_text(encoding="utf-8")
    glance_html = edition_html.split("<h2>At A Glance</h2>", 1)[1].split("</ul>", 1)[0]
    today_read_html = edition_html.split("<h2>Today’s Read</h2>", 1)[1].split("<h2>At A Glance</h2>", 1)[0]

    assert result["public_rendered"] is True
    assert result["skip_reason"] == ""
    assert result["primary_disqualification_reason"] == ""
    assert result["lead_source_record_id"] == "food-line-auto-d746124a0786b5f9"
    assert result["selected_lead_source_role"] == "local_signal"
    assert result["selected_lead_pressure_type"] == "demand strain"
    assert result["selected_lead_pressure_scope_label"] == "Local / operational"
    assert result["bluesky_post_ready"] is True
    assert result["bluesky_post_text"]
    assert len(result["bluesky_post_text"]) <= 300
    assert result["public_url"] == "https://dispatches.thebluefernco.com/food-line/editions/2026-06-05/"
    assert kltv["pressure_signal"] == "true"
    assert kltv["pressure_verification_status"] == "source_text_verified"
    assert kltv["source_published_date"] == "2026-06-05"
    assert kltv["freshness_status"] == "fresh_daily_signal"
    assert kltv["primary_eligible"] == "true"
    assert kltv["primary_disqualification_reason"] == ""
    assert lead["source_title"].startswith("Local food pantries are preparing for increased demand")
    assert lead["location_name"] == "Toledo, OH"
    assert lead["pressure_signal"] == "true"
    assert lead["pressure_verification_status"] == "source_text_verified"
    assert lead["source_published_date"] == "2026-06-05"
    assert lead["freshness_status"] == "fresh_daily_signal"
    assert lead["primary_eligible"] == "true"
    assert lead["primary_disqualification_reason"] == ""
    assert "editions/2026-06-05/" in archive_html
    assert glance_html.count("<li>") <= 3
    assert "Today&apos;s lead:" not in glance_html
    assert "What happened:" not in glance_html
    assert "More background sources appear below" not in glance_html
    assert "Today’s Food Line found 5 reported pressure signals." in today_read_html
    assert "The run reviewed" in today_read_html
    assert "Washington food providers report rising pantry demand" in glance_html
    assert "Partial-source update / June 5, 2026" in edition_html
    assert "Generated from saved source records available for June 5, 2026." in edition_html
    assert "Today’s Read" in edition_html
    assert "At A Glance" in edition_html
    assert "Core Food Pressure Signals" in edition_html
    assert "Other Food Line Signals" in edition_html
    assert "Source Mix" in edition_html
    assert "Source Note" in edition_html
    assert edition_html.index("Today’s Read") < edition_html.index("At A Glance") < edition_html.index("Core Food Pressure Signals") < edition_html.index("Other Food Line Signals") < edition_html.index("Source Mix") < edition_html.index("Source Note")
    assert "Main Food Access Story" not in edition_html
    assert "What Else We’re Watching" not in edition_html
    assert "Context and Watch Items" not in edition_html
    assert "Sources Behind This Briefing" not in edition_html
    assert "Source Audit" not in edition_html
    assert "source mix" in edition_html.lower()
    assert "USDA FNS" not in edition_html
    assert "USDA ERS" not in edition_html
    assert "13abc" in source_table_html
    assert "Cascade PBS" in source_table_html
    assert "KLTV" in source_table_html
    assert "USDA FNS" in source_table_html
    assert "USDA ERS" in source_table_html
    assert "5 sources were used on the public page" in source_table_html
    assert "2 additional background reference sources" in source_table_html
    assert "Background reference" in source_table_html
    assert "Yes" in source_table_html
    assert "Skip to main content" not in edition_html
    assert "Here’s how you know" not in edition_html
    assert "Here&#x27;s how you know" not in edition_html
    assert "Secure .gov websites use HTTPS" not in edition_html
    assert ".gov website belongs" not in edition_html
    assert "Background records remain traceable here" not in edition_html
    assert "More background sources appear below" not in edition_html
    assert "public item(s)" not in edition_html
    assert "primary pressure lead" not in edition_html
    assert "source_text_verified" not in edition_html
    assert "pressure_signal" not in edition_html
    assert "Today’s pressure point" not in edition_html
    assert "What changed" not in edition_html
    assert "Who is exposed" not in edition_html
    assert "Field signals" not in edition_html
    assert "Where pressure is visible" not in edition_html
    assert "Map notes" not in edition_html
    assert "What to watch tomorrow" not in edition_html


def test_food_line_2026_06_05_audio_outputs_skip_static_background_cards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    _clear_food_line_registries(tmp_path)
    _mock_food_line_tts(monkeypatch)

    payload_path = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "food-line" / "sources" / "2026-06-05" / "auto_sources.json"
    date = "2026-06-05"
    payload = _freshen_food_line_payload_for_publication(json.loads(payload_path.read_text(encoding="utf-8")), date)
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    result = run_food_line_dispatch(tmp_path, date, generate_audio=True)

    audio_json = json.loads((tmp_path / "output" / "site" / "food-line" / "audio" / f"{date}.json").read_text(encoding="utf-8"))
    transcript = (tmp_path / "output" / "site" / "food-line" / "audio" / f"{date}-transcript.html").read_text(encoding="utf-8")
    audio_index = (tmp_path / "output" / "site" / "food-line" / "audio" / "index.html").read_text(encoding="utf-8")
    podcast = (tmp_path / "output" / "site" / "food-line" / "audio" / "podcast.xml").read_text(encoding="utf-8")

    assert result["audio_generated"] is True
    assert audio_json["episode_title"] == "Food Line Briefing — June 5, 2026"
    assert audio_json["episode_summary"].lower().startswith("today's food line briefing tracks")
    assert "toledo" in audio_json["episode_summary"].lower()
    assert "reported rising food-assistance demand" not in audio_json["episode_summary"]
    assert "Background and source links are available in the public source table." in audio_json["episode_summary"]
    assert "current public signals selected from" in audio_json["script_text"]
    assert "Source links, excerpts, and background references are available in the public source table." in audio_json["script_text"]
    assert "We are also watching two related food-access reports." not in audio_json["script_text"]
    assert "Another report points to related pressure on pantry capacity." not in audio_json["script_text"]
    assert "In Washington, Cascade PBS reported that" in audio_json["script_text"]
    assert "In East Texas" in audio_json["script_text"]
    assert "KLTV reported that" in audio_json["script_text"]
    assert "The run reviewed" not in audio_json["script_text"]
    assert "Read the source:" not in audio_json["script_text"]
    assert "Source:" not in audio_json["script_text"]
    assert "Where:" not in audio_json["script_text"]
    assert "Date:" not in audio_json["script_text"]
    assert "https://" not in audio_json["script_text"]
    assert transcript.count("This is the Food Line briefing for June 5, 2026.") == 1
    assert "current public signals selected from" in transcript
    assert "Core Food Pressure Signals" in transcript
    assert "Other Food Line Signals" in transcript
    assert "Cascade PBS" in transcript
    assert "KLTV" in transcript
    assert "In Washington, Cascade PBS reported that" in transcript
    assert "In East Texas" in transcript
    assert 'href="/american-pressure/"' not in transcript
    assert "No additional current food-access items were strong enough to change today’s lead." not in transcript
    assert "USDA FNS" not in transcript
    assert "USDA ERS" not in transcript
    assert "<h2>Transcript</h2>" not in transcript
    assert "Source Note" in transcript
    assert "Source links" in transcript
    assert "Main Food Access Story" not in transcript
    assert "What Else We’re Watching" not in transcript
    assert "Sources Behind This Briefing" not in transcript
    assert "/food-line/audio/2026-06-05.mp3" in transcript
    assert "/food-line/editions/2026-06-05/source_table.html" in transcript
    assert "Open the public edition" in transcript
    assert "Open the podcast feed" in transcript
    assert "Podcast enclosure:</strong> present" in transcript
    assert "current public signals selected from" in audio_index
    assert "Core Food Pressure Signals" in audio_index
    assert "Other Food Line Signals" in audio_index
    assert "Cascade PBS" in audio_index
    assert "KLTV" in audio_index
    assert "In Washington, Cascade PBS reported that" in audio_index
    assert "In East Texas" in audio_index
    assert 'href="/american-pressure/"' not in audio_index
    assert "No additional current food-access items were strong enough to change today’s lead." not in audio_index
    assert "USDA FNS" not in audio_index
    assert "USDA ERS" not in audio_index
    assert "Source Note" in audio_index
    assert "Source links" in audio_index
    assert "Main Food Access Story" not in audio_index
    assert "What Else We’re Watching" not in audio_index
    assert "Sources Behind This Briefing" not in audio_index
    assert "/food-line/audio/2026-06-05-transcript.html" in audio_index
    assert "/food-line/editions/2026-06-05/source_table.html" in audio_index
    assert "Open the podcast feed" in audio_index
    assert "<title>Food Line Briefing — June 5, 2026</title>" in podcast
    assert "Today's Food Line briefing tracks" in podcast
    assert "toledo" in podcast.lower()
    assert "reported rising food-assistance demand" not in podcast
    assert "Background and source links are available in the public source table." in podcast
    assert "USDA FNS" not in podcast
    assert "USDA ERS" not in podcast
    assert "Pressure type" not in podcast
    assert "Evidence excerpt" not in podcast
    assert "Context record" not in podcast
    assert "Source role" not in podcast
    assert "Source record ID" not in podcast
    assert "local / operational signal" not in podcast
    assert "source_text_verified" not in podcast
    assert "pressure_signal" not in podcast


def test_food_line_2026_06_06_blocks_stale_prior_year_current_story_candidates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    _clear_food_line_registries(tmp_path)
    monkeypatch.setattr(food_line, "FOOD_LINE_FRESHNESS_WINDOW_DAYS", 3)
    monkeypatch.setattr(
        food_line,
        "collect_food_line_auto_sources",
        lambda *args, **kwargs: {"ok": True, "source_count": 44},
    )
    monkeypatch.setattr(
        food_line,
        "_food_line_discovery_gap_summary",
        lambda *args, **kwargs: {
            "run": True,
            "report_found": True,
            "report_path": str(tmp_path / "data" / "dispatches" / "food-line" / "discovery_gap" / "2026-06-06" / "discovery_gap_report.json"),
            "report_markdown_path": str(tmp_path / "data" / "dispatches" / "food-line" / "discovery_gap" / "2026-06-06" / "discovery_gap_report.md"),
            "likely_qualifying_count": 0,
            "unreviewed_likely_qualifying_count": 0,
            "public_no_qualifying_update_validated": True,
            "warning": "",
        },
    )
    date = "2026-06-06"
    payload_path = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "food-line" / "sources" / "2026-06-06" / "auto_sources.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    result = run_food_line_dispatch(tmp_path, date, collect=True, include_discovery_gap_summary=True)

    review_path = tmp_path / "output" / "review" / "food-line" / date / "pressure_review.csv"
    review_rows = list(csv.DictReader(review_path.open(encoding="utf-8")))
    review_by_id = {row["source_record_id"]: row for row in review_rows}
    edition_path = tmp_path / "output" / "site" / "food-line" / "editions" / date / "index.html"
    source_table_path = tmp_path / "output" / "site" / "food-line" / "editions" / date / "source_table.html"
    edition_html = edition_path.read_text(encoding="utf-8")
    index_html = (tmp_path / "output" / "site" / "food-line" / "index.html").read_text(encoding="utf-8")
    archive_html = (tmp_path / "output" / "site" / "food-line" / "archive.html").read_text(encoding="utf-8")
    manifest = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "editions" / date / "run_manifest.json").read_text(encoding="utf-8"))
    stale_ids = {
        "food-line-auto-6effc522ae28d822",
    }

    assert result["public_rendered"] is True
    assert result["edition_mode"] == "no_current_update"
    assert result["source_freshness_status"] == "passed_no_qualifying_update"
    assert result["food_line_publish_blocked_reason"] == ""
    assert result["food_line_no_current_update_policy_status"] == "allowed"
    assert result["stale_public_story_count"] >= 3
    assert stale_ids.issubset(set(result["stale_source_ids"]))
    assert result["lead_source_record_id"] not in stale_ids
    assert not stale_ids.intersection(set(result["continuing_pressure_source_record_ids"] or []))
    assert review_by_id["food-line-auto-6effc522ae28d822"]["freshness_status"] == "stale_outside_daily_window"
    assert review_by_id["food-line-auto-6effc522ae28d822"]["pressure_signal"] == "false"
    assert review_by_id["food-line-auto-8766e7659336949d"]["freshness_status"] == "stale_outside_daily_window"
    assert review_by_id["food-line-auto-8766e7659336949d"]["pressure_signal"] == "false"
    assert review_by_id["food-line-auto-6effc522ae28d822"]["freshness_status"] == "stale_outside_daily_window"
    assert review_by_id["food-line-auto-6effc522ae28d822"]["pressure_signal"] == "false"
    assert "No qualifying update / June 6, 2026" in edition_html
    assert "Generated from saved source records available for June 6, 2026." in edition_html
    assert "No fresh source-backed current food-pressure signal qualified today." in edition_html
    assert "Today’s Read" in edition_html
    assert "At A Glance" in edition_html
    assert "Core Food Pressure Signals" in edition_html
    assert "Other Food Line Signals" in edition_html
    assert "Source Mix" in edition_html
    assert "Source Note" in edition_html
    assert "Main Food Access Story" not in edition_html
    assert "Current secondary item" not in edition_html
    assert "What Else We’re Watching" not in edition_html
    assert "Background References" not in edition_html
    assert "Source Audit" not in edition_html
    assert "stale_source_ids" not in edition_html
    assert "Stale source IDs:" not in edition_html
    assert "food-line-auto-" not in edition_html
    assert "2025/04" not in edition_html
    assert "2025/10/28" not in edition_html
    assert "2025/10/29" not in edition_html
    assert "source_table.html" in edition_html
    assert edition_path.exists()
    source_table_html = source_table_path.read_text(encoding="utf-8")
    audio_index_html = (tmp_path / "output" / "site" / "food-line" / "audio" / "index.html").read_text(encoding="utf-8")
    podcast_xml = (tmp_path / "output" / "site" / "food-line" / "podcast.xml").read_text(encoding="utf-8")
    assert "Background reference" in source_table_html
    assert "Sources behind this briefing" in source_table_html
    assert "Record ID" in source_table_html
    assert "What the source says" in source_table_html
    assert "stale current-story candidate source" in source_table_html
    assert "food-line-auto-" in source_table_html
    assert "2026-06-06 — No qualifying update" in index_html
    assert "editions/2026-06-06/" in index_html
    assert 'href="/american-pressure/"' not in index_html
    assert 'href="/gaza/"' in index_html
    assert 'href="/cascadia/"' in index_html
    assert 'href="/food-line/"' in index_html
    assert "2026-06-06 — No qualifying update" in archive_html
    assert "Food Line Audio" in audio_index_html
    assert "Open the podcast feed" in audio_index_html
    assert f"/food-line/audio/{date}-transcript.html" in podcast_xml
    assert "Podcast enclosure status" in audio_index_html
    assert manifest["public_rendered"] is True
    assert manifest["edition_mode"] == "no_current_update"
    assert manifest["skip_reason"] == ""
    assert manifest["source_freshness_status"] == "passed_no_qualifying_update"


def test_food_line_no_current_update_blocks_publication_when_high_confidence_unresolved_candidates_remain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    _clear_food_line_registries(tmp_path)
    monkeypatch.setattr(food_line, "FOOD_LINE_FRESHNESS_WINDOW_DAYS", 3)
    monkeypatch.setattr(
        food_line,
        "collect_food_line_auto_sources",
        lambda *args, **kwargs: {"ok": True, "source_count": 44},
    )
    monkeypatch.setattr(
        food_line,
        "_food_line_discovery_gap_summary",
        lambda *args, **kwargs: {
            "run": True,
            "report_found": True,
            "report_path": str(tmp_path / "data" / "dispatches" / "food-line" / "discovery_gap" / "2026-06-06" / "discovery_gap_report.json"),
            "report_markdown_path": str(tmp_path / "data" / "dispatches" / "food-line" / "discovery_gap" / "2026-06-06" / "discovery_gap_report.md"),
            "likely_qualifying_count": 2,
            "blocking_likely_qualifying_count": 0,
            "unresolved_likely_qualifying_count": 2,
            "unresolved_high_confidence_direct_pressure_count": 2,
            "unresolved_high_confidence_direct_pressure_titles": [
                "Food bank demand surges in north Omaha as SNAP cuts and rising grocery costs strain families",
                "Some food banks see up to 1,800% surge in demand since SNAP benefits were halted",
            ],
            "manual_review_only_count": 0,
            "unreviewed_likely_qualifying_count": 0,
            "public_no_qualifying_update_validated": False,
            "warning": "Food Line discovery gap check found 2 unresolved high-confidence direct-pressure candidates that block public no-current-update publication. Top titles: Food bank demand surges in north Omaha as SNAP cuts and rising grocery costs strain families; Some food banks see up to 1,800% surge in demand since SNAP benefits were halted. See discovery_gap_report.md.",
        },
    )
    date = "2026-06-06"
    payload_path = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "food-line" / "sources" / date / "auto_sources.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    result = run_food_line_dispatch(tmp_path, date, collect=True, include_discovery_gap_summary=True)
    manifest = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "editions" / date / "run_manifest.json").read_text(encoding="utf-8"))

    assert result["ok"] is True
    assert result["public_rendered"] is False
    assert result["edition_mode"] == "internal_no_qualifying_update"
    assert result["food_line_no_current_update_policy_status"] == "blocked"
    assert any("unresolved high-confidence direct-pressure candidate" in reason for reason in result["food_line_no_current_update_policy_reasons"])
    assert "north Omaha" in result["food_line_publish_blocked_reason"]
    assert "north Omaha" in result["skip_reason"]
    assert manifest["public_rendered"] is False
    assert manifest["edition_mode"] == "internal_no_qualifying_update"
    assert "north Omaha" in manifest["food_line_publish_blocked_reason"]
    assert manifest["skip_reason"]


def test_food_line_no_current_update_policy_blocks_when_discovery_gap_did_not_run() -> None:
    result = food_line.evaluate_food_line_no_current_update_publication_policy(
        edition_mode="no_current_update",
        collector_result={"ok": True, "source_count": 12},
        discovery_gap_check={"run": False},
        discovery_expansion_used=False,
        source_freshness_status="passed_no_qualifying_update",
        news_item_count=6,
        local_signal_count=3,
        state_signal_count=2,
        discovery_gap_blocking_likely_qualifying_count=0,
    )

    assert result["allowed"] is False
    assert result["status"] == "blocked"
    assert any("discovery-gap or equivalent expanded discovery did not run" in reason for reason in result["reasons"])


def test_food_line_no_current_update_policy_blocks_when_freshness_status_is_blocked() -> None:
    result = food_line.evaluate_food_line_no_current_update_publication_policy(
        edition_mode="no_current_update",
        collector_result={"ok": True, "source_count": 18},
        discovery_gap_check={"run": True},
        discovery_expansion_used=False,
        source_freshness_status="blocked_insufficient_fresh_current_stories",
        news_item_count=8,
        local_signal_count=4,
        state_signal_count=2,
        discovery_gap_blocking_likely_qualifying_count=0,
    )

    assert result["allowed"] is False
    assert result["status"] == "blocked"
    assert any("source freshness status blocked_insufficient_fresh_current_stories blocks public no-qualifying-update publication" in reason for reason in result["reasons"])


def test_food_line_no_current_update_policy_blocks_when_collector_failed() -> None:
    result = food_line.evaluate_food_line_no_current_update_publication_policy(
        edition_mode="no_current_update",
        collector_result={"ok": False, "source_count": 18},
        discovery_gap_check={"run": True},
        discovery_expansion_used=False,
        source_freshness_status="passed_no_qualifying_update",
        news_item_count=8,
        local_signal_count=4,
        state_signal_count=2,
        discovery_gap_blocking_likely_qualifying_count=0,
    )

    assert result["allowed"] is False
    assert result["status"] == "blocked"
    assert "source collection did not run successfully" in result["reasons"]


def test_food_line_no_current_update_policy_allows_public_no_qualifying_update_only_with_full_checks() -> None:
    result = food_line.evaluate_food_line_no_current_update_publication_policy(
        edition_mode="no_current_update",
        collector_result={"ok": True, "source_count": 18},
        discovery_gap_check={"run": True},
        discovery_expansion_used=False,
        source_freshness_status="passed_no_qualifying_update",
        news_item_count=8,
        local_signal_count=4,
        state_signal_count=2,
        discovery_gap_blocking_likely_qualifying_count=0,
    )

    assert result["allowed"] is True
    assert result["status"] == "allowed"
    assert result["reasons"] == []


def test_food_line_no_current_update_policy_blocks_high_confidence_unresolved_direct_pressure_candidates() -> None:
    result = food_line.evaluate_food_line_no_current_update_publication_policy(
        edition_mode="no_current_update",
        collector_result={"ok": True, "source_count": 18},
        discovery_gap_check={
            "run": True,
            "blocking_likely_qualifying_count": 0,
            "unresolved_high_confidence_direct_pressure_count": 2,
            "unresolved_high_confidence_direct_pressure_titles": [
                "Food bank demand surges in north Omaha as SNAP cuts and rising grocery costs strain families",
                "Some food banks see up to 1,800% surge in demand since SNAP benefits were halted",
            ],
        },
        discovery_expansion_used=False,
        source_freshness_status="passed_no_qualifying_update",
        news_item_count=8,
        local_signal_count=4,
        state_signal_count=2,
        discovery_gap_blocking_likely_qualifying_count=0,
    )

    assert result["allowed"] is False
    assert result["status"] == "blocked"
    assert any("unresolved high-confidence direct-pressure candidate" in reason for reason in result["reasons"])
    assert any("north Omaha" in reason for reason in result["reasons"])


def test_food_line_no_current_update_policy_blocks_stale_candidate_even_with_discovery_gap_and_high_counts() -> None:
    result = food_line.evaluate_food_line_no_current_update_publication_policy(
        edition_mode="no_current_update",
        collector_result={"ok": True, "source_count": 18},
        discovery_gap_check={"run": True},
        discovery_expansion_used=False,
        source_freshness_status="blocked_insufficient_fresh_current_stories",
        news_item_count=8,
        local_signal_count=4,
        state_signal_count=2,
        discovery_gap_blocking_likely_qualifying_count=0,
    )

    assert result["allowed"] is False
    assert result["status"] == "blocked"
    assert any("blocked_insufficient_fresh_current_stories" in reason for reason in result["reasons"])


def test_food_line_no_current_update_policy_freshness_status_uses_real_blocked_status_for_stale_candidate() -> None:
    status = food_line._food_line_no_current_update_policy_freshness_status(
        future_date_blocked=False,
        no_current_update_candidate=True,
        stale_public_story_count=5,
        public_rendered=False,
        discovery_gap_check={"run": True},
        discovery_bridge_result={"discovery_expansion_used": False},
    )

    assert status == "blocked_insufficient_fresh_current_stories"


def test_food_line_no_current_update_policy_freshness_status_allows_validated_no_qualifying_update() -> None:
    status = food_line._food_line_no_current_update_policy_freshness_status(
        future_date_blocked=False,
        no_current_update_candidate=True,
        stale_public_story_count=5,
        public_rendered=False,
        discovery_gap_check={"run": True, "public_no_qualifying_update_validated": True},
        discovery_bridge_result={"discovery_expansion_used": False},
    )

    assert status == "passed_no_qualifying_update"


def test_food_line_no_current_update_auto_runs_discovery_gap_when_coverage_is_sufficient(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    _clear_food_line_registries(tmp_path)
    monkeypatch.setattr(food_line, "FOOD_LINE_FRESHNESS_WINDOW_DAYS", 3)
    monkeypatch.setattr(
        food_line,
        "collect_food_line_auto_sources",
        lambda *args, **kwargs: {"ok": True, "source_count": 44},
    )
    date = "2026-06-06"
    payload_path = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "food-line" / "sources" / date / "auto_sources.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    calls: list[str] = []

    def fake_run_gap_check(root: Path, edition_date: str, **kwargs):
        calls.append(edition_date)
        assert root == tmp_path
        assert kwargs["fast"] is True
        _write_food_line_discovery_gap_report(tmp_path, edition_date, [])
        return {"date": edition_date}

    monkeypatch.setattr(food_line, "run_food_line_discovery_gap_check", fake_run_gap_check)

    result = run_food_line_dispatch(tmp_path, date, collect=True, include_discovery_gap_summary=False)
    manifest = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "editions" / date / "run_manifest.json").read_text(encoding="utf-8"))

    assert result["ok"] is True
    assert calls == [date]
    assert result["public_rendered"] is True
    assert result["edition_mode"] == "no_current_update"
    assert result["food_line_no_current_update_policy_status"] == "allowed"
    assert result["food_line_no_current_update_policy_metrics"]["source_freshness_status"] == "passed_no_qualifying_update"
    assert result["discovery_gap_check"]["run"] is True
    assert result["discovery_gap_check"]["public_no_qualifying_update_validated"] is True
    assert result["food_line_publish_blocked_reason"] == ""
    assert manifest["public_rendered"] is True
    assert manifest["edition_mode"] == "no_current_update"
    assert (tmp_path / "output" / "site" / "food-line" / "editions" / date / "index.html").exists() is True


def test_food_line_archive_lists_no_current_update_edition_is_archive_safe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    _clear_food_line_registries(tmp_path)
    monkeypatch.setattr(food_line, "FOOD_LINE_FRESHNESS_WINDOW_DAYS", 3)
    monkeypatch.setattr(food_line, "_food_line_local_today", lambda: dt_date(2026, 6, 8))

    date = "2026-06-07"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            **_pressure_row(
                1,
                "Food banks continue to see increased need as SNAP requirements shift",
                "Food banks continue to see increased need as SNAP requirements shift and demand stays elevated.",
                family="local_news",
                state="MA",
                source_type="page",
                publisher="NEPM",
            ),
            "url": "https://www.nepm.org/regional-news/2026-06-03/stale-food-banks",
            "published_at": "2026-06-03T12:00:00Z",
        },
        {
            **_row(2, family="state_official", state="PA", title="SNAP program information", summary="SNAP program information page.", source_type="page", publisher="Pennsylvania DHS"),
            "url": "https://www.pa.gov/services/dhs/apply-for-the-supplemental-nutrition-assistance-program-snap.html",
            "published_at": "2026-06-07T12:00:00Z",
        },
        {
            **_row(3, family="state_official", state="US", title="WIC information page", summary="WIC information page and program details.", source_type="page", publisher="USDA FNS"),
            "url": "https://www.fns.usda.gov/wic",
            "published_at": "2026-06-07T12:00:00Z",
        },
    ]
    p.write_text(json.dumps(rows, indent=2), encoding="utf-8")

    result = run_food_line_dispatch(tmp_path, date)
    manifest = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "editions" / date / "run_manifest.json").read_text(encoding="utf-8"))

    assert result["public_rendered"] is False
    assert result["edition_mode"] == "internal_no_qualifying_update"
    assert result["source_freshness_status"] == "blocked_insufficient_fresh_current_stories"
    assert result["qualified_primary_count"] == 0
    assert any("source collection did not run successfully" in reason for reason in result["food_line_no_current_update_policy_reasons"])
    assert any("discovery-gap or equivalent expanded discovery did not run" in reason for reason in result["food_line_no_current_update_policy_reasons"])
    assert public_edition_is_listable(tmp_path / "output" / "site", "food-line", date) is False
    assert manifest["edition_mode"] == "internal_no_qualifying_update"
    assert manifest["source_freshness_status"] == "blocked_insufficient_fresh_current_stories"
    assert (tmp_path / "output" / "site" / "food-line" / "editions" / date / "index.html").exists() is False


def test_food_line_archive_lists_current_update_edition_is_archive_safe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    _clear_food_line_registries(tmp_path)
    monkeypatch.setattr(food_line, "FOOD_LINE_FRESHNESS_WINDOW_DAYS", 3)
    monkeypatch.setattr(food_line, "_food_line_local_today", lambda: dt_date(2026, 6, 9))

    date = "2026-06-08"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(_load_food_line_regression_fixture(), indent=2), encoding="utf-8")

    result = run_food_line_dispatch(tmp_path, date)
    index_html = (tmp_path / "output" / "site" / "food-line" / "index.html").read_text(encoding="utf-8")
    archive_html = (tmp_path / "output" / "site" / "food-line" / "archive.html").read_text(encoding="utf-8")
    edition_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "index.html").read_text(encoding="utf-8")
    manifest = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "editions" / date / "run_manifest.json").read_text(encoding="utf-8"))

    assert result["public_rendered"] is True
    assert result["edition_mode"] == "current_update"
    assert result["qualified_primary_count"] == 1
    assert result["lead_source_record_id"] == "food-line-src-002"
    assert "across its service area in Maine" not in edition_html
    assert "service area in Maine" not in edition_html
    assert "Maine in Maine" not in edition_html
    assert "Massachusetts in Massachusetts" not in edition_html
    assert "Food Line tracks source-backed reported signals of food pressure available at publish time." in edition_html
    assert "It should not be read as a complete national measure of food insecurity." in edition_html
    assert "No current update" not in index_html
    assert "2026-06-08" in archive_html
    assert "Blocked" not in archive_html
    assert "Food Line tracks source-backed reported signals of food pressure available at publish time." in index_html
    assert public_edition_is_listable(tmp_path / "output" / "site", "food-line", date) is True
    assert manifest["edition_mode"] == "current_update"


def test_food_line_2026_06_08_rendered_public_files_regressions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    _clear_food_line_registries(tmp_path)
    monkeypatch.setattr(food_line, "FOOD_LINE_FRESHNESS_WINDOW_DAYS", 3)
    monkeypatch.setattr(food_line, "_food_line_local_today", lambda: dt_date(2026, 6, 9))

    date = "2026-06-08"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    production_payload_path = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "food-line" / "sources" / date / "auto_sources.json"
    p.write_text(production_payload_path.read_text(encoding="utf-8"), encoding="utf-8")

    result = run_food_line_dispatch(tmp_path, date, generate_audio=False)

    edition_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "index.html").read_text(encoding="utf-8")
    transcript_html = (tmp_path / "output" / "site" / "food-line" / "audio" / f"{date}-transcript.html").read_text(encoding="utf-8")

    assert result["public_rendered"] is True
    assert "Core Food Pressure Signals" in edition_html
    assert "Other Food Line Signals" in edition_html
    assert "Source Mix" in edition_html
    assert "reported signals" in edition_html
    assert "complete national measure" in edition_html
    assert "service area in Maine" not in edition_html
    assert "Maine rising" not in edition_html
    assert "Main Food Access Story" not in edition_html
    assert "Sources Behind This Briefing" not in edition_html

    assert "selected from 33 reviewed records" in transcript_html
    assert "Core Food Pressure Signals" in transcript_html
    assert "Other Food Line Signals" in transcript_html
    assert "service area in Maine" not in transcript_html
    assert "Maine rising" not in transcript_html
    assert "related food-access reports" not in transcript_html
    assert "Main Food Access Story" not in transcript_html
    assert "What Else We’re Watching" not in transcript_html


def test_food_line_homepage_omits_pressure_map_link_when_map_artifact_is_absent(tmp_path: Path):
    _ensure_assets(tmp_path)
    _clear_food_line_registries(tmp_path)

    for date in ("2026-06-06", "2026-06-07"):
        edition_dir = tmp_path / "output" / "site" / "food-line" / "editions" / date
        edition_dir.mkdir(parents=True, exist_ok=True)
        (edition_dir / "index.html").write_text("<html>Food Line edition</html>", encoding="utf-8")
        (edition_dir / "edition_manifest.json").write_text(
            json.dumps(
                {
                    "dispatch_slug": "food-line",
                    "edition_date": date,
                    "public_rendered": True,
                    "edition_mode": "no_current_update",
                    "source_freshness_status": "blocked_insufficient_fresh_current_stories",
                    "freshness_window_days": 14,
                    "stale_public_story_count": 1,
                    "excluded_stale_source_count": 1,
                    "stale_source_ids": ["food-line-auto-stale"],
                    "qualified_primary_count": 0,
                    "skip_reason": "",
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    food_line._update_index_archive(tmp_path, "2026-06-07", "Food Line mission", max_edition_date="2026-06-07")

    index_html = (tmp_path / "output" / "site" / "food-line" / "index.html").read_text(encoding="utf-8")
    archive_html = (tmp_path / "output" / "site" / "food-line" / "archive.html").read_text(encoding="utf-8")

    assert "2026-06-07 — No qualifying update" in index_html
    assert "2026-06-07 — No qualifying update" in archive_html
    assert "2026-06-06 — No qualifying update" in archive_html
    assert "2026-06-05" not in index_html
    assert "2026-06-05" not in archive_html
    assert 'href="map/"' not in index_html
    assert 'href="/american-pressure/"' not in index_html
    assert 'href="/gaza/"' in index_html
    assert 'href="/cascadia/"' in index_html
    assert 'href="/food-line/"' in index_html


def test_food_line_homepage_omits_pressure_map_link_when_marker_count_is_zero(tmp_path: Path):
    _ensure_assets(tmp_path)
    _clear_food_line_registries(tmp_path)

    for date in ("2026-06-06", "2026-06-07"):
        edition_dir = tmp_path / "output" / "site" / "food-line" / "editions" / date
        edition_dir.mkdir(parents=True, exist_ok=True)
        (edition_dir / "index.html").write_text("<html>Food Line edition</html>", encoding="utf-8")
        (edition_dir / "edition_manifest.json").write_text(
            json.dumps(
                {
                    "dispatch_slug": "food-line",
                    "edition_date": date,
                    "public_rendered": True,
                    "edition_mode": "no_current_update",
                    "source_freshness_status": "blocked_insufficient_fresh_current_stories",
                    "freshness_window_days": 14,
                    "stale_public_story_count": 1,
                    "excluded_stale_source_count": 1,
                    "stale_source_ids": ["food-line-auto-stale"],
                    "qualified_primary_count": 0,
                    "skip_reason": "",
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    map_root = tmp_path / "output" / "site" / "food-line" / "map"
    map_root.mkdir(parents=True, exist_ok=True)
    (map_root / "index.html").write_text(
        '<html><body><div id="foodLineMap" data-rendered-marker-count="0" data-skipped-marker-count="0"></div><p>Plotted markers: 0 | Skipped markers: 0</p></body></html>',
        encoding="utf-8",
    )
    (map_root / "map_data.json").write_text(
        json.dumps({"markers": [], "mapped_markers": [], "diagnostics": {"pressure_marker_count": 0, "rendered_marker_count": 0}}, indent=2),
        encoding="utf-8",
    )

    food_line._update_index_archive(tmp_path, "2026-06-07", "Food Line mission", max_edition_date="2026-06-07")

    index_html = (tmp_path / "output" / "site" / "food-line" / "index.html").read_text(encoding="utf-8")

    assert "2026-06-07 — No qualifying update" in index_html
    assert 'href="map/"' not in index_html


def test_food_line_homepage_shows_pressure_map_link_when_marker_count_is_positive(tmp_path: Path):
    _ensure_assets(tmp_path)
    _clear_food_line_registries(tmp_path)

    for date in ("2026-06-06", "2026-06-07"):
        edition_dir = tmp_path / "output" / "site" / "food-line" / "editions" / date
        edition_dir.mkdir(parents=True, exist_ok=True)
        (edition_dir / "index.html").write_text("<html>Food Line edition</html>", encoding="utf-8")
        (edition_dir / "edition_manifest.json").write_text(
            json.dumps(
                {
                    "dispatch_slug": "food-line",
                    "edition_date": date,
                    "public_rendered": True,
                    "edition_mode": "no_current_update",
                    "source_freshness_status": "blocked_insufficient_fresh_current_stories",
                    "freshness_window_days": 14,
                    "stale_public_story_count": 1,
                    "excluded_stale_source_count": 1,
                    "stale_source_ids": ["food-line-auto-stale"],
                    "qualified_primary_count": 0,
                    "skip_reason": "",
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    map_root = tmp_path / "output" / "site" / "food-line" / "map"
    map_root.mkdir(parents=True, exist_ok=True)
    (map_root / "index.html").write_text(
        '<html><body><div id="foodLineMap" data-rendered-marker-count="2" data-skipped-marker-count="0"></div><p>Plotted markers: 2 | Skipped markers: 0</p></body></html>',
        encoding="utf-8",
    )
    (map_root / "map_data.json").write_text(
        json.dumps({"markers": [{"source_record_id": "one"}, {"source_record_id": "two"}], "diagnostics": {"pressure_marker_count": 2}}, indent=2),
        encoding="utf-8",
    )

    food_line._update_index_archive(tmp_path, "2026-06-07", "Food Line mission", max_edition_date="2026-06-07")

    index_html = (tmp_path / "output" / "site" / "food-line" / "index.html").read_text(encoding="utf-8")

    assert "2026-06-07 — No qualifying update" in index_html
    assert 'href="map/"' in index_html


def test_food_line_old_usda_background_references_stay_background_only():
    background_freshness = food_line.validate_food_line_source_freshness(
        "2026-06-06",
        "2025-04-01T00:00:00Z",
        "https://www.fns.usda.gov/summer/2025/04/background",
        "resource_context",
        background=True,
        freshness_window_days=3,
    )
    stale_freshness = food_line.validate_food_line_source_freshness(
        "2026-06-06",
        "2025-04-01T00:00:00Z",
        "https://www.fns.usda.gov/summer/2025/04/background",
        "resource_context",
        background=False,
        freshness_window_days=3,
    )

    assert background_freshness["source_freshness_status"] == "background_reference"
    assert background_freshness["public_story_eligible"] is False
    assert background_freshness["source_published_date"] == "2025-04-01"
    assert stale_freshness["source_freshness_status"] == "stale_outside_daily_window"
    assert stale_freshness["public_story_eligible"] is False


def test_food_line_url_path_date_alone_is_not_public_story_eligible():
    url_only = food_line.validate_food_line_source_freshness(
        "2026-06-06",
        "",
        "https://example.com/2026/06/06/url-dated-source",
        "current_public_story",
        freshness_window_days=3,
    )

    assert url_only["source_freshness_status"] == "url_path_only"
    assert url_only["freshness_status"] == "url_path_only"
    assert url_only["public_story_eligible"] is False
    assert url_only["source_published_date"] == "2026-06-06"
    assert url_only["source_published_date_basis"] == "url_path"
    assert url_only["source_freshness_date_basis"] == "url_path_only"


def test_food_line_verified_published_at_can_be_public_story_eligible_when_fresh():
    fresh = food_line.validate_food_line_source_freshness(
        "2026-06-06",
        "2026-06-06T09:00:00Z",
        "https://example.com/2026/06/01/fresh-source",
        "current_public_story",
        freshness_window_days=3,
    )

    assert fresh["source_freshness_status"] == "fresh_daily_signal"
    assert fresh["freshness_status"] == "fresh_daily_signal"
    assert fresh["public_story_eligible"] is True
    assert fresh["source_published_date"] == "2026-06-06"
    assert fresh["source_published_date_basis"] == "published_at"
    assert fresh["source_freshness_date_basis"] == "published_at"


def test_food_line_page_metadata_date_can_support_public_story_eligibility():
    fresh = food_line.validate_food_line_source_freshness(
        "2026-06-06",
        "",
        "https://example.com/2026/06/01/fresh-source",
        "current_public_story",
        page_metadata_date="2026-06-06T11:30:00Z",
        freshness_window_days=3,
    )

    assert fresh["source_freshness_status"] == "fresh_daily_signal"
    assert fresh["freshness_status"] == "fresh_daily_signal"
    assert fresh["public_story_eligible"] is True
    assert fresh["source_published_date"] == "2026-06-06"
    assert fresh["source_published_date_basis"] == "page_metadata"
    assert fresh["source_freshness_date_basis"] == "page_metadata"


def test_food_line_stale_published_at_is_not_overridden_by_fresh_url_path_date():
    stale = food_line.validate_food_line_source_freshness(
        "2026-06-06",
        "2026-06-01T09:00:00Z",
        "https://example.com/2026/06/06/stale-source",
        "current_public_story",
        freshness_window_days=3,
    )

    assert stale["source_freshness_status"] == "stale_outside_daily_window"
    assert stale["freshness_status"] == "stale_outside_daily_window"
    assert stale["public_story_eligible"] is False
    assert stale["source_published_date"] == "2026-06-01"
    assert stale["source_published_date_basis"] == "published_at"
    assert stale["source_freshness_date_basis"] == "published_at"
    assert stale["date_provenance_warning"] == ""


def test_food_line_auto_collector_does_not_backfill_missing_published_at_from_edition_date(tmp_path: Path):
    _ensure_assets(tmp_path)
    _clear_food_line_registries(tmp_path)
    _write_source_registry(
        tmp_path,
        [
            {
                "source_id": "future-food-line-page",
                "source_name": "Future Food Line Page",
                "publisher": "Example News",
                "url": "https://example.com/2026-06-08/future-food-access-story",
                "source_family": "local_news",
                "source_type": "page",
                "state": "MA",
                "location_name": "Massachusetts",
                "location_scope": "state_local",
                "enabled": True,
                "notes": "Future-dated page without publication metadata.",
            }
        ],
    )

    def fetcher(url: str, timeout: int = 15):
        if url == "https://example.com/2026-06-08/future-food-access-story":
            return b"""<html><head><title>Food banks report rising demand</title><meta name='description' content='Pantries are seeing longer lines and more requests.'></head><body><p>Food banks report rising demand as pantries are seeing longer lines and more requests.</p></body></html>"""
        raise AssertionError(f"unexpected fetch url: {url}")

    result = food_line.collect_food_line_auto_sources(tmp_path, "2026-06-07", fetcher=fetcher)
    auto_path = tmp_path / "data" / "dispatches" / "food-line" / "sources" / "2026-06-07" / "auto_sources.json"
    rows = json.loads(auto_path.read_text(encoding="utf-8"))
    row = next(item for item in rows if item["url"] == "https://example.com/2026-06-08/future-food-access-story")

    assert result["source_count"] == 1
    assert row["published_at"] == ""
    assert row["page_metadata_date"] == ""
    assert row["published_date_basis"] == "retrieved_at_fallback"
    assert row["date_provenance_warning"] == "no verified publication date supplied"


def test_food_line_future_url_story_stays_no_current_update_without_verified_date(tmp_path: Path):
    _ensure_assets(tmp_path)
    _clear_food_line_registries(tmp_path)
    _write_source_registry(
        tmp_path,
        [
            {
                "source_id": "future-food-line-page",
                "source_name": "Future Food Line Page",
                "publisher": "Example News",
                "url": "https://example.com/2026-06-08/future-food-access-story",
                "source_family": "local_news",
                "source_type": "page",
                "state": "MA",
                "location_name": "Massachusetts",
                "location_scope": "state_local",
                "enabled": True,
                "notes": "Future-dated page without publication metadata.",
            }
        ],
    )

    def fetcher(url: str, timeout: int = 15):
        if url == "https://example.com/2026-06-08/future-food-access-story":
            return b"""<html><head><title>Food banks report rising demand</title><meta property='article:published_time' content='2026-06-08T12:00:00Z'><meta name='description' content='Pantries are seeing longer lines and more requests.'></head><body><p>Food banks report rising demand as pantries are seeing longer lines and more requests.</p></body></html>"""
        raise AssertionError(f"unexpected fetch url: {url}")

    result = run_food_line_dispatch(tmp_path, "2026-06-07", collect=True, collect_fetcher=fetcher)
    edition_manifest_path = tmp_path / "data" / "dispatches" / "food-line" / "editions" / "2026-06-07" / "run_manifest.json"
    review_path = tmp_path / "output" / "review" / "food-line" / "2026-06-07" / "pressure_review.csv"
    edition_manifest = json.loads(edition_manifest_path.read_text(encoding="utf-8"))
    review_rows = list(csv.DictReader(review_path.open(encoding="utf-8")))
    row = next(item for item in review_rows if item["source_url"] == "https://example.com/2026-06-08/future-food-access-story")

    assert row["source_freshness_date_basis"] == "page_metadata"
    assert row["source_public_story_eligible"] == "false"
    assert row["source_freshness_status"] == "stale_outside_daily_window"
    assert result["public_rendered"] is False
    assert result["edition_mode"] == "internal_no_qualifying_update"
    assert result["qualified_primary_count"] == 0
    assert edition_manifest["edition_mode"] == "internal_no_qualifying_update"
    assert edition_manifest["claim_count"] == 0
    assert edition_manifest["claim_ledger_path"] == "/food-line/editions/2026-06-07/claim_ledger.html"
    assert edition_manifest["source_table_path"] == "/food-line/editions/2026-06-07/source_table.html"
    assert edition_manifest["qualified_source_count"] == 0
    assert edition_manifest["excluded_source_count"] >= 0
    assert edition_manifest["correction_status"] == "none"
    assert edition_manifest["validation_status"] == "pending"
    assert edition_manifest["food_line_no_current_update_policy_status"] == "blocked"
    assert any("discovery-gap or equivalent expanded discovery did not run" in reason for reason in edition_manifest["food_line_no_current_update_policy_reasons"])
    assert (tmp_path / "output" / "site" / "food-line" / "editions" / "2026-06-07" / "index.html").exists() is False


def test_food_line_url_date_only_rows_stay_background_reference_and_audit_material(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-06"
    path = _manual_path(tmp_path, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = _pressure_row(
        1,
        "Fresh verified source",
        "Food bank demand increased and pantry lines grew.",
        family="local_news",
        state="TX",
        source_type="manual",
    )
    fresh["published_at"] = "2026-06-06T09:00:00Z"
    fresh["url"] = "https://example.com/2026/06/06/fresh-verified-source"
    url_only = _row(2, "local_news", "TX", title="URL-only audit source")
    url_only["published_at"] = ""
    url_only["url"] = "https://example.com/2026/06/06/url-only-audit-source"
    path.write_text(json.dumps([fresh, url_only], indent=2), encoding="utf-8")

    result = run_food_line_dispatch(tmp_path, date)

    review_rows = list(csv.DictReader((tmp_path / "output" / "review" / "food-line" / date / "pressure_review.csv").open(encoding="utf-8")))
    review_by_id = {row["source_record_id"]: row for row in review_rows}
    sources_manifest = json.loads((tmp_path / "output" / "site" / "food-line" / "editions" / date / "sources_manifest.json").read_text(encoding="utf-8"))
    url_only_manifest = next(row for row in sources_manifest if row["source_record_id"] == "food-line-src-002")
    source_table_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "source_table.html").read_text(encoding="utf-8")

    assert result["lead_source_record_id"] == "food-line-src-001"
    assert review_by_id["food-line-src-002"]["source_freshness_status"] == "url_path_only"
    assert review_by_id["food-line-src-002"]["freshness_status"] == "url_path_only"
    assert review_by_id["food-line-src-002"]["source_freshness_date_basis"] == "url_path_only"
    assert review_by_id["food-line-src-002"]["source_public_story_eligible"] == "false"
    assert url_only_manifest["source_freshness_date_basis"] == "url_path_only"
    assert url_only_manifest["source_public_story_eligible"] is False
    assert url_only_manifest["freshness_status"] == "url_path_only"
    assert "URL-only audit source" in source_table_html
    assert "Background reference" in source_table_html
    assert "source_freshness_status" in source_table_html
    assert "source_freshness_date_basis" in source_table_html
    assert "source_public_story_eligible" in source_table_html
    assert "published_at" in source_table_html
    assert "url_path_only" in source_table_html
    assert ">true<" in source_table_html
    assert ">false<" in source_table_html
    edition_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "index.html").read_text(encoding="utf-8")
    assert "source_freshness_status" not in edition_html
    assert "source_freshness_date_basis" not in edition_html
    assert "source_public_story_eligible" not in edition_html


def test_food_line_secondary_items_render_in_what_else_when_present():
    lead = {
        "source_record_id": "lead-1",
        "title": "Primary story",
        "publisher": "Lead News",
        "location_name": "Toledo",
        "pressure_summary": "Lead story summary.",
        "evidence_text": "Lead story summary.",
        "url": "https://example.com/lead",
        "pressure_signal": True,
        "source_role": "local_signal",
        "source_family": "local_news",
        "affected_groups": ["SNAP households"],
    }
    secondary = {
        "source_record_id": "secondary-1",
        "title": "Secondary food-access item",
        "publisher": "Neighbor News",
        "location_name": "Toledo",
        "pressure_summary": "Secondary item summary.",
        "evidence_text": "Secondary item summary.",
        "url": "https://example.com/secondary",
        "pressure_signal": True,
        "source_role": "local_signal",
        "source_family": "local_news",
    }
    background = {
        "source_record_id": "background-1",
        "title": "USDA FNS background",
        "publisher": "USDA FNS",
        "location_name": "United States",
        "pressure_summary": "",
        "evidence_text": "Background only.",
        "url": "https://example.com/background",
        "pressure_signal": False,
        "source_role": "resource_context",
        "source_family": "school_meals_child_nutrition",
    }

    edition_html = food_line.render_edition(
        "2026-06-05",
        [lead, secondary, background],
        {},
        lead,
        "daily",
        {},
        {},
        {},
        "new_primary",
        [],
    )

    assert "Limited-source update / June 5, 2026" in edition_html
    assert "Today’s Read" in edition_html
    assert "At A Glance" in edition_html
    assert "Core Food Pressure Signals" in edition_html
    assert "Other Food Line Signals" in edition_html
    assert "Source Mix" in edition_html
    assert "Source Note" in edition_html
    assert "Main Food Access Story" not in edition_html
    assert "What Else We’re Watching" not in edition_html
    assert "No fresh source-backed current food-pressure signal qualified today." not in edition_html
    assert "USDA FNS background" not in edition_html


def test_food_line_stale_future_edition_folders_are_pruned_from_public_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    _clear_food_line_registries(tmp_path)
    monkeypatch.setattr(food_line, "_food_line_local_today", lambda: dt_date(2026, 6, 6))

    stale_date = "2026-06-12"
    stale_dir = tmp_path / "output" / "site" / "food-line" / "editions" / stale_date
    stale_dir.mkdir(parents=True, exist_ok=True)
    (stale_dir / "index.html").write_text("<html><body>stale</body></html>", encoding="utf-8")
    (stale_dir / "edition_manifest.json").write_text(
        json.dumps(
            {
                "dispatch_slug": "food-line",
                "edition_date": stale_date,
                "public_rendered": True,
                "qualified_primary_count": 1,
                "skip_reason": "",
                "future_date_blocked": False,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (stale_dir / "sources_manifest.json").write_text("[]", encoding="utf-8")
    (stale_dir / "curation_manifest.json").write_text("[]", encoding="utf-8")
    audio_root = tmp_path / "output" / "site" / "food-line" / "audio"
    audio_root.mkdir(parents=True, exist_ok=True)
    (audio_root / f"{stale_date}.json").write_text(
        json.dumps(
            {
                "dispatch_slug": "food-line",
                "edition_date": stale_date,
                "transcript_url": f"https://dispatches.thebluefernco.com/food-line/audio/{stale_date}-transcript.html",
                "audio_available": True,
                "audio_file": f"{stale_date}.mp3",
                "audio_url": f"/food-line/audio/{stale_date}.mp3",
                "audio_mime_type": "audio/mpeg",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (audio_root / f"{stale_date}-transcript.html").write_text("<html>stale transcript</html>", encoding="utf-8")
    (audio_root / f"{stale_date}.mp3").write_bytes(b"stale-mp3")

    payload_path = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "food-line" / "sources" / "2026-06-05" / "auto_sources.json"
    date = "2026-06-05"
    payload = _freshen_food_line_payload_for_publication(json.loads(payload_path.read_text(encoding="utf-8")), date)
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    result = run_food_line_dispatch(tmp_path, date)

    archive_html = (tmp_path / "output" / "site" / "food-line" / "archive.html").read_text(encoding="utf-8")
    podcast = (tmp_path / "output" / "site" / "food-line" / "audio" / "podcast.xml").read_text(encoding="utf-8")

    assert result["public_rendered"] is True
    assert result["future_date_blocked"] is False
    assert not stale_dir.exists()
    assert not (audio_root / f"{stale_date}.json").exists()
    assert not (audio_root / f"{stale_date}-transcript.html").exists()
    assert not (audio_root / f"{stale_date}.mp3").exists()
    assert "editions/2026-06-05/" in archive_html
    assert "editions/2026-06-12/" not in archive_html
    assert stale_date not in podcast


def test_food_line_2026_06_13_is_blocked_by_default_without_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    _clear_food_line_registries(tmp_path)
    date = "2026-06-13"
    payload_path = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "food-line" / "sources" / "2026-06-13" / "auto_sources.json"
    payload = _freshen_food_line_payload_for_publication(json.loads(payload_path.read_text(encoding="utf-8")), date)
    monkeypatch.setattr(food_line, "_food_line_local_today", lambda: dt_date(2026, 6, 7))
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)

    site_edition = tmp_path / "output" / "site" / "food-line" / "editions" / date
    archive_html = (tmp_path / "output" / "site" / "food-line" / "archive.html").read_text(encoding="utf-8")
    audio_index = (tmp_path / "output" / "site" / "food-line" / "audio" / "index.html").read_text(encoding="utf-8")
    podcast_feed = (tmp_path / "output" / "site" / "food-line" / "audio" / "podcast.xml").read_text(encoding="utf-8")
    manifest = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "editions" / date / "run_manifest.json").read_text(encoding="utf-8"))

    assert result["public_rendered"] is False
    assert result["future_date_blocked"] is True
    assert result["future_date_override_used"] is False
    assert result["skip_reason"] == "Future-dated Food Line public editions are blocked unless explicitly allowed."
    assert result["bluesky_post_ready"] is False
    assert result["bluesky_post_text"] is None
    assert result["qualified_primary_count"] == 0
    assert site_edition.exists() is False
    assert "2026-06-13" not in archive_html
    assert "2026-06-13" not in audio_index
    assert "2026-06-13" not in podcast_feed
    assert manifest["public_rendered"] is False
    assert manifest["future_date_blocked"] is True
    assert manifest["future_date_override_used"] is False
    assert manifest["same_day_allowed"] is False
    assert manifest["qualified_primary_count"] == 0
    assert manifest["skip_reason"] == "Future-dated Food Line public editions are blocked unless explicitly allowed."


def test_food_line_2026_06_12_is_blocked_by_default_without_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    _clear_food_line_registries(tmp_path)
    date = "2026-06-12"
    payload_path = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "food-line" / "sources" / "2026-06-12" / "auto_sources.json"
    payload = _freshen_food_line_payload_for_publication(json.loads(payload_path.read_text(encoding="utf-8")), date)
    monkeypatch.setattr(food_line, "_food_line_local_today", lambda: dt_date(2026, 6, 7))
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)

    site_edition = tmp_path / "output" / "site" / "food-line" / "editions" / date
    archive_html = (tmp_path / "output" / "site" / "food-line" / "archive.html").read_text(encoding="utf-8")
    audio_index = (tmp_path / "output" / "site" / "food-line" / "audio" / "index.html").read_text(encoding="utf-8")
    podcast_feed = (tmp_path / "output" / "site" / "food-line" / "audio" / "podcast.xml").read_text(encoding="utf-8")
    manifest = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "editions" / date / "run_manifest.json").read_text(encoding="utf-8"))

    assert result["public_rendered"] is False
    assert result["future_date_blocked"] is True
    assert result["future_date_override_used"] is False
    assert result["skip_reason"] == "Future-dated Food Line public editions are blocked unless explicitly allowed."
    assert result["bluesky_post_ready"] is False
    assert result["bluesky_post_text"] is None
    assert result["qualified_primary_count"] == 0
    assert site_edition.exists() is False
    assert "2026-06-12" not in archive_html
    assert "2026-06-12" not in audio_index
    assert "2026-06-12" not in podcast_feed
    assert manifest["public_rendered"] is False
    assert manifest["future_date_blocked"] is True
    assert manifest["future_date_override_used"] is False
    assert manifest["same_day_allowed"] is False
    assert manifest["qualified_primary_count"] == 0
    assert manifest["skip_reason"] == "Future-dated Food Line public editions are blocked unless explicitly allowed."


def test_food_line_2026_06_13_can_publish_when_future_override_is_allowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    _clear_food_line_registries(tmp_path)
    payload_path = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "food-line" / "sources" / "2026-06-13" / "auto_sources.json"
    monkeypatch.setattr(food_line, "_food_line_local_today", lambda: dt_date(2026, 6, 7))

    date = "2026-06-13"
    payload = _freshen_food_line_payload_for_publication(json.loads(payload_path.read_text(encoding="utf-8")), date)
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date, allow_future_date=True)

    site_edition = tmp_path / "output" / "site" / "food-line" / "editions" / date
    manifest = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "editions" / date / "run_manifest.json").read_text(encoding="utf-8"))

    assert result["public_rendered"] is True
    assert result["future_date_blocked"] is False
    assert result["future_date_override_used"] is True
    assert result["skip_reason"] == ""
    assert result["bluesky_post_ready"] is True
    assert result["qualified_primary_count"] == 1
    assert site_edition.exists()
    assert manifest["public_rendered"] is True
    assert manifest["future_date_blocked"] is False
    assert manifest["future_date_override_used"] is True
    assert manifest["bluesky_post_ready"] is True


def test_food_line_2026_06_06_scheduled_yesterday_public_renders_and_generates_audio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    _clear_food_line_registries(tmp_path)
    _mock_food_line_tts(monkeypatch)
    monkeypatch.setattr(food_line, "_food_line_local_today", lambda: dt_date(2026, 6, 7))

    payload_path = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "food-line" / "sources" / "2026-06-06" / "auto_sources.json"
    date = "2026-06-06"
    payload = _freshen_food_line_payload_for_publication(json.loads(payload_path.read_text(encoding="utf-8")), date)
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    result = run_food_line_dispatch(tmp_path, date, generate_audio=True)

    audio_root = tmp_path / "output" / "site" / "food-line" / "audio"
    site_edition = tmp_path / "output" / "site" / "food-line" / "editions" / date
    archive_html = (tmp_path / "output" / "site" / "food-line" / "archive.html").read_text(encoding="utf-8")
    audio_index = (audio_root / "index.html").read_text(encoding="utf-8")
    transcript = (audio_root / f"{date}-transcript.html").read_text(encoding="utf-8")
    podcast = (audio_root / "podcast.xml").read_text(encoding="utf-8")
    audio_json = json.loads((audio_root / f"{date}.json").read_text(encoding="utf-8"))

    assert result["public_rendered"] is True
    assert result["future_date_blocked"] is False
    assert result["future_date_override_used"] is False
    assert result["skip_reason"] == ""
    assert result["audio_generated"] is True
    assert result["audio_available"] is True
    assert result["podcast_enclosure_present"] is True
    assert result["bluesky_post_ready"] is True
    assert site_edition.exists()
    assert (audio_root / f"{date}.mp3").exists()
    assert audio_json["episode_title"] == "Food Line Briefing — June 6, 2026"
    assert audio_json["audio_file"] == "2026-06-06.mp3"
    assert "/food-line/audio/2026-06-06.mp3" in audio_index
    assert "/food-line/audio/2026-06-06-transcript.html" in audio_index
    assert "/food-line/editions/2026-06-06/source_table.html" in audio_index
    assert "Podcast enclosure:</strong> present" in transcript
    assert "Open the podcast feed" in transcript
    assert '<enclosure url="https://dispatches.thebluefernco.com/food-line/audio/2026-06-06.mp3"' in podcast
    assert "type=\"audio/mpeg\"" in podcast
    assert "editions/2026-06-06/" in archive_html


def test_food_line_local_today_uses_pacific_timezone_when_utc_is_next_day():
    utc_late_night = datetime(2026, 7, 3, 1, 6, 12, tzinfo=timezone.utc)
    assert food_line._food_line_pacific_today(utc_late_night) == dt_date(2026, 7, 2)


def test_food_line_same_day_is_allowed_without_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    _clear_food_line_registries(tmp_path)
    monkeypatch.setattr(food_line, "_food_line_local_today", lambda: dt_date(2026, 6, 7))

    date = "2026-06-07"
    payload_path = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "food-line" / "sources" / "2026-06-07" / "auto_sources.json"
    payload = _freshen_food_line_payload_for_publication(json.loads(payload_path.read_text(encoding="utf-8")), date)
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)

    site_edition = tmp_path / "output" / "site" / "food-line" / "editions" / date
    archive_html = (tmp_path / "output" / "site" / "food-line" / "archive.html").read_text(encoding="utf-8")
    audio_index = (tmp_path / "output" / "site" / "food-line" / "audio" / "index.html").read_text(encoding="utf-8")
    podcast_feed = (tmp_path / "output" / "site" / "food-line" / "audio" / "podcast.xml").read_text(encoding="utf-8")
    manifest = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "editions" / date / "run_manifest.json").read_text(encoding="utf-8"))

    assert result["public_rendered"] is True
    assert result["future_date_blocked"] is False
    assert result["future_date_override_used"] is False
    assert result["same_day_allowed"] is True
    assert result["skip_reason"] == ""
    assert result["bluesky_post_ready"] is True
    assert result["bluesky_post_text"]
    assert result["qualified_primary_count"] >= 1
    assert site_edition.exists() is True
    assert "2026-06-07" in archive_html
    assert "2026-06-07" in audio_index
    assert "2026-06-07" in podcast_feed
    assert manifest["public_rendered"] is True
    assert manifest["future_date_blocked"] is False
    assert manifest["future_date_override_used"] is False
    assert manifest["same_day_allowed"] is True
    assert manifest["qualified_primary_count"] >= 1
    assert manifest["skip_reason"] == ""


def test_food_line_podcast_description_varies_by_pressure_summary(tmp_path: Path):
    _ensure_assets(tmp_path)
    date_a = "2026-06-02"
    pa = _manual_path(tmp_path, date_a)
    pa.parent.mkdir(parents=True, exist_ok=True)
    a = _pressure_row(1, "Food bank demand rises", "Food banks across Texas are working hard to keep up with rising demand.", family="local_news", state="TX")
    pa.write_text(json.dumps([a], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date_a)
    desc_a = json.loads((tmp_path / "output" / "site" / "food-line" / "audio" / f"{date_a}.json").read_text(encoding="utf-8"))["episode_summary"]
    date_b = "2026-06-03"
    pb = _manual_path(tmp_path, date_b)
    pb.parent.mkdir(parents=True, exist_ok=True)
    b = _pressure_row(2, "SNAP benefits delayed", "SNAP benefit delays are disrupting households and service centers.", family="local_news", state="TX")
    pb.write_text(json.dumps([b], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date_b)
    desc_b = json.loads((tmp_path / "output" / "site" / "food-line" / "audio" / f"{date_b}.json").read_text(encoding="utf-8"))["episode_summary"]
    assert desc_a != desc_b
    assert "Background and source links are available in the public source table." in desc_a
    assert "Background and source links are available in the public source table." in desc_b
    assert "Review summary:" not in desc_a
    assert "Review summary:" not in desc_b
    assert "Accountability note:" not in desc_a
    assert "Accountability note:" not in desc_b
    assert "matched terms" not in desc_a.lower()
    assert "matched terms" not in desc_b.lower()


def test_food_line_audio_generation_writes_clean_metadata_and_enclosure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(
        1,
        "Food bank sees rising demand from families",
        "Food bank demand increased and pantry lines grew. KLTV reported that food banks across Texas were working to keep up, and one East Texas pantry operator described a 17 percent increase over three weeks in people asking for food assistance. Skip to content Advertise With Us Weather Sports Contests Closings & Delays",
        family="local_news",
        state="TX",
    )
    row["source_name"] = "KLTV"
    row["publisher"] = "KLTV"
    row["location_name"] = "East Texas"
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    _mock_food_line_tts(monkeypatch)

    result = run_food_line_dispatch(tmp_path, date, generate_audio=True)

    audio_json = json.loads((tmp_path / "output" / "site" / "food-line" / "audio" / f"{date}.json").read_text(encoding="utf-8"))
    audio_index = (tmp_path / "output" / "site" / "food-line" / "audio" / "index.html").read_text(encoding="utf-8")
    transcript = (tmp_path / "output" / "site" / "food-line" / "audio" / f"{date}-transcript.html").read_text(encoding="utf-8")
    podcast = (tmp_path / "output" / "site" / "food-line" / "audio" / "podcast.xml").read_text(encoding="utf-8")

    assert result["audio_generated"] is True
    assert result["audio_required"] is False
    assert result["podcast_enclosure_present"] is True
    assert result["audio_mp3_url"] == "/food-line/audio/2026-06-04.mp3"
    assert Path(result["audio_mp3_path"]).exists()
    assert result["audio_story_section_count"] >= 4
    assert result["audio_story_sections"]
    assert audio_json["episode_title"] == "Food Line Briefing — June 4, 2026"
    assert audio_json["audio_story_section_count"] >= 4
    assert audio_json["audio_story_sections"]
    assert "Food Line Audio &mdash; June 4, 2026" in audio_index
    assert audio_json["episode_summary"].lower().startswith("today's food line briefing tracks food-bank demand in east texas")
    assert "Background and source links are available in the public source table." in audio_json["episode_summary"]
    assert "Today's pressure point:" not in audio_json["script_text"]
    assert "When benefits are delayed or paused" not in audio_json["script_text"]
    assert "Publishing note:" not in audio_json["script_text"]
    assert "Source note:" not in audio_json["script_text"]
    assert "Source links and excerpts are available in the public source table." in audio_json["script_text"]
    assert "Source links, excerpts, and background references are available in the public source table." not in audio_json["script_text"]
    assert "The run reviewed" not in audio_json["script_text"]
    assert "Read the source:" not in audio_json["script_text"]
    assert "Source:" not in audio_json["script_text"]
    assert "Where:" not in audio_json["script_text"]
    assert "Date:" not in audio_json["script_text"]
    assert "https://" not in audio_json["script_text"]
    assert "This edition uses only saved Food Line source records available at publish time." not in audio_json["script_text"]
    assert "Edition status: Daily edition" not in audio_json["script_text"]
    assert "Where pressure is visible" not in audio_json["script_text"]
    assert "For traceability" not in audio_json["script_text"]
    assert "Accountability note:" not in audio_json["script_text"]
    assert "Review summary:" not in audio_json["script_text"]
    assert "matched terms" not in audio_json["script_text"].lower()
    assert "the verified record came from" not in audio_json["script_text"].lower()
    assert "Skip to content" not in audio_json["script_text"]
    assert "Advertise With Us" not in audio_json["script_text"]
    assert transcript.count("This is the Food Line briefing for June 4, 2026.") == 1
    assert transcript.index("Opening") < transcript.index("Today&apos;s Read")
    assert transcript.index("Today&apos;s Read") < transcript.index("Core Food Pressure Signals")
    assert transcript.index("Core Food Pressure Signals") < transcript.index("Other Food Line Signals")
    assert transcript.index("Other Food Line Signals") < transcript.index("Source Note")
    assert transcript.index("Source Note") < transcript.index("Source links")
    assert "<h2>Transcript</h2>" not in transcript
    assert "Review summary:" not in transcript
    assert "Edition status: Daily edition" not in transcript
    assert "Where pressure is visible" not in transcript
    assert "For traceability" not in transcript
    assert "matched terms" not in transcript.lower()
    assert "the verified record came from" not in transcript.lower()
    assert "/food-line/audio/2026-06-04.mp3" in transcript
    assert "/food-line/editions/2026-06-04/source_table.html" in transcript
    assert "Open the public edition" in transcript
    assert "Open the podcast feed" in transcript
    assert "Podcast enclosure:</strong> present" in transcript
    assert "Review summary:" not in transcript.split("Source Note", 1)[0]
    assert "Review summary:" not in audio_index.split("Source Note", 1)[0]
    assert audio_index.index("Opening") < audio_index.index("Today&apos;s Read")
    assert audio_index.index("Today&apos;s Read") < audio_index.index("Core Food Pressure Signals")
    assert audio_index.index("Core Food Pressure Signals") < audio_index.index("Other Food Line Signals")
    assert audio_index.index("Other Food Line Signals") < audio_index.index("Source Note")
    assert "Source Note" in audio_index
    assert "Source links" in audio_index
    assert "Where pressure is visible" not in audio_index
    assert "For traceability" not in audio_index
    assert "The briefing also tracks related public background sources." not in audio_index
    assert "Main Food Access Story" not in transcript
    assert "What Else We’re Watching" not in transcript
    assert "Sources Behind This Briefing" not in transcript
    assert "Main Food Access Story" not in audio_index
    assert "What Else We’re Watching" not in audio_index
    assert "Sources Behind This Briefing" not in audio_index
    assert "/food-line/audio/2026-06-04-transcript.html" in audio_index
    assert "/food-line/editions/2026-06-04/source_table.html" in audio_index
    assert "Open the podcast feed" in audio_index
    assert result["selected_lead_pressure_scope_label"] == "Local / operational"
    assert result["selected_lead_pressure_scope_text"] == "local/operational"
    assert result["bluesky_post_ready"] is True
    assert result["bluesky_post_text"]
    assert len(result["bluesky_post_text"]) <= 300
    assert "<enclosure " in podcast
    assert "Review summary:" not in podcast
    assert "matched terms" not in podcast.lower()
    assert "the verified record came from" not in podcast.lower()
    assert "Edition status: Daily edition" not in podcast
    assert "Where pressure is visible" not in podcast
    assert "The briefing also tracks related public background sources." not in podcast
    assert podcast.index("food-bank demand in East Texas") < podcast.index("Background and source links are available in the public source table.")
    assert 'src="/food-line/audio/2026-06-04.mp3"' in audio_index
    assert "<strong>Podcast enclosure:</strong> present" in audio_index
    assert "<strong>Podcast enclosure:</strong> present" in transcript


def test_food_line_audio_reuses_existing_mp3_when_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    date = "2026-06-05"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food banks see rising demand", "Food banks across Texas are working hard to keep up with rising demand.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    existing_audio = _seed_existing_food_line_audio(tmp_path, date, b"existing-food-line-mp3")
    _mock_food_line_tts_failure(monkeypatch)

    result = run_food_line_dispatch(tmp_path, date, generate_audio=True)

    audio_json = json.loads((tmp_path / "output" / "site" / "food-line" / "audio" / f"{date}.json").read_text(encoding="utf-8"))
    podcast = (tmp_path / "output" / "site" / "food-line" / "audio" / "podcast.xml").read_text(encoding="utf-8")

    assert result["audio_generated"] is False
    assert result["audio_available"] is True
    assert result["audio_reused_existing"] is True
    assert result["podcast_enclosure_present"] is True
    assert result["audio_mp3_path"] == str(existing_audio)
    assert result["audio_mp3_url"] == "/food-line/audio/2026-06-05.mp3"
    assert audio_json["audio_generated"] is False
    assert audio_json["audio_available"] is True
    assert audio_json["audio_reused_existing"] is True
    assert audio_json["audio_file"] == "2026-06-05.mp3"
    assert audio_json["podcast_enclosure_present"] is True
    assert "<enclosure " in podcast
    assert existing_audio.read_bytes() == b"existing-food-line-mp3"


def test_food_line_audio_force_regenerate_replaces_only_on_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    date = "2026-06-06"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food banks see rising demand", "Food banks across Texas are working hard to keep up with rising demand.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    existing_audio = _seed_existing_food_line_audio(tmp_path, date, b"old-audio")
    _mock_food_line_tts(monkeypatch, audio_bytes=b"new-audio")

    result = run_food_line_dispatch(tmp_path, date, generate_audio=True, force_audio_regenerate=True)

    audio_json = json.loads((tmp_path / "output" / "site" / "food-line" / "audio" / f"{date}.json").read_text(encoding="utf-8"))

    assert result["audio_generated"] is True
    assert result["audio_available"] is True
    assert result["audio_reused_existing"] is False
    assert result["audio_replacement_performed"] is True
    assert result["audio_mp3_path"] == str(existing_audio)
    assert existing_audio.read_bytes() == b"new-audio"
    assert audio_json["audio_generated"] is True
    assert audio_json["audio_available"] is True
    assert audio_json["audio_replacement_performed"] is True
    assert audio_json["audio_file"] == "2026-06-06.mp3"


def test_food_line_audio_force_regenerate_failure_keeps_existing_mp3(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    date = "2026-06-07"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food banks see rising demand", "Food banks across Texas are working hard to keep up with rising demand.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    existing_audio = _seed_existing_food_line_audio(tmp_path, date, b"preserved-audio")
    _mock_food_line_tts_failure(monkeypatch)

    result = run_food_line_dispatch(tmp_path, date, generate_audio=True, force_audio_regenerate=True)

    audio_json = json.loads((tmp_path / "output" / "site" / "food-line" / "audio" / f"{date}.json").read_text(encoding="utf-8"))
    podcast = (tmp_path / "output" / "site" / "food-line" / "audio" / "podcast.xml").read_text(encoding="utf-8")

    assert result["audio_generated"] is False
    assert result["audio_available"] is True
    assert result["audio_reused_existing"] is True
    assert result["audio_replacement_performed"] is False
    assert result["podcast_enclosure_present"] is True
    assert result["ok"] is True
    assert existing_audio.read_bytes() == b"preserved-audio"
    assert audio_json["audio_generated"] is False
    assert audio_json["audio_available"] is True
    assert audio_json["audio_reused_existing"] is True
    assert audio_json["podcast_enclosure_present"] is True
    assert "<enclosure " in podcast


def test_food_line_audio_require_audio_passes_when_existing_mp3_is_reused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    date = "2026-06-08"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food banks see rising demand", "Food banks across Texas are working hard to keep up with rising demand.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    _seed_existing_food_line_audio(tmp_path, date, b"existing-audio")
    _mock_food_line_tts_failure(monkeypatch)

    result = run_food_line_dispatch(tmp_path, date, generate_audio=True, require_audio=True)

    assert result["ok"] is True
    assert result["audio_generated"] is False
    assert result["audio_available"] is True
    assert result["audio_reused_existing"] is True
    assert result["podcast_enclosure_present"] is True


def test_food_line_audio_require_audio_fails_when_missing_and_tts_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    date = "2026-06-09"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food banks see rising demand", "Food banks across Texas are working hard to keep up with rising demand.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    _mock_food_line_tts_failure(monkeypatch)

    result = run_food_line_dispatch(tmp_path, date, generate_audio=True, require_audio=True, force_audio_regenerate=True)

    assert result["ok"] is False
    assert result["audio_generated"] is False
    assert result["audio_available"] is False
    assert result["audio_reused_existing"] is False
    assert result["podcast_enclosure_present"] is False


def test_food_line_public_evidence_excerpt_strips_kltv_site_chrome():
    raw = (
        "Food bank demand increased and pantry lines grew. "
        "Teacher Tribute Health Update Aging Untold Local News Video Extra Community We the People Reception Issues About Us "
        "KLTV.com - Channel 7 News, , for East Texas - KLTV.com - Tyler, Longview, Jacksonville | ETX News. "
        "KLTV reported that food banks across Texas were working to keep up, and one East Texas pantry operator described a 17 percent increase over three weeks in people asking for food assistance."
    )
    cleaned = food_line.clean_food_line_public_evidence_excerpt(raw, title="Food bank sees rising demand from families", limit=420)
    assert "Teacher Tribute" not in cleaned
    assert "Health Update" not in cleaned
    assert "Aging Untold" not in cleaned
    assert "Local News Video" not in cleaned
    assert "Extra Community" not in cleaned
    assert "We the People" not in cleaned
    assert "Reception Issues" not in cleaned
    assert "KLTV.com - Channel 7 News" not in cleaned
    assert "ETX News" not in cleaned
    assert "Food bank demand increased and pantry lines grew" in cleaned


def test_food_line_public_evidence_excerpt_strips_usda_gov_chrome():
    raw = (
        "Summer nutrition programs can help families keep meals on the table. "
        "Skip to main content Here’s how you know An official website of the United States government "
        "Official websites use .gov A .gov website belongs to an official government organization in the United States "
        "Secure .gov websites use HTTPS A lock ( Lock Locked padlock ) or https:// means you’ve safely connected to the .gov website "
        "Share sensitive information only on official, secure websites."
    )
    cleaned = food_line.clean_food_line_public_evidence_excerpt(raw, title="Summer Nutrition Programs", limit=420)
    assert cleaned != food_line.FOOD_LINE_PUBLIC_EVIDENCE_FALLBACK
    assert "Skip to main content" not in cleaned
    assert "Here’s how you know" not in cleaned
    assert "An official website of the United States government" not in cleaned
    assert "Official websites use .gov" not in cleaned
    assert "A .gov website belongs" not in cleaned
    assert "Secure .gov websites use HTTPS" not in cleaned
    assert "Share sensitive information only on official, secure websites" not in cleaned
    assert "can help families keep meals on the table" in cleaned


def test_food_line_public_evidence_excerpt_strips_wpde_sinclair_footer():
    raw = (
        "Grand Strand food providers say inflation is driving more families to pantries. "
        "WPDE reported that food insecurity is rising above levels seen during the COVID-19 pandemic, and local food providers along the Grand Strand say they are seeing the impact firsthand. "
        "In Horry County, Feeding America’s most recent Map the Meal Gap report shows about 14 percent of residents are food insecure. "
        "When looking only at children, about 20 percent of Horry County’s kids are considered food insecure. "
        "The Lowcountry Food Bank said demand is climbing at pantries and mobile distributions. "
        "ABC 15 is teaming up with Feeding America for Sinclair Cares: Summer Hunger Relief, encouraging donations to help provide food for kids during the summer. "
        "More information and donations are available at sinclaircares.com."
    )
    cleaned = food_line.clean_food_line_public_evidence_excerpt(
        raw,
        title="Grand Strand food providers say inflation is driving more families to pantries",
        limit=900,
    )
    assert cleaned != food_line.FOOD_LINE_PUBLIC_EVIDENCE_FALLBACK
    assert "Sinclair Cares" not in cleaned
    assert "More information and donations are available at" not in cleaned
    assert "abc 15 is teaming up" not in cleaned.lower()
    assert "14 percent" in cleaned
    assert "20 percent" in cleaned
    assert "mobile distributions" in cleaned


def test_food_line_source_card_omits_excerpt_when_boilerplate_cleans_to_fallback():
    row = {
        "title": "USDA Summer Food Service Program",
        "publisher": "USDA FNS",
        "location_name": "United States",
        "url": "https://www.fns.usda.gov/summer",
        "source_record_id": "food-line-context-test",
        "pressure_type": "context only",
        "pressure_summary": "",
        "evidence_text": (
            "Skip to main content Here’s how you know An official website of the United States government "
            "Official websites use .gov A .gov website belongs to an official government organization in the United States "
            "Secure .gov websites use HTTPS A lock ( Lock Locked padlock ) or https:// means you’ve safely connected to the .gov website "
            "Share sensitive information only on official, secure websites."
        ),
        "affected_groups": [],
    }
    html_output = food_line._food_line_source_card_html(row, label="Context record", heading_prefix="Context:")
    assert "Evidence excerpt:" not in html_output
    assert "USDA FNS" in html_output
    assert "https://www.fns.usda.gov/summer" in html_output
    assert "Context: USDA Summer Food Service Program" in html_output


def test_food_line_discovery_source_configuration_includes_wpde_and_south_carolina():
    registry = json.loads((Path(__file__).parent.parent / "data" / "dispatches" / "food-line" / "source_registry.json").read_text(encoding="utf-8"))
    by_id = {row["source_id"]: row for row in registry}

    wpde = by_id["wpde-grand-strand-local-news"]
    assert wpde["source_family"] == "local_news"
    assert wpde["source_type"] == "page"
    assert wpde["url"] == "https://wpde.com/news/local"
    assert wpde["state"] == "SC"
    assert wpde["location_name"] == "Horry County, SC"

    priority = json.loads((Path(__file__).parent.parent / "data" / "dispatches" / "food-line" / "source_discovery_priority_domains.json").read_text(encoding="utf-8"))
    priority_domains = {str(item).strip().lower() for item in priority.get("priority_domains") or []}
    priority_states = {str(item).strip().upper() for item in priority.get("priority_states") or []}
    assert "wpde.com" in priority_domains
    assert "www.wpde.com" in priority_domains
    assert "SC" in priority_states
    assert "SC" in food_line_discovery.STATES


def test_food_line_audio_transcript_only_omits_enclosure(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")

    result = run_food_line_dispatch(tmp_path, date)

    audio_json = json.loads((tmp_path / "output" / "site" / "food-line" / "audio" / f"{date}.json").read_text(encoding="utf-8"))
    podcast = (tmp_path / "output" / "site" / "food-line" / "audio" / "podcast.xml").read_text(encoding="utf-8")
    audio_index = (tmp_path / "output" / "site" / "food-line" / "audio" / "index.html").read_text(encoding="utf-8")

    assert result["audio_generated"] is False
    assert result["podcast_enclosure_present"] is False
    assert result["audio_mp3_path"] is None
    assert result["audio_mp3_url"] is None
    assert audio_json["audio_generated"] is False
    assert audio_json["audio_required"] is False
    assert audio_json["audio_file"] is None
    assert audio_json["audio_status"] == "transcript_only"
    assert "<enclosure " not in podcast
    assert "Food Line Audio" in audio_index


def test_food_line_cli_defaults_to_audio_generation():
    args = food_line.parse_args(["--date", "2026-06-05"])
    assert args.generate_audio is True

    transcript_only_args = food_line.parse_args(["--date", "2026-06-05", "--no-generate-audio"])
    assert transcript_only_args.generate_audio is False


def test_food_line_blocked_date_keeps_existing_audio_landing_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    _mock_food_line_tts(monkeypatch)

    public_date = "2026-06-05"
    blocked_date = "2026-07-12"
    p_public = _manual_path(tmp_path, public_date)
    p_public.parent.mkdir(parents=True, exist_ok=True)
    p_public.write_text(json.dumps([_pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, public_date, generate_audio=True)

    p_blocked = _manual_path(tmp_path, blocked_date)
    p_blocked.parent.mkdir(parents=True, exist_ok=True)
    p_blocked.write_text(json.dumps([_pressure_row(2, "Food banks brace for demand", "Food bank demand increased and pantry lines grew.", family="local_news", state="WA")], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, blocked_date)

    audio_index = (tmp_path / "output" / "site" / "food-line" / "audio" / "index.html").read_text(encoding="utf-8")
    podcast = (tmp_path / "output" / "site" / "food-line" / "audio" / "podcast.xml").read_text(encoding="utf-8")

    assert "No public audio episode was published for this run." not in audio_index
    assert "No public audio episode was published" not in audio_index
    assert "Food Line Briefing — June 5, 2026" in audio_index
    assert "/food-line/audio/2026-06-05.mp3" in audio_index
    assert "/food-line/audio/2026-06-05-transcript.html" in audio_index
    assert "/food-line/editions/2026-06-05/source_table.html" in audio_index
    assert "podcast.xml" in audio_index
    assert "Podcast enclosure:</strong> present" in audio_index
    assert '<enclosure url="https://dispatches.thebluefernco.com/food-line/audio/2026-06-05.mp3" length="13" type="audio/mpeg" />' in podcast


def test_food_line_public_audio_links_use_explicit_index_html(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    _mock_food_line_tts(monkeypatch)

    date = "2026-06-05"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps([_pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")], indent=2), encoding="utf-8")

    run_food_line_dispatch(tmp_path, date, generate_audio=True)

    home_index = (tmp_path / "output" / "site" / "food-line" / "index.html").read_text(encoding="utf-8")
    audio_index = (tmp_path / "output" / "site" / "food-line" / "audio" / "index.html").read_text(encoding="utf-8")

    assert 'href="audio/index.html"' in home_index
    assert 'href="audio/"' not in home_index
    assert 'href="https://dispatches.thebluefernco.com/food-line/audio/index.html"' in audio_index
    assert 'href="https://dispatches.thebluefernco.com/food-line/audio/"' not in audio_index


def test_food_line_audio_failure_reports_sanitized_diagnostics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-1234567890")
    _mock_food_line_tts_failure(monkeypatch)

    result = run_food_line_dispatch(tmp_path, date, generate_audio=True, audio_timeout_seconds=12.0)

    assert result["audio_generated"] is False
    assert result["audio_required"] is False
    assert result["ok"] is True
    assert result["tts_provider"] == "openai"
    assert result["tts_model_requested"] == "gpt-4o-mini-tts"
    assert result["tts_voice_requested"] == "alloy"
    assert result["tts_narration_char_count"] > 0
    assert result["tts_output_path_attempted"].endswith("2026-06-04.tmp.mp3")
    assert result["tts_api_key_present"] is True
    assert result["tts_output_dir_exists"] is True
    assert result["tts_partial_mp3_exists"] is False
    assert result["tts_elapsed_seconds"] >= 0
    assert result["tts_exception_type"] == "TimeoutError"
    assert "redacted-api-key" in str(result["tts_exception_message_sanitized"])
    assert "sk-test-1234567890" not in str(result["tts_exception_message_sanitized"])
    assert result["tts_timeout_seconds"] == 12.0
    assert result["warnings"]


def test_food_line_audio_failure_blocks_when_required(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-1234567890")
    _mock_food_line_tts_failure(monkeypatch)

    result = run_food_line_dispatch(tmp_path, date, generate_audio=True, require_audio=True)

    assert result["ok"] is False
    assert result["audio_required"] is True
    assert result["audio_generated"] is False
    assert result["audio_status"] == "openai_tts_request_failed"
    assert any("audio narration was not generated" in warning.lower() for warning in result["warnings"])


def test_tts_tls_context_uses_explicit_bluefern_ca_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    pem = tmp_path / "corp-ca.pem"
    pem.write_text("PEM", encoding="utf-8")
    monkeypatch.setenv("BLUEFERN_TTS_CA_FILE", str(pem))
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    monkeypatch.delenv("BLUEFERN_TTS_USE_TRUSTSTORE", raising=False)
    monkeypatch.delenv("BLUEFERN_TTS_CA_SOURCE", raising=False)
    called: dict[str, str | None] = {"cafile": None}

    def fake_create_default_context(*, cafile=None):
        called["cafile"] = cafile
        return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    monkeypatch.setattr(tts_provider.ssl, "create_default_context", fake_create_default_context)
    _ctx, meta = tts_provider._build_tls_context()
    assert called["cafile"] == str(pem.resolve())
    assert meta["tls_verify"] is True
    assert meta["ca_source"] == "bluefern_tts_ca_file"
    assert meta["ca_file_used"] == str(pem.resolve())
    assert meta["bluefern_tts_ca_file_env"] == str(pem)


def test_tts_tls_context_uses_ssl_cert_file_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    pem = tmp_path / "ssl-cert-file.pem"
    pem.write_text("PEM", encoding="utf-8")
    monkeypatch.delenv("BLUEFERN_TTS_CA_FILE", raising=False)
    monkeypatch.setenv("SSL_CERT_FILE", str(pem))
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    monkeypatch.delenv("BLUEFERN_TTS_USE_TRUSTSTORE", raising=False)
    monkeypatch.delenv("BLUEFERN_TTS_CA_SOURCE", raising=False)
    called: dict[str, str | None] = {"cafile": None}

    def fake_create_default_context(*, cafile=None):
        called["cafile"] = cafile
        return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    monkeypatch.setattr(tts_provider.ssl, "create_default_context", fake_create_default_context)
    _ctx, meta = tts_provider._build_tls_context()
    assert called["cafile"] == str(pem.resolve())
    assert meta["ca_source"] == "SSL_CERT_FILE"
    assert meta["ca_file_used"] == str(pem.resolve())
    assert meta["ssl_cert_file_env"] == str(pem)


def test_tts_tls_context_uses_requests_ca_bundle_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    pem = tmp_path / "requests-ca-bundle.pem"
    pem.write_text("PEM", encoding="utf-8")
    monkeypatch.delenv("BLUEFERN_TTS_CA_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(pem))
    monkeypatch.delenv("BLUEFERN_TTS_USE_TRUSTSTORE", raising=False)
    monkeypatch.delenv("BLUEFERN_TTS_CA_SOURCE", raising=False)
    called: dict[str, str | None] = {"cafile": None}

    def fake_create_default_context(*, cafile=None):
        called["cafile"] = cafile
        return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    monkeypatch.setattr(tts_provider.ssl, "create_default_context", fake_create_default_context)
    _ctx, meta = tts_provider._build_tls_context()
    assert called["cafile"] == str(pem.resolve())
    assert meta["ca_source"] == "REQUESTS_CA_BUNDLE"
    assert meta["ca_file_used"] == str(pem.resolve())
    assert meta["requests_ca_bundle_env"] == str(pem)


def test_tts_tls_context_uses_certifi_when_requested(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    certifi_pem = tmp_path / "certifi.pem"
    certifi_pem.write_text("PEM", encoding="utf-8")
    fake_certifi = types.SimpleNamespace(where=lambda: str(certifi_pem))
    monkeypatch.setitem(sys.modules, "certifi", fake_certifi)
    monkeypatch.setenv("BLUEFERN_TTS_CA_SOURCE", "certifi")
    monkeypatch.delenv("BLUEFERN_TTS_CA_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    monkeypatch.delenv("BLUEFERN_TTS_USE_TRUSTSTORE", raising=False)
    called: dict[str, str | None] = {"cafile": None}

    def fake_create_default_context(*, cafile=None):
        called["cafile"] = cafile
        return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    monkeypatch.setattr(tts_provider.ssl, "create_default_context", fake_create_default_context)
    _ctx, meta = tts_provider._build_tls_context()
    assert called["cafile"] == str(certifi_pem)
    assert meta["ca_source"] == "certifi"
    assert meta["ca_file_used"] == str(certifi_pem)


def test_tts_tls_context_reports_truststore_requested(monkeypatch: pytest.MonkeyPatch):
    fake_truststore = types.SimpleNamespace(SSLContext=lambda protocol: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT))
    monkeypatch.setitem(sys.modules, "truststore", fake_truststore)
    monkeypatch.setenv("BLUEFERN_TTS_USE_TRUSTSTORE", "1")
    monkeypatch.delenv("BLUEFERN_TTS_CA_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    monkeypatch.delenv("BLUEFERN_TTS_CA_SOURCE", raising=False)
    _ctx, meta = tts_provider._build_tls_context()
    assert meta["truststore_requested"] is True
    assert meta["truststore_available"] is True
    assert meta["ca_source"] == "truststore"


def test_tts_failure_includes_tls_diagnostics_and_masks_api_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-1234567890")
    monkeypatch.delenv("BLUEFERN_TTS_CA_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    monkeypatch.delenv("BLUEFERN_TTS_USE_TRUSTSTORE", raising=False)
    monkeypatch.delenv("BLUEFERN_TTS_CA_SOURCE", raising=False)
    monkeypatch.setattr(
        tts_provider.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(urllib.error.URLError("certificate verify failed")),
    )

    result, diag = tts_provider.synthesize_speech_with_diagnostics(
        text="Smoke test",
        provider="openai",
        model="gpt-4o-mini-tts",
        voice="alloy",
        audio_format="mp3",
        timeout=5.0,
    )

    assert result.ok is False
    assert diag.api_key_present is True
    assert diag.tls_verify is True
    assert diag.ca_source == "system_default"
    assert diag.ca_file_used is None
    assert diag.truststore_requested is False
    assert diag.truststore_available is False
    assert "sk-test-1234567890" not in (diag.exception_message_sanitized or "")
    assert "certificate verify failed" in (diag.exception_message_sanitized or "")


def test_food_line_tts_smoke_command_writes_fake_mp3(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    smoke_dir = tmp_path / "smoke"
    smoke_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        food_line_tts,
        "synthesize_speech_with_diagnostics",
        lambda **kwargs: (
            TTSResult(True, b"fake-mp3-data", "openai", kwargs.get("model"), kwargs.get("voice"), kwargs.get("audio_format"), None),
            TTSDiagnostics(
                provider="openai",
                model_requested=kwargs.get("model"),
                voice_requested=kwargs.get("voice"),
                narration_char_count=len(str(kwargs.get("text") or "")),
                output_path_attempted=str(kwargs.get("output_path") or ""),
                api_key_present=True,
                output_dir_exists=True,
                partial_mp3_exists=False,
                elapsed_seconds=0.05,
                exception_type=None,
                exception_message_sanitized=None,
                timeout_seconds=float(kwargs.get("timeout") or 90.0),
                audio_format=kwargs.get("audio_format"),
                tls_verify=True,
                ca_file_used=None,
                ca_source="system_default",
                truststore_requested=False,
                truststore_available=False,
                ssl_cert_file_env=None,
                requests_ca_bundle_env=None,
                bluefern_tts_ca_file_env=None,
                tls_workaround_warning=None,
            ),
        ),
    )

    result = food_line_tts.run_food_line_tts_smoke(
        date="2026-06-04",
        sample_text="This is a Food Line Dispatch audio smoke test.",
        output=smoke_dir,
    )

    assert result["ok"] is True
    assert result["mp3_path"].endswith("tts_smoke-test.mp3")
    assert result["mp3_size_bytes"] > 0
    assert (smoke_dir / "tts_smoke_test.json").exists()
    assert (smoke_dir / "tts_smoke-test.mp3").exists()
    assert result["api_key_present"] is True
    assert result["error_type"] is None
    assert result["tls_verify"] is True
    assert result["ca_source"] == "system_default"


def test_food_line_manifest_includes_lead_and_role_counts(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-02"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    lead = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([lead, _row(2, "state_official", "OR"), _row(3, "economic_data", "US")], indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    manifest = json.loads((tmp_path / "output" / "site" / "food-line" / "editions" / date / "edition_manifest.json").read_text(encoding="utf-8"))
    assert result["lead_source_record_id"] in {row["source_record_id"] for row in json.loads((tmp_path / "output" / "site" / "food-line" / "editions" / date / "sources_manifest.json").read_text(encoding="utf-8"))}
    assert isinstance(manifest.get("source_roles_count"), dict)
    assert manifest.get("editorial_status")
    assert manifest.get("why_this_lead")
    assert manifest.get("primary_signal_status") in {"new_primary", "continuing_only", "none"}
    assert "previous_edition_date" in manifest
    assert manifest.get("public_rendered") is True
    assert manifest.get("qualified_primary_count") == 1
    assert manifest.get("skip_reason") == ""


def test_food_line_sparse_day_labeled_clearly(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-02"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps([_row(1), _row(2, "state_official", "OR")], indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    assert result["editorial_status"] == "sparse"
    assert result["source_adequacy"]["status"] == "limited"


def test_food_line_registry_supports_state_local_entries(tmp_path: Path):
    _write_source_registry(
        tmp_path,
        [
            {
                "source_id": "local-source-1",
                "source_name": "Local Source One",
                "publisher": "Local Publisher",
                "url": "https://example.com/local-source-1",
                "source_family": "local_news",
                "source_type": "page",
                "state": "OR",
                "location_name": "Portland, OR",
                "location_scope": "state_local",
                "enabled": True,
            }
        ],
    )
    _write_pressure_registry(tmp_path, [])
    entries = load_food_line_registry(tmp_path)
    assert entries
    state_rows = [row for row in entries if str(row.get("state") or "").upper() not in {"", "US"}]
    assert state_rows
    required = {
        "source_id",
        "source_name",
        "source_family",
        "source_type",
        "url",
        "state",
        "location_name",
        "location_scope",
        "source_role_allowed",
        "pressure_required",
        "freshness_mode",
        "max_age_days",
        "positive_keywords",
        "negative_keywords",
        "affected_group_keywords",
        "default_issue_tags",
        "default_map_category",
        "enabled",
        "notes",
    }
    assert required.issubset(set(state_rows[0].keys()))


def test_food_line_state_local_classifies_as_local_signal(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-03"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    local = _row(1, family="state_official", state="WA")
    local["issue_tags"] = ["SNAP", "benefits", "service access"]
    local["published_date_basis"] = "source_published"
    p.write_text(json.dumps([local, _row(2, "economic_data", "US"), _row(3, "policy_research", "US")], indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    assert result["source_roles_count"]["local_signal"] >= 1


def test_food_line_national_record_not_local_signal(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-03"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    national = _row(1, family="economic_data", state="US")
    national["issue_tags"] = ["household food insecurity"]
    p.write_text(json.dumps([national, _row(2, "policy_research", "US"), _row(3, "federal_official", "US")], indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    assert result["source_roles_count"]["local_signal"] == 0


def test_food_line_local_signal_beats_background_as_lead(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-03"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    bg = _row(1, family="economic_data", state="US")
    bg["map_category"] = "context / monitoring only"
    local = _pressure_row(2, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="PA")
    local["issue_tags"] = ["SNAP", "benefits", "service access"]
    local["map_category"] = "benefit disruption"
    local["summary_or_snippet"] = "Food bank demand increased and pantry lines grew."
    p.write_text(json.dumps([bg, local, _row(3, "policy_research", "US")], indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    assert result["lead_source_record_id"] == local["source_record_id"]


def test_food_line_wsls_roanoke_shortage_is_local_map_eligible_and_traceable(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-10"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    wsls_id = "wsls-roanoke-st-francis-house-food-shortage-20260610"
    wsls_excerpt = (
        "ROANOKE, Va. - Roanoke City's St. Francis House Food Pantry faced completely empty shelves in May. "
        "Now in June, the pantry is facing an even tighter situation heading into summer, and the people who run it say the situation is only getting harder. "
        "St. Francis House received a new USDA food shipment for June, but the entire delivery is expected to last through the end of the month, and they received even less food than they had in May. "
        "In May, the pantry ran out of food in just two weeks. The June delivery was even smaller than May's. "
        "Enge said the shortfall is significant and is causing them to hand out less food. "
        "Summer is one of the busiest seasons for food pantries, as children who typically receive free or reduced-price lunches during the school year lose access to those daily meals. "
        "At the same time, cuts to SNAP and other USDA programs are leaving more families with fewer options."
    )
    wsls = {
        "source_record_id": wsls_id,
        "title": "Why Roanoke's St. Francis House is facing its tightest food shortage ever this summer",
        "url": "https://www.wsls.com/news/local/2026/06/10/why-roanokes-st-francis-house-is-facing-its-tightest-food-shortage-ever-this-summer/",
        "publisher": "WSLS",
        "published_at": "2026-06-10T06:24:00",
        "page_metadata_date": "2026-06-10T09:57:00",
        "retrieved_at": "2026-06-11T00:00:00Z",
        "summary_or_snippet": "St. Francis House had empty shelves in May. The June USDA delivery was smaller than May's, and the pantry is down 64% compared with January. Summer school-meal gaps and SNAP/USDA pressure are adding strain.",
        "evidence_text": wsls_excerpt,
        "evidence_text_basis": "page_text_excerpt",
        "source_type": "page",
        "source_family": "local_news",
        "state": "VA",
        "location_name": "Roanoke, VA",
        "location_scope": "local",
        "country": "US",
        "source_purpose": "current_news",
        "primary_source_url": "https://www.wsls.com/news/local/2026/06/10/why-roanokes-st-francis-house-is-facing-its-tightest-food-shortage-ever-this-summer/",
        "source_traceability_role": "article_url",
        "issue_tags": ["food shortage", "pantry capacity", "SNAP", "school meals"],
        "map_category": "acute strain / service disruption",
        "positive_keywords": ["food shortage", "empty shelves", "USDA", "SNAP", "school meals", "pantry"],
        "negative_keywords": ["recipe", "restaurant review", "menu", "cooking tips", "chef", "grocery sale"],
        "affected_group_keywords": ["pantry clients", "SNAP households", "families", "children"],
    }
    background = _row(2, family="economic_data", state="US", title="USDA ERS food security context", summary="USDA ERS context note.", source_type="page", publisher="USDA ERS")
    background["map_category"] = "context / monitoring only"
    background["issue_tags"] = ["household food insecurity", "economic pressure"]
    background["location_name"] = "United States"
    second_background = _row(3, family="school_meals_child_nutrition", state="US", title="USDA summer meals context", summary="USDA summer meal programs and guidance.", source_type="page", publisher="USDA FNS")
    second_background["map_category"] = "context / monitoring only"
    second_background["issue_tags"] = ["summer meals", "child hunger", "school meals"]
    second_background["location_name"] = "United States"
    p.write_text(json.dumps([wsls, background, second_background], indent=2), encoding="utf-8")

    result = run_food_line_dispatch(tmp_path, date)

    sources_manifest = json.loads((tmp_path / "output" / "site" / "food-line" / "editions" / date / "sources_manifest.json").read_text(encoding="utf-8"))
    wsls_manifest = next(row for row in sources_manifest if row["source_record_id"] == wsls_id)
    review_rows = list(csv.DictReader((tmp_path / "output" / "review" / "food-line" / date / "pressure_review.csv").open(encoding="utf-8")))
    wsls_review = next(row for row in review_rows if row["source_record_id"] == wsls_id)
    edition_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "index.html").read_text(encoding="utf-8")
    source_table_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "source_table.html").read_text(encoding="utf-8")
    map_data = json.loads((tmp_path / "output" / "site" / "food-line" / "map" / "map_data.json").read_text(encoding="utf-8"))
    edition_manifest = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "editions" / date / "run_manifest.json").read_text(encoding="utf-8"))

    assert result["public_rendered"] is True
    assert result["future_date_blocked"] is False
    assert result["lead_source_record_id"] == wsls_id
    assert result["selected_lead_source_role"] == "local_signal"
    assert result["pressure_signal_count"] >= 1
    assert result["pressure_marker_count"] >= 1
    assert edition_manifest["public_rendered"] is True
    assert edition_manifest["future_date_blocked"] is False
    assert wsls_review["source_url"] == wsls["url"]
    assert wsls_review["primary_source_url"] == wsls["url"]
    assert wsls_review["source_traceability_role"] == "article_url"
    assert wsls_review["pressure_verification_status"] == "source_text_verified"
    assert wsls_manifest["pressure_signal"] is True
    assert map_data["pressure_markers"]
    assert any(marker["source_record_id"] == wsls_id for marker in map_data["pressure_markers"])
    assert "Why Roanoke" in source_table_html
    assert "faced completely empty shelves in May" in source_table_html
    assert "they received even less food" in source_table_html
    assert "WSLS" in source_table_html
    assert "No current update" not in (tmp_path / "output" / "site" / "food-line" / "editions" / date / "index.html").read_text(encoding="utf-8")
    assert "data_anchor_signal" not in edition_html
    assert "research_signal" not in edition_html
    assert "data_anchor_signal" not in source_table_html
    assert "research_signal" not in source_table_html


def test_food_line_june_11_wsls_only_shows_audit_reason_and_summary_counts_match(tmp_path: Path):
    _ensure_assets(tmp_path)
    seed_date = "2026-06-10"
    seed_path = _manual_path(tmp_path, seed_date)
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    seed_wsls = {
        "source_record_id": "wsls-roanoke-st-francis-house-food-shortage-20260610",
        "title": "Why Roanoke's St. Francis House is facing its tightest food shortage ever this summer",
        "url": "https://www.wsls.com/news/local/2026/06/10/why-roanokes-st-francis-house-is-facing-its-tightest-food-shortage-ever-this-summer/",
        "publisher": "WSLS",
        "published_at": "2026-06-10T06:24:00",
        "page_metadata_date": "2026-06-10T09:57:00",
        "retrieved_at": "2026-06-11T00:00:00Z",
        "summary_or_snippet": "St. Francis House had empty shelves in May. The June USDA delivery was smaller than May's, and the pantry is down 64% compared with January. Summer school-meal gaps and SNAP/USDA pressure are adding strain.",
        "evidence_text": (
            "ROANOKE, Va. - Roanoke City's St. Francis House Food Pantry faced completely empty shelves in May. "
            "Now in June, the pantry is facing an even tighter situation heading into summer, and the people who run it say the situation is only getting harder. "
            "St. Francis House received a new USDA food shipment for June, but the entire delivery is expected to last through the end of the month, and they received even less food than they had in May. "
            "In May, the pantry ran out of food in just two weeks. The June delivery was even smaller than May's. "
            "Enge said the shortfall is significant and is causing them to hand out less food. "
            "Summer is one of the busiest seasons for food pantries, as children who typically receive free or reduced-price lunches during the school year lose access to those daily meals. "
            "At the same time, cuts to SNAP and other USDA programs are leaving more families with fewer options."
        ),
        "evidence_text_basis": "page_text_excerpt",
        "source_type": "page",
        "source_family": "local_news",
        "state": "VA",
        "location_name": "Roanoke, VA",
        "location_scope": "local",
        "country": "US",
        "source_purpose": "current_news",
        "primary_source_url": "https://www.wsls.com/news/local/2026/06/10/why-roanokes-st-francis-house-is-facing-its-tightest-food-shortage-ever-this-summer/",
        "source_traceability_role": "article_url",
        "issue_tags": ["food shortage", "pantry capacity", "SNAP", "school meals"],
        "map_category": "acute strain / service disruption",
        "positive_keywords": ["food shortage", "empty shelves", "USDA", "SNAP", "school meals", "pantry"],
        "negative_keywords": ["recipe", "restaurant review", "menu", "cooking tips", "chef", "grocery sale"],
        "affected_group_keywords": ["pantry clients", "SNAP households", "families", "children"],
    }
    seed_path.write_text(json.dumps([seed_wsls], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, seed_date)

    date = "2026-06-11"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    stale_row = _row(2, family="local_news", state="VA", title="Older local news update", summary="A local community update from earlier in the month.")
    stale_row["published_at"] = "2026-05-20T12:00:00"
    stale_row["page_metadata_date"] = "2026-05-20T12:00:00"
    wsls = {
        "source_record_id": "wsls-roanoke-st-francis-house-food-shortage-20260610",
        "title": "Why Roanoke's St. Francis House is facing its tightest food shortage ever this summer",
        "url": "https://www.wsls.com/news/local/2026/06/10/why-roanokes-st-francis-house-is-facing-its-tightest-food-shortage-ever-this-summer/",
        "publisher": "WSLS",
        "published_at": "2026-06-10T06:24:00",
        "page_metadata_date": "2026-06-10T09:57:00",
        "retrieved_at": "2026-06-11T00:00:00Z",
        "summary_or_snippet": "St. Francis House had empty shelves in May. The June USDA delivery was smaller than May's, and the pantry is down 64% compared with January. Summer school-meal gaps and SNAP/USDA pressure are adding strain.",
        "evidence_text": (
            "ROANOKE, Va. - Roanoke City's St. Francis House Food Pantry faced completely empty shelves in May. "
            "Now in June, the pantry is facing an even tighter situation heading into summer, and the people who run it say the situation is only getting harder. "
            "St. Francis House received a new USDA food shipment for June, but the entire delivery is expected to last through the end of the month, and they received even less food than they had in May. "
            "In May, the pantry ran out of food in just two weeks. The June delivery was even smaller than May's. "
            "Enge said the shortfall is significant and is causing them to hand out less food. "
            "Summer is one of the busiest seasons for food pantries, as children who typically receive free or reduced-price lunches during the school year lose access to those daily meals. "
            "At the same time, cuts to SNAP and other USDA programs are leaving more families with fewer options."
        ),
        "evidence_text_basis": "page_text_excerpt",
        "source_type": "page",
        "source_family": "local_news",
        "state": "VA",
        "location_name": "Roanoke, VA",
        "location_scope": "local",
        "country": "US",
        "source_purpose": "current_news",
        "primary_source_url": "https://www.wsls.com/news/local/2026/06/10/why-roanokes-st-francis-house-is-facing-its-tightest-food-shortage-ever-this-summer/",
        "source_traceability_role": "article_url",
        "issue_tags": ["food shortage", "pantry capacity", "SNAP", "school meals"],
        "map_category": "acute strain / service disruption",
        "positive_keywords": ["food shortage", "empty shelves", "USDA", "SNAP", "school meals", "pantry"],
        "negative_keywords": ["recipe", "restaurant review", "menu", "cooking tips", "chef", "grocery sale"],
        "affected_group_keywords": ["pantry clients", "SNAP households", "families", "children"],
    }
    p.write_text(json.dumps([wsls, stale_row], indent=2), encoding="utf-8")

    result = run_food_line_dispatch(tmp_path, date)
    manifest = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "editions" / date / "run_manifest.json").read_text(encoding="utf-8"))
    review_path = tmp_path / "output" / "review" / "food-line" / date / "pressure_review.csv"
    review_rows = list(csv.DictReader(review_path.open(encoding="utf-8")))

    assert result["public_rendered"] is False
    assert result["edition_mode"] == "internal_no_qualifying_update"
    assert result["lead_source_record_id"] is None
    assert manifest["food_line_no_current_update_policy_status"] == "blocked"
    assert any("source collection did not run successfully" in reason for reason in manifest["food_line_no_current_update_policy_reasons"])
    assert review_rows
    assert any(row["source_record_id"] == wsls["source_record_id"] for row in review_rows)
    assert (tmp_path / "output" / "site" / "food-line" / "editions" / date / "index.html").exists() is False


def test_food_line_june_11_with_kold_becomes_current_update_and_map_eligible(tmp_path: Path):
    _ensure_assets(tmp_path)
    seed_date = "2026-06-10"
    seed_path = _manual_path(tmp_path, seed_date)
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    seed_wsls = {
        "source_record_id": "wsls-roanoke-st-francis-house-food-shortage-20260610",
        "title": "Why Roanoke's St. Francis House is facing its tightest food shortage ever this summer",
        "url": "https://www.wsls.com/news/local/2026/06/10/why-roanokes-st-francis-house-is-facing-its-tightest-food-shortage-ever-this-summer/",
        "publisher": "WSLS",
        "published_at": "2026-06-10T06:24:00",
        "page_metadata_date": "2026-06-10T09:57:00",
        "retrieved_at": "2026-06-11T00:00:00Z",
        "summary_or_snippet": "St. Francis House had empty shelves in May. The June USDA delivery was smaller than May's, and the pantry is down 64% compared with January. Summer school-meal gaps and SNAP/USDA pressure are adding strain.",
        "evidence_text": (
            "ROANOKE, Va. - Roanoke City's St. Francis House Food Pantry faced completely empty shelves in May. "
            "Now in June, the pantry is facing an even tighter situation heading into summer, and the people who run it say the situation is only getting harder. "
            "St. Francis House received a new USDA food shipment for June, but the entire delivery is expected to last through the end of the month, and they received even less food than they had in May. "
            "In May, the pantry ran out of food in just two weeks. The June delivery was even smaller than May's. "
            "Enge said the shortfall is significant and is causing them to hand out less food. "
            "Summer is one of the busiest seasons for food pantries, as children who typically receive free or reduced-price lunches during the school year lose access to those daily meals. "
            "At the same time, cuts to SNAP and other USDA programs are leaving more families with fewer options."
        ),
        "evidence_text_basis": "page_text_excerpt",
        "source_type": "page",
        "source_family": "local_news",
        "state": "VA",
        "location_name": "Roanoke, VA",
        "location_scope": "local",
        "country": "US",
        "source_purpose": "current_news",
        "primary_source_url": "https://www.wsls.com/news/local/2026/06/10/why-roanokes-st-francis-house-is-facing-its-tightest-food-shortage-ever-this-summer/",
        "source_traceability_role": "article_url",
        "issue_tags": ["food shortage", "pantry capacity", "SNAP", "school meals"],
        "map_category": "acute strain / service disruption",
        "positive_keywords": ["food shortage", "empty shelves", "USDA", "SNAP", "school meals", "pantry"],
        "negative_keywords": ["recipe", "restaurant review", "menu", "cooking tips", "chef", "grocery sale"],
        "affected_group_keywords": ["pantry clients", "SNAP households", "families", "children"],
    }
    seed_path.write_text(json.dumps([seed_wsls], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, seed_date)

    date = "2026-06-11"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    wsls = {
        "source_record_id": "wsls-roanoke-st-francis-house-food-shortage-20260610",
        "title": "Why Roanoke's St. Francis House is facing its tightest food shortage ever this summer",
        "url": "https://www.wsls.com/news/local/2026/06/10/why-roanokes-st-francis-house-is-facing-its-tightest-food-shortage-ever-this-summer/",
        "publisher": "WSLS",
        "published_at": "2026-06-10T06:24:00",
        "page_metadata_date": "2026-06-10T09:57:00",
        "retrieved_at": "2026-06-11T00:00:00Z",
        "summary_or_snippet": "St. Francis House had empty shelves in May. The June USDA delivery was smaller than May's, and the pantry is down 64% compared with January. Summer school-meal gaps and SNAP/USDA pressure are adding strain.",
        "evidence_text": (
            "ROANOKE, Va. - Roanoke City's St. Francis House Food Pantry faced completely empty shelves in May. "
            "Now in June, the pantry is facing an even tighter situation heading into summer, and the people who run it say the situation is only getting harder. "
            "St. Francis House received a new USDA food shipment for June, but the entire delivery is expected to last through the end of the month, and they received even less food than they had in May. "
            "In May, the pantry ran out of food in just two weeks. The June delivery was even smaller than May's. "
            "Enge said the shortfall is significant and is causing them to hand out less food. "
            "Summer is one of the busiest seasons for food pantries, as children who typically receive free or reduced-price lunches during the school year lose access to those daily meals. "
            "At the same time, cuts to SNAP and other USDA programs are leaving more families with fewer options."
        ),
        "evidence_text_basis": "page_text_excerpt",
        "source_type": "page",
        "source_family": "local_news",
        "state": "VA",
        "location_name": "Roanoke, VA",
        "location_scope": "local",
        "country": "US",
        "source_purpose": "current_news",
        "primary_source_url": "https://www.wsls.com/news/local/2026/06/10/why-roanokes-st-francis-house-is-facing-its-tightest-food-shortage-ever-this-summer/",
        "source_traceability_role": "article_url",
        "issue_tags": ["food shortage", "pantry capacity", "SNAP", "school meals"],
        "map_category": "acute strain / service disruption",
        "positive_keywords": ["food shortage", "empty shelves", "USDA", "SNAP", "school meals", "pantry"],
        "negative_keywords": ["recipe", "restaurant review", "menu", "cooking tips", "chef", "grocery sale"],
        "affected_group_keywords": ["pantry clients", "SNAP households", "families", "children"],
    }
    kold = {
        "source_record_id": "kold-tucson-food-bank-sees-surge-visitors-inflation-rises-20260610",
        "title": "Tucson food bank sees surge in visitors as inflation rises",
        "url": "https://www.kold.com/2026/06/11/tucson-food-bank-sees-surge-visitors-inflation-rises/",
        "publisher": "KOLD / 13 News",
        "published_at": "2026-06-10T18:53:00-07:00",
        "page_metadata_date": "2026-06-10T18:53:00-07:00",
        "retrieved_at": "2026-06-11T00:00:00Z",
        "summary_or_snippet": "Catholic Community Services' Tucson food bank saw rising demand from first-time visitors. Supplies were running out more regularly, and some visitors could not get food because lines were too long or supplies were nearly gone.",
        "evidence_text": (
            "Catholic Community Services' food bank has seen rising demand from first-time visitors and to their clothing donation center. "
            "Many of the people who are now coming in haven't consistently used community food resources in the past. "
            "Vanessa Rodriquez said she's begun utilizing food banks for the very first time in her life as her grocery bills have gotten too high. "
            "Tim Kromer with Catholic Community Services said he's seen that need increase on a daily basis at the food bank. "
            "With rising demand, supplies are dwindling quicker than usual. "
            "Rodriquez said her most recent trip was unsuccessful because the line was so long and the bank was already giving out the last of what it had. "
            "Because it's summer, families with children are seeing a large increase in need."
        ),
        "evidence_text_basis": "page_text_excerpt",
        "source_type": "page",
        "source_family": "local_news",
        "state": "AZ",
        "location_name": "Tucson, AZ",
        "location_scope": "local",
        "country": "US",
        "source_purpose": "current_news",
        "primary_source_url": "https://www.kold.com/2026/06/11/tucson-food-bank-sees-surge-visitors-inflation-rises/",
        "source_traceability_role": "article_url",
        "issue_tags": ["food bank demand", "pantry capacity", "SNAP", "food assistance"],
        "map_category": "demand strain",
        "positive_keywords": ["food bank", "food assistance", "first-time", "supplies are dwindling", "running out", "SNAP", "inflation", "families with children"],
        "negative_keywords": ["recipe", "restaurant review", "menu", "cooking tips", "chef", "grocery sale"],
        "affected_group_keywords": ["first-time food bank users", "families with children", "SNAP households", "low-income households"],
    }
    p.write_text(json.dumps([kold, wsls], indent=2), encoding="utf-8")

    result = run_food_line_dispatch(tmp_path, date)
    edition_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "index.html").read_text(encoding="utf-8")
    source_table_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "source_table.html").read_text(encoding="utf-8")
    map_data = json.loads((tmp_path / "output" / "site" / "food-line" / "map" / "map_data.json").read_text(encoding="utf-8"))
    sources_manifest = json.loads((tmp_path / "output" / "site" / "food-line" / "editions" / date / "sources_manifest.json").read_text(encoding="utf-8"))
    by_id = {row["source_record_id"]: row for row in sources_manifest}
    wsls_summary = food_line._food_line_public_summary_sentence(wsls)
    kold_summary = food_line._food_line_public_summary_sentence(kold)

    assert result["public_rendered"] is True
    assert result["edition_mode"] == "current_update"
    assert result["lead_source_record_id"] == kold["source_record_id"]
    assert result["selected_lead_source_role"] == "local_signal"
    assert "Today’s Food Line found 2 reported pressure signals." in edition_html
    assert "St. Francis House" in edition_html
    assert "Catholic Community Services" in edition_html
    assert "reported rising food-assistance demand" not in edition_html
    assert "No current update" not in edition_html
    assert "Source audit: reused prior lead" not in source_table_html
    assert kold["url"] in source_table_html
    assert by_id[kold["source_record_id"]]["map_eligible"] is True
    assert by_id[kold["source_record_id"]]["source_freshness_status"] == "fresh_daily_signal"
    assert by_id[kold["source_record_id"]]["source_public_story_eligible"] is True
    assert by_id[wsls["source_record_id"]]["source_freshness_status"] == "fresh_daily_signal"
    assert map_data["diagnostics"]["pressure_marker_count"] >= 2
    assert any(marker["source_record_id"] == kold["source_record_id"] for marker in map_data["pressure_markers"])
    assert "pressure_signal" not in edition_html
    assert "local_signal" not in edition_html
    assert "Source audit" not in edition_html
    home_html = (tmp_path / "output" / "site" / "food-line" / "index.html").read_text(encoding="utf-8")
    archive_html = (tmp_path / "output" / "site" / "food-line" / "archive.html").read_text(encoding="utf-8")
    assert '<h2>Current coverage</h2>' in home_html
    assert 'editions/2026-06-11/' in home_html
    assert 'Browse the Food Line archive' in home_html
    assert 'No current update' not in home_html
    assert '2026-06-11' in home_html
    assert '2026-06-11 — Tucson food-bank strain and Roanoke St. Francis House shortage' in archive_html


def test_food_line_archive_titles_and_home_link_are_source_specific(tmp_path: Path):
    _ensure_assets(tmp_path)
    fixtures = [
        ("2026-06-10", [_ktal_manual_source()]),
        ("2026-06-11", list(_food_line_june_11_rows())),
        ("2026-06-12", [_wpde_manual_source(), _tulsa_manual_source(), _wkrn_policy_access_source()]),
    ]
    for date, rows in fixtures:
        path = _manual_path(tmp_path, date)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        run_food_line_dispatch(tmp_path, date, generate_audio=False)

    home_html = (tmp_path / "output" / "site" / "food-line" / "index.html").read_text(encoding="utf-8")
    archive_html = (tmp_path / "output" / "site" / "food-line" / "archive.html").read_text(encoding="utf-8")

    assert "Browse the Food Line archive" in home_html
    assert "<h2>Recent Editions</h2>" in home_html
    assert "Open the full archive" in home_html
    assert "2026-06-12 — Horry County pantry demand, Tulsa fuel costs, and Tennessee SNAP enrollment" in home_html
    assert "2026-06-10 — Northwest Louisiana food-bank inventory" in archive_html
    assert "2026-06-11 — Roanoke St. Francis House shortage and Tucson food-bank strain" in archive_html
    assert "2026-06-12 — Horry County pantry demand, Tulsa fuel costs, and Tennessee SNAP enrollment" in archive_html
    assert "Pantry demand and summer food-bank strain" not in home_html
    assert "Pantry demand and summer food-bank strain" not in archive_html


def test_food_line_review_only_backfill_archive_label_stays_descriptive(tmp_path: Path):
    _ensure_assets(tmp_path)
    edition_dir = tmp_path / "output" / "site" / "food-line" / "editions" / "2026-06-12"
    edition_dir.mkdir(parents=True, exist_ok=True)
    (edition_dir / "index.html").write_text("<html><body>Review-only backfill</body></html>", encoding="utf-8")
    (edition_dir / "review_render_manifest.json").write_text(
        json.dumps(
            {
                "ok": True,
                "render_mode": "review_only",
                "edition_date": "2026-06-12",
                "rendered_public_claim_count": 1,
                "lead_title": "USDA Proposal to End Broad-Based Categorical Eligibility for SNAP Would Increase Hunger for Families and Children - Food Research & Action Center",
                "lead_source_role": "policy_analysis",
                "lead_pressure_type": "SNAP policy pressure",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    label = food_line._food_line_public_edition_label(tmp_path, "2026-06-12")

    assert label == "2026-06-12 — FRAC warns SNAP eligibility proposal could increase hunger"
    assert label != "2026-06-12 — Food Line Dispatch - 2026-06-12"


def test_food_line_home_recent_editions_are_relative_and_date_descending(tmp_path: Path):
    _ensure_assets(tmp_path)
    fixtures = [
        ("2026-06-10", [_ktal_manual_source()]),
        ("2026-06-11", list(_food_line_june_11_rows())),
    ]
    for date, rows in fixtures:
        path = _manual_path(tmp_path, date)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        run_food_line_dispatch(tmp_path, date, generate_audio=False)

    review_only_dir = tmp_path / "output" / "site" / "food-line" / "editions" / "2026-06-12"
    review_only_dir.mkdir(parents=True, exist_ok=True)
    (review_only_dir / "index.html").write_text("<html><body>Review-only backfill</body></html>", encoding="utf-8")
    (review_only_dir / "review_render_manifest.json").write_text(
        json.dumps(
            {
                "ok": True,
                "render_mode": "review_only",
                "edition_date": "2026-06-12",
                "rendered_public_claim_count": 1,
                "lead_title": "USDA Proposal to End Broad-Based Categorical Eligibility for SNAP Would Increase Hunger for Families and Children - Food Research & Action Center",
                "lead_source_role": "policy_analysis",
                "lead_pressure_type": "SNAP policy pressure",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    food_line._update_index_archive(
        tmp_path,
        "2026-06-12",
        "The Food Line Dispatch tracks daily signs of food insecurity across the United States - benefit disruptions, pantry strain, school-meal gaps, price pressure, and local access failures - using source-backed public records and reporting.",
        max_edition_date="2026-06-12",
    )

    home_html = (tmp_path / "output" / "site" / "food-line" / "index.html").read_text(encoding="utf-8")
    recent_html = home_html.split("<h2>Recent Editions</h2>", 1)[1].split("</section>", 1)[0]

    assert '<a href="editions/2026-06-12/">' in recent_html
    assert '<a href="editions/2026-06-11/">' in recent_html
    assert '<a href="editions/2026-06-10/">' in recent_html
    assert 'href="/food-line/editions/2026-06-12/"' not in recent_html
    assert "2026-06-12 — FRAC warns SNAP eligibility proposal could increase hunger" in recent_html
    assert recent_html.index("2026-06-12") < recent_html.index("2026-06-11")
    assert recent_html.index("2026-06-11") < recent_html.index("2026-06-10")


def test_food_line_june_11_audio_transcript_reuses_public_summary_without_regenerating_mp3(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    monkeypatch.setattr(food_line, "_food_line_local_today", lambda: dt_date(2026, 6, 12))
    seed_date = "2026-06-10"
    seed_path = _manual_path(tmp_path, seed_date)
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    _kold, seed_wsls = _food_line_june_11_rows()
    seed_path.write_text(json.dumps([seed_wsls], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, seed_date)
    date = "2026-06-11"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    kold, wsls = _food_line_june_11_rows()
    payload = [kold, wsls]
    auto_payload = [dict(row) for row in payload]
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _manual_path(tmp_path, date).with_name("auto_sources.json").write_text(json.dumps(auto_payload, indent=2), encoding="utf-8")
    existing_audio = _seed_existing_food_line_audio(tmp_path, date, b"existing-food-line-mp3")
    existing_audio_bytes = existing_audio.read_bytes()
    existing_audio_v2 = _seed_existing_food_line_audio(tmp_path, f"{date}-v2", b"existing-food-line-mp3-v2")
    existing_audio_v2_bytes = existing_audio_v2.read_bytes()
    wsls_summary = food_line._food_line_public_summary_sentence(wsls)
    kold_summary = food_line._food_line_public_summary_sentence(kold)

    result = run_food_line_dispatch(tmp_path, date, generate_audio=False)

    audio_json = json.loads((tmp_path / "output" / "site" / "food-line" / "audio" / f"{date}.json").read_text(encoding="utf-8"))
    published_audio_root = tmp_path / "output" / "site" / "food-line" / "audio"
    transcript = (published_audio_root / f"{date}-transcript.html").read_text(encoding="utf-8")
    audio_index = (published_audio_root / "index.html").read_text(encoding="utf-8")
    edition_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "index.html").read_text(encoding="utf-8")
    source_table_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "source_table.html").read_text(encoding="utf-8")
    claim_ledger_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "claim_ledger.html").read_text(encoding="utf-8")
    edition_manifest = json.loads((tmp_path / "output" / "site" / "food-line" / "editions" / date / "edition_manifest.json").read_text(encoding="utf-8"))

    assert result["audio_generated"] is False
    assert result["audio_reused_existing"] is True
    assert result["audio_status"] == "audio_file_reused_existing"
    assert result["lead_source_record_id"] == kold["source_record_id"]
    assert result["audio_story_sections"] == ["opening", "today_read", "main_story", "what_else", "sources_behind", "closing"]
    assert result["bluesky_post_text"]
    assert "reported that why" not in result["bluesky_post_text"]
    assert "reported that how" not in result["bluesky_post_text"]
    assert "Why Roanoke's St. Francis House" not in result["bluesky_post_text"]
    assert "Source-backed public briefing" in result["bluesky_post_text"]
    assert "Catholic Community Services" in result["bluesky_post_text"]
    assert "first-time" in result["bluesky_post_text"].lower()
    assert "supplies" in result["bluesky_post_text"].lower()
    assert "running out" in result["bluesky_post_text"].lower()
    assert "Tucson" in result["bluesky_post_text"]
    assert "pressure_signal" not in result["bluesky_post_text"]
    assert "local_signal" not in result["bluesky_post_text"]
    assert len(result["bluesky_post_text"]) <= 300
    assert audio_json["audio_file"] == "2026-06-11-v2.mp3"
    assert audio_json["audio_mp3_url"] == "/food-line/audio/2026-06-11-v2.mp3"
    assert existing_audio.read_bytes() == existing_audio_bytes
    assert existing_audio_v2.read_bytes() == existing_audio_v2_bytes
    assert result["audio_mp3_url"] == "/food-line/audio/2026-06-11-v2.mp3"
    assert "Catholic Community Services" in transcript
    assert "St. Francis House" in transcript
    assert "empty shelves" in transcript.lower()
    assert "supplies were running out" in transcript.lower() or "running out more regularly" in transcript.lower()
    assert "When benefits are delayed or paused" not in transcript
    assert "â€”" not in transcript
    assert "reported rising food-assistance demand" not in transcript
    assert "pressure_signal" not in transcript
    assert "local_signal" not in transcript
    assert "map_signal" not in transcript
    assert "claim_ledger.html" in edition_html
    assert "Limits:" in edition_html
    assert "Open the claim ledger" in edition_html
    assert "Sources behind this briefing" in source_table_html
    assert "Open the claim ledger" in source_table_html
    assert "Food Line Claim Ledger" in claim_ledger_html
    assert "Catholic Community Services" in claim_ledger_html
    assert "first-time visitors" in claim_ledger_html
    assert "supplies were running out more regularly" in claim_ledger_html.lower()
    assert "Tucson, AZ" in claim_ledger_html
    assert "In Roanoke, VA" in claim_ledger_html
    assert "St. Francis House had empty shelves in May" in claim_ledger_html
    assert "KOLD / 13 News" in claim_ledger_html
    assert "WSLS" in claim_ledger_html
    assert "Evidence level" in claim_ledger_html
    assert "Confidence" in claim_ledger_html
    assert "Limitation" in claim_ledger_html
    assert "Source URL" in claim_ledger_html
    assert "food-line-auto-" not in claim_ledger_html
    assert "pressure_signal" not in claim_ledger_html
    assert "Confidence</th>" in claim_ledger_html
    assert ">moderate<" in claim_ledger_html
    from html import unescape

    claim_cells = re.findall(r"<td>(.*?)</td>", claim_ledger_html)
    claim_texts = [unescape(cell) for cell in claim_cells[::12]]
    roanoke_claim = next(text for text in claim_texts if "St. Francis House" in text)
    tucson_claim = next(text for text in claim_texts if "Catholic Community Services" in text)
    assert roanoke_claim.endswith(".")
    assert tucson_claim.endswith(".")
    assert "adding strain in Roanoke, VA" not in roanoke_claim
    assert "..." not in roanoke_claim
    assert "..." not in tucson_claim
    assert "64%" in roanoke_claim
    assert "empty shelves" in roanoke_claim.lower()
    assert "smaller than May" in roanoke_claim
    assert "first-time visitors" in tucson_claim
    assert "Tucson" in tucson_claim
    assert edition_manifest["claim_count"] == 2
    assert edition_manifest["claim_ledger_path"] == "/food-line/editions/2026-06-11/claim_ledger.html"
    assert edition_manifest["source_table_path"] == "/food-line/editions/2026-06-11/source_table.html"
    assert edition_manifest["qualified_source_count"] == 2
    assert edition_manifest["excluded_source_count"] == 0
    assert edition_manifest["correction_status"] == "none"
    assert edition_manifest["validation_status"] == "ok"
    other_match = re.search(r"<h2>Other Food Line Signals</h2>\s*<p>(.*?)</p>", transcript)
    assert other_match is not None
    other_text = other_match.group(1)
    assert "Another report points to related pressure on pantry capacity." not in other_text
    assert "In Roanoke" in other_text
    assert "WSLS reported that" in other_text
    assert "St. Francis House" in other_text
    assert "empty shelves" in other_text.lower()
    assert "smaller" in other_text.lower()
    assert "USDA" in other_text
    assert "64%" in other_text
    assert "are..." not in other_text
    assert "..." not in other_text
    assert other_text.endswith(".")
    assert transcript.count("Opening") == 1
    assert transcript.count("Today&apos;s Read") == 1
    assert transcript.count("Core Food Pressure Signals") == 1
    assert transcript.count("Other Food Line Signals") == 1
    assert transcript.count("Source Note") == 1
    assert transcript.count("Closing") == 1
    assert transcript.index("Opening") < transcript.index("Today&apos;s Read") < transcript.index("Core Food Pressure Signals") < transcript.index("Other Food Line Signals") < transcript.index("Source Note") < transcript.index("Closing")
    assert "Food Line Audio &mdash; June 11, 2026" in audio_index
    assert "/food-line/audio/2026-06-11-v2.mp3" in audio_index
    assert "reported rising food-assistance demand" not in audio_index
    assert "St. Francis House" in audio_index
    assert "Catholic Community Services" in audio_index
    assert "empty shelves" in audio_index.lower()
    assert "running out" in audio_index.lower()
    assert "When benefits are delayed or paused" not in audio_index
    assert "â€”" not in audio_index
    assert "/food-line/audio/2026-06-11-v2.mp3" in audio_index
    assert "/food-line/audio/2026-06-11.mp3" not in audio_index
    audio_teaser_match = re.search(r'<h1>Food Line Audio &mdash; June 11, 2026</h1>\s*<p>(.*?)</p>', audio_index, re.S)
    assert audio_teaser_match is not None
    assert audio_teaser_match.group(1).endswith(".")
    other_index_match = re.search(r"<h2>Other Food Line Signals</h2>\s*<p>(.*?)</p>", audio_index)
    assert other_index_match is not None
    other_index_text = other_index_match.group(1)
    assert "Another report points to related pressure on pantry capacity." not in other_index_text
    assert "In Roanoke" in other_index_text
    assert "WSLS reported that" in other_index_text
    assert "St. Francis House" in other_index_text
    assert "empty shelves" in other_index_text.lower()
    assert "64%" in other_index_text
    assert "are..." not in other_index_text
    assert "..." not in other_index_text
    assert other_index_text.endswith(".")
    assert "St. Francis House" in wsls_summary
    assert "empty shelves" in wsls_summary.lower()
    assert "USDA" in wsls_summary
    assert "Roanoke" in wsls_summary
    assert len(wsls_summary.split()) <= 60
    assert "Catholic Community Services" in kold_summary
    assert "running out" in kold_summary.lower() or "supplies" in kold_summary.lower()
    assert "Tucson" in kold_summary
    assert len(kold_summary.split()) <= 60
    assert "reported rising food-assistance demand" not in wsls_summary.lower()
    assert "reported rising food-assistance demand" not in kold_summary.lower()


def test_food_line_resource_framed_provider_update_qualifies_and_wrapper_dedupes(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-10"
    provider_row = {
        "source_record_id": "foodbank-find-food-summer-feeding-20260610",
        "title": "Find food this summer as inventory tightens",
        "url": "https://example.com/find-food-summer-feeding",
        "publisher": "Example Food Bank",
        "published_at": "2026-06-10T12:00:00Z",
        "retrieved_at": "2026-06-10T12:00:00Z",
        "summary_or_snippet": "Food Bank of Northwest Louisiana is providing summer meals and groceries to children and families. Meeting need has become increasingly difficult. Rising food costs and lower donations have left the pantry with one of its lowest inventory levels in years, and inventory is about 31% lower than at the same time last year.",
        "evidence_text": "Food Bank of Northwest Louisiana is providing summer meals and groceries to children and families. Meeting need has become increasingly difficult. Rising food costs and lower donations have left the pantry with one of its lowest inventory levels in years, and inventory is about 31% lower than at the same time last year.",
        "evidence_text_basis": "manual_review",
        "source_type": "manual",
        "source_family": "food_bank_provider",
        "state": "LA",
        "location_name": "Northwest Louisiana",
        "map_category": "summer meal / child nutrition",
        "source_purpose": "",
        "issue_tags": ["food banks", "pantry capacity", "summer meals"],
    }
    purpose = food_line.classify_food_line_source_purpose(provider_row)
    assert purpose["source_purpose"] == "provider_update"
    pressure = food_line.evaluate_food_line_pressure(
        provider_row,
        edition_date=date,
        pressure_required=True,
        positive_keywords=["food bank", "summer meals", "inventory", "donations"],
        negative_keywords=["recipe"],
    )
    assert pressure["pressure_signal"] is True
    assert pressure["pressure_type"] == "service reduction"
    assert pressure["source_role"] == "provider_signal"
    assert pressure["pressure_summary"].lower().startswith("example food bank reported")
    assert "children" in pressure["pressure_summary"].lower()
    assert "low-income households" in pressure["pressure_summary"].lower()

    manual_dir = tmp_path / "data" / "dispatches" / "food-line" / "sources" / date
    manual_dir.mkdir(parents=True, exist_ok=True)
    manual_dir.joinpath("manual_sources.json").write_text(
        json.dumps(
            [
                {
                    "source_record_id": "ktal-food-bank-summer-feeding-20260610",
                    "title": "Food Bank of Northwest Louisiana says summer feeding need is tightening inventory",
                    "url": "https://www.ktalnews.com/news/food-bank-summer-feeding/",
                    "publisher": "KTAL / KMSS",
                    "published_at": "2026-06-10T12:00:00Z",
                    "retrieved_at": "2026-06-10T12:00:00Z",
                    "summary_or_snippet": "Food Bank of Northwest Louisiana is providing summer meals and groceries to children and families. Meeting need has become increasingly difficult. Rising food costs and lower donations have left the pantry with one of its lowest inventory levels in years, and inventory is about 31% lower than at the same time last year.",
                    "evidence_text": "Food Bank of Northwest Louisiana is providing summer meals and groceries to children and families. Meeting need has become increasingly difficult. Rising food costs and lower donations have left the pantry with one of its lowest inventory levels in years, and inventory is about 31% lower than at the same time last year. The food bank says it has capacity to feed more children but needs partners.",
                    "evidence_text_basis": "manual_review",
                    "source_type": "manual",
                    "provider_id": "manual",
                    "source_family": "local_news",
                    "region_scope": "regional",
                    "location_name": "Northwest Louisiana",
                    "state": "LA",
                        "map_category": "summer meal / child nutrition",
                    "pressure_signal": True,
                    "pressure_type": "service reduction",
                    "pressure_reason": "matched service reduction; record-low inventory, lower donations, and rising food costs",
                    "affected_groups": ["children", "families", "low-income households"],
                    "evidence_level": "news report",
                    "freshness_role": "fresh_daily_signal",
                    "source_role": "local_signal",
                    "map_eligible": True,
                    "location_scope": "regional",
                    "date_basis": "published_at",
                    "source_purpose": "current_news",
                    "primary_source_url": "https://www.ktalnews.com/news/food-bank-summer-feeding/",
                    "source_traceability_role": "article_url",
                    "pressure_summary": "KTAL reported that Food Bank of Northwest Louisiana is running one of its lowest inventories in years after lower donations and rising food costs, even as it provides summer meals and groceries to children and families.",
                    "traceability_note": "Original publisher URL; reviewed because the article is framed as a summer feeding story but includes concrete inventory, donation, and cost strain evidence.",
                },
                {
                    "source_record_id": "msn-food-bank-struggles-low-inventory-20260610",
                    "title": "Food bank struggles to meet rising demand amid low inventory",
                    "url": "https://www.msn.com/en-us/news/us/food-bank-struggles-to-meet-rising-demand-amid-low-inventory/ar-AA25lPEi",
                    "publisher": "MSN",
                    "published_at": "2026-06-10T12:00:00Z",
                    "retrieved_at": "2026-06-10T12:00:00Z",
                    "summary_or_snippet": "MSN wrapper for the KTAL / KMSS article about Food Bank of Northwest Louisiana summer feeding strain and low inventory.",
                    "evidence_text": "MSN wrapper for the KTAL / KMSS article about Food Bank of Northwest Louisiana summer feeding strain and low inventory.",
                    "evidence_text_basis": "manual_review",
                    "source_type": "manual",
                    "provider_id": "manual",
                    "source_family": "local_news",
                    "region_scope": "regional",
                    "location_name": "Northwest Louisiana",
                    "state": "LA",
                        "map_category": "summer meal / child nutrition",
                    "pressure_signal": True,
                    "pressure_type": "service reduction",
                    "pressure_reason": "wrapper duplicate of KTAL / KMSS source",
                    "affected_groups": ["children", "families", "low-income households"],
                    "evidence_level": "news report",
                    "freshness_role": "fresh_daily_signal",
                    "source_role": "local_signal",
                    "map_eligible": True,
                    "location_scope": "regional",
                    "date_basis": "published_at",
                    "source_purpose": "current_news",
                    "primary_source_url": "https://www.ktalnews.com/news/food-bank-summer-feeding/",
                    "source_traceability_role": "syndicated_wrapper",
                    "pressure_summary": "MSN duplicated the KTAL / KMSS story about Food Bank of Northwest Louisiana summer feeding strain and low inventory.",
                    "traceability_note": "MSN wrapper record kept for traceability but should dedupe to the original KTAL / KMSS source URL for public use.",
                },
            ],
            indent=2,
        ),
        encoding="utf-8",
    )

    merged, rejected, diagnostics = food_line._merged_sources(tmp_path, date)
    assert rejected == []
    assert len(merged) == 1
    assert merged[0]["source_record_id"] == "ktal-food-bank-summer-feeding-20260610"
    assert any("duplicate override" in item for item in diagnostics)


def test_food_line_state_centroid_basis_labeled_clearly(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-03"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _row(1, family="state_official", state="OR")
    row["location_name"] = "Oregon"
    row["summary_or_snippet"] = "SNAP benefit delay reported by county office."
    row["issue_tags"] = ["SNAP", "benefits", "service access"]
    p.write_text(json.dumps([row, _row(2, "policy_research", "US"), _row(3, "economic_data", "US")], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)
    map_data = json.loads((tmp_path / "output" / "site" / "food-line" / "map" / "map_data.json").read_text(encoding="utf-8"))
    mapped = map_data.get("mapped_markers") or []
    assert any(marker.get("coordinate_basis") == "state centroid" for marker in mapped)


def test_food_line_output_includes_scope_counts(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-03"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps([_row(1, "state_official", "WA"), _row(2, "policy_research", "US"), _row(3, "economic_data", "US")], indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    assert "local_signal_count" in result
    assert "state_signal_count" in result
    assert "national_context_count" in result


@pytest.mark.parametrize(
    "title,summary,family,expected_pressure_type",
    [
        ("Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", "local_news", "demand strain"),
        ("Pantry cuts hours due to low inventory", "The pantry reduced hours because shelves were bare.", "food_bank_provider", "service reduction"),
        ("SNAP benefits delayed", "Households reported a SNAP delay and application backlog.", "state_official", "benefit disruption"),
        ("Summer meal site closure", "The meal site closed and children are missing meals.", "school_meals_child_nutrition", "child meal gap"),
        ("Meals on Wheels waitlist grows", "The senior meal waitlist grew and providers could not serve seniors.", "senior_meals", "senior meal strain"),
        ("Families face medical bills and food hardship", "Households are skipping meals because medical bills and prescription costs keep rising.", "local_news", "household hardship"),
        ("Grocery closure creates access gap", "A grocery closure left rural residents without nearby food access.", "local_news", "access gap"),
        ("Emergency food distribution after flood", "D-SNAP and emergency food distribution responded to flood disruption.", "disaster_emergency", "disaster disruption"),
    ],
)
def test_food_line_pressure_classification_examples(tmp_path: Path, title: str, summary: str, family: str, expected_pressure_type: str):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, title, summary, family=family)
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    assert result["pressure_signal_count"] == 1
    assert result["pressure_marker_count"] == 1
    manifest = json.loads((tmp_path / "output" / "site" / "food-line" / "editions" / date / "sources_manifest.json").read_text(encoding="utf-8"))
    assert manifest[0]["pressure_signal"] is True
    assert manifest[0]["pressure_type"] == expected_pressure_type
    assert manifest[0]["pressure_summary"]
    assert manifest[0]["pressure_summary"].lower() not in {
        "source-backed food insecurity context signal",
        "food insecurity context signal",
        "source-backed pressure signal",
        "elevated demand signal",
        "context signal",
    }


def test_food_line_broad_context_terms_do_not_create_current_story_lead(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-08"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        _row(1, family="local_news", state="TX", title="Food insecurity survey updated", summary="National food insecurity and low food security remain elevated.", source_type="page", publisher="Texas Tribune"),
        _row(2, family="public_radio", state="MA", title="Food insecurity context report", summary="Community food security and nutritional insecurity remain part of the regional context.", source_type="page", publisher="NEPM"),
        _row(3, family="nonprofit_news", state="ME", title="ALICE food costs context", summary="ALICE and food prices remain a background issue for the region.", source_type="page", publisher="The Maine Monitor"),
    ]
    for row in rows:
        row["published_at"] = "2026-06-08T12:00:00Z"
    p.write_text(json.dumps(rows, indent=2), encoding="utf-8")

    result = run_food_line_dispatch(tmp_path, date)

    assert result["pressure_signal_count"] == 0
    assert result["qualified_primary_count"] == 0
    assert result["lead_source_record_id"] in {"", None}
    assert result["edition_mode"] == "no_public_edition"


def test_food_line_generic_snap_program_pages_stay_demoted_without_specific_pressure_evidence(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-08"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        _row(1, family="state_official", state="PA", title="SNAP eligibility and recertification information", summary="SNAP eligibility page for households and recertification information.", source_type="page", publisher="Pennsylvania DHS"),
        _row(2, family="federal_official", state="US", title="WIC information page", summary="WIC information, eligibility, and program details.", source_type="page", publisher="USDA FNS"),
        _row(3, family="state_policy_news", state="MS", title="Food assistance program overview", summary="Food assistance program overview and eligibility guidance.", source_type="page", publisher="Mississippi DHS"),
    ]
    for row in rows:
        row["published_at"] = "2026-06-08T12:00:00Z"
    p.write_text(json.dumps(rows, indent=2), encoding="utf-8")

    result = run_food_line_dispatch(tmp_path, date)
    review_rows = list(csv.DictReader((tmp_path / "output" / "review" / "food-line" / date / "pressure_review.csv").open(encoding="utf-8")))
    review_by_id = {row["source_record_id"]: row for row in review_rows}

    assert result["pressure_signal_count"] == 0
    assert result["qualified_primary_count"] == 0
    assert result["edition_mode"] == "no_public_edition"
    assert review_by_id["food-line-src-001"]["pressure_signal"] == "false"
    assert review_by_id["food-line-src-002"]["pressure_signal"] == "false"
    assert review_by_id["food-line-src-003"]["pressure_signal"] == "false"


def test_food_line_cli_publish_skips_no_public_edition_without_pages_publish(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    sentinel_result = {
        "ok": True,
        "edition_mode": "no_public_edition",
        "public_rendered": False,
        "skip_reason": "No new primary food-access signal qualified for public Food Line publication.",
        "source_adequacy": {"status": "blocked_insufficient_current_story_sources"},
        "discovery_gap_check": {"run": False, "report_found": False},
        "public_url": None,
    }

    def fake_run_food_line_dispatch(*args, **kwargs):
        return dict(sentinel_result)

    def fail_publish(*args, **kwargs):
        raise AssertionError("publish_food_line_pages should not be called for no_public_edition")

    def fail_push(*args, **kwargs):
        raise AssertionError("push_pages_repo should not be called for no_public_edition")

    monkeypatch.setattr(food_line, "run_food_line_dispatch", fake_run_food_line_dispatch)
    monkeypatch.setattr(food_line, "publish_food_line_pages", fail_publish)
    monkeypatch.setattr(food_line, "push_pages_repo", fail_push)

    rc = food_line.main(["--date", "2026-06-19", "--publish"])
    out = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert out["ok"] is True
    assert out["edition_mode"] == "no_public_edition"
    assert out["publish_status"] == "no_public_edition"
    assert out["publish_skipped_reason"] == "no_public_edition"
    assert out["pages_publish_skipped_reason"] == "no_public_edition"
    assert out["pages_publish_copied"] is False
    assert out["pushed"] is False
    assert out["skip_reason"] == sentinel_result["skip_reason"]
    assert out["source_adequacy"]["status"] == "blocked_insufficient_current_story_sources"


def test_food_line_run_range_iterates_each_date_inclusively(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen_dates: list[str] = []

    def fake_run_food_line_dispatch(root: Path, edition_date: str, **kwargs):
        seen_dates.append(edition_date)
        assert root == tmp_path
        assert kwargs["collect"] is True
        assert kwargs["include_discovery_gap_summary"] is True
        return {"ok": True, "edition_date": edition_date}

    monkeypatch.setattr(food_line, "run_food_line_dispatch", fake_run_food_line_dispatch)

    runs = food_line.run_range(
        tmp_path,
        "2026-06-21",
        "2026-06-25",
        collect=True,
        include_discovery_gap_summary=True,
    )

    assert seen_dates == ["2026-06-21", "2026-06-22", "2026-06-23", "2026-06-24", "2026-06-25"]
    assert [run["edition_date"] for run in runs] == seen_dates


def test_food_line_main_range_mode_returns_successful_aggregate(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def fake_run_range(root: Path, start_date: str, end_date: str, **kwargs):
        assert root == Path.cwd()
        assert start_date == "2026-06-21"
        assert end_date == "2026-06-25"
        assert kwargs["collect"] is True
        return [
            {"ok": True, "edition_date": "2026-06-21", "public_rendered": False, "edition_mode": "no_public_edition"},
            {"ok": True, "edition_date": "2026-06-22", "public_rendered": True, "edition_mode": "no_current_update"},
        ]

    monkeypatch.setattr(food_line, "run_range", fake_run_range)

    rc = food_line.main(
        [
            "--start-date",
            "2026-06-21",
            "--end-date",
            "2026-06-25",
            "--collect",
        ]
    )
    out = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert out["ok"] is True
    assert out["run_count"] == 2
    assert out["failed_dates"] == []
    assert out["start_date"] == "2026-06-21"
    assert out["end_date"] == "2026-06-22"
    assert [run["edition_date"] for run in out["runs"]] == ["2026-06-21", "2026-06-22"]


def test_food_line_main_range_mode_reports_failed_date(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def fake_run_range(root: Path, start_date: str, end_date: str, **kwargs):
        return [
            {"ok": True, "edition_date": "2026-06-21"},
            {"ok": False, "edition_date": "2026-06-22", "errors": ["collector failed"]},
            {"ok": True, "edition_date": "2026-06-23"},
        ]

    monkeypatch.setattr(food_line, "run_range", fake_run_range)

    rc = food_line.main(
        [
            "--start-date",
            "2026-06-21",
            "--end-date",
            "2026-06-23",
            "--collect",
        ]
    )
    out = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert out["ok"] is False
    assert out["failed_dates"] == ["2026-06-22"]
    assert out["errors"] == ["2026-06-22: collector failed"]


def test_food_line_publish_food_line_pages_fails_when_expected_edition_missing(monkeypatch: pytest.MonkeyPatch):
    done = types.SimpleNamespace(
        returncode=1,
        stdout="",
        stderr="expected Food Line Dispatch edition missing: 2026-06-19",
    )
    monkeypatch.setattr(food_line, "_run_cmd", lambda *args, **kwargs: done)

    ok, errors, payload = food_line.publish_food_line_pages(Path.cwd(), "2026-06-19")

    assert ok is False
    assert errors == ["expected Food Line Dispatch edition missing: 2026-06-19"]
    assert payload == {}


def test_food_line_publish_food_line_pages_requests_shared_homepage_refresh(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, list[str]] = {}

    def fake_run_cmd(args, cwd):
        captured["args"] = list(args)
        return types.SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True, "errors": [], "copied": True, "commit_sha": "abc1234", "target_pages_branch": "gh-pages", "committed_branch": "gh-pages"}), stderr="")

    monkeypatch.setattr(food_line, "_run_cmd", fake_run_cmd)

    ok, errors, payload = food_line.publish_food_line_pages(Path.cwd(), "2026-06-19")

    assert ok is True
    assert errors == []
    assert payload["ok"] is True
    assert "--shared-homepage-dispatch" in captured["args"]
    assert "food-line" in captured["args"]


def test_food_line_regression_sources_promote_with_verified_date_and_specific_pressure_evidence(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-08"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(_load_food_line_regression_fixture(), indent=2), encoding="utf-8")

    result = run_food_line_dispatch(tmp_path, date)
    manifest_path = tmp_path / "output" / "site" / "food-line" / "editions" / date / "sources_manifest.json"
    edition_manifest_path = tmp_path / "output" / "site" / "food-line" / "editions" / date / "edition_manifest.json"
    review_path = tmp_path / "output" / "review" / "food-line" / date / "pressure_review.csv"
    manifest_rows = json.loads(manifest_path.read_text(encoding="utf-8"))
    edition_manifest = json.loads(edition_manifest_path.read_text(encoding="utf-8"))
    review_rows = list(csv.DictReader(review_path.open(encoding="utf-8")))
    manifest_by_title = {row["title"]: row for row in manifest_rows}
    review_by_id = {row["source_record_id"]: row for row in review_rows}

    assert result["public_rendered"] is True
    assert result["qualified_primary_count"] == 1
    assert result["lead_source_record_id"] in {"food-line-src-001", "food-line-src-002", "food-line-src-003"}
    assert edition_manifest["edition_mode"] == "current_update"
    for title in (
        "Food banks continue to see increased need as SNAP requirements shift",
        "Giant freezer helps Aroostook food pantries",
        "Rising food insecurity strains South Florida food banks",
    ):
        row = manifest_by_title[title]
        assert row["source_freshness_status"] == "fresh_daily_signal"
        assert row["source_freshness_date_basis"] == "published_at"
        assert row["source_public_story_eligible"] is True
        assert row["pressure_signal"] is True
        assert row["pressure_verification_status"] == "source_text_verified"
        assert row["primary_eligible"] is True
        assert row["primary_disqualification_reason"] == ""
        assert row["map_eligible"] is True
        assert row["source_purpose"] == "current_news"
    assert manifest_by_title["Giant freezer helps Aroostook food pantries"]["non_promotable_reason"] == ""
    assert review_by_id["food-line-src-002"]["pressure_verification_status"] == "source_text_verified"
    assert review_by_id["food-line-src-003"]["pressure_verification_status"] == "source_text_verified"


def test_food_line_regression_fixture_lives_in_test_fixture_path():
    fixture_path = _food_line_regression_fixture_path()
    assert "tests" in fixture_path.parts
    assert "fixtures" in fixture_path.parts
    assert "food_line" in fixture_path.parts
    assert fixture_path.name == "regression_2026-06-08_sources.json"


def test_food_line_production_dated_manual_sources_do_not_include_regression_fixture():
    production_fixture_path = Path("data/dispatches/food-line/sources/2026-06-08/manual_sources.json")
    assert not production_fixture_path.exists()


def test_food_line_production_manual_sources_do_not_contain_known_regression_urls():
    known_regression_urls = {
        "nepm.org/regional-news/2026-06-08/food-banks-continue",
        "themainemonitor.org/giant-freezer-help-aroostook-food-pantries",
        "miamiherald.com/news/local/article315996054.html",
    }
    production_sources_root = Path("data/dispatches/food-line/sources")
    for manual_sources_path in production_sources_root.glob("*/manual_sources.json"):
        payload = json.loads(manual_sources_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            rows = payload.get("sources") or payload.get("manual_sources") or []
        else:
            rows = payload
        urls = {
            str(row.get("url") or row.get("source_url") or row.get("candidate_url") or "")
            for row in rows
            if isinstance(row, dict)
        }
        assert not any(
            any(known_url in url for url in urls)
            for known_url in known_regression_urls
        ), f"found known regression URL in {manual_sources_path}"


def test_food_line_scheduled_run_ignores_test_fixture_path(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-08"
    fixture_dir = tmp_path / "tests" / "fixtures" / "food_line"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    (fixture_dir / "regression_2026-06-08_sources.json").write_text(
        json.dumps(_load_food_line_regression_fixture(), indent=2),
        encoding="utf-8",
    )
    result = run_food_line_dispatch(tmp_path, date)

    assert result["edition_mode"] == "no_public_edition"
    assert result["qualified_primary_count"] == 0
    assert result["lead_source_record_id"] in {"", None}


@pytest.mark.parametrize(
    "url",
    [
        "https://www.nepm.org/regional-news/2026-06-08/food-banks-continue-to-see-increased-need-as-snap-requirements-shift",
        "https://themainemonitor.org/2026/06/08/giant-freezer-help-aroostook-food-pantries/",
        "https://www.miamiherald.com/news/local/2026/06/08/article315996054.html",
    ],
)
def test_food_line_regression_sources_url_date_only_fail_public_story_eligibility(url: str):
    freshness = food_line.validate_food_line_source_freshness(
        "2026-06-08",
        "",
        url,
        "current_public_story",
        freshness_window_days=3,
    )
    assert freshness["public_story_eligible"] is False
    assert freshness["source_freshness_date_basis"] in {"url_path_only", "missing"}


def test_food_line_bluesky_ready_summary_tracks_scope_and_url(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(
        1,
        "National food insecurity rises",
        "National food insecurity and household hardship remain elevated as medical bills and prescription costs keep forcing tradeoffs.",
        family="national_news",
        state="US",
    )
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)

    assert result["selected_lead_pressure_scope_label"] == "National / systemic"
    assert result["selected_lead_pressure_scope_text"] == "national/systemic"
    assert result["bluesky_post_ready"] is True
    assert result["bluesky_post_text"]
    assert len(result["bluesky_post_text"]) <= 300
    assert result["public_url"] == "https://dispatches.thebluefernco.com/food-line/editions/2026-06-04/"
    assert "Food Line Dispatch, June 4, 2026:" in result["bluesky_post_text"]
    assert "reported household food hardship tied to health-care costs in Sacramento" in result["bluesky_post_text"]
    assert "Source-backed public briefing" in result["bluesky_post_text"]
    assert result["public_url"] in result["bluesky_post_text"]


def test_food_line_bluesky_dry_run_records_social_image_without_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    edition_date = "2026-06-11"
    public_url = "https://dispatches.thebluefernco.com/food-line/editions/2026-06-11/"
    post_text = "Food Line Dispatch, June 11, 2026: test post text."
    _write_food_line_bluesky_preview_fixture(
        tmp_path,
        edition_date,
        public_url=public_url,
        public_summary="Test summary for dry-run preview fixture.",
    )

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("network call should not happen during Bluesky dry-run")

    monkeypatch.setattr(bluesky_post.request, "urlopen", fail_if_called)

    result = bluesky_post.maybe_post_food_line_dispatch_to_bluesky(
        edition_date=edition_date,
        public_url=public_url,
        post_text=post_text,
        run_succeeded=True,
        public_rendered=True,
        public_signal_count=2,
        post_requested=True,
        project_root=tmp_path,
        allow_publish=False,
        dry_run=True,
    )

    state_path = tmp_path / "data" / "dispatches" / "food-line" / "editions" / edition_date / "bluesky_post.json"
    assert result["status"] == "skipped"
    assert result["reason"] == "dry_run"
    assert not state_path.exists()


def test_food_line_bluesky_dry_run_state_and_duplicate_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    edition_date = "2026-06-11"
    public_url = "https://dispatches.thebluefernco.com/food-line/"
    post_text = "Food Line Dispatch, June 11, 2026: WSLS reported that Roanoke's St. Francis House faced empty shelves. Source-backed public briefing:"
    state_path = tmp_path / "data" / "dispatches" / "food-line" / "editions" / edition_date / "bluesky_post.json"
    preview_summary = "WSLS reported that Roanoke's St. Francis House faced empty shelves."
    _write_food_line_bluesky_preview_fixture(
        tmp_path,
        edition_date,
        public_url=public_url,
        public_summary=preview_summary,
    )

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("network call should not happen during Bluesky dry-run or duplicate guard")

    monkeypatch.setattr(bluesky_post.request, "urlopen", fail_if_called)

    dry_run_result = bluesky_post.maybe_post_food_line_dispatch_to_bluesky(
        edition_date=edition_date,
        public_url=public_url,
        post_text=post_text,
        run_succeeded=True,
        public_rendered=True,
        public_signal_count=2,
        post_requested=True,
        project_root=tmp_path,
        allow_publish=False,
        dry_run=True,
    )
    assert dry_run_result["reason"] == "dry_run"
    assert not state_path.exists()

    approval_payload = food_line_bluesky_approval.build_pending_approval(tmp_path, edition_date)
    approval_payload.update({"approved": True, "approved_at": "2026-06-11T00:00:00Z", "approved_by": "test"})
    food_line_bluesky_approval.write_approval(tmp_path, approval_payload)

    state_path.write_text(
        json.dumps(
            {
                "dispatch_slug": "food-line",
                "edition_date": edition_date,
                "public_url": public_url,
                "post_text": post_text,
                "card_title": "The Food Line Dispatch - June 11, 2026",
                "card_description": "Food Line Dispatch, June 11, 2026: WSLS reported that Roanoke's St. Francis House faced empty shelves.",
                "image_path": "assets/food-line-dispatch-social.png",
                "image_alt": bluesky_post.FOOD_LINE_SOCIAL_IMAGE_ALT,
                "status": "success",
                "skip_reason": None,
                "dry_run": False,
                "forced_post": False,
                "post_uri": "at://did:plc:example/app.bsky.feed.post/abc",
                "post_cid": "bafyreexample",
                "embed_type": "app.bsky.embed.external",
                "thumb_status": "uploaded",
                "posted_at": "2026-06-11T00:00:00Z",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    duplicate_result = bluesky_post.maybe_post_food_line_dispatch_to_bluesky(
        edition_date=edition_date,
        public_url=public_url,
        post_text=post_text,
        run_succeeded=True,
        public_rendered=True,
        public_signal_count=2,
        post_requested=True,
        project_root=tmp_path,
        allow_publish=True,
        dry_run=False,
        allow_archival_bluesky_post=True,
    )
    assert duplicate_result["reason"] == "skipped_existing_receipt"
    assert duplicate_result["post_uri"] == "at://did:plc:example/app.bsky.feed.post/abc"
    assert duplicate_result["post_cid"] == "bafyreexample"


def test_food_line_13abc_style_pantry_snap_story_publishes_when_fresh_and_clean(tmp_path: Path):
    _ensure_assets(tmp_path)
    _clear_food_line_registries(tmp_path)
    date = "2026-06-05"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload_path = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "food-line" / "sources" / "2026-06-05" / "auto_sources.json"
    payload = _freshen_food_line_payload_for_publication(json.loads(payload_path.read_text(encoding="utf-8")), date)
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    review = list(csv.DictReader((tmp_path / "output" / "review" / "food-line" / date / "pressure_review.csv").open(encoding="utf-8")))
    lead = next(row for row in review if row["source_record_id"] == "food-line-auto-d746124a0786b5f9")
    edition_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "index.html").read_text(encoding="utf-8")

    assert result["public_rendered"] is True
    assert result["skip_reason"] == ""
    assert result["primary_disqualification_reason"] == ""
    assert result["lead_source_record_id"] == "food-line-auto-d746124a0786b5f9"
    assert result["selected_lead_source_role"] == "local_signal"
    assert result["selected_lead_pressure_type"] == "demand strain"
    assert result["selected_lead_pressure_scope_label"] == "Local / operational"
    assert result["bluesky_post_ready"] is True
    assert result["bluesky_post_text"]
    assert len(result["bluesky_post_text"]) <= 300
    assert result["public_url"] == "https://dispatches.thebluefernco.com/food-line/editions/2026-06-05/"
    assert lead["pressure_signal"] == "true"
    assert lead["pressure_type"] == "demand strain"
    assert lead["pressure_verification_status"] == "source_text_verified"
    assert lead["source_published_date"] == "2026-06-05"
    assert lead["freshness_status"] == "fresh_daily_signal"
    assert lead["primary_eligible"] == "true"
    assert lead["primary_disqualification_reason"] == ""
    assert "Today’s Read" in edition_html
    assert "At A Glance" in edition_html
    assert "Core Food Pressure Signals" in edition_html
    assert "Other Food Line Signals" in edition_html
    assert "Source Mix" in edition_html
    assert "Source Note" in edition_html
    assert "Main Food Access Story" not in edition_html
    assert "Sources Behind This Briefing" not in edition_html
    assert 'href="/american-pressure/"' not in edition_html
    assert "Today&apos;s pressure point" not in edition_html
    assert "What changed" not in edition_html
    assert "Where pressure is visible" not in edition_html


def test_food_line_cascade_pbs_style_funding_cut_story_publishes_when_fresh_and_clean(tmp_path: Path):
    _ensure_assets(tmp_path)
    _clear_food_line_registries(tmp_path)
    date = "2026-06-13"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload_path = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "food-line" / "sources" / "2026-06-13" / "auto_sources.json"
    payload = _freshen_food_line_payload_for_publication(
        [
            row
            for row in json.loads(payload_path.read_text(encoding="utf-8"))
            if row["source_record_id"] == "food-line-auto-6effc522ae28d822"
        ],
        date,
    )
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    review = list(csv.DictReader((tmp_path / "output" / "review" / "food-line" / date / "pressure_review.csv").open(encoding="utf-8")))
    lead = next(row for row in review if row["source_record_id"] == "food-line-auto-6effc522ae28d822")
    edition_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "index.html").read_text(encoding="utf-8")

    assert result["public_rendered"] is True
    assert result["skip_reason"] == ""
    assert result["primary_disqualification_reason"] == ""
    assert result["lead_source_record_id"] == "food-line-auto-6effc522ae28d822"
    assert result["selected_lead_source_role"] == "local_signal"
    assert result["selected_lead_pressure_type"] == "demand strain"
    assert result["selected_lead_pressure_scope_label"] == "Local / operational"
    assert result["bluesky_post_ready"] is True
    assert result["bluesky_post_text"]
    assert len(result["bluesky_post_text"]) <= 300
    assert result["public_url"] == "https://dispatches.thebluefernco.com/food-line/editions/2026-06-13/"
    assert lead["pressure_signal"] == "true"
    assert lead["pressure_type"] == "demand strain"
    assert lead["pressure_verification_status"] == "source_text_verified"
    assert lead["source_published_date"] == "2026-06-13"
    assert lead["freshness_status"] == "fresh_daily_signal"
    assert lead["primary_eligible"] == "true"
    assert lead["primary_disqualification_reason"] == ""
    assert "Today’s Read" in edition_html
    assert "At A Glance" in edition_html
    assert "Core Food Pressure Signals" in edition_html
    assert "Other Food Line Signals" in edition_html
    assert "Source Mix" in edition_html
    assert "Source Note" in edition_html
    assert "Main Food Access Story" not in edition_html
    assert "Sources Behind This Briefing" not in edition_html


def test_food_line_candidate_review_artifact_and_manifest_include_classification_diagnostics(tmp_path: Path):
    _ensure_assets(tmp_path)
    _clear_food_line_registries(tmp_path)
    date = "2026-06-13"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload_path = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "food-line" / "sources" / "2026-06-13" / "auto_sources.json"
    payload = _freshen_food_line_payload_for_publication(
        [
            row
            for row in json.loads(payload_path.read_text(encoding="utf-8"))
            if row["source_record_id"] == "food-line-auto-6effc522ae28d822"
        ],
        date,
    )
    payload.append(
        {
            **_ktal_manual_source(),
            "source_record_id": "food-line-watchlist-1",
            "title": "Regional pantry asks for help ahead of summer demand",
            "url": "https://example.org/pantry-update",
            "primary_source_url": "https://example.org/pantry-update",
            "publisher": "Example Pantry",
            "state": "SC",
            "location_name": "Example County, SC",
            "map_category": "elevated demand",
            "summary_or_snippet": "The pantry says demand may increase this summer and asked for donations.",
            "evidence_text": "The pantry says demand may increase this summer and asked for donations.",
            "evidence_text_basis": "page_title_only",
            "pressure_signal": False,
            "pressure_type": "context only",
            "pressure_summary": "",
            "pressure_reason": "insufficient specific pressure evidence",
            "pressure_verification_status": "demoted_context",
            "source_role": "resource_context",
            "source_public_story_eligible": False,
            "primary_eligible": False,
            "primary_disqualification_reason": "resource-only / no pressure signal",
        }
    )
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    result = run_food_line_dispatch(tmp_path, date)
    review_json = json.loads((tmp_path / "output" / "review" / "food-line" / date / "candidate_review.json").read_text(encoding="utf-8"))
    review_html = (tmp_path / "output" / "review" / "food-line" / date / "candidate_review.html").read_text(encoding="utf-8")
    manifest = json.loads((tmp_path / "output" / "site" / "food-line" / "editions" / date / "edition_manifest.json").read_text(encoding="utf-8"))

    assert result["ok"] is True
    assert review_json["candidate_count_total"] == 2
    assert review_json["candidate_count_approved"] == 1
    assert review_json["candidate_count_watchlist"] == 1
    assert review_json["public_claim_blocker_counts"]["weak_pressure_signal"] >= 1
    assert "Food Line candidate review" in review_html
    assert "weak_pressure_signal" in json.dumps(review_json)
    assert manifest["candidate_count_total"] == 2
    assert manifest["candidate_count_watchlist"] == 1
    assert manifest["public_claim_eligible_count"] == 1
    assert manifest["intake_broadened"] is True


def test_food_line_review_only_render_uses_only_candidate_review_records(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-12"
    review_path = _write_review_only_candidate_review(
        tmp_path,
        date,
        [
            _review_only_candidate(
                title="USDA Proposal to End Broad-Based Categorical Eligibility for SNAP Would Increase Hunger for Families and Children - Food Research & Action Center",
                publisher="FRAC News",
                source_url="https://frac.org/blog/usda-proposal-to-end-broad-based-categorical-eligibility-for-snap-would-increase-hunger-for-families-and-children",
                pressure_summary="FRAC warned that a USDA proposal to end broad-based categorical eligibility for SNAP would increase hunger for families and children.",
                pressure_type="SNAP policy pressure",
                affected_groups=["children", "SNAP households", "low-income households"],
                source_role="policy_analysis",
            ),
        ],
    )
    manual_path = _manual_path(tmp_path, date)
    manual_path.parent.mkdir(parents=True, exist_ok=True)
    manual_path.write_text(json.dumps([_wpde_manual_source(), _tulsa_manual_source(), _wkrn_policy_access_source()], indent=2), encoding="utf-8")

    result = food_line.render_food_line_review_only(
        tmp_path,
        date=date,
        candidate_review_path=review_path,
        public_eligible_only=True,
    )

    edition_dir = tmp_path / "output" / "site-review-only" / "food-line" / "editions" / date
    edition_html = (edition_dir / "index.html").read_text(encoding="utf-8")
    source_table_html = (edition_dir / "source_table.html").read_text(encoding="utf-8")
    claim_ledger_html = (edition_dir / "claim_ledger.html").read_text(encoding="utf-8")
    manifest = json.loads((edition_dir / "review_render_manifest.json").read_text(encoding="utf-8"))

    assert result["ok"] is True
    assert result["render_mode"] == "review_only"
    assert result["source_count"] == 1
    assert result["public_eligible_candidate_count"] == 1
    assert "Today’s Food Line found 1 reported pressure signal." in edition_html
    assert "Source mix: 1 signal from 1 publisher." in edition_html
    assert "Nationally, FRAC warned that a USDA proposal to end broad-based categorical eligibility for SNAP would increase hunger for families and children." in edition_html
    assert "In United States" not in edition_html
    assert "FRAC warned that a USDA proposal to end broad-based categorical eligibility for SNAP would increase hunger for families and children." in edition_html
    assert "USDA Proposal to End Broad-Based Categorical Eligibility for SNAP Would Increase Hunger for Families and Children" in source_table_html
    assert "This ledger records 1 public claim supported by source-backed Food Line signals for June 12, 2026." in claim_ledger_html
    assert "Records reviewed: 1. Public claims: 1. Excluded records: 0." in claim_ledger_html
    assert "FRAC News" in claim_ledger_html
    assert "This indicates national policy pressure around SNAP eligibility and food assistance access." in claim_ledger_html
    assert "local food-access strain" not in claim_ledger_html
    assert "in United States" not in claim_ledger_html
    assert "WPDE / ABC 15" not in edition_html
    assert "Tulsa Flyer" not in edition_html
    assert "WKRN" not in edition_html
    assert "WPDE / ABC 15" not in source_table_html
    assert "Tulsa Flyer" not in claim_ledger_html
    assert manifest["lead_source_role"] == "policy_analysis"
    assert manifest["lead_pressure_type"] == "SNAP policy pressure"
    assert manifest["lead_source_published_date"] == "2026-06-12"
    assert manifest["production_output_mutated"] is False
    assert manifest["pages_repo_mutated"] is False
    assert not (tmp_path / "output" / "site" / "food-line").exists()
    assert not (tmp_path / "bluefern-dispatches-pages").exists()


def test_food_line_review_only_render_public_eligible_only_excludes_blocked_records(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-12"
    review_path = _write_review_only_candidate_review(
        tmp_path,
        date,
        [
            _review_only_candidate(
                title="FRAC eligible article",
                publisher="FRAC News",
                source_url="https://frac.org/blog/eligible",
                pressure_summary="FRAC warned that a USDA proposal would increase hunger for families and children.",
                pressure_type="SNAP policy pressure",
                affected_groups=["children", "SNAP households", "low-income households"],
            ),
            _review_only_candidate(
                title="Homepage block",
                publisher="FAO",
                source_url="https://www.fao.org/",
                pressure_summary="",
                pressure_type="",
                public_claim_eligible=False,
                public_claim_blockers=["homepage_or_landing_url", "missing_public_prose_fields"],
                candidate_review_status="rejected",
                source_role="resource_context",
                pressure_signal_hint="food insecurity",
            ),
        ],
    )

    result = food_line.render_food_line_review_only(
        tmp_path,
        date=date,
        candidate_review_path=review_path,
        public_eligible_only=True,
    )

    edition_html = (tmp_path / "output" / "site-review-only" / "food-line" / "editions" / date / "index.html").read_text(encoding="utf-8")
    assert result["source_count"] == 1
    assert "FRAC eligible article" in edition_html
    assert "Homepage block" not in edition_html


def test_food_line_review_only_render_source_url_selector_renders_only_selected_candidate(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-16"
    boston_url = "https://www.bostonherald.com/2026/06/16/greater-boston-food-bank-to-spend-record-breaking-65m-on-food-in-2026"
    review_path = _write_review_only_candidate_review(
        tmp_path,
        date,
        [
            _review_only_candidate(
                title="Greater Boston Food Bank to spend record-breaking $65M on food in 2026 - Boston Herald",
                publisher="Boston Herald",
                source_url=boston_url,
                pressure_summary="Boston Herald reported that Greater Boston Food Bank expects to spend a record $65M on food in 2026 as need grows.",
                pressure_type="food bank demand pressure",
                affected_groups=["Not clearly isolated by source"],
                source_role="policy_context",
            ),
            _review_only_candidate(
                title="Summer meal programs expect increased demand this year",
                publisher="TribLIVE",
                source_url="https://triblive.com/local/valley-news-dispatch/summer-meal-programs-expect-increased-demand-this-year",
                pressure_summary="TribLIVE reported summer meal programs expect increased demand this year.",
                pressure_type="school meal access pressure",
                affected_groups=["children"],
                source_role="provider_signal",
                location_scope="state_local",
            ),
        ],
    )

    result = food_line.render_food_line_review_only(
        tmp_path,
        date=date,
        candidate_review_path=review_path,
        public_eligible_only=True,
        source_url=boston_url,
    )

    edition_dir = tmp_path / "output" / "site-review-only" / "food-line" / "editions" / date
    edition_html = (edition_dir / "index.html").read_text(encoding="utf-8")
    source_table_html = (edition_dir / "source_table.html").read_text(encoding="utf-8")
    claim_ledger_html = (edition_dir / "claim_ledger.html").read_text(encoding="utf-8")
    manifest = json.loads((edition_dir / "review_render_manifest.json").read_text(encoding="utf-8"))

    assert result["source_count"] == 1
    assert result["selected_candidate_count"] == 1
    assert "Today’s Food Line found 1 reported pressure signal." in edition_html
    assert "Source mix: 1 signal from 1 publisher." in edition_html
    assert "Greater Boston Food Bank to spend record-breaking $65M on food in 2026 - Boston Herald" in edition_html
    assert "Boston Herald reported that Greater Boston Food Bank expects to spend a record $65M on food in 2026 as need grows." in edition_html
    assert "Summer meal programs expect increased demand this year" not in edition_html
    assert "This ledger records 1 public claim supported by source-backed Food Line signals for June 16, 2026." in claim_ledger_html
    assert "Records reviewed: 1. Public claims: 1. Excluded records: 0." in claim_ledger_html
    assert "TribLIVE" not in source_table_html
    assert "TribLIVE" not in claim_ledger_html
    assert ">national<" in claim_ledger_html
    assert manifest["selector_type"] == "source_url"
    assert manifest["selector_value"] == boston_url
    assert manifest["selector_match_count"] == 1
    assert manifest["selector_deduplicated"] is False
    assert manifest["selected_source_url"] == boston_url
    assert manifest["public_eligible_candidate_count_before_selector"] == 2
    assert manifest["rendered_public_claim_count"] == 1
    assert manifest["lead_source_url"] == boston_url


def test_food_line_review_only_render_local_selected_candidate_stays_source_grounded(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-18"
    wrdw_url = "https://www.wrdw.com/video/2026/06/18/augusta-dream-center-sees-surge-families-needing-food-summer-begins"
    review_path = _write_review_only_candidate_review(
        tmp_path,
        date,
        [
            _review_only_candidate(
                title="Augusta Dream Center sees surge in families needing food as summer begins",
                publisher="WRDW",
                source_url=wrdw_url,
                source_published_date="2026-06-18",
                pressure_summary="WRDW reported rising food-assistance demand, affecting children, low-income households.",
                pressure_type="demand strain",
                affected_groups=["children", "low-income households"],
                source_role="local_news_report",
                location_scope="national",
            ),
            _review_only_candidate(
                title="Other eligible candidate",
                publisher="News12 | New Jersey",
                source_url="https://newjersey.news12.com/2026/06/18/other",
                source_published_date="2026-06-18",
                pressure_summary="News12 | New Jersey reported household food hardship, affecting children, SNAP households, low-income households.",
                pressure_type="household hardship",
                affected_groups=["children", "SNAP households", "low-income households"],
                source_role="policy_context",
                location_scope="national",
            ),
        ],
    )

    result = food_line.render_food_line_review_only(
        tmp_path,
        date=date,
        candidate_review_path=review_path,
        public_eligible_only=True,
        source_url=wrdw_url,
    )

    edition_dir = tmp_path / "output" / "site-review-only" / "food-line" / "editions" / date
    edition_html = (edition_dir / "index.html").read_text(encoding="utf-8")
    source_table_html = (edition_dir / "source_table.html").read_text(encoding="utf-8")
    claim_ledger_html = (edition_dir / "claim_ledger.html").read_text(encoding="utf-8")

    assert result["selected_candidate_count"] == 1
    assert result["rendered_public_claim_count"] == 1
    assert "Today’s Food Line found 1 reported pressure signal." in edition_html
    assert "WRDW reported rising food-assistance demand, affecting children, low-income households." in edition_html
    assert "In United States, food providers reported rising pantry demand and child food insecurity." not in edition_html
    assert "United States food providers report rising pantry demand" not in edition_html
    assert "child food insecurity" not in edition_html
    assert "Other eligible candidate" not in edition_html
    assert "News12 | New Jersey" not in edition_html
    assert wrdw_url in edition_html
    assert "WRDW" in source_table_html
    assert wrdw_url in source_table_html
    assert "This indicates local food-assistance demand pressure affecting children, low-income households." in claim_ledger_html
    assert "national policy-pressure signal" not in claim_ledger_html
    assert "national policy pressure around SNAP eligibility and food assistance access" not in claim_ledger_html
    assert "In United States" not in claim_ledger_html
    assert "child food insecurity" not in claim_ledger_html
    assert ">source-local<" in claim_ledger_html
    assert ">national<" not in claim_ledger_html


def test_food_line_review_only_render_fails_closed_for_missing_or_zero_public_eligible(tmp_path: Path):
    _ensure_assets(tmp_path)
    missing_path = tmp_path / "output" / "review" / "food-line" / "2026-06-12" / "candidate_review.json"
    with pytest.raises(ValueError, match="candidate review file not found"):
        food_line.render_food_line_review_only(tmp_path, date="2026-06-12", candidate_review_path=missing_path, public_eligible_only=True)

    review_path = _write_review_only_candidate_review(
        tmp_path,
        "2026-06-12",
        [
            _review_only_candidate(
                title="Blocked review row",
                publisher="FRAC News",
                source_url="https://frac.org/action",
                public_claim_eligible=False,
                public_claim_blockers=["rejected_action_link"],
                candidate_review_status="rejected",
                source_role="resource_context",
                pressure_signal_hint="food insecurity",
            )
        ],
    )
    with pytest.raises(ValueError, match="zero public-eligible candidates"):
        food_line.render_food_line_review_only(tmp_path, date="2026-06-12", candidate_review_path=review_path, public_eligible_only=True)


def test_food_line_review_only_render_source_url_selector_fails_closed_for_zero_or_ineligible_match(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-16"
    blocked_url = "https://www.bostonherald.com/2026/06/16/blocked-candidate"
    review_path = _write_review_only_candidate_review(
        tmp_path,
        date,
        [
            _review_only_candidate(
                title="Blocked candidate",
                publisher="Boston Herald",
                source_url=blocked_url,
                public_claim_eligible=False,
                public_claim_blockers=["missing_public_prose_fields"],
                candidate_review_status="needs_review",
                pressure_signal_hint="record food spending",
            ),
            _review_only_candidate(
                title="Eligible other candidate",
                publisher="TribLIVE",
                source_url="https://triblive.com/local/eligible",
                pressure_summary="TribLIVE reported summer meal demand is rising.",
                pressure_type="school meal access pressure",
            ),
        ],
    )

    with pytest.raises(ValueError, match="matched zero candidates"):
        food_line.render_food_line_review_only(
            tmp_path,
            date=date,
            candidate_review_path=review_path,
            public_eligible_only=True,
            source_url="https://www.bostonherald.com/2026/06/16/not-found",
        )

    with pytest.raises(ValueError, match="matched zero public-eligible candidates"):
        food_line.render_food_line_review_only(
            tmp_path,
            date=date,
            candidate_review_path=review_path,
            public_eligible_only=True,
            source_url=blocked_url,
        )


def test_food_line_review_only_render_source_url_selector_fails_closed_for_ambiguous_match(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-16"
    shared_url = "https://www.bostonherald.com/2026/06/16/greater-boston-food-bank"
    review_path = _write_review_only_candidate_review(
        tmp_path,
        date,
        [
            _review_only_candidate(
                title="Boston Herald candidate A",
                publisher="Boston Herald",
                source_url=shared_url,
                pressure_summary="Greater Boston Food Bank expects record food spending.",
                pressure_type="food bank demand pressure",
            ),
            _review_only_candidate(
                title="Boston Herald candidate B",
                publisher="Boston Herald",
                source_url=shared_url,
                pressure_summary="Greater Boston Food Bank expects record spending in a separate summary.",
                pressure_type="food bank demand pressure",
            ),
        ],
    )

    with pytest.raises(ValueError, match="selector is ambiguous"):
        food_line.render_food_line_review_only(
            tmp_path,
            date=date,
            candidate_review_path=review_path,
            public_eligible_only=True,
            source_url=shared_url,
        )


def test_food_line_review_only_render_source_url_selector_deduplicates_exact_duplicates(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-16"
    boston_url = "https://www.bostonherald.com/2026/06/16/greater-boston-food-bank-to-spend-record-breaking-65m-on-food-in-2026"
    candidate = _review_only_candidate(
        title="Greater Boston Food Bank to spend record-breaking $65M on food in 2026 - Boston Herald",
        publisher="Boston Herald",
        source_url=boston_url,
        pressure_summary="Boston Herald reported that Greater Boston Food Bank expects to spend a record $65M on food in 2026 as need grows.",
        pressure_type="food bank demand pressure",
        affected_groups=["Not clearly isolated by source"],
        source_role="policy_context",
    )
    review_path = _write_review_only_candidate_review(tmp_path, date, [candidate, dict(candidate)])

    result = food_line.render_food_line_review_only(
        tmp_path,
        date=date,
        candidate_review_path=review_path,
        public_eligible_only=True,
        source_url=boston_url,
    )

    manifest = json.loads(
        (tmp_path / "output" / "site-review-only" / "food-line" / "editions" / date / "review_render_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert result["selected_candidate_count"] == 1
    assert result["source_count"] == 1
    assert manifest["selector_match_count"] == 2
    assert manifest["selector_deduplicated"] is True
    assert manifest["selected_source_url"] == boston_url


def test_food_line_review_only_render_preserves_source_backed_attribution(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-12"
    review_path = _write_review_only_candidate_review(
        tmp_path,
        date,
        [
            _review_only_candidate(
                title="FRAC policy article",
                publisher="FRAC News",
                source_url="https://frac.org/blog/policy",
                pressure_summary="FRAC warned that a USDA proposal to end broad-based categorical eligibility for SNAP would increase hunger for families and children.",
                pressure_type="SNAP policy pressure",
                affected_groups=["children", "SNAP households", "low-income households"],
                source_role="policy_analysis",
            ),
        ],
    )

    food_line.render_food_line_review_only(
        tmp_path,
        date=date,
        candidate_review_path=review_path,
        public_eligible_only=True,
    )

    edition_html = (tmp_path / "output" / "site-review-only" / "food-line" / "editions" / date / "index.html").read_text(encoding="utf-8")
    claim_ledger_html = (tmp_path / "output" / "site-review-only" / "food-line" / "editions" / date / "claim_ledger.html").read_text(encoding="utf-8")
    assert "Nationally, FRAC warned that a USDA proposal to end broad-based categorical eligibility for SNAP would increase hunger for families and children." in edition_html
    assert "FRAC warned that a USDA proposal to end broad-based categorical eligibility for SNAP would increase hunger for families and children." in edition_html
    assert "was enacted" not in edition_html
    assert "benefit cuts occurred" not in edition_html
    assert "measured hunger increased" not in edition_html
    assert ">national<" in claim_ledger_html


def test_food_line_public_signal_reader_sentence_polishes_national_location():
    row = {
        "location_scope": "national",
        "location_name": "United States",
        "state": "US",
        "publisher": "FRAC News",
        "pressure_type": "SNAP policy pressure",
        "pressure_summary": "FRAC warned that a USDA proposal to end broad-based categorical eligibility for SNAP would increase hunger for families and children.",
    }
    sentence = food_line._food_line_public_signal_reader_sentence(row)
    assert sentence == "Nationally, FRAC warned that a USDA proposal to end broad-based categorical eligibility for SNAP would increase hunger for families and children."


def test_food_line_public_signal_reader_sentence_preserves_local_location():
    row = {
        "location_scope": "state_local",
        "location_name": "Horry County, SC",
        "state": "SC",
        "publisher": "WPDE / ABC 15",
        "pressure_type": "demand strain",
        "pressure_summary": "Food insecurity in Horry County is about 14 percent and about 20 percent of children are food insecure.",
    }
    sentence = food_line._food_line_public_signal_reader_sentence(row)
    assert sentence.startswith("In Horry County, South Carolina,")


def test_food_line_public_signal_reader_sentence_uses_summary_for_local_review_candidate():
    row = {
        "source_type": "review_candidate",
        "source_purpose": "review_candidate",
        "source_role": "local_news_report",
        "location_scope": "national",
        "location_name": "United States",
        "state": "US",
        "publisher": "WRDW",
        "pressure_type": "demand strain",
        "pressure_summary": "WRDW reported rising food-assistance demand, affecting children, low-income households.",
        "affected_groups": ["children", "low-income households"],
    }
    sentence = food_line._food_line_public_signal_reader_sentence(row)
    assert sentence == "WRDW reported rising food-assistance demand, affecting children, low-income households."
    assert "In United States" not in sentence
    assert "child food insecurity" not in sentence


def test_food_line_claim_interpretation_uses_national_policy_wording():
    row = {
        "source_role": "policy_analysis",
        "location_scope": "national",
        "location_name": "United States",
        "state": "US",
        "pressure_type": "SNAP policy pressure",
    }
    interpretation = food_line._food_line_claim_interpretation(row)
    assert interpretation == "This indicates national policy pressure around SNAP eligibility and food assistance access."
    assert "local food-access strain" not in interpretation
    assert "in United States" not in interpretation


def test_food_line_claim_interpretation_preserves_local_strain_wording():
    row = {
        "source_role": "provider_signal",
        "location_scope": "state_local",
        "location_name": "Horry County",
        "state": "SC",
        "pressure_type": "demand strain",
    }
    interpretation = food_line._food_line_claim_interpretation(row)
    assert interpretation == "This points to pantry supply strain in Horry County."


def test_food_line_claim_interpretation_uses_local_review_candidate_framing():
    row = {
        "source_type": "review_candidate",
        "source_purpose": "review_candidate",
        "source_role": "local_news_report",
        "location_scope": "national",
        "location_name": "United States",
        "state": "US",
        "pressure_type": "demand strain",
        "affected_groups": ["children", "low-income households"],
    }
    interpretation = food_line._food_line_claim_interpretation(row)
    assert interpretation == "This indicates local food-assistance demand pressure affecting children, low-income households."
    assert "national policy-pressure signal" not in interpretation
    assert "United States" not in interpretation


def test_food_line_claim_ledger_scope_label_uses_source_local_for_local_review_candidate():
    row = {
        "source_type": "review_candidate",
        "source_purpose": "review_candidate",
        "source_role": "local_news_report",
        "location_scope": "national",
        "location_name": "United States",
        "location_name_inferred": True,
        "state": "US",
    }
    assert food_line._food_line_claim_ledger_scope_label(row) == "source-local"


def test_food_line_claim_ledger_scope_label_preserves_national_for_policy_review_candidate():
    row = {
        "source_type": "review_candidate",
        "source_purpose": "review_candidate",
        "source_role": "policy_analysis",
        "location_scope": "national",
        "location_name": "United States",
        "location_name_inferred": False,
        "state": "US",
    }
    assert food_line._food_line_claim_ledger_scope_label(row) == "national"


def test_food_line_review_only_publish_dry_run_makes_no_mutations(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-12"
    render_dir = _build_review_only_render_dir(
        tmp_path,
        date,
        [
            _review_only_candidate(
                title="FRAC policy article",
                publisher="FRAC News",
                source_url="https://frac.org/blog/usda-proposal-to-end-broad-based-categorical-eligibility-for-snap-would-increase-hunger-for-families-and-children",
                pressure_summary="FRAC warned that a USDA proposal to end broad-based categorical eligibility for SNAP would increase hunger for families and children.",
                pressure_type="SNAP policy pressure",
                affected_groups=["children", "SNAP households", "low-income households"],
                source_role="policy_analysis",
            ),
        ],
    )

    result = food_line_review_publish.publish_review_only_render(
        root=tmp_path,
        date=date,
        review_render_dir=render_dir,
    )

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["copied_targets"] == []
    assert result["production_output_mutated"] is False
    assert result["pages_repo_mutated"] is False
    assert "archive" in result["validation_checks"]["archive_homepage_rss_podcast_impacts"]
    assert not (tmp_path / "output" / "site" / "food-line" / "editions" / date).exists()
    assert not (tmp_path / "bluefern-dispatches-pages" / "food-line" / "editions" / date).exists()


def test_food_line_review_only_publish_missing_manifest_fails(tmp_path: Path):
    edition_dir = tmp_path / "output" / "site-review-only" / "food-line" / "editions" / "2026-06-12"
    edition_dir.mkdir(parents=True, exist_ok=True)
    for name in ("index.html", "source_table.html", "claim_ledger.html"):
        (edition_dir / name).write_text("<html></html>", encoding="utf-8")
    with pytest.raises(ValueError, match="missing required files"):
        food_line_review_publish.publish_review_only_render(
            root=tmp_path,
            date="2026-06-12",
            review_render_dir=edition_dir,
        )


def test_food_line_review_only_publish_wrong_render_mode_fails(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-12"
    render_dir = _build_review_only_render_dir(
        tmp_path,
        date,
        [
            _review_only_candidate(
                title="FRAC policy article",
                publisher="FRAC News",
                source_url="https://frac.org/blog/usda-proposal-to-end-broad-based-categorical-eligibility-for-snap-would-increase-hunger-for-families-and-children",
                pressure_summary="FRAC warned that a USDA proposal to end broad-based categorical eligibility for SNAP would increase hunger for families and children.",
                pressure_type="SNAP policy pressure",
                affected_groups=["children", "SNAP households", "low-income households"],
                source_role="policy_analysis",
            ),
        ],
    )
    manifest_path = render_dir / "review_render_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["render_mode"] = "current_update"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    result = food_line_review_publish.publish_review_only_render(
        root=tmp_path,
        date=date,
        review_render_dir=render_dir,
    )
    assert result["ok"] is False
    assert any("render_mode must be review_only" in error for error in result["errors"])


def test_food_line_review_only_publish_date_mismatch_fails(tmp_path: Path):
    _ensure_assets(tmp_path)
    render_dir = _build_review_only_render_dir(
        tmp_path,
        "2026-06-12",
        [
            _review_only_candidate(
                title="FRAC policy article",
                publisher="FRAC News",
                source_url="https://frac.org/blog/usda-proposal-to-end-broad-based-categorical-eligibility-for-snap-would-increase-hunger-for-families-and-children",
                pressure_summary="FRAC warned that a USDA proposal to end broad-based categorical eligibility for SNAP would increase hunger for families and children.",
                pressure_type="SNAP policy pressure",
                affected_groups=["children", "SNAP households", "low-income households"],
                source_role="policy_analysis",
            ),
        ],
    )
    result = food_line_review_publish.publish_review_only_render(
        root=tmp_path,
        date="2026-06-13",
        review_render_dir=render_dir,
    )
    assert result["ok"] is False
    assert any("edition_date 2026-06-12 does not match 2026-06-13" in error for error in result["errors"])


def test_food_line_review_only_publish_leak_content_fails(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-12"
    render_dir = _build_review_only_render_dir(
        tmp_path,
        date,
        [
            _review_only_candidate(
                title="FRAC policy article",
                publisher="FRAC News",
                source_url="https://frac.org/blog/usda-proposal-to-end-broad-based-categorical-eligibility-for-snap-would-increase-hunger-for-families-and-children",
                pressure_summary="FRAC warned that a USDA proposal to end broad-based categorical eligibility for SNAP would increase hunger for families and children.",
                pressure_type="SNAP policy pressure",
                affected_groups=["children", "SNAP households", "low-income households"],
                source_role="policy_analysis",
            ),
        ],
    )
    index_path = render_dir / "index.html"
    index_path.write_text(index_path.read_text(encoding="utf-8") + "<p>WPDE / ABC 15</p>", encoding="utf-8")

    result = food_line_review_publish.publish_review_only_render(
        root=tmp_path,
        date=date,
        review_render_dir=render_dir,
    )
    assert result["ok"] is False
    assert any("out-of-scope leak content" in error for error in result["errors"])


def test_food_line_review_only_publish_valid_frac_review_render_passes_validation(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-12"
    render_dir = _build_review_only_render_dir(
        tmp_path,
        date,
        [
            _review_only_candidate(
                title="FRAC policy article",
                publisher="FRAC News",
                source_url="https://frac.org/blog/usda-proposal-to-end-broad-based-categorical-eligibility-for-snap-would-increase-hunger-for-families-and-children",
                pressure_summary="FRAC warned that a USDA proposal to end broad-based categorical eligibility for SNAP would increase hunger for families and children.",
                pressure_type="SNAP policy pressure",
                affected_groups=["children", "SNAP households", "low-income households"],
                source_role="policy_analysis",
            ),
        ],
    )
    result = food_line_review_publish.publish_review_only_render(
        root=tmp_path,
        date=date,
        review_render_dir=render_dir,
    )
    assert result["ok"] is True
    assert result["validation_checks"]["expected_source_url"] == "https://frac.org/blog/usda-proposal-to-end-broad-based-categorical-eligibility-for-snap-would-increase-hunger-for-families-and-children"
    assert result["validation_checks"]["expected_source_url_in_all_rendered_files"] is True
    assert result["validation_checks"]["leak_hits"] == []


def test_food_line_review_only_publish_to_output_site_copies_only_edition_dir(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-12"
    render_dir = _build_review_only_render_dir(
        tmp_path,
        date,
        [
            _review_only_candidate(
                title="FRAC policy article",
                publisher="FRAC News",
                source_url="https://frac.org/blog/usda-proposal-to-end-broad-based-categorical-eligibility-for-snap-would-increase-hunger-for-families-and-children",
                pressure_summary="FRAC warned that a USDA proposal to end broad-based categorical eligibility for SNAP would increase hunger for families and children.",
                pressure_type="SNAP policy pressure",
                affected_groups=["children", "SNAP households", "low-income households"],
                source_role="policy_analysis",
            ),
        ],
    )

    result = food_line_review_publish.publish_review_only_render(
        root=tmp_path,
        date=date,
        review_render_dir=render_dir,
        publish_to_output_site=True,
    )

    target = tmp_path / "output" / "site" / "food-line" / "editions" / date
    assert result["ok"] is True
    assert result["dry_run"] is False
    assert result["production_output_mutated"] is True
    assert result["pages_repo_mutated"] is False
    assert target.exists()
    assert (target / "index.html").exists()
    assert not (tmp_path / "output" / "site" / "food-line" / "archive.html").exists()
    assert not (tmp_path / "output" / "site" / "food-line" / "podcast.xml").exists()


def test_food_line_review_only_publish_to_pages_copies_only_edition_dir_without_commit_or_push(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-12"
    render_dir = _build_review_only_render_dir(
        tmp_path,
        date,
        [
            _review_only_candidate(
                title="FRAC policy article",
                publisher="FRAC News",
                source_url="https://frac.org/blog/usda-proposal-to-end-broad-based-categorical-eligibility-for-snap-would-increase-hunger-for-families-and-children",
                pressure_summary="FRAC warned that a USDA proposal to end broad-based categorical eligibility for SNAP would increase hunger for families and children.",
                pressure_type="SNAP policy pressure",
                affected_groups=["children", "SNAP households", "low-income households"],
                source_role="policy_analysis",
            ),
        ],
    )
    pages_repo = tmp_path / "bluefern-dispatches-pages"
    pages_repo.mkdir(parents=True, exist_ok=True)

    result = food_line_review_publish.publish_review_only_render(
        root=tmp_path,
        date=date,
        review_render_dir=render_dir,
        publish_to_pages=True,
        pages_repo=pages_repo,
    )

    target = pages_repo / "food-line" / "editions" / date
    assert result["ok"] is True
    assert result["dry_run"] is False
    assert result["production_output_mutated"] is False
    assert result["pages_repo_mutated"] is True
    assert result["committed"] is False
    assert result["pushed"] is False
    assert target.exists()
    assert (target / "claim_ledger.html").exists()
    assert not (pages_repo / "food-line" / "archive.html").exists()


def test_food_line_archive_review_only_updater_dry_run_makes_no_mutation(tmp_path: Path):
    pages_repo, archive_path, edition_path = _setup_food_line_archive_review_only_pages_fixture(tmp_path)
    before_archive = archive_path.read_text(encoding="utf-8")
    before_home = (pages_repo / "food-line" / "index.html").read_text(encoding="utf-8")

    result = food_line_archive_update.update_food_line_archive_for_review_only(
        date="2026-06-12",
        title="Food Line Dispatch - 2026-06-12",
        edition_url="./editions/2026-06-12/",
        pages_repo=pages_repo,
    )

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["entry_added"] is True
    assert result["already_present"] is False
    assert result["changed_files"] == []
    assert result["pages_repo_mutated"] is False
    assert result["archive_path"] == str(archive_path.resolve())
    assert result["edition_path"] == str(edition_path.resolve())
    assert archive_path.read_text(encoding="utf-8") == before_archive
    assert (pages_repo / "food-line" / "index.html").read_text(encoding="utf-8") == before_home


def test_food_line_archive_review_only_updater_apply_adds_one_entry_and_only_archive_changes(tmp_path: Path):
    pages_repo, archive_path, _edition_path = _setup_food_line_archive_review_only_pages_fixture(tmp_path)
    before_files = {
        "archive": archive_path.read_text(encoding="utf-8"),
        "index": (pages_repo / "food-line" / "index.html").read_text(encoding="utf-8"),
        "rss": (pages_repo / "food-line" / "rss.xml").read_text(encoding="utf-8"),
        "podcast": (pages_repo / "food-line" / "podcast.xml").read_text(encoding="utf-8"),
        "audio": (pages_repo / "food-line" / "audio" / "index.html").read_text(encoding="utf-8"),
        "map": (pages_repo / "food-line" / "map" / "index.html").read_text(encoding="utf-8"),
    }

    result = food_line_archive_update.update_food_line_archive_for_review_only(
        date="2026-06-12",
        title="Food Line Dispatch - 2026-06-12",
        edition_url="./editions/2026-06-12/",
        pages_repo=pages_repo,
        apply=True,
    )

    archive_html = archive_path.read_text(encoding="utf-8")
    assert result["ok"] is True
    assert result["dry_run"] is False
    assert result["entry_added"] is True
    assert result["already_present"] is False
    assert result["pages_repo_mutated"] is True
    assert result["changed_files"] == [str(archive_path.resolve())]
    assert archive_html.count("2026-06-12 — Food Line Dispatch - 2026-06-12") == 1
    assert 'href="editions/2026-06-12/"' in archive_html
    assert archive_html.index("2026-06-13") < archive_html.index("2026-06-12")
    assert archive_html.index("2026-06-12") < archive_html.index("2026-06-09")
    assert (pages_repo / "food-line" / "index.html").read_text(encoding="utf-8") == before_files["index"]
    assert (pages_repo / "food-line" / "rss.xml").read_text(encoding="utf-8") == before_files["rss"]
    assert (pages_repo / "food-line" / "podcast.xml").read_text(encoding="utf-8") == before_files["podcast"]
    assert (pages_repo / "food-line" / "audio" / "index.html").read_text(encoding="utf-8") == before_files["audio"]
    assert (pages_repo / "food-line" / "map" / "index.html").read_text(encoding="utf-8") == before_files["map"]
    assert before_files["archive"] != archive_html


def test_food_line_archive_review_only_updater_repeat_apply_is_idempotent(tmp_path: Path):
    pages_repo, archive_path, _edition_path = _setup_food_line_archive_review_only_pages_fixture(tmp_path)
    first = food_line_archive_update.update_food_line_archive_for_review_only(
        date="2026-06-12",
        title="Food Line Dispatch - 2026-06-12",
        edition_url="./editions/2026-06-12/",
        pages_repo=pages_repo,
        apply=True,
    )
    first_archive = archive_path.read_text(encoding="utf-8")

    second = food_line_archive_update.update_food_line_archive_for_review_only(
        date="2026-06-12",
        title="Food Line Dispatch - 2026-06-12",
        edition_url="./editions/2026-06-12/",
        pages_repo=pages_repo,
        apply=True,
    )

    assert first["entry_added"] is True
    assert second["entry_added"] is False
    assert second["already_present"] is True
    assert second["pages_repo_mutated"] is False
    assert second["changed_files"] == []
    assert archive_path.read_text(encoding="utf-8") == first_archive
    assert first_archive.count("2026-06-12 — Food Line Dispatch - 2026-06-12") == 1


def test_food_line_archive_review_only_updater_missing_edition_fails_closed(tmp_path: Path):
    pages_repo, _archive_path, edition_path = _setup_food_line_archive_review_only_pages_fixture(tmp_path)
    edition_path.unlink()

    with pytest.raises(ValueError, match="review-only edition index not found"):
        food_line_archive_update.update_food_line_archive_for_review_only(
            date="2026-06-12",
            title="Food Line Dispatch - 2026-06-12",
            edition_url="./editions/2026-06-12/",
            pages_repo=pages_repo,
        )


def test_food_line_archive_review_only_updater_requires_expected_frac_url(tmp_path: Path):
    pages_repo, archive_path, _edition_path = _setup_food_line_archive_review_only_pages_fixture(tmp_path, include_frac_url=False)
    before_archive = archive_path.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="does not contain expected source URL"):
        food_line_archive_update.update_food_line_archive_for_review_only(
            date="2026-06-12",
            title="Food Line Dispatch - 2026-06-12",
            edition_url="./editions/2026-06-12/",
            pages_repo=pages_repo,
            apply=True,
        )

    assert archive_path.read_text(encoding="utf-8") == before_archive


def test_food_line_public_html_hides_internal_candidate_labels(tmp_path: Path):
    _ensure_assets(tmp_path)
    _clear_food_line_registries(tmp_path)
    date = "2026-06-13"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload_path = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "food-line" / "sources" / "2026-06-13" / "auto_sources.json"
    payload = _freshen_food_line_payload_for_publication(
        [
            row
            for row in json.loads(payload_path.read_text(encoding="utf-8"))
            if row["source_record_id"] == "food-line-auto-6effc522ae28d822"
        ],
        date,
    )
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    run_food_line_dispatch(tmp_path, date)
    edition_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "index.html").read_text(encoding="utf-8")

    for forbidden in (
        "review_status",
        "public_claim_eligible",
        "public_claim_blockers",
        "candidate_source_role",
        "pressure_signal_strength",
        "traceability_status",
    ):
        assert forbidden not in edition_html


def test_food_line_nonpressure_rss_items_are_excluded_from_pressure_map(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        _pressure_row(1, "Restaurant announces new menu", "The restaurant announced a seasonal menu change.", family="national_news"),
        _pressure_row(2, "Food bank volunteer day", "The food bank thanked volunteers and shared shift times.", family="nonprofit_news"),
    ]
    p.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    map_data = json.loads((tmp_path / "output" / "review" / "food-line" / date / "map_data.json").read_text(encoding="utf-8"))
    assert result["public_rendered"] is False
    assert result["skip_reason"] == "No new primary food-access signal qualified for public Food Line publication."
    assert map_data.get("pressure_markers") == []
    assert any(record.get("reason") for record in map_data.get("excluded_records") or [])
    assert not (tmp_path / "output" / "site" / "food-line" / "map" / "index.html").exists()


def test_food_line_vague_provider_source_is_demoted_when_summary_cannot_be_generated(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    vague = _pressure_row(1, "Food bank update", "Volunteer day and community partners.", family="food_bank_provider", state="TX")
    vague["map_category"] = "elevated demand"
    p.write_text(json.dumps([vague], indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    map_data = json.loads((tmp_path / "output" / "review" / "food-line" / date / "map_data.json").read_text(encoding="utf-8"))
    assert result["public_rendered"] is False
    assert result["skip_reason"] == "No new primary food-access signal qualified for public Food Line publication."
    assert map_data["markers"][0]["pressure_signal"] is False
    assert map_data["markers"][0]["pressure_summary"] == ""
    assert "insufficient specific pressure evidence" in map_data["markers"][0]["pressure_reason"]


def test_food_line_affected_groups_require_supporting_text_and_baseline_stays_baseline(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    pressure = _pressure_row(1, "Food bank demand increased", "Food bank demand increased sharply.", family="food_bank_provider", state="TX")
    pressure["issue_tags"] = ["food banks", "pantry capacity"]
    supported = _pressure_row(2, "SNAP benefits delayed for families", "SNAP benefits delayed for families and children.", family="state_official", state="TX")
    baseline = _row(3, family="economic_data", state="US", title="USDA context", summary="National food security context and trend information.")
    baseline["map_category"] = "context / monitoring only"
    p.write_text(json.dumps([pressure, supported, baseline], indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    manifest = json.loads((tmp_path / "output" / "site" / "food-line" / "editions" / date / "sources_manifest.json").read_text(encoding="utf-8"))
    by_title = {row["title"]: row for row in manifest}
    assert by_title["Food bank demand increased"]["affected_groups"] == []
    assert any(group in {"children", "SNAP households", "low-income households"} for group in by_title["SNAP benefits delayed for families"]["affected_groups"])
    assert by_title["USDA context"]["source_role"] == "baseline_condition"
    assert by_title["USDA context"]["pressure_signal"] is False
    assert result["baseline_source_count"] == 1
    source_table = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "source_table.html").read_text(encoding="utf-8")
    assert "Not clearly isolated by source" not in source_table
    edition_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "index.html").read_text(encoding="utf-8")
    assert "children" in edition_html.lower() or "snap households" in edition_html.lower() or "low-income households" in edition_html.lower()


def test_food_line_pressure_summary_includes_specific_evidence_for_core_cases(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX"),
        _pressure_row(2, "Pantry cuts hours due to low inventory", "The pantry reduced hours because shelves were bare.", family="food_bank_provider", state="WA"),
        _pressure_row(3, "SNAP benefits delayed", "Households reported a SNAP delay and application backlog.", family="state_official", state="OR"),
    ]
    p.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)
    manifest = json.loads((tmp_path / "output" / "site" / "food-line" / "editions" / date / "sources_manifest.json").read_text(encoding="utf-8"))
    by_title = {row["title"]: row for row in manifest}
    assert "rising food-assistance demand" in by_title["Food bank sees rising demand from families"]["pressure_summary"].lower()
    assert "reduced distribution hours" in by_title["Pantry cuts hours due to low inventory"]["pressure_summary"].lower()
    assert "snap benefit delay" in by_title["SNAP benefits delayed"]["pressure_summary"].lower()


def test_food_line_source_table_includes_pressure_summary(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)
    table = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "source_table.html").read_text(encoding="utf-8")
    assert "What happened" in table
    assert "What the source says" in table
    assert "Record ID" in table
    assert "How it was used" in table
    assert "Verification status" in table
    assert "rising food-assistance demand" in table.lower()


def test_food_line_public_edition_uses_pressure_summary_and_cleans_public_excerpts(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    verified = _pressure_row(
        1,
        "KLTV food pantries struggle to keep up with rising demand",
        "The government shutdown is now in its 4th week and food banks across Texas are working hard to keep up with rising demand. Skip to content Advertise With Us Weather Sports Contests Closings & Delays",
        family="local_news",
        state="TX",
    )
    verified["location_name"] = "East Texas, TX"
    verified["publisher"] = "KLTV"
    noisy_context = _row(
        2,
        family="public_radio",
        state="TX",
        title="Unrelated local arts story",
        summary="Skip to content Advertise With Us Weather Sports Contests Closings & Delays",
    )
    noisy_context["map_category"] = "context / monitoring only"
    p.write_text(json.dumps([verified, noisy_context], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)
    edition_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "index.html").read_text(encoding="utf-8")
    source_table_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "source_table.html").read_text(encoding="utf-8")
    map_html = (tmp_path / "output" / "site" / "food-line" / "map" / "index.html").read_text(encoding="utf-8")

    assert "Limited-source update / June 4, 2026" in edition_html
    assert "Generated from saved source records available for June 4, 2026." in edition_html
    assert "Today’s Read" in edition_html
    assert "At A Glance" in edition_html
    assert "Core Food Pressure Signals" in edition_html
    assert "Other Food Line Signals" in edition_html
    assert "Source Mix" in edition_html
    assert "Source Note" in edition_html
    assert "Main Food Access Story" not in edition_html
    assert "What Else We’re Watching" not in edition_html
    assert "Context and Watch Items" not in edition_html
    assert "Sources Behind This Briefing" not in edition_html
    assert "source mix" in edition_html.lower()
    assert "No fresh source-backed current food-pressure signal qualified today." not in edition_html
    assert "Background records remain in the source audit and public source table only." not in edition_html
    assert "public item(s)" not in edition_html
    assert "primary pressure lead" not in edition_html
    assert "What changed today" not in edition_html
    assert "Publishing note" not in edition_html
    assert "Skip to content" not in edition_html
    assert "Advertise With Us" not in edition_html
    assert "Weather" not in edition_html
    assert "Sports" not in edition_html
    assert "Contests" not in edition_html
    assert "Closings & Delays" not in edition_html
    assert "Open the public source table for source links, traceability, and cleaned excerpts." in edition_html
    assert "<strong>Sources</strong>" in edition_html
    assert "Context:" in edition_html
    assert "KLTV" in edition_html
    assert "Demand strain" in edition_html
    assert "East Texas, TX" in edition_html
    assert "Record ID" in source_table_html
    assert "What the source says" in source_table_html
    assert "Unrelated local arts story" not in source_table_html
    assert "Skip to content" not in source_table_html
    assert "Verification status" in source_table_html
    assert "Used on public page" in source_table_html
    assert "source_text_verified" not in source_table_html
    assert "pressure_signal" not in source_table_html
    assert "Source record ID" not in source_table_html
    assert "Skip to content" not in map_html
    assert "What the source says:" in map_html


def test_food_line_public_source_table_matches_rendered_public_urls(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-05"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload_path = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "food-line" / "sources" / date / "auto_sources.json"
    payload = _freshen_food_line_payload_for_publication(json.loads(payload_path.read_text(encoding="utf-8")), date)
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)

    edition_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "index.html").read_text(encoding="utf-8")
    source_table_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "source_table.html").read_text(encoding="utf-8")

    page_urls = _http_urls(edition_html)
    table_urls = _http_urls(source_table_html)
    assert page_urls
    assert table_urls
    assert set(page_urls).issubset(set(table_urls))
    assert "Today’s Read" in edition_html
    assert "At A Glance" in edition_html
    assert "Core Food Pressure Signals" in edition_html
    assert "Other Food Line Signals" in edition_html
    assert "Source Mix" in edition_html
    assert "Source Note" in edition_html
    assert "Main Food Access Story" not in edition_html
    assert "What Else We’re Watching" not in edition_html
    assert "Sources Behind This Briefing" not in edition_html
    assert "Context and Watch Items" not in edition_html
    assert "Source Mix" in edition_html
    assert "Local food pantries are preparing for increased demand" in edition_html
    assert "USDA set to cut $1B for food programs" in edition_html
    assert "local_signal" not in source_table_html
    assert "source_text_verified" not in source_table_html
    assert "demoted_context" not in source_table_html
    assert "Cascade PBS" in source_table_html
    assert "KLTV" in source_table_html
    assert "USDA FNS" in source_table_html
    assert "USDA ERS" in source_table_html
    assert "USDA FNS" not in edition_html
    assert "USDA ERS" not in edition_html
    assert "Used on public page" in source_table_html
    assert "Background reference" in source_table_html
    assert source_table_html.count("Yes") >= 3


def test_food_line_reuses_previous_day_lead_as_continuing_pressure(tmp_path: Path):
    _ensure_assets(tmp_path)
    kltv = _pressure_row(
        1,
        "KLTV food pantries struggle to keep up with rising demand",
        "The government shutdown is now in its 4th week and food banks across Texas are working hard to keep up with rising demand.",
        family="local_news",
        state="TX",
    )
    kltv["publisher"] = "KLTV"
    kltv["location_name"] = "East Texas, TX"
    kltv["summary_or_snippet"] = "The government shutdown is now in its 4th week and food banks across Texas are working hard to keep up with rising demand. Skip to content Advertise With Us Weather Sports Contests Closings & Delays"
    context = _row(2, family="economic_data", state="US", title="USDA context", summary="National food security context.")
    context["map_category"] = "context / monitoring only"

    date_a = "2026-06-04"
    p_a = _manual_path(tmp_path, date_a)
    p_a.parent.mkdir(parents=True, exist_ok=True)
    p_a.write_text(json.dumps([kltv, context], indent=2), encoding="utf-8")
    result_a = run_food_line_dispatch(tmp_path, date_a)
    assert result_a["primary_signal_status"] == "new_primary"
    assert result_a["lead_source_record_id"] == kltv["source_record_id"]

    date_b = "2026-06-05"
    p_b = _manual_path(tmp_path, date_b)
    p_b.parent.mkdir(parents=True, exist_ok=True)
    p_b.write_text(json.dumps([kltv, context], indent=2), encoding="utf-8")
    result_b = run_food_line_dispatch(tmp_path, date_b)
    site_edition = tmp_path / "output" / "site" / "food-line" / "editions" / date_b
    review_manifest = tmp_path / "output" / "review" / "food-line" / date_b / "run_manifest.json"
    data_manifest = tmp_path / "data" / "dispatches" / "food-line" / "editions" / date_b / "run_manifest.json"
    manifest_b = json.loads(data_manifest.read_text(encoding="utf-8"))

    assert result_b["primary_signal_status"] == "continuing_only"
    assert result_b["lead_source_record_id"] is None
    assert result_b["public_rendered"] is False
    assert result_b["qualified_primary_count"] == 0
    assert result_b["skip_reason"] == "No new primary food-access signal qualified for public Food Line publication."
    assert result_b["continuing_pressure_source_record_ids"] == [kltv["source_record_id"]]
    assert not site_edition.exists()
    assert review_manifest.exists()
    assert data_manifest.exists()
    assert manifest_b["public_rendered"] is False
    assert manifest_b["qualified_primary_count"] == 0
    assert manifest_b["skip_reason"] == "No new primary food-access signal qualified for public Food Line publication."
    assert manifest_b["continuing_pressure_source_record_ids"] == [kltv["source_record_id"]]


def test_food_line_map_popup_uses_pressure_summary_and_not_tags_as_primary_evidence(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)
    map_html = (tmp_path / "output" / "site" / "food-line" / "map" / "index.html").read_text(encoding="utf-8")
    assert "Location:" in map_html
    assert "Verification status:" in map_html
    assert "Record ID:" in map_html
    assert "Source URL:" in map_html
    assert "What happened:" in map_html
    assert "What the source says:" in map_html
    assert "rising food-assistance demand" in map_html.lower()
    assert "Source-backed food insecurity context signal" not in map_html
    assert "<div><strong>Category:</strong>" not in map_html
    assert "<div><strong>Issue tags:</strong>" not in map_html
    assert "Not clearly isolated by source" not in map_html


def test_food_line_blank_affected_groups_render_placeholder_everywhere(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Pantry cuts hours due to low inventory", "The pantry reduced hours because shelves were bare.", family="food_bank_provider", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)
    map_html = (tmp_path / "output" / "site" / "food-line" / "map" / "index.html").read_text(encoding="utf-8")
    source_table = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "source_table.html").read_text(encoding="utf-8")
    edition_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "index.html").read_text(encoding="utf-8")
    assert "Not clearly isolated by source" not in map_html
    assert "Not clearly isolated by source" not in source_table
    assert "Not clearly isolated by source" not in edition_html


def test_food_line_map_and_runner_diagnostics_count_only_pressure_records(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="CA"),
        _pressure_row(2, "Restaurant announces new menu", "The restaurant announced a seasonal menu change.", family="national_news"),
        _row(3, family="economic_data", state="US", title="USDA context", summary="National food security context and trend information."),
    ]
    rows[2]["map_category"] = "context / monitoring only"
    p.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    map_data = json.loads((tmp_path / "output" / "site" / "food-line" / "map" / "map_data.json").read_text(encoding="utf-8"))
    assert result["pressure_source_count_by_family"]["local_news"] == 1
    assert result["pressure_source_count_by_state"]["CA"] == 1
    assert result["news_item_count"] == 2
    assert result["baseline_source_count"] == 1
    assert map_data.get("diagnostics", {}).get("pressure_signal_count") == 1
    assert map_data.get("diagnostics", {}).get("pressure_marker_count") == 1
    assert map_data.get("diagnostics", {}).get("excluded_record_count") == 2
    assert map_data.get("diagnostics", {}).get("excluded_context_count") == 1
    assert map_data.get("diagnostics", {}).get("exclusion_reason_counts", {}).get("background/context only") == 1
    assert map_data.get("diagnostics", {}).get("exclusion_reason_counts", {}).get("weak pressure signal") == 1
    assert len(map_data.get("mapped_markers") or []) == 1
    assert map_data.get("mapped_markers")[0]["source_title"] == "Food bank sees rising demand from families"
    assert map_data.get("excluded_records") and all("reason" in record for record in map_data["excluded_records"])
    assert result["exclusion_reason_counts"]["background/context only"] == 1
    assert result["exclusion_reason_counts"]["weak pressure signal"] == 1
    assert "background or context material 1" in result["exclusion_reason_summary"]
    manifest = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "editions" / date / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["exclusion_reason_counts"]["background/context only"] == 1
    assert manifest["exclusion_reason_counts"]["weak pressure signal"] == 1
    public_source_table = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "source_table.html").read_text(encoding="utf-8")
    assert "Food bank sees rising demand from families" in public_source_table
    assert "Restaurant announces new menu" not in public_source_table
    assert "USDA context" in public_source_table
    with (tmp_path / "output" / "review" / "food-line" / date / "pressure_review.csv").open(encoding="utf-8") as handle:
        pressure_review = list(csv.DictReader(handle))
    assert len(pressure_review) == 3
    assert any(row["pressure_signal"] == "false" for row in pressure_review)


def test_food_line_outside_product_geography_is_bucketed_and_not_rendered(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(
        1,
        "Hamilton food banks see rising demand",
        "Hamilton food banks report rising demand and more families asking for food support.",
        family="local_news",
        state="Ontario",
        source_type="rss",
        publisher="Hamilton Spectator",
    )
    row["country"] = "Canada"
    row["location_name"] = "Hamilton, Ontario"
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")

    result = run_food_line_dispatch(tmp_path, date)

    review_rows = list(csv.DictReader((tmp_path / "output" / "review" / "food-line" / date / "pressure_review.csv").open(encoding="utf-8")))
    review_row = review_rows[0]
    map_data = json.loads((tmp_path / "output" / "review" / "food-line" / date / "map_data.json").read_text(encoding="utf-8"))
    manifest = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "editions" / date / "run_manifest.json").read_text(encoding="utf-8"))

    assert result["public_rendered"] is False
    assert result["pressure_signal_count"] == 0
    assert result["pressure_marker_count"] == 0
    assert result["exclusion_reason_counts"]["outside product geography"] == 1
    assert manifest["exclusion_reason_counts"]["outside product geography"] == 1
    assert map_data["diagnostics"]["exclusion_reason_counts"]["outside product geography"] == 1
    assert review_row["pressure_signal"] == "false"
    assert review_row["pressure_verification_status"] == "demoted_context"
    assert review_row["source_title"] == "Hamilton food banks see rising demand"
    assert review_row["location_name"] == "Hamilton, Ontario"
    assert review_row["state"] == "Ontario"
    assert review_row["pressure_summary"] == ""
    assert not (tmp_path / "output" / "site" / "food-line" / "editions" / date / "index.html").exists()
    assert not map_data.get("pressure_markers")
    assert map_data.get("excluded_records")
    assert any(record.get("source_title") == "Hamilton food banks see rising demand" for record in map_data["excluded_records"])


def test_food_line_us_research_signal_renders_publicly_without_map_marker(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-10"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)

    local = _pressure_row(
        1,
        "Pantry sees more first-time visitors",
        "A local pantry reported rising food-assistance demand and more first-time visitors seeking help.",
        family="local_news",
        state="TX",
        source_type="manual",
        publisher="Austin Monitor",
    )
    local["location_name"] = "Austin, Texas"

    research = _pressure_row(
        2,
        "Sports-betting study links legal access to lower food sufficiency",
        (
            "GamblingHarm.org reported on research that linked legal sports-betting access to lower food sufficiency "
            "among some U.S. households, especially adults without a college degree, adults ages 25 to 44, and non-white adults."
        ),
        family="policy_research",
        state="US",
        source_type="manual",
        publisher="GamblingHarm.org",
    )
    research["location_name"] = "United States"
    research["url"] = "https://gamblingharm.org/legal-sports-betting-food-insecurity-study/"
    research["primary_source_url"] = "https://www.nber.org/papers/example-food-sufficiency-study"
    research["secondary_source_url"] = research["url"]
    research["source_traceability_role"] = "secondary_explainer_with_primary_reference"

    background = _row(3, "economic_data", "US", title="USDA context", summary="USDA food security context.", source_type="manual", publisher="USDA")
    background["location_name"] = "United States"

    p.write_text(json.dumps([local, research, background], indent=2), encoding="utf-8")

    result = run_food_line_dispatch(tmp_path, date, generate_audio=False)

    edition_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "index.html").read_text(encoding="utf-8")
    transcript_html = (tmp_path / "output" / "site" / "food-line" / "audio" / f"{date}-transcript.html").read_text(encoding="utf-8")
    audio_json = json.loads((tmp_path / "output" / "site" / "food-line" / "audio" / f"{date}.json").read_text(encoding="utf-8"))
    source_table_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "source_table.html").read_text(encoding="utf-8")
    map_data = json.loads((tmp_path / "output" / "site" / "food-line" / "map" / "map_data.json").read_text(encoding="utf-8"))
    review_rows = list(csv.DictReader((tmp_path / "output" / "review" / "food-line" / date / "pressure_review.csv").open(encoding="utf-8")))
    review_by_title = {row["source_title"]: row for row in review_rows}

    assert result["ok"] is True
    assert result["public_rendered"] is True
    assert result["selected_lead_source_role"] == "local_signal"
    assert result["pressure_signal_count"] == 2
    assert result["pressure_marker_count"] == 1
    assert "Sports-betting study links legal access to lower food sufficiency" in edition_html
    assert "legal sports-betting access to lower food sufficiency among some U.S. households" in edition_html
    assert "pantry-demand story" not in edition_html
    assert "https://www.nber.org/papers/example-food-sufficiency-study" in edition_html
    assert "https://gamblingharm.org/legal-sports-betting-food-insecurity-study/" not in edition_html
    assert "Household financial stress" in source_table_html
    assert "Research / Context Signals" in edition_html
    assert "research_signal" not in edition_html
    assert "data_anchor_signal" not in edition_html
    assert "institutional_context_signal" not in edition_html
    assert "research_signal" not in source_table_html
    assert "data_anchor_signal" not in source_table_html
    assert "institutional_context_signal" not in source_table_html
    assert review_by_title["Sports-betting study links legal access to lower food sufficiency"]["primary_eligible"] == "false"
    assert "Other Food Line Signals" in transcript_html
    assert "pantry-demand story" not in transcript_html
    assert food_line._food_line_claim_confidence(
        {
            "source_role": "research_signal",
            "evidence_level": "research report",
        }
    ) == "moderate"
    assert review_by_title["Sports-betting study links legal access to lower food sufficiency"]["pressure_signal"] == "true"
    assert review_by_title["Sports-betting study links legal access to lower food sufficiency"]["primary_source_url"] == "https://www.nber.org/papers/example-food-sufficiency-study"
    assert review_by_title["Sports-betting study links legal access to lower food sufficiency"]["secondary_source_url"] == "https://gamblingharm.org/legal-sports-betting-food-insecurity-study/"
    assert review_by_title["Sports-betting study links legal access to lower food sufficiency"]["source_traceability_role"] == "secondary_explainer_with_primary_reference"
    assert any(
        record.get("source_title") == "Sports-betting study links legal access to lower food sufficiency"
        and record.get("reason") == "not_map_eligible"
        for record in map_data["excluded_records"]
    )
    assert all(
        marker.get("source_title") != "Sports-betting study links legal access to lower food sufficiency"
        for marker in map_data["pressure_markers"]
    )


def test_food_line_hospital_linked_caregiver_research_prefers_primary_source_url_and_stays_contextual(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-13"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)

    wrapper_url = "https://news.example.org/2026/06/13/caregivers-food-insecurity-hospitalization-study/"
    primary_url = "https://www.example.edu/research/caregiver-food-insecurity-hospitalization/"
    research = _pressure_row(
        1,
        "Study: caregiver food insecurity during hospitalization of sick children",
        (
            "University researchers reported caregiver food insecurity during hospitalization of sick children, "
            "including families of children with cancer and blood disorders."
        ),
        family="policy_research",
        state="US",
        source_type="manual",
        publisher="Example University",
    )
    research.update(
        {
            "location_name": "United States",
            "url": wrapper_url,
            "primary_source_url": primary_url,
            "secondary_source_url": wrapper_url,
            "source_traceability_role": "secondary_explainer_with_primary_reference",
            "source_purpose": "research_report",
            "evidence_text_basis": "manual_review",
            "evidence_text": (
                "University researchers reported caregiver food insecurity during hospitalization of sick children, "
                "including families of children with cancer and blood disorders."
            ),
            "pressure_summary": "University researchers reported caregiver food insecurity during hospitalization of sick children.",
            "pressure_type": "hospital-linked caregiver food insecurity",
            "pressure_signal": True,
            "source_role": "research_signal",
            "map_eligible": False,
            "pressure_verification_status": "source_text_verified",
            "issue_tags": [],
            "map_category": "context / monitoring only",
            "source_family": "policy_research",
        }
    )

    p.write_text(json.dumps([research], indent=2), encoding="utf-8")

    result = run_food_line_dispatch(tmp_path, date, generate_audio=False)

    edition_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "index.html").read_text(encoding="utf-8")
    source_table_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "source_table.html").read_text(encoding="utf-8")
    review_rows = list(csv.DictReader((tmp_path / "output" / "review" / "food-line" / date / "pressure_review.csv").open(encoding="utf-8")))
    review_by_title = {row["source_title"]: row for row in review_rows}
    map_data = json.loads((tmp_path / "output" / "site" / "food-line" / "map" / "map_data.json").read_text(encoding="utf-8"))

    assert result["ok"] is True
    assert result["public_rendered"] is True
    assert result["selected_lead_source_role"] == "research_signal"
    assert result["pressure_signal_count"] == 1
    assert result["pressure_marker_count"] == 0
    assert "Research / Context Signals" in edition_html
    assert "hospital-linked caregiver food insecurity" not in edition_html
    assert "research_signal" not in edition_html
    assert "research_signal" not in source_table_html
    assert review_by_title["Study: caregiver food insecurity during hospitalization of sick children"]["source_url"] == primary_url
    assert review_by_title["Study: caregiver food insecurity during hospitalization of sick children"]["primary_source_url"] == primary_url
    assert review_by_title["Study: caregiver food insecurity during hospitalization of sick children"]["secondary_source_url"] == wrapper_url
    assert review_by_title["Study: caregiver food insecurity during hospitalization of sick children"]["source_traceability_role"] == "secondary_explainer_with_primary_reference"
    assert review_by_title["Study: caregiver food insecurity during hospitalization of sick children"]["pressure_type"] == "hospital-linked caregiver food insecurity"
    assert review_by_title["Study: caregiver food insecurity during hospitalization of sick children"]["primary_eligible"] == "false"
    assert not map_data.get("pressure_markers")
    assert any(
        record.get("source_title") == "Study: caregiver food insecurity during hospitalization of sick children"
        and record.get("reason") == "not_map_eligible"
        for record in map_data["excluded_records"]
    )


def test_food_line_bluesky_research_signal_avoids_duplicate_attribution_and_keeps_public_url(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-10"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)

    research = _pressure_row(
        1,
        "Sports-betting study links legal access to lower food sufficiency",
        (
            "GamblingHarm.org reported on research that linked legal sports-betting access to lower food sufficiency "
            "among some U.S. households, especially adults without a college degree, adults ages 25 to 44, and non-white adults."
        ),
        family="policy_research",
        state="US",
        source_type="manual",
        publisher="GamblingHarm.org",
    )
    research["location_name"] = "United States"
    research["url"] = "https://gamblingharm.org/legal-sports-betting-food-insecurity-study/"
    research["primary_source_url"] = "https://www.nber.org/papers/example-food-sufficiency-study"
    research["secondary_source_url"] = research["url"]
    research["source_traceability_role"] = "secondary_explainer_with_primary_reference"

    background = _row(2, "economic_data", "US", title="USDA context", summary="USDA food security context.", source_type="manual", publisher="USDA")
    background["location_name"] = "United States"

    p.write_text(json.dumps([research, background], indent=2), encoding="utf-8")

    result = run_food_line_dispatch(tmp_path, date, generate_audio=False)

    assert result["selected_lead_source_role"] == "research_signal"
    assert result["bluesky_post_ready"] is True
    assert result["bluesky_post_text"]
    assert len(result["bluesky_post_text"]) <= 300
    assert "GamblingHarm.org reported that GamblingHarm.org reported" not in result["bluesky_post_text"]
    assert "reported on research linking legal sports-betting access to lower food sufficiency among some U.S. households" in result["bluesky_post_text"]
    assert "pantry-demand story" not in result["bluesky_post_text"]
    assert "research_signal" not in result["bluesky_post_text"]
    assert "data_anchor_signal" not in result["bluesky_post_text"]
    assert "institutional_context_signal" not in result["bluesky_post_text"]
    assert "pressure_signal" not in result["bluesky_post_text"]
    assert "source_role" not in result["bluesky_post_text"]
    assert "evidence_level" not in result["bluesky_post_text"]
    assert result["public_url"] in result["bluesky_post_text"]
    prefix = result["bluesky_post_text"].split(result["public_url"], 1)[0].rstrip()
    assert not prefix.endswith("pa")
    assert not prefix.endswith("food-pressure pa")
    assert prefix.endswith(".") or prefix.endswith("...")


def test_food_line_bluesky_research_signal_drops_second_sentence_before_clipping_first(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-10"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)

    research = _pressure_row(
        1,
        "Sports-betting study links legal access to lower food sufficiency",
        (
            "GamblingHarm.org reported on research that linked legal sports-betting access to lower food sufficiency "
            "among some U.S. households, especially adults without a college degree, adults ages 25 to 44, and non-white adults."
        ),
        family="policy_research",
        state="US",
        source_type="manual",
        publisher="GamblingHarm.org",
    )
    research["location_name"] = "United States"
    research["url"] = "https://gamblingharm.org/legal-sports-betting-food-insecurity-study/"

    p.write_text(json.dumps([research], indent=2), encoding="utf-8")

    result = run_food_line_dispatch(tmp_path, date, generate_audio=False)

    assert result["selected_lead_source_role"] == "research_signal"
    assert result["bluesky_post_text"]
    assert len(result["bluesky_post_text"]) <= 300
    assert "reported on research linking legal sports-betting access to lower food sufficiency among some U.S. households." in result["bluesky_post_text"]
    assert "household financial stress" not in result["bluesky_post_text"]
    assert result["public_url"] in result["bluesky_post_text"]
    prefix = result["bluesky_post_text"].split(result["public_url"], 1)[0].rstrip()
    assert not prefix.endswith("pa")
    assert prefix.endswith(".") or prefix.endswith("...")


def test_food_line_june_17_national_context_limitations_and_source_table_evidence_fallback(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-17"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)

    stateline = _pressure_row(
        1,
        "More Americans are hungry in the face of federal cuts, rising grocery prices",
        "National food insecurity pressure is rising as federal cuts and grocery prices strain households.",
        family="local_news",
        state="US",
        source_type="manual",
        publisher="Stateline",
    )
    stateline.update(
        {
            "source_record_id": "stateline-hungry-federal-cuts-grocery-prices-20260617",
            "url": "https://stateline.org/2026/06/17/more-americans-are-hungry-in-the-face-of-federal-cuts-rising-grocery-prices/",
            "published_at": "2026-06-17T00:00:00Z",
            "retrieved_at": "2026-06-18T22:15:36Z",
            "summary_or_snippet": "National food insecurity pressure is rising as federal cuts and grocery prices strain households.",
            "evidence_text": "National food insecurity pressure is rising as federal cuts and grocery prices strain households.",
            "evidence_text_basis": "manual_review",
            "source_family": "local_news",
            "state": "US",
            "location_name": "United States",
            "location_scope": "national",
            "source_purpose": "current_news",
            "primary_source_url": "https://stateline.org/2026/06/17/more-americans-are-hungry-in-the-face-of-federal-cuts-rising-grocery-prices/",
            "source_traceability_role": "article_url",
            "issue_tags": ["food insecurity", "federal cuts", "grocery prices"],
            "map_category": "acute strain / service disruption",
            "pressure_signal": True,
            "pressure_type": "household hardship",
            "pressure_summary": "National food insecurity pressure is rising as federal cuts and grocery prices strain households.",
            "evidence_level": "direct reported hardship",
            "source_role": "daily_signal",
        }
    )

    wsu = _pressure_row(
        2,
        "Study: Food security varies widely across U.S. ethnic groups",
        "University research shows food security varies widely across U.S. ethnic groups, providing lower-priority context for the national signal.",
        family="policy_research",
        state="US",
        source_type="manual",
        publisher="Washington State University",
    )
    wsu.update(
        {
            "source_record_id": "wsu-food-security-ethnic-groups-20260617",
            "url": "https://news.wsu.edu/press-release/2026/06/17/study-food-security-varies-widely-across-u-s-ethnic-groups/",
            "published_at": "2026-06-17T00:00:00Z",
            "retrieved_at": "2026-06-18T22:15:36Z",
            "summary_or_snippet": "University research shows food security varies widely across U.S. ethnic groups, providing lower-priority context for the national signal.",
            "evidence_text": "University research shows food security varies widely across U.S. ethnic groups, providing lower-priority context for the national signal.",
            "evidence_text_basis": "manual_review",
            "source_family": "policy_research",
            "state": "US",
            "location_name": "United States",
            "location_scope": "national",
            "source_purpose": "research_report",
            "primary_source_url": "https://news.wsu.edu/press-release/2026/06/17/study-food-security-varies-widely-across-u-s-ethnic-groups/",
            "source_traceability_role": "article_url",
            "issue_tags": [],
            "map_category": "context / monitoring only",
            "pressure_signal": True,
            "pressure_type": "household food insecurity data signal",
            "pressure_summary": "University research shows food security varies widely across U.S. ethnic groups, providing lower-priority context for the national signal.",
            "evidence_level": "research context",
            "source_role": "research_signal",
            "map_eligible": False,
            "pressure_verification_status": "source_text_verified",
        }
    )

    p.write_text(json.dumps([stateline, wsu], indent=2), encoding="utf-8")

    result = run_food_line_dispatch(tmp_path, date, generate_audio=False)

    edition_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "index.html").read_text(encoding="utf-8")
    source_table_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "source_table.html").read_text(encoding="utf-8")
    claim_ledger_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "claim_ledger.html").read_text(encoding="utf-8")
    manifest = json.loads((tmp_path / "output" / "site" / "food-line" / "editions" / date / "edition_manifest.json").read_text(encoding="utf-8"))
    review_rows = list(csv.DictReader((tmp_path / "output" / "review" / "food-line" / date / "pressure_review.csv").open(encoding="utf-8")))
    review_by_title = {row["source_title"]: row for row in review_rows}
    soup = BeautifulSoup(source_table_html, "html.parser")
    table = soup.find("table")
    headers = [th.get_text(" ", strip=True) for th in table.find_all("th")]
    rows = []
    for tr in table.find_all("tr")[1:]:
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if cells:
            rows.append(dict(zip(headers, cells)))
    row_by_title = {row["Title"]: row for row in rows}

    assert result["public_rendered"] is True
    assert result["selected_lead_source_role"] == "daily_signal"
    assert result["bluesky_post_ready"] is True
    assert "Stateline reports rising food insecurity pressure" in manifest["bluesky_post_text"]
    assert "Research / Context Signals" in edition_html
    assert "Study: Food security varies widely across U.S. ethnic groups" in edition_html
    assert "The source supports a national food-pressure signal" in food_line._food_line_claim_limitation(stateline)
    assert "The source supports a national research/context signal" in food_line._food_line_claim_limitation(wsu)
    local_row = _pressure_row(
        3,
        "Pantry demand rises in Roanoke",
        "A local pantry reported rising food-assistance demand.",
        family="local_news",
        state="VA",
        source_type="manual",
        publisher="Roanoke Times",
    )
    local_row["location_name"] = "Roanoke, VA"
    local_row["pressure_type"] = "demand strain"
    assert "Roanoke" in food_line._food_line_claim_limitation(local_row)
    assert "The source supports a national food-pressure signal" in claim_ledger_html
    assert "The source supports a national research/context signal" in claim_ledger_html
    assert row_by_title["More Americans are hungry in the face of federal cuts, rising grocery prices"]["What the source says"] != ""
    assert row_by_title["Study: Food security varies widely across U.S. ethnic groups"]["What the source says"] != ""
    assert review_by_title["Study: Food security varies widely across U.S. ethnic groups"]["primary_eligible"] == "false"
    research_section = edition_html.split("Research / Context Signals", 1)[1].split("Policy / Benefits Signals", 1)[0]
    policy_section = edition_html.split("Policy / Benefits Signals", 1)[1]
    assert "Study: Food security varies widely across U.S. ethnic groups" in research_section
    assert "Study: Food security varies widely across U.S. ethnic groups" not in policy_section


def test_food_line_public_inclusion_helpers_separate_lead_from_public_eligibility():
    wpde = _wpde_manual_source()
    tulsa = _tulsa_manual_source()
    policy = _wkrn_policy_access_source()
    resource_only = {
        "source_record_id": "food-drive-resource-only-20260612",
        "title": "Food drive to stock local shelves",
        "url": "https://example.com/food-drive",
        "publisher": "Example Charity",
        "published_at": "2026-06-12T12:00:00Z",
        "retrieved_at": "2026-06-12T12:00:00Z",
        "summary_or_snippet": "Food drive announcement with donation details only.",
        "evidence_text": "Food drive announcement with donation details only.",
        "evidence_text_basis": "manual_review",
        "source_type": "manual",
        "source_family": "food_bank_provider",
        "state": "OK",
        "location_name": "Tulsa, OK",
        "location_scope": "local",
        "source_purpose": "donation_page",
        "pressure_signal": False,
        "pressure_type": "context only",
        "pressure_reason": "resource-only / no pressure signal",
        "pressure_summary": "",
        "affected_groups": [],
        "evidence_level": "background context",
        "freshness_role": "fresh_daily_signal",
        "source_role": "resource_context",
        "map_category": "context / monitoring only",
        "map_eligible": False,
        "pressure_verification_status": "demoted_context",
    }

    rows = [dict(wpde), dict(tulsa), dict(policy), resource_only]
    previous_context = {
        "previous_edition_date": "2026-06-11",
        "lead_source_record_id": wpde["source_record_id"],
        "lead_canonical_url": wpde["url"],
    }
    food_line._annotate_food_line_primary_eligibility(rows, previous_context)
    for row in rows:
        row_id = str(row.get("source_record_id") or "").strip()
        row["qualifies_for_public_inclusion"] = food_line._food_line_qualifies_for_public_inclusion(row)
        row["public_inclusion_reason"] = food_line._food_line_public_inclusion_reason(row)
        row["public_inclusion_bucket"] = food_line._food_line_public_inclusion_bucket(
            row,
            is_lead=bool(row_id == wpde["source_record_id"]),
        )
        row["eligible_for_lead"] = bool(row.get("primary_eligible")) and bool(row.get("qualifies_for_public_inclusion"))
    classification_summary = food_line._annotate_food_line_candidate_review_fields(rows)

    assert rows[0]["primary_eligible"] is False
    assert food_line._food_line_qualifies_for_public_inclusion(rows[0]) is True
    assert rows[0]["public_claim_eligible"] is True
    assert rows[0]["review_status"] == "approved"
    assert rows[0]["eligible_for_lead"] is False
    assert classification_summary["public_claim_eligible_count"] == 3
    assert food_line._food_line_public_inclusion_bucket(rows[1]) == "included_as_provider_operations_signal"
    assert food_line._food_line_public_inclusion_bucket(rows[2]) == "included_as_policy_access_signal"
    assert food_line._food_line_qualifies_for_public_inclusion(resource_only) is False
    assert food_line._food_line_public_inclusion_reason(resource_only) == "resource-only / no pressure signal"


def test_food_line_qualified_but_not_public_count_warns_when_public_rows_are_omitted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    date = "2026-06-12"
    manual_path = _manual_path(tmp_path, date)
    manual_path.parent.mkdir(parents=True, exist_ok=True)
    manual_path.write_text(
        json.dumps([_wpde_manual_source(), _tulsa_manual_source(), _wkrn_policy_access_source()], indent=2),
        encoding="utf-8",
    )

    original_public_story_rows = food_line._food_line_public_story_rows

    def fake_public_story_rows(sources, primary_row, continuing_rows, *, edition_mode="current_update"):
        rows = original_public_story_rows(sources, primary_row, continuing_rows, edition_mode=edition_mode)
        return rows[:1]

    monkeypatch.setattr(food_line, "_food_line_public_story_rows", fake_public_story_rows)
    result = run_food_line_dispatch(tmp_path, date, generate_audio=False)

    assert result["qualified_but_not_public_count"] >= 1
    assert result["qualified_but_not_public_warning"]
    assert result["qualified_but_not_public_warning"].endswith(".")


def test_food_line_wpde_manual_seed_repairs_june_12_public_outputs(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-12"
    manual_path = _manual_path(tmp_path, date)
    manual_path.parent.mkdir(parents=True, exist_ok=True)
    manual_source = {
        "source_record_id": "wpde-grand-strand-food-insecurity-20260612",
        "title": "Grand Strand food providers say inflation is driving more families to pantries",
        "url": "https://wpde.com/news/local/new-data-show-food-insecurity-higher-than-during-covid-19-with-horry-county-at-14",
        "publisher": "WPDE / ABC 15",
        "published_at": "2026-06-12T21:58:00Z",
        "retrieved_at": "2026-06-12T21:58:00Z",
        "summary_or_snippet": "Food insecurity in Horry County is about 14 percent and about 20 percent of children are food insecure, while the Lowcountry Food Bank said some Conway distributions usually serving about 100 families had 185 and demand is climbing at pantries and mobile distributions.",
        "evidence_text": (
            "Grand Strand food providers say inflation is driving more families to pantries WPDE — Food insecurity is rising above levels seen during the COVID-19 pandemic. "
            "In Horry County, Feeding America’s most recent Map the Meal Gap report shows about 14 percent of residents are food insecure and about 20 percent of Horry County’s children are considered food insecure. "
            "The Lowcountry Food Bank said some mobile distributions in Conway that usually served about 100 families had 185. "
            "Inflation and higher costs are making it harder for families to afford food and for food banks to source it. "
            "ABC 15 is teaming up with Feeding America for Sinclair Cares: Summer Hunger Relief, encouraging donations to help provide food for kids during the summer. "
            "More information and donations are available at sinclaircares.com."
        ),
        "evidence_text_basis": "manual_review",
        "source_type": "manual",
        "source_family": "local_news",
        "state": "SC",
        "location_name": "Horry County",
        "location_scope": "state_local",
        "source_purpose": "current_news",
        "primary_source_url": "https://wpde.com/news/local/new-data-show-food-insecurity-higher-than-during-covid-19-with-horry-county-at-14",
        "source_traceability_role": "article_url",
        "pressure_signal": True,
        "pressure_type": "demand strain",
        "pressure_reason": "Matched demand strain; the article reports higher food insecurity, rising pantry demand, and mobile distributions serving 185 families.",
        "pressure_summary": "Food insecurity in Horry County is about 14 percent and about 20 percent of children are food insecure, while the Lowcountry Food Bank said some Conway distributions usually serving about 100 families had 185 and inflation was making food harder to afford and source.",
        "affected_groups": ["children", "low-income households", "pantry clients"],
        "evidence_level": "news report",
        "freshness_role": "fresh_daily_signal",
        "source_role": "local_signal",
        "map_category": "elevated demand",
        "map_eligible": True,
        "pressure_verification_status": "source_text_verified",
    }
    tulsa_source = _tulsa_manual_source()
    policy_source = _wkrn_policy_access_source()
    manual_path.write_text(json.dumps([manual_source, tulsa_source, policy_source], indent=2), encoding="utf-8")

    result = run_food_line_dispatch(tmp_path, date, generate_audio=False)

    edition_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "index.html").read_text(encoding="utf-8")
    source_table_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "source_table.html").read_text(encoding="utf-8")
    claim_ledger_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "claim_ledger.html").read_text(encoding="utf-8")
    manifest = json.loads((tmp_path / "output" / "site" / "food-line" / "editions" / date / "edition_manifest.json").read_text(encoding="utf-8"))

    assert result["ok"] is True
    assert result["edition_mode"] == "current_update"
    assert result["public_signal_count"] == 3
    assert result["qualified_but_not_public_count"] == 0
    assert result["audio_generated"] is False
    assert not (tmp_path / "output" / "site" / "food-line" / "audio" / f"{date}.mp3").exists()
    assert "No current update" not in edition_html
    assert "WPDE / ABC 15" in source_table_html
    assert "Tulsa Flyer" in source_table_html
    assert "WKRN" in source_table_html
    assert "Horry County" in source_table_html
    assert "Tulsa, OK" in source_table_html
    assert "Tennessee" in source_table_html
    assert "Today’s Food Line found 3 reported pressure signals." in edition_html
    assert "In Horry County, South Carolina, food providers reported rising pantry demand and child food insecurity." in edition_html
    assert "In Tulsa, Oklahoma, higher diesel costs are reducing food-bank meal capacity." in edition_html
    assert "In Tennessee, WKRN reported that SNAP enrollment fell by more than 100,000 people, though the source does not prove why people left the program." in edition_html
    assert "Horry County food providers report rising pantry demand" in edition_html
    assert "Eastern Oklahoma food bank says diesel costs are reducing meal capacity" in edition_html
    assert "Tennessee SNAP enrollment dropped by more than 100,000" in edition_html
    assert "Additional qualifying signals are grouped below by type." in edition_html
    assert "No additional Food Line signals qualified today." not in edition_html
    assert "Claim supported:" not in edition_html
    assert "14 percent" in claim_ledger_html
    assert "20 percent" in claim_ledger_html
    assert "185" in claim_ledger_html
    assert "Tulsa Flyer" in claim_ledger_html
    assert "$24,000–$26,000" in claim_ledger_html
    assert "$12,000–$14,000" in claim_ledger_html
    assert "reducing meal capacity" in claim_ledger_html.lower()
    assert "WKRN" in claim_ledger_html
    assert "fuel-cost pressure" not in claim_ledger_html.lower()
    assert "100,000" in claim_ledger_html
    assert "Sinclair Cares" not in claim_ledger_html
    assert "More information and donations are available at" not in claim_ledger_html
    assert "Comment with Bubbles" not in claim_ledger_html
    assert "food insecurity in horry county is about 14 percent" in claim_ledger_html.lower()
    assert "food insecurity in horry county is about 14 percent" in edition_html.lower()
    assert "Tulsa Flyer" in edition_html
    assert "children, summer meal recipients, and food-bank clients" in source_table_html
    assert "WKRN" in edition_html
    assert "Policy / Benefits Signals" in edition_html
    assert "Provider / Operations Signals" in edition_html
    assert manifest["public_signal_count"] == 3
    assert manifest["claim_count"] == 3
    assert manifest["lead_source_record_id"] == "wpde-grand-strand-food-insecurity-20260612"
    assert manifest["qualified_but_not_public_count"] == 0


def test_food_line_discovery_gap_summary_warns_for_unreviewed_likely_qualifying_candidate(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-12"
    path = _manual_path(tmp_path, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    manual_source = _wpde_manual_source()
    path.write_text(json.dumps([manual_source], indent=2), encoding="utf-8")
    _write_food_line_discovery_gap_report(
        tmp_path,
        date,
        [
            {
                "title": manual_source["title"],
                "url": manual_source["url"],
                "classification": "likely_qualifying",
                "score": 5,
                "reason": "food bank demand; local news domain",
                "known_status": "known_domain_new_article",
            },
            {
                "title": "Another qualifying story",
                "url": "https://example.com/2026/06/12/another-qualifying-story",
                "classification": "likely_qualifying",
                "score": 4,
                "reason": "food pantry demand; local news domain",
                "known_status": "known_domain_new_article",
            },
        ],
    )

    result = run_food_line_dispatch(tmp_path, date, include_discovery_gap_summary=True)
    gap_summary = result["discovery_gap_check"]
    manifest = json.loads((tmp_path / "output" / "site" / "food-line" / "editions" / date / "edition_manifest.json").read_text(encoding="utf-8"))

    assert result["ok"] is True
    assert gap_summary["run"] is True
    assert gap_summary["report_found"] is True
    assert gap_summary["likely_qualifying_count"] == 2
    assert gap_summary["blocking_likely_qualifying_count"] == 1
    assert gap_summary["unresolved_likely_qualifying_count"] == 0
    assert gap_summary["unreviewed_likely_qualifying_count"] == 1
    assert gap_summary["warning"]
    assert gap_summary["warning"].endswith(".")
    assert gap_summary["report_markdown_path"].endswith(".md")
    assert "Food Line discovery gap check found 1 traceable likely qualifying candidate not included in this edition." in gap_summary["warning"]
    assert result["discovery_gap_likely_qualifying_count"] == 2
    assert result["discovery_gap_blocking_likely_qualifying_count"] == 1
    assert result["discovery_gap_unresolved_likely_qualifying_count"] == 0
    assert result["discovery_gap_unreviewed_likely_qualifying_count"] == 1
    assert result["discovery_gap_warning"] == gap_summary["warning"]
    assert manifest["discovery_gap_check"]["unreviewed_likely_qualifying_count"] == 1
    assert manifest["discovery_gap_warning"] == gap_summary["warning"]
    assert manifest["discovery_gap_report_path"].endswith("discovery_gap_report.json")
    assert not (tmp_path / "bluefern-dispatches-pages").exists()


def test_food_line_discovery_gap_summary_ignores_duplicates_and_resource_only_candidates(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-13"
    _write_food_line_discovery_gap_report(
        tmp_path,
        date,
        [
            {
                "title": "Existing known article",
                "url": "https://example.com/2026/06/13/existing-known-article",
                "classification": "duplicate_or_known",
                "score": 3,
                "reason": "already included",
                "known_status": "already_included",
            },
            {
                "title": "Summer meal resource",
                "url": "https://example.com/2026/06/13/summer-meal-resource",
                "classification": "likely_resource_only",
                "score": 1,
                "reason": "resource only",
                "known_status": "unknown_domain_new_article",
            },
        ],
    )

    result = run_food_line_dispatch(tmp_path, date, include_discovery_gap_summary=True)
    gap_summary = result["discovery_gap_check"]

    assert result["ok"] is True
    assert gap_summary["run"] is True
    assert gap_summary["report_found"] is True
    assert gap_summary["likely_qualifying_count"] == 0
    assert gap_summary["unreviewed_likely_qualifying_count"] == 0
    assert gap_summary["unresolved_high_confidence_direct_pressure_count"] == 0
    assert gap_summary["warning"] == ""
    assert result["discovery_gap_warning"] == ""
    assert result["discovery_gap_likely_qualifying_count"] == 0
    assert result["discovery_gap_unreviewed_likely_qualifying_count"] == 0
    assert result["discovery_gap_unresolved_high_confidence_direct_pressure_count"] == 0


def test_food_line_discovery_gap_summary_treats_unresolved_google_news_likely_candidates_as_manual_review_only(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-17"
    _write_food_line_discovery_gap_report(
        tmp_path,
        date,
        [
            {
                "title": "Google News only likely candidate",
                "url": "https://news.google.com/rss/articles/CBMiUNRESOLVED?oc=5",
                "google_news_url": "https://news.google.com/rss/articles/CBMiUNRESOLVED?oc=5",
                "resolved_url": "",
                "url_resolution_status": "resolution_skipped_max_candidates",
                "reason": "title-only pressure match",
                "known_status": "unknown_domain_new_article",
                "classification": "likely_qualifying",
                "publication_blocking_candidate": False,
            }
        ],
        likely_qualifying_count=1,
        blocking_likely_qualifying_count=0,
        unresolved_likely_qualifying_count=1,
    )

    result = run_food_line_dispatch(tmp_path, date, include_discovery_gap_summary=True)
    gap_summary = result["discovery_gap_check"]

    assert result["ok"] is True
    assert gap_summary["run"] is True
    assert gap_summary["likely_qualifying_count"] == 1
    assert gap_summary["blocking_likely_qualifying_count"] == 0
    assert gap_summary["unresolved_likely_qualifying_count"] == 1
    assert gap_summary["unreviewed_likely_qualifying_count"] == 0
    assert gap_summary["unresolved_high_confidence_direct_pressure_count"] == 0
    assert gap_summary["public_no_qualifying_update_validated"] is True
    assert "manual review only" in gap_summary["warning"]
    assert result["discovery_gap_blocking_likely_qualifying_count"] == 0
    assert result["discovery_gap_unresolved_likely_qualifying_count"] == 1
    assert result["discovery_gap_unresolved_high_confidence_direct_pressure_count"] == 0


def test_food_line_discovery_gap_summary_blocks_unresolved_high_confidence_direct_pressure_candidates(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-18"
    _write_food_line_discovery_gap_report(
        tmp_path,
        date,
        [
            {
                "title": "Food bank demand surges in north Omaha as SNAP cuts and rising grocery costs strain families",
                "url": "https://news.google.com/rss/articles/CBMiNORTH?oc=5",
                "google_news_url": "https://news.google.com/rss/articles/CBMiNORTH?oc=5",
                "resolved_url": "",
                "url_resolution_status": "resolution_skipped_max_candidates",
                "reason": "food bank pressure; direct pressure signal: food bank demand, SNAP cuts, rising grocery costs",
                "known_status": "unknown_domain_new_article",
                "classification": "likely_qualifying",
                "publication_blocking_candidate": False,
                "score": 11,
                "review_traceability_status": "unresolved_google_news",
            }
        ],
        likely_qualifying_count=1,
        blocking_likely_qualifying_count=0,
        unresolved_likely_qualifying_count=1,
    )

    summary = food_line._food_line_discovery_gap_summary(tmp_path, date, [])

    assert summary["run"] is True
    assert summary["blocking_likely_qualifying_count"] == 0
    assert summary["unresolved_likely_qualifying_count"] == 1
    assert summary["unresolved_high_confidence_direct_pressure_count"] == 1
    assert "north Omaha" in " ".join(summary["unresolved_high_confidence_direct_pressure_titles"])
    assert summary["public_no_qualifying_update_validated"] is False
    assert "high-confidence direct-pressure" in summary["warning"]


def test_food_line_discovery_gap_summary_missing_report_does_not_fail(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-14"
    path = _manual_path(tmp_path, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    manual_source = _wpde_manual_source()
    path.write_text(json.dumps([manual_source], indent=2), encoding="utf-8")

    result = run_food_line_dispatch(tmp_path, date, include_discovery_gap_summary=True)

    assert result["ok"] is True
    assert result["discovery_gap_check"]["run"] is False
    assert result["discovery_gap_check"]["report_found"] is False
    assert result["discovery_gap_check"]["likely_qualifying_count"] == 0
    assert result["discovery_gap_check"]["unreviewed_likely_qualifying_count"] == 0
    assert result["discovery_gap_warning"] == ""
    assert result["discovery_gap_report_path"].endswith("discovery_gap_report.json")
    assert result["discovery_gap_report_markdown_path"].endswith("discovery_gap_report.md")
    assert not (tmp_path / "bluefern-dispatches-pages").exists()


def test_food_line_discovery_gap_summary_marks_validated_when_no_unreviewed_candidates(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-15"
    _write_food_line_discovery_gap_report(tmp_path, date, [])

    summary = food_line._food_line_discovery_gap_summary(tmp_path, date, [])

    assert summary["run"] is True
    assert summary["public_no_qualifying_update_validated"] is True


def test_food_line_discovery_gap_summary_blocks_validation_when_traceable_candidates_remain(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-16"
    _write_food_line_discovery_gap_report(
        tmp_path,
        date,
        [
            {
                "title": "Qualifying candidate",
                "url": "https://example.com/2026/06/16/qualifying-candidate",
                "classification": "likely_qualifying",
                "score": 4,
                "reason": "food bank demand; local news domain",
                "known_status": "known_domain_new_article",
            }
        ],
    )

    summary = food_line._food_line_discovery_gap_summary(tmp_path, date, [])

    assert summary["run"] is True
    assert summary["public_no_qualifying_update_validated"] is False


def test_food_line_collect_reports_rejected_news_reasons(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    registry_dir = tmp_path / "data" / "dispatches" / "food-line"
    registry_dir.mkdir(parents=True, exist_ok=True)
    registry_dir.joinpath("source_registry.json").write_text("[]", encoding="utf-8")
    registry_dir.joinpath("pressure_source_registry.json").write_text(
        json.dumps(
            [
                {
                    "source_id": "test-rss-feed",
                    "source_name": "Test RSS Feed",
                    "publisher": "Test Publisher",
                    "source_type": "rss",
                    "url": "https://example.com/test-rss",
                    "source_family": "national_news",
                    "state": "US",
                    "location_name": "United States",
                    "location_scope": "national",
                    "source_role_allowed": "pressure_evidence",
                    "pressure_required": True,
                    "freshness_mode": "pressure",
                    "max_age_days": 7,
                    "positive_keywords": ["food bank", "hunger", "SNAP"],
                    "negative_keywords": ["recipe", "menu", "restaurant review"],
                    "affected_group_keywords": ["families", "children"],
                    "enabled": True,
                    "notes": "Test RSS feed for rejected item diagnostics.",
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )

    rss_payload = b"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<rss version=\"2.0\"><channel>
  <item><title>Restaurant announces new menu</title><link>https://example.com/menu</link><pubDate>Mon, 03 Jun 2026 12:00:00 GMT</pubDate><description>Restaurant review and menu update.</description></item>
  <item><title>Food bank sees rising demand from families</title><link>https://example.com/demand</link><pubDate>Mon, 03 Jun 2026 12:00:00 GMT</pubDate><description>Food bank demand increased and pantry lines grew.</description></item>
</channel></rss>"""

    result = food_line.collect_food_line_auto_sources(tmp_path, date, fetcher=lambda _url, timeout=15: rss_payload)
    assert result["source_count"] == 1
    assert result["rejected_news_count"] == 1
    assert result["rejected_news_reasons"]
    assert any("excluded by negative filter" in reason for reason in result["rejected_news_reasons"])


def test_food_line_ap_article_is_not_rejected_by_menu_negative_filter(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-25"
    _write_pressure_registry(
        tmp_path,
        [
            {
                "source_id": "ap-food-bank-cuts",
                "source_name": "Funding cuts threaten to deepen hunger crisis as rising costs send more families to food banks",
                "publisher": "Associated Press",
                "source_type": "page",
                "url": "https://apnews.com/article/665c19251b5d83bbed45a29958f79609",
                "source_family": "national_news",
                "state": "US",
                "location_name": "United States",
                "location_scope": "national",
                "source_role_allowed": "pressure_evidence",
                "pressure_required": True,
                "freshness_mode": "pressure",
                "max_age_days": 14,
                "positive_keywords": ["food bank", "food banks", "hunger", "SNAP", "pantry", "families", "funding cuts", "rising costs"],
                "negative_keywords": ["recipe", "restaurant", "menu"],
                "affected_group_keywords": ["families", "children", "SNAP households"],
                "enabled": True,
                "notes": "AP pressure candidate should not be rejected by navigation boilerplate.",
            }
        ],
    )

    payload = b"""
    <html>
      <head>
        <title>Funding cuts threaten to deepen hunger crisis as rising costs send more families to food banks</title>
        <meta property="article:published_time" content="2026-06-25T13:00:00Z" />
        <meta name="description" content="AP reports that food banks are seeing more families seek help as SNAP pressure and rising costs deepen hunger." />
      </head>
      <body>
        <nav>Menu World U.S. Politics</nav>
        <article>
          <p>Funding cuts threaten to deepen the hunger crisis as rising costs send more families to food banks.</p>
          <p>Food banks say SNAP pressure and pantry demand are increasing for low-income households.</p>
        </article>
      </body>
    </html>
    """

    result = food_line.collect_food_line_auto_sources(tmp_path, date, fetcher=lambda _url, timeout=15: payload)

    assert result["source_count"] == 1
    assert result["rejected_news_count"] == 0
    row = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "sources" / date / "auto_sources.json").read_text(encoding="utf-8"))[0]
    assert row["pressure_signal"] is True
    assert "negative filter menu ignored" in row["pressure_reason"]


def test_food_line_article_with_heavy_site_chrome_is_not_rejected_by_menu_negative_filter():
    pressure = food_line.evaluate_food_line_pressure(
        {
            "title": "Food pantries overwhelmed as thousands across Pittsburgh miss SNAP payments",
            "summary_or_snippet": (
                "Food pantries overwhelmed as thousands across Pittsburgh miss SNAP payments | Pittsburgh Post-Gazette "
                "MENU SUBSCRIBE LOGIN REGISTER"
            ),
            "url": "https://www.post-gazette.com/news/social-services/2025/11/03/pittsburgh-allegheny-snap-food-bank-pantry/stories/202511030070",
            "evidence_text": (
                "Food pantries overwhelmed as thousands across Pittsburgh miss SNAP payments. "
                "As hundreds of thousands continue to go without SNAP assistance amid the ongoing shutdown, overwhelming local food pantries. "
                "MENU SUBSCRIBE LOGIN REGISTER"
            ),
            "evidence_text_basis": "page_text_excerpt",
            "source_family": "local_news",
            "source_type": "page",
            "state": "PA",
            "published_at": "2025-11-03T15:35:37-05:00",
        },
        edition_date="2026-06-25",
        pressure_required=True,
        positive_keywords=["food pantries", "SNAP", "food bank"],
        negative_keywords=["menu"],
    )

    assert pressure["rejected"] is False
    assert pressure["rejection_reason"] == ""


def test_food_line_menu_navigation_page_is_still_rejected_by_menu_negative_filter():
    pressure = food_line.evaluate_food_line_pressure(
        {
            "title": "Weekend menu",
            "summary_or_snippet": "Menu update for weekend service.",
            "url": "https://example.com/menu",
            "evidence_text": "Menu Menu highlights for weekend service.",
            "evidence_text_basis": "page_text_excerpt",
            "source_family": "national_news",
            "source_type": "page",
            "state": "US",
            "published_at": "2026-06-25T12:00:00Z",
        },
        edition_date="2026-06-25",
        pressure_required=True,
        positive_keywords=["food bank", "hunger", "SNAP"],
        negative_keywords=["menu"],
    )

    assert pressure["pressure_signal"] is False
    assert pressure["rejected"] is True
    assert pressure["rejection_reason"] == "excluded by negative filter: menu"


@pytest.mark.parametrize(
    ("row", "positive_keywords", "expected_reason", "expected_rejected"),
    [
        (
            {
                "title": "Find food near you",
                "summary_or_snippet": "Menu Find food and pantry locator.",
                "url": "https://www.example.org/find-food",
                "evidence_text": "Menu Find food and pantry locator for households seeking help.",
                "evidence_text_basis": "page_text_excerpt",
                "source_family": "food_bank_provider",
                "source_type": "page",
                "state": "US",
                "published_at": "2026-06-25T12:00:00Z",
            },
            ["find food", "pantry"],
            "resource-only / no pressure signal",
            False,
        ),
        (
            {
                "title": "SNAP Food Benefits",
                "summary_or_snippet": "Menu Oregon SNAP program page.",
                "url": "https://www.oregon.gov/odhs/food/Pages/snap.aspx",
                "evidence_text": "Menu Oregon SNAP benefits program information and application details.",
                "evidence_text_basis": "page_text_excerpt",
                "source_family": "state_official",
                "source_type": "page",
                "state": "OR",
                "published_at": "2026-06-25T12:00:00Z",
            },
            ["SNAP", "benefits"],
            "official/provider page lacks current dated pressure evidence",
            True,
        ),
        (
            {
                "title": "Supplemental Nutrition Assistance Program SNAP",
                "summary_or_snippet": "Menu SNAP program overview and policy context.",
                "url": "https://frac.org/programs/supplemental-nutrition-assistance-program-snap",
                "evidence_text": "Menu SNAP program overview, eligibility context, and policy background.",
                "evidence_text_basis": "page_text_excerpt",
                "source_family": "policy_research",
                "source_type": "page",
                "state": "US",
                "published_at": "2026-06-25T12:00:00Z",
            },
            ["SNAP"],
            "evergreen context / no current pressure signal",
            True,
        ),
    ],
)
def test_food_line_menu_rejects_use_clearer_non_article_reasons(
    row: dict,
    positive_keywords: list[str],
    expected_reason: str,
    expected_rejected: bool,
):
    pressure = food_line.evaluate_food_line_pressure(
        row,
        edition_date="2026-06-25",
        pressure_required=True,
        positive_keywords=positive_keywords,
        negative_keywords=["menu"],
    )

    assert pressure["pressure_signal"] is False
    assert pressure["rejected"] is expected_rejected
    assert pressure["rejection_reason"] == expected_reason


def test_food_line_disabled_pressure_sources_are_skipped(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    _write_pressure_registry(
        tmp_path,
        [
            {
                "source_id": "disabled-pressure-feed",
                "source_name": "Disabled Pressure Feed",
                "publisher": "Test Publisher",
                "source_type": "rss",
                "url": "https://example.com/disabled",
                "source_family": "national_news",
                "state": "US",
                "location_name": "United States",
                "location_scope": "national",
                "source_role_allowed": "pressure_evidence",
                "pressure_required": True,
                "freshness_mode": "pressure",
                "max_age_days": 7,
                "positive_keywords": ["food bank", "SNAP"],
                "negative_keywords": ["recipe"],
                "affected_group_keywords": ["families"],
                "enabled": False,
                "notes": "Disabled candidate should never be collected.",
            },
            {
                "source_id": "enabled-pressure-feed",
                "source_name": "Enabled Pressure Feed",
                "publisher": "Test Publisher",
                "source_type": "rss",
                "url": "https://example.com/enabled",
                "source_family": "national_news",
                "state": "US",
                "location_name": "United States",
                "location_scope": "national",
                "source_role_allowed": "pressure_evidence",
                "pressure_required": True,
                "freshness_mode": "pressure",
                "max_age_days": 7,
                "positive_keywords": ["food bank", "SNAP"],
                "negative_keywords": ["recipe"],
                "affected_group_keywords": ["families"],
                "enabled": True,
                "notes": "Enabled test feed.",
            },
        ],
    )
    rss_payload = _rss_payload(
        [
            {
                "title": "Food bank sees rising demand from families",
                "link": "https://example.com/demand",
                "description": "Food bank demand increased and pantry lines grew.",
            }
        ]
    )
    fetch_calls: list[str] = []

    def fetcher(url: str, timeout: int = 15) -> bytes:  # noqa: ARG001
        fetch_calls.append(url)
        return rss_payload

    result = food_line.collect_food_line_auto_sources(tmp_path, date, fetcher=fetcher)
    assert fetch_calls == ["https://example.com/enabled"]
    assert result["source_count"] == 1
    assert result["collected_source_count_by_source_id"] == {"enabled-pressure-feed": 1}
    assert "disabled-pressure-feed" not in result["collected_source_count_by_source_id"]


def test_food_line_verified_rss_source_produces_pressure_records(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    _write_pressure_registry(
        tmp_path,
        [
            {
                "source_id": "kff-health-news-rss",
                "source_name": "KFF Health News RSS",
                "publisher": "KFF Health News",
                "source_type": "rss",
                "url": "https://kffhealthnews.org/RSS.aspx",
                "source_family": "national_news",
                "state": "US",
                "location_name": "United States",
                "location_scope": "national",
                "source_role_allowed": "pressure_evidence",
                "pressure_required": True,
                "freshness_mode": "pressure",
                "max_age_days": 7,
                "positive_keywords": ["food bank", "SNAP", "food insecurity", "hunger"],
                "negative_keywords": ["recipe", "restaurant", "menu"],
                "affected_group_keywords": ["families", "children"],
                "enabled": True,
                "notes": "Verified RSS feed used for pressure collection tests.",
            }
        ],
    )
    rss_payload = _rss_payload(
        [
            {
                "title": "Food bank sees rising demand from families",
                "link": "https://example.com/demand",
                "description": "Food bank demand increased and pantry lines grew.",
            }
        ]
    )
    result = food_line.collect_food_line_auto_sources(tmp_path, date, fetcher=lambda _url, timeout=15: rss_payload)
    rows = json.loads(Path(result["auto_sources_path"]).read_text(encoding="utf-8"))
    assert result["source_count"] == 1
    assert result["collected_source_count_by_source_id"] == {"kff-health-news-rss": 1}
    assert rows and rows[0]["pressure_signal"] is True
    assert rows[0]["pressure_summary"]
    assert rows[0]["pressure_summary"] not in GENERIC_PRESSURE_SUMMARIES
    assert "rising food-assistance demand" in rows[0]["pressure_summary"].lower()
    assert rows[0]["pressure_verification_status"] == "source_text_verified"
    assert rows[0]["evidence_text_basis"] == "rss_item_text"
    assert rows[0]["extraction_quality"] == "high"
    assert rows[0]["expected_text_basis"] == "rss_summary"
    assert rows[0]["pressure_verification_required"] is True
    assert rows[0]["evidence_text"]
    assert rows[0]["pressure_match_terms"]
    audit = json.loads(Path(result["collector_audit_path"]).read_text(encoding="utf-8"))
    assert audit and audit[0]["fetched"] is True
    assert audit[0]["accepted_pressure_count"] == 1
    assert audit[0]["extraction_basis_used"] == ["rss_item_text"]


def test_food_line_generic_page_source_is_demoted_without_verified_evidence(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    _write_pressure_registry(
        tmp_path,
        [
            {
                "source_id": "generic-page-feed",
                "source_name": "Generic Page Feed",
                "publisher": "Test Publisher",
                "source_type": "page",
                "url": "https://example.com/generic-page",
                "source_family": "food_bank_provider",
                "state": "TX",
                "location_name": "Dallas, TX",
                "location_scope": "state_local",
                "source_role_allowed": "pressure_evidence",
                "pressure_required": True,
                "pressure_verification_required": True,
                "freshness_mode": "pressure",
                "max_age_days": 7,
                "extraction_quality": "low",
                "expected_text_basis": "page_text",
                "positive_keywords": ["demand", "shortage", "waitlist", "hours", "capacity", "inventory"],
                "negative_keywords": ["recipe", "restaurant", "menu", "festival", "gala", "donation"],
                "affected_group_keywords": ["families", "children"],
                "enabled": True,
                "notes": "Generic page used to ensure registry defaults do not create pressure.",
                "summary_fallback": "Food bank demand increased and pantry lines grew.",
            }
        ],
    )
    html_payload = b"""<!doctype html>
<html>
<head><title>Generic Food Bank Page</title><meta name=\"description\" content=\"Community updates and general information.\"></head>
<body><p>Community bulletin with updates for members today.</p></body></html>"""
    result = food_line.collect_food_line_auto_sources(tmp_path, date, fetcher=lambda _url, timeout=15: html_payload)
    rows = json.loads(Path(result["auto_sources_path"]).read_text(encoding="utf-8"))
    assert result["source_count"] == 1
    assert result["pressure_signal_count"] == 0
    assert result["pressure_demoted_unverified_count"] == 1
    assert result["pressure_registry_only_count"] == 0
    assert rows[0]["pressure_signal"] is False
    assert rows[0]["pressure_verification_status"] == "demoted_context"
    assert rows[0]["evidence_text_basis"] == "page_text_excerpt"
    assert rows[0]["extraction_quality"] == "low"
    assert rows[0]["expected_text_basis"] == "page_text"
    assert rows[0]["evidence_text"]
    assert rows[0]["pressure_match_terms"] == []
    assert rows[0]["pressure_summary"] == ""


def test_food_line_rejects_recipe_lifestyle_and_charity_items(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    _write_pressure_registry(
        tmp_path,
        [
            {
                "source_id": "test-rejection-feed",
                "source_name": "Test Rejection Feed",
                "publisher": "Test Publisher",
                "source_type": "rss",
                "url": "https://example.com/reject",
                "source_family": "national_news",
                "state": "US",
                "location_name": "United States",
                "location_scope": "national",
                "source_role_allowed": "pressure_evidence",
                "pressure_required": True,
                "freshness_mode": "pressure",
                "max_age_days": 7,
                "positive_keywords": ["food bank", "SNAP", "hunger"],
                "negative_keywords": ["recipe", "restaurant", "menu", "festival", "gala", "donation"],
                "affected_group_keywords": ["families", "children"],
                "enabled": True,
                "notes": "Rejected-item diagnostics feed.",
            }
        ],
    )
    rss_payload = _rss_payload(
        [
            {
                "title": "Restaurant announces new menu",
                "link": "https://example.com/menu",
                "description": "Restaurant review and menu update.",
            },
            {
                "title": "Recipe roundup for summer dinners",
                "link": "https://example.com/recipe",
                "description": "Lifestyle recipe ideas and cooking tips.",
            },
            {
                "title": "Food bank gala invites community donations",
                "link": "https://example.com/gala",
                "description": "Join the charity gala and fundraiser this weekend.",
            },
        ]
    )
    result = food_line.collect_food_line_auto_sources(tmp_path, date, fetcher=lambda _url, timeout=15: rss_payload)
    assert result["source_count"] == 0
    assert result["rejected_news_count"] == 3
    assert result["rejected_news_by_source"] == {"test-rejection-feed": 3}
    assert result["rejected_news_reasons"]
    assert all(
        "excluded by negative filter" in reason or "donation page is not current pressure evidence" in reason
        for reason in result["rejected_news_reasons"]
    )


def test_food_line_food_bank_demand_and_snap_delay_items_are_accepted(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    _write_pressure_registry(
        tmp_path,
        [
            {
                "source_id": "test-accepted-feed",
                "source_name": "Test Accepted Feed",
                "publisher": "Test Publisher",
                "source_type": "rss",
                "url": "https://example.com/accept",
                "source_family": "national_news",
                "state": "US",
                "location_name": "United States",
                "location_scope": "national",
                "source_role_allowed": "pressure_evidence",
                "pressure_required": True,
                "freshness_mode": "pressure",
                "max_age_days": 7,
                "positive_keywords": ["food bank", "SNAP", "food insecurity", "hunger"],
                "negative_keywords": ["recipe", "restaurant", "menu"],
                "affected_group_keywords": ["families", "children", "SNAP"],
                "enabled": True,
                "notes": "Accepted-item diagnostics feed.",
            }
        ],
    )
    rss_payload = _rss_payload(
        [
            {
                "title": "Food bank sees rising demand from families",
                "link": "https://example.com/demand",
                "description": "Food bank demand increased and pantry lines grew.",
            },
            {
                "title": "State officials report SNAP benefit delay affecting households",
                "link": "https://example.com/snap-delay",
                "description": "Households in Oregon will see a SNAP benefit delay and food pantries expect extra demand.",
            },
        ]
    )
    result = food_line.collect_food_line_auto_sources(tmp_path, date, fetcher=lambda _url, timeout=15: rss_payload)
    rows = json.loads(Path(result["auto_sources_path"]).read_text(encoding="utf-8"))
    assert result["source_count"] == 2
    assert result["collected_source_count_by_source_id"] == {"test-accepted-feed": 2}
    assert result["rejected_news_count"] == 0
    assert result["collected_count_by_extraction_quality"] == {"high": 2}
    assert result["verified_pressure_count_by_extraction_quality"] == {"high": 2}
    assert all(row["pressure_signal"] is True for row in rows)
    assert any("rising food-assistance demand" in row["pressure_summary"].lower() for row in rows)
    assert any("snap benefit delay" in row["pressure_summary"].lower() or "benefit delay" in row["pressure_summary"].lower() for row in rows)
    assert all(row["pressure_summary"] not in GENERIC_PRESSURE_SUMMARIES for row in rows)
    assert all(row["pressure_verification_status"] == "source_text_verified" for row in rows)
    assert all(row["evidence_text"] for row in rows)
    assert all(row["pressure_match_terms"] for row in rows)


def test_food_line_map_data_includes_verification_fields(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)
    map_data = json.loads((tmp_path / "output" / "site" / "food-line" / "map" / "map_data.json").read_text(encoding="utf-8"))
    marker = (map_data.get("pressure_markers") or [])[0]
    assert marker["pressure_verification_status"] == "source_text_verified"
    assert marker["evidence_text"]
    assert marker["pressure_match_terms"]
    assert marker["evidence_text_basis"]
    assert marker["extraction_quality"]
    assert marker["expected_text_basis"]
    assert marker["pressure_verification_required"] is True


def test_food_line_source_table_includes_evidence_fields(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)
    table = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "source_table.html").read_text(encoding="utf-8")
    assert "Record ID" in table
    assert "What the source says" in table
    assert "How it was used" in table
    assert "Verification status" in table
    assert "Source family" in table
    assert "rising food-assistance demand" in table.lower()


def test_food_line_logo_is_copied_and_referenced_in_generated_output(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)

    repo_logo = Path(__file__).parent.parent / "assets" / "food-line-logo.png"
    copied_logo = tmp_path / "output" / "site" / "food-line" / "assets" / "food-line-logo.png"
    assert copied_logo.exists()
    assert copied_logo.read_bytes() == repo_logo.read_bytes()

    index_html = (tmp_path / "output" / "site" / "food-line" / "index.html").read_text(encoding="utf-8")
    edition_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "index.html").read_text(encoding="utf-8")
    source_table_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "source_table.html").read_text(encoding="utf-8")
    map_html = (tmp_path / "output" / "site" / "food-line" / "map" / "index.html").read_text(encoding="utf-8")
    audio_html = (tmp_path / "output" / "site" / "food-line" / "audio" / "index.html").read_text(encoding="utf-8")

    assert 'alt="The Food Line Dispatch"' in index_html
    assert 'src="assets/food-line-logo.png"' in index_html
    assert 'href="/american-pressure/"' not in index_html
    assert 'href="/gaza/"' in index_html
    assert 'href="/cascadia/"' in index_html
    assert 'href="/food-line/"' in index_html
    assert 'src="../../assets/food-line-logo.png"' in edition_html
    assert 'href="/american-pressure/"' not in edition_html
    assert 'src="../../assets/food-line-logo.png"' in source_table_html
    assert 'href="/american-pressure/"' not in source_table_html
    assert 'src="../assets/food-line-logo.png"' in map_html
    assert 'href="/american-pressure/"' not in audio_html
    assert 'src="../assets/food-line-logo.png"' in audio_html


def test_food_line_dispatch_refreshes_historical_source_tables(tmp_path: Path):
    _ensure_assets(tmp_path)
    current_date = "2026-06-04"
    old_dates = ["2026-06-01", "2026-06-02"]
    for old_date in old_dates:
        edition_dir = tmp_path / "output" / "site" / "food-line" / "editions" / old_date
        edition_dir.mkdir(parents=True, exist_ok=True)
        _write_source_registry(tmp_path, [_row(1, title=f"Archive Source {old_date}")])
        (edition_dir / "index.html").write_text("<html><body>archive</body></html>", encoding="utf-8")
        (edition_dir / "edition_manifest.json").write_text(
            json.dumps(
                {
                    "dispatch_slug": "food-line",
                    "edition_date": old_date,
                    "public_rendered": True,
                    "qualified_primary_count": 1,
                    "skip_reason": "",
                    "future_date_blocked": False,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        (edition_dir / "sources_manifest.json").write_text(json.dumps([_row(1, title=f"Archive Source {old_date}")], indent=2), encoding="utf-8")
        (edition_dir / "source_table.html").write_text("<html><head></head><body><table><tr><th>Legacy</th></tr></table></body></html>", encoding="utf-8")

    p = _manual_path(tmp_path, current_date)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps([_pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, current_date)

    for old_date in old_dates:
        source_table_html = (tmp_path / "output" / "site" / "food-line" / "editions" / old_date / "source_table.html").read_text(encoding="utf-8")
    assert 'name="viewport" content="width=device-width, initial-scale=1"' in source_table_html
    assert "Record ID" in source_table_html
    assert "What the source says" in source_table_html
    assert "Verification status" in source_table_html
    assert "source_freshness_status" in source_table_html
    assert "source_freshness_date_basis" in source_table_html
    assert "source_public_story_eligible" in source_table_html
    assert 'src="../../assets/food-line-logo.png"' in source_table_html


def test_food_line_dispatch_reads_historical_discovery_audit_fields(tmp_path: Path):
    audit_dir = tmp_path / "output" / "review" / "food-line" / "2026-06-19"
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "discovery_audit.json").write_text(
        json.dumps(
            {
                "discovery_confidence": "limited",
                "historical_source_count": 2,
                "historical_sources": ["Archive One", "Archive Two"],
                "historical_sources_with_exact_date_items": ["Archive One"],
                "no_current_update": True,
                "no_current_update_reason": "Historical archives yielded only one exact-date item.",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    audit = food_line._food_line_discovery_expansion_audit(tmp_path, "2026-06-19")

    assert audit["historical_source_count"] == 2
    assert audit["historical_sources"] == ["Archive One", "Archive Two"]
    assert audit["historical_sources_with_exact_date_items"] == ["Archive One"]
    assert audit["no_current_update"] is True


def test_food_line_blue_fern_compliance_report_is_written(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)

    result = food_line_compliance.run_food_line_blue_fern_compliance(tmp_path, date)
    report_json = tmp_path / "output" / "review" / "food-line" / date / "blue_fern_compliance_report.json"
    report_md = tmp_path / "output" / "review" / "food-line" / date / "blue_fern_compliance_report.md"

    assert result["ok"] is True
    assert report_json.exists()
    assert report_md.exists()
    payload = json.loads(report_json.read_text(encoding="utf-8"))
    assert payload["logo_checks"]["asset_exists"] is True
    assert payload["logo_checks"]["podcast_artwork_exists"] is True
    assert payload["visual_checks"]["required_colors_present"] is True
    assert payload["source_table_checks"]["required_columns_present"] is True
    assert payload["mobile_basic_html_checks"]["viewport_meta_present"] is True


def test_food_line_blue_fern_compliance_missing_logo_fails(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)

    (tmp_path / "output" / "site" / "food-line" / "assets" / "food-line-logo.png").unlink()
    result = food_line_compliance.run_food_line_blue_fern_compliance(tmp_path, date)

    assert result["ok"] is False
    assert any("missing logo asset" in failure.lower() for failure in result["failures"])


def test_food_line_blue_fern_compliance_missing_blue_fern_color_fails(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)

    for html_path in [
        tmp_path / "output" / "site" / "food-line" / "index.html",
        tmp_path / "output" / "site" / "food-line" / "archive.html",
        tmp_path / "output" / "site" / "food-line" / "map" / "index.html",
        tmp_path / "output" / "site" / "food-line" / "audio" / "index.html",
        tmp_path / "output" / "site" / "food-line" / "editions" / date / "index.html",
        tmp_path / "output" / "site" / "food-line" / "editions" / date / "source_table.html",
    ]:
        text = html_path.read_text(encoding="utf-8")
        text = text.replace("#1E3F4F", "#000000").replace("#EFE7DA", "#000000").replace("#4E6B79", "#000000")
        html_path.write_text(text, encoding="utf-8")

    result = food_line_compliance.run_food_line_blue_fern_compliance(tmp_path, date)

    assert result["ok"] is False
    assert any("blue fern palette" in failure.lower() for failure in result["failures"])


def test_food_line_blue_fern_compliance_resource_directory_language_fails(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)

    audio_index_path = tmp_path / "output" / "site" / "food-line" / "audio" / "index.html"
    audio_index_path.write_text(audio_index_path.read_text(encoding="utf-8") + "\n<p>Find food resources near you.</p>\n", encoding="utf-8")

    result = food_line_compliance.run_food_line_blue_fern_compliance(tmp_path, date)

    assert result["ok"] is False
    assert any("resource-directory language" in failure.lower() for failure in result["failures"])


def test_food_line_blue_fern_compliance_fails_on_public_chrome_and_signal_mix(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)

    audio_index_path = tmp_path / "output" / "site" / "food-line" / "audio" / "index.html"
    audio_index_path.write_text(audio_index_path.read_text(encoding="utf-8") + "\n<p>Skip to content Watch Live Signal mix today: daily=0, provider=0, local=24, background=0.</p>\n", encoding="utf-8")

    result = food_line_compliance.run_food_line_blue_fern_compliance(tmp_path, date)

    assert result["ok"] is False
    assert any("scraped site chrome" in failure.lower() for failure in result["failures"])
    assert any("signal mix" in failure.lower() for failure in result["failures"])


def test_food_line_blue_fern_compliance_fails_on_audio_transcript_chrome(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    _mock_food_line_tts(monkeypatch)
    run_food_line_dispatch(tmp_path, date, generate_audio=True)

    transcript_path = tmp_path / "output" / "site" / "food-line" / "audio" / f"{date}-transcript.html"
    transcript_path.write_text(transcript_path.read_text(encoding="utf-8") + "\n<p>Skip to content Watch Live Signal mix today: daily=0, provider=0, local=24, background=0.</p>\n", encoding="utf-8")

    result = food_line_compliance.run_food_line_blue_fern_compliance(tmp_path, date)

    assert result["ok"] is False
    assert any(str(transcript_path) in failure for failure in result["failures"])
    assert any("signal mix" in failure.lower() for failure in result["failures"])


def test_food_line_blue_fern_compliance_fails_on_audio_podcast_chrome(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    _mock_food_line_tts(monkeypatch)
    run_food_line_dispatch(tmp_path, date, generate_audio=True)

    podcast_path = tmp_path / "output" / "site" / "food-line" / "audio" / "podcast.xml"
    podcast_path.write_text(podcast_path.read_text(encoding="utf-8").replace("Food Line Briefing — June 4, 2026", "Food Line Briefing — June 4, 2026 Skip to content Advertise With Us"), encoding="utf-8")

    result = food_line_compliance.run_food_line_blue_fern_compliance(tmp_path, date)

    assert result["ok"] is False
    assert any(str(podcast_path) in failure for failure in result["failures"])
    assert any("scraped site chrome" in failure.lower() for failure in result["failures"])


def test_food_line_blue_fern_compliance_fails_on_audio_briefing_debug_phrases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    _mock_food_line_tts(monkeypatch)
    run_food_line_dispatch(tmp_path, date, generate_audio=True)

    transcript_path = tmp_path / "output" / "site" / "food-line" / "audio" / f"{date}-transcript.html"
    transcript_path.write_text(
        transcript_path.read_text(encoding="utf-8") + "\n<p>matched terms source_text_verified the verified record came from Example News</p>\n",
        encoding="utf-8",
    )

    result = food_line_compliance.run_food_line_blue_fern_compliance(tmp_path, date)

    assert result["ok"] is False
    assert any(str(transcript_path) in failure for failure in result["failures"])
    assert any("internal/debug phrasing" in failure.lower() for failure in result["failures"])


def test_food_line_blue_fern_compliance_fails_on_unfiltered_public_source_table_rows(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)

    table_path = tmp_path / "output" / "site" / "food-line" / "editions" / date / "source_table.html"
    table_path.write_text(table_path.read_text(encoding="utf-8") + "\n<tr><td>false</td></tr>\n", encoding="utf-8")

    result = food_line_compliance.run_food_line_blue_fern_compliance(tmp_path, date)

    assert result["ok"] is False
    assert any("excluded context records" in failure.lower() for failure in result["failures"])


def test_food_line_blue_fern_compliance_ignores_orphaned_stale_edition_pages(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)

    stale_edition = tmp_path / "output" / "site" / "food-line" / "editions" / "2026-05-01"
    stale_edition.mkdir(parents=True, exist_ok=True)
    (stale_edition / "index.html").write_text("<p>Skip to content Signal mix today: daily=0, provider=0, local=24, background=0.</p>", encoding="utf-8")
    (stale_edition / "source_table.html").write_text("<table><tr><td>false</td></tr></table>", encoding="utf-8")

    result = food_line_compliance.run_food_line_blue_fern_compliance(tmp_path, date)

    assert result["ok"] is True
    assert "2026-05-01" not in "".join(result.get("checked_files") or [])
    assert all("2026-05-01" not in failure for failure in result["failures"])


def test_food_line_blue_fern_compliance_missing_map_popup_fields_fails(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)

    map_path = tmp_path / "output" / "site" / "food-line" / "map" / "index.html"
    map_text = map_path.read_text(encoding="utf-8").replace("What happened:", "Summary removed:").replace("What the source says:", "Evidence removed:")
    map_path.write_text(map_text, encoding="utf-8")

    result = food_line_compliance.run_food_line_blue_fern_compliance(tmp_path, date)

    assert result["ok"] is False
    assert any("map popup is missing required fields" in failure.lower() for failure in result["failures"])


def test_food_line_blue_fern_compliance_missing_source_table_verification_columns_fails(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)

    table_path = tmp_path / "output" / "site" / "food-line" / "editions" / date / "source_table.html"
    table_text = table_path.read_text(encoding="utf-8").replace("Verification status", "verification_status_removed")
    table_path.write_text(table_text, encoding="utf-8")

    result = food_line_compliance.run_food_line_blue_fern_compliance(tmp_path, date)

    assert result["ok"] is False
    assert any("missing required headers" in failure.lower() for failure in result["failures"])


def test_food_line_unverified_pressure_records_are_counted_in_diagnostics(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    _write_pressure_registry(
        tmp_path,
        [
            {
                "source_id": "generic-page-feed",
                "source_name": "Generic Page Feed",
                "publisher": "Test Publisher",
                "source_type": "page",
                "url": "https://example.com/generic-page",
                "source_family": "food_bank_provider",
                "state": "TX",
                "location_name": "Dallas, TX",
                "location_scope": "state_local",
                "source_role_allowed": "pressure_evidence",
                "pressure_required": True,
                "pressure_verification_required": True,
                "freshness_mode": "pressure",
                "max_age_days": 7,
                "extraction_quality": "low",
                "expected_text_basis": "page_text",
                "positive_keywords": ["demand", "shortage", "waitlist", "hours", "capacity", "inventory"],
                "negative_keywords": ["recipe", "restaurant", "menu", "festival", "gala"],
                "affected_group_keywords": ["families", "children"],
                "enabled": True,
                "notes": "Generic page used to ensure registry defaults do not create pressure.",
                "summary_fallback": "Food bank demand increased and pantry lines grew.",
            }
        ],
    )
    html_payload = b"""<!doctype html>
<html>
<head><title>Generic Food Bank Page</title><meta name=\"description\" content=\"Community updates and general information.\"></head>
<body><p>Community bulletin with updates for members today.</p></body></html>"""
    result = food_line.collect_food_line_auto_sources(tmp_path, date, fetcher=lambda _url, timeout=15: html_payload)
    assert result["pressure_signal_count"] == 0
    assert result["pressure_demoted_unverified_count"] == 1
    assert result["pressure_registry_only_count"] == 0
    assert result["pressure_evidence_basis_counts"]["page_text_excerpt"] == 1
    assert result["demoted_count_by_extraction_quality"] == {"low": 1}
    assert result["collected_count_by_extraction_quality"] == {"low": 1}


def test_food_line_collector_audit_json_is_written(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    _write_pressure_registry(
        tmp_path,
        [
            {
                "source_id": "audit-feed",
                "source_name": "Audit Feed",
                "publisher": "Audit Publisher",
                "source_type": "rss",
                "url": "https://example.com/audit",
                "source_family": "national_news",
                "state": "US",
                "location_name": "United States",
                "location_scope": "national",
                "source_role_allowed": "pressure_evidence",
                "pressure_required": True,
                "pressure_verification_required": True,
                "freshness_mode": "pressure",
                "max_age_days": 7,
                "extraction_quality": "high",
                "expected_text_basis": "rss_summary",
                "positive_keywords": ["food bank", "SNAP"],
                "negative_keywords": ["recipe", "restaurant", "menu"],
                "affected_group_keywords": ["families"],
                "enabled": True,
                "notes": "Audit output test feed.",
            }
        ],
    )
    rss_payload = _rss_payload(
        [
            {
                "title": "Food bank sees rising demand from families",
                "link": "https://example.com/audit-item",
                "description": "Food bank demand increased and pantry lines grew.",
            }
        ]
    )
    result = food_line.collect_food_line_auto_sources(tmp_path, date, fetcher=lambda _url, timeout=15: rss_payload)
    audit_path = Path(result["collector_audit_path"])
    assert audit_path.exists()
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit and audit[0]["source_id"] == "audit-feed"
    assert audit[0]["fetched"] is True
    assert audit[0]["accepted_pressure_count"] == 1
    assert audit[0]["top_rejection_reasons"] == []


def test_food_line_source_collection_audit_matches_normalized_review_candidate(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-25"
    manual_path = _manual_path(tmp_path, date)
    manual_path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        **_pressure_row(
            1,
            "Austin food bank reports rising summer demand",
            "Food bank demand increased and pantry lines grew for families seeking help.",
            family="local_news",
            state="TX",
            source_type="manual",
            publisher="Austin Monitor",
        ),
        "url": "https://example.com/2026/06/24/austin-food-bank-demand",
        "published_at": "2026-06-24T10:00:00Z",
        "retrieved_at": "2026-06-25T12:00:00Z",
        "pressure_signal": True,
        "pressure_type": "demand strain",
        "pressure_summary": "Austin food-bank demand increased and pantry lines grew for families seeking help.",
        "pressure_reason": "matched demand strain",
        "pressure_verification_status": "source_text_verified",
        "source_role": "local_signal",
        "source_purpose": "current_news",
        "location_name": "Austin, TX",
        "location_scope": "local",
        "map_category": "elevated demand",
        "source_public_story_eligible": True,
        "source_freshness_status": "fresh_daily_signal",
        "source_published_date": "2026-06-24",
        "source_freshness_date_basis": "url_path",
        "primary_eligible": True,
    }
    manual_path.write_text(json.dumps([row], indent=2), encoding="utf-8")
    gold_set_path = _write_source_collection_gold_set(
        tmp_path,
        date,
        [
            {
                "date": date,
                "query": "\"food bank\" after:2026-06-20",
                "url": "https://example.com/2026/06/24/austin-food-bank-demand/?utm_source=test",
                "title": "Austin food bank reports rising summer demand",
                "expected_status": "review_candidate",
                "expected_reason": "pantry demand pressure",
                "priority": "high",
                "source_family": "local_reporting",
            }
        ],
    )

    result = run_food_line_dispatch(
        tmp_path,
        date,
        generate_audio=False,
        audit_source_collection=True,
        gold_set_path=gold_set_path,
    )

    assert result["source_collection_audit_run"] is True
    assert result["source_collection_found_count"] == 1
    assert result["source_collection_reached_review_count"] == 1
    assert result["source_collection_missed_count"] == 0
    audit = json.loads(Path(result["source_collection_audit_path"]).read_text(encoding="utf-8"))
    assert audit["summary"]["recall"] == 1.0
    assert audit["items"][0]["found"] is True
    assert audit["items"][0]["highest_stage_reached"] == "qualified_public_candidate"
    assert audit["items"][0]["matched_artifact"] == "pressure_review"
    assert audit["items"][0]["fuzzy_match"] is False


def test_food_line_source_collection_audit_marks_missing_high_priority_candidate(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-25"
    gold_set_path = _write_source_collection_gold_set(
        tmp_path,
        date,
        [
            {
                "date": date,
                "query": "\"food banks\" after:2026-06-20",
                "url": "https://example.com/reuters-missed-pressure-2026-06-25",
                "title": "Millions lose food stamps as pantry demand rises",
                "expected_status": "review_candidate",
                "expected_reason": "SNAP loss / pantry demand pressure",
                "priority": "high",
                "source_family": "national_wire",
            }
        ],
    )

    result = run_food_line_dispatch(
        tmp_path,
        date,
        generate_audio=False,
        audit_source_collection=True,
        gold_set_path=gold_set_path,
    )

    assert result["source_collection_audit_run"] is True
    assert result["source_collection_found_count"] == 0
    assert result["source_collection_missed_count"] == 1
    assert result["source_collection_high_priority_missed_count"] == 1
    assert result["source_collection_likely_failure_category"] == "discovery_query_gap"
    audit = json.loads(Path(result["source_collection_audit_path"]).read_text(encoding="utf-8"))
    assert audit["items"][0]["found"] is False
    assert audit["items"][0]["highest_stage_reached"] == "not_discovered"
    assert audit["items"][0]["miss_reason"] == "not_discovered"


def test_food_line_source_collection_audit_records_rejected_candidate_with_reason(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-25"
    manual_path = _manual_path(tmp_path, date)
    manual_path.parent.mkdir(parents=True, exist_ok=True)
    rejected_row = {
        **_pressure_row(
            2,
            "Community pantry volunteer signup and donation page",
            "Volunteer opportunities and donation information for the pantry.",
            family="food_bank_provider",
            state="TX",
            source_type="manual",
            publisher="Community Pantry",
        ),
        "url": "https://example.com/community-pantry-donations",
        "published_at": "2026-06-24T08:00:00Z",
        "retrieved_at": "2026-06-25T12:00:00Z",
        "pressure_signal": False,
        "pressure_type": "context / monitoring only",
        "pressure_summary": "",
        "pressure_reason": "resource-only / no pressure signal",
        "pressure_verification_status": "demoted_context",
        "source_role": "resource_context",
        "source_purpose": "resource_page",
        "location_name": "Austin, TX",
        "location_scope": "local",
        "map_category": "context / monitoring only",
        "source_public_story_eligible": True,
        "source_freshness_status": "fresh_daily_signal",
        "source_published_date": "2026-06-24",
        "source_freshness_date_basis": "published_at",
        "primary_eligible": False,
        "primary_disqualification_reason": "resource-only / no pressure signal",
    }
    manual_path.write_text(json.dumps([rejected_row], indent=2), encoding="utf-8")
    gold_set_path = _write_source_collection_gold_set(
        tmp_path,
        date,
        [
            {
                "date": date,
                "query": "\"pantry\" after:2026-06-20",
                "url": rejected_row["url"],
                "title": rejected_row["title"],
                "expected_status": "review_candidate",
                "expected_reason": "should be reviewed then rejected as resource-only",
                "priority": "medium",
                "source_family": "food_bank_provider",
            }
        ],
    )

    result = run_food_line_dispatch(
        tmp_path,
        date,
        generate_audio=False,
        audit_source_collection=True,
        gold_set_path=gold_set_path,
    )

    assert result["source_collection_rejected_with_reason_count"] == 1
    assert result["source_collection_missed_count"] == 0
    audit = json.loads(Path(result["source_collection_audit_path"]).read_text(encoding="utf-8"))
    assert audit["items"][0]["highest_stage_reached"] == "rejected_with_reason"
    assert audit["items"][0]["miss_reason"] == "rejected_resource_only"
    assert "resource-only" in audit["items"][0]["rejection_reason"]


def test_food_line_source_collection_audit_mode_dry_run_does_not_publish(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    _ensure_assets(tmp_path)
    date = "2026-06-25"
    gold_set_path = _write_source_collection_gold_set(
        tmp_path,
        date,
        [
            {
                "date": date,
                "query": "\"food banks\" after:2026-06-20",
                "url": "https://example.com/missed-food-line-candidate",
                "title": "Missed audit candidate",
                "expected_status": "review_candidate",
                "expected_reason": "pressure candidate",
                "priority": "high",
                "source_family": "national_wire",
            }
        ],
    )

    exit_code = food_line.main(
        [
            "--date",
            date,
            "--dry-run",
            "--no-generate-audio",
            "--audit-source-collection",
            "--gold-set",
            str(gold_set_path),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["source_collection_audit_run"] is True
    assert payload["pages_publish_copied"] is False
    assert payload["pushed"] is False
    assert payload["bluesky_status"] == "skipped"


def test_food_line_source_collection_audit_dry_run_reuses_existing_collector_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    _ensure_assets(tmp_path)
    date = "2026-06-25"
    auto_path = tmp_path / "data" / "dispatches" / "food-line" / "sources" / date / "auto_sources.json"
    auto_path.parent.mkdir(parents=True, exist_ok=True)
    auto_path.write_text(
        json.dumps(
            [
                {
                    **_pressure_row(
                        11,
                        "Food banks continue to see increased need as SNAP requirements shift",
                        "Regional food banks continue to see increased need as SNAP requirements shift.",
                        family="public_radio",
                        state="MA",
                    ),
                    "url": "https://www.nepm.org/regional-news/2026-06-08/food-banks-continue-to-see-increased-need-as-snap-requirements-shift",
                    "source_id": "nepm-dd978f466973",
                    "source_family": "public_radio",
                    "source_public_story_eligible": True,
                    "qualifies_for_public_inclusion": True,
                    "pressure_signal": True,
                    "pressure_verification_status": "source_text_verified",
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    collector_audit_path = tmp_path / "data" / "dispatches" / "food-line" / "sources" / date / "collector_audit.json"
    collector_audit_path.write_text(
        json.dumps(
            [
                {
                    "source_id": "nepm-dd978f466973",
                    "source_name": "Food banks continue to see increased need as SNAP requirements shift | New England Public Media",
                    "source_family": "public_radio",
                    "url": "https://www.nepm.org/regional-news/2026-06-08/food-banks-continue-to-see-increased-need-as-snap-requirements-shift",
                    "fetched": True,
                    "item_count": 1,
                    "accepted_pressure_count": 1,
                    "demoted_count": 0,
                    "rejected_count": 0,
                    "top_rejection_reasons": [],
                    "extraction_basis_used": ["page_text_excerpt"],
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    gold_set_path = _write_source_collection_gold_set(
        tmp_path,
        date,
        [
            {
                "date": date,
                "query": "\"food banks\" SNAP requirements shift site:nepm.org after:2026-06-01",
                "url": "https://www.nepm.org/regional-news/2026-06-08/food-banks-continue-to-see-increased-need-as-snap-requirements-shift",
                "title": "Food banks continue to see increased need as SNAP requirements shift",
                "expected_status": "review_candidate",
                "expected_reason": "regional food-bank demand candidate tied to SNAP requirement changes",
                "priority": "high",
                "source_family": "public_radio",
            }
        ],
    )

    collector_called = False

    def _unexpected_collect(*args, **kwargs):
        nonlocal collector_called
        collector_called = True
        raise AssertionError("live collector should not run when reusable audit artifacts exist")

    monkeypatch.setattr(food_line, "collect_food_line_auto_sources", _unexpected_collect)
    monkeypatch.chdir(tmp_path)

    exit_code = food_line.main(
        [
            "--date",
            date,
            "--collect",
            "--dry-run",
            "--no-generate-audio",
            "--audit-source-collection",
            "--gold-set",
            str(gold_set_path),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert collector_called is False
    assert payload["source_collection_audit_run"] is True
    assert payload["source_collection_collect_reused_existing"] is True
    assert payload["source_collection_collect_live_ran"] is False
    assert payload["source_collection_runtime_bounded"] is True
    assert payload["source_collection_runtime_bound_reason"] == "reused_existing_collection_artifacts"
    assert payload["pages_publish_copied"] is False
    assert payload["pushed"] is False
    assert payload["bluesky_status"] == "skipped"


def test_food_line_source_collection_audit_is_disabled_without_flag(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-25"
    result = run_food_line_dispatch(tmp_path, date, generate_audio=False)
    assert result["source_collection_audit_run"] is False
    assert result["source_collection_audit_skipped_reason"] == ""
    assert result["source_collection_audit_warning"] == ""
    assert result["source_collection_gold_set_path"] == ""
    assert result["source_collection_gold_count"] == 0
    assert result["source_collection_audit_path"] == ""


def test_food_line_source_collection_audit_skips_missing_default_gold_set_without_failing_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    date = "2026-06-25"
    auto_path = tmp_path / "data" / "dispatches" / "food-line" / "sources" / date / "auto_sources.json"
    collector_audit_path = tmp_path / "data" / "dispatches" / "food-line" / "sources" / date / "collector_audit.json"
    default_gold_set_path = tmp_path / "data" / "dispatches" / "food-line" / "source_collection_gold_sets" / f"{date}.json"
    monkeypatch.setattr(
        food_line,
        "write_food_line_audio",
        lambda *args, **kwargs: {
            "audio_generated": False,
            "audio_available": False,
            "audio_reused_existing": False,
            "audio_required": False,
            "force_audio_regenerate": False,
            "audio_status": "skipped",
            "audio_story_section_count": 0,
            "audio_story_sections": [],
            "warnings": [],
            "errors": [],
        },
    )
    monkeypatch.setattr(food_line, "write_food_line_podcast_feed", lambda *args, **kwargs: None)

    def fake_collect(root: Path, edition_date: str, fetcher=None):
        auto_path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            **_pressure_row(
                41,
                "Food bank lines lengthen as pantry inventory tightens",
                "Pantry lines lengthened and food bank inventory tightened as more families sought help.",
                family="local_news",
                state="TX",
            ),
            "url": "https://example.com/food-line/2026/06/25/pantry-lines-lengthen",
            "published_at": f"{edition_date}T12:00:00Z",
            "retrieved_at": f"{edition_date}T13:00:00Z",
        }
        auto_path.write_text(json.dumps([row], indent=2), encoding="utf-8")
        collector_audit_path.write_text(
            json.dumps(
                [
                    {
                        "source_id": "food-line-src-041",
                        "source_name": row["title"],
                        "source_family": row["source_family"],
                        "url": row["url"],
                        "fetched": True,
                        "item_count": 1,
                        "accepted_pressure_count": 1,
                        "demoted_count": 0,
                        "rejected_count": 0,
                        "top_rejection_reasons": [],
                        "extraction_basis_used": ["rss_summary"],
                    }
                ],
                indent=2,
            ),
            encoding="utf-8",
        )
        return {
            "ok": True,
            "source_count": 1,
            "collector_audit_path": str(collector_audit_path),
            "collected_source_count_by_source_id": {"food-line-src-041": 1},
            "pressure_verified_count": 1,
            "pressure_evidence_basis_counts": {"rss_summary": 1},
            "collected_count_by_extraction_quality": {"high": 1},
            "verified_pressure_count_by_extraction_quality": {"high": 1},
        }

    monkeypatch.setattr(food_line, "collect_food_line_auto_sources", fake_collect)

    result = run_food_line_dispatch(
        tmp_path,
        date,
        collect=True,
        generate_audio=False,
        audit_source_collection=True,
    )

    edition_manifest = json.loads(
        (tmp_path / "output" / "dispatches" / "food-line" / "editions" / date / "edition_manifest.json").read_text(encoding="utf-8")
    )

    assert result["ok"] is True
    assert result["source_collection_audit_run"] is False
    assert result["source_collection_audit_skipped_reason"] == "gold set missing"
    assert result["source_collection_gold_count"] == 0
    assert result["source_collection_gold_set_path"] == str(default_gold_set_path)
    assert result["source_collection_audit_path"] == ""
    assert result["source_collection_collect_live_ran"] is True
    assert result["collector_result"]["source_count"] == 1
    assert auto_path.exists()
    assert default_gold_set_path.exists() is False
    assert "default gold set file was missing" in result["source_collection_audit_warning"]
    assert str(default_gold_set_path) in result["source_collection_audit_warning"]
    assert result["warnings"]
    assert result["warnings"][0] == result["source_collection_audit_warning"]
    assert edition_manifest["source_collection_audit_skipped_reason"] == "gold set missing"
    assert edition_manifest["source_collection_gold_set_path"] == str(default_gold_set_path)
    assert edition_manifest["source_collection_audit_warning"] == result["source_collection_audit_warning"]


def test_food_line_source_collection_audit_uses_discovery_intake_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    date = "2026-06-25"
    candidate_url = "https://example.com/2026/06/25/pantry-demand-surges"
    gold_set_path = _write_source_collection_gold_set(
        tmp_path,
        date,
        [
            {
                "date": date,
                "query": "\"food banks\" after:2026-06-24 before:2026-06-26",
                "url": candidate_url,
                "title": "Pantry demand surges as families turn to food banks",
                "expected_status": "review_candidate",
                "expected_reason": "discovery intake should surface this candidate for audit review",
                "priority": "high",
                "source_family": "local_reporting",
            }
        ],
    )

    def fake_run_discovery_expansion(root: Path, edition_date: str, **kwargs):
        candidate_path = root / "data" / "dispatches" / "food-line" / "discovery" / edition_date / "discovery_candidates.json"
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        candidate_path.write_text(
            json.dumps(
                [
                    {
                        "candidate_id": "food-line-discovery-1",
                        "discovered_title": "Pantry demand surges as families turn to food banks",
                        "source_name": "Local Monitor",
                        "final_trace_url": candidate_url,
                        "canonical_url": candidate_url,
                        "discovered_url": candidate_url,
                        "google_news_url": "https://news.google.com/rss/articles/food-line-discovery-1",
                        "fetch_status": "ok",
                        "classification_status": "qualified_pressure_signal",
                    }
                ],
                indent=2,
            ),
            encoding="utf-8",
        )
        return {"ok": True, "discovery_candidates_path": str(candidate_path)}

    def fake_run_discovery_bridge(root: Path, edition_date: str, dry_run: bool = False):
        review_path = root / "output" / "review" / "food-line" / edition_date / "discovery_intake.json"
        review_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ok": True,
            "discovery_source_rows": [
                {
                    "source_record_id": "food-line-discovery-1",
                    "candidate_id": "food-line-discovery-1",
                    "title": "Pantry demand surges as families turn to food banks",
                    "url": candidate_url,
                    "classification_status": "qualified_pressure_signal",
                    "fetch_status": "ok",
                }
            ],
        }
        review_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    monkeypatch.setattr(food_line, "run_food_line_discovery_expansion", fake_run_discovery_expansion)
    monkeypatch.setattr(food_line, "run_food_line_discovery_intake_bridge", fake_run_discovery_bridge)

    result = run_food_line_dispatch(
        tmp_path,
        date,
        collect=True,
        generate_audio=False,
        audit_source_collection=True,
        gold_set_path=gold_set_path,
    )

    assert result["source_collection_audit_run"] is True
    assert result["source_collection_found_count"] == 1
    assert result["source_collection_reached_review_count"] == 1
    assert result["source_collection_qualified_count"] == 1
    audit = json.loads(Path(result["source_collection_audit_path"]).read_text(encoding="utf-8"))
    assert audit["items"][0]["matched_artifact"] == "discovery_intake_review"
    assert audit["items"][0]["highest_stage_reached"] == "qualified_public_candidate"
    assert audit["items"][0]["source_public_story_eligible"] is True


def test_food_line_source_collection_audit_marks_discovery_candidate_rejected_with_reason(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-25"
    candidate_url = "https://example.com/2026/06/25/summer-meal-locations"
    candidate_path = tmp_path / "data" / "dispatches" / "food-line" / "discovery" / date / "discovery_candidates.json"
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_text(
        json.dumps(
            [
                {
                    "candidate_id": "food-line-discovery-2",
                    "discovered_title": "Summer meal locations and pantry donation information",
                    "source_name": "Community Pantry",
                    "final_trace_url": candidate_url,
                    "canonical_url": candidate_url,
                    "discovered_url": candidate_url,
                    "fetch_status": "ok",
                    "classification_status": "context_only",
                    "exclusion_reason": "resource-only / no pressure signal",
                    "source_purpose": "resource_page",
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    gold_set_path = _write_source_collection_gold_set(
        tmp_path,
        date,
        [
            {
                "date": date,
                "query": "\"summer meals\" after:2026-06-24 before:2026-06-26",
                "url": candidate_url,
                "title": "Summer meal locations and pantry donation information",
                "expected_status": "review_candidate",
                "expected_reason": "resource-only discovery candidate should be rejected with a reason",
                "priority": "medium",
                "source_family": "food_bank_provider",
            }
        ],
    )

    pressure_review_path = tmp_path / "output" / "review" / "food-line" / date / "pressure_review.csv"
    pressure_review_path.parent.mkdir(parents=True, exist_ok=True)
    pressure_review_path.write_text("", encoding="utf-8")
    audit = food_line.run_food_line_source_collection_audit(
        tmp_path,
        date,
        gold_set_path=gold_set_path,
        sources=[],
        rejected_records=[],
        pressure_review_path=pressure_review_path,
        collect_result={"ok": True},
    )

    payload = json.loads(Path(audit["source_collection_audit_path"]).read_text(encoding="utf-8"))
    assert audit["source_collection_rejected_with_reason_count"] == 1
    assert payload["items"][0]["highest_stage_reached"] == "rejected_with_reason"
    assert payload["items"][0]["matched_artifact"] == "discovery_candidates"
    assert payload["items"][0]["miss_reason"] == "rejected_resource_only"


def test_food_line_source_collection_rejection_reason_aggregates_concrete_sources():
    reason, miss_reason = food_line._food_line_source_collection_rejection_reason(
        {
            "exclusion_reason": "outside daily window",
            "pressure_verification_status": "demoted_context",
            "source_purpose": "current_news",
        },
        {
            "reason": "rejected in pressure review after freshness check",
            "source_freshness_disqualification_reason": "published_at is outside daily window",
        },
        {
            "reason": "resource-only candidate was reviewed and rejected",
        },
        ["missing required field: published_at"],
    )

    assert reason == (
        "outside daily window; rejected in pressure review after freshness check; "
        "published_at is outside daily window; resource-only candidate was reviewed and rejected; "
        "missing required field: published_at"
    )
    assert miss_reason == "rejected_stale"


def test_food_line_source_collection_audit_uses_collector_audit_for_ap_alias_match(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-25"
    collector_audit_path = tmp_path / "data" / "dispatches" / "food-line" / "sources" / date / "collector_audit.json"
    collector_audit_path.parent.mkdir(parents=True, exist_ok=True)
    collector_audit_path.write_text(
        json.dumps(
            [
                {
                    "source_id": "ap-food-bank-cuts",
                    "source_name": "Funding cuts threaten to deepen hunger crisis as rising costs send more families to food banks",
                    "source_family": "national_news",
                    "url": "https://apnews.com/article/665c19251b5d83bbed45a29958f79609",
                    "fetched": True,
                    "item_count": 1,
                    "accepted_pressure_count": 0,
                    "demoted_count": 0,
                    "rejected_count": 1,
                    "top_rejection_reasons": ["excluded by negative filter: menu"],
                    "extraction_basis_used": ["page_text_excerpt"],
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    gold_set_path = _write_source_collection_gold_set(
        tmp_path,
        date,
        [
            {
                "date": date,
                "query": "\"food banks\" funding cuts families after:2025-04-28",
                "url": "https://apnews.com/article/food-banks-campaign-against-hunger-snap-pantry-665c19251b5d83bbed45a29958f79609",
                "title": "Funding cuts threaten to deepen hunger crisis as rising costs send more families to food banks",
                "expected_status": "review_candidate",
                "expected_reason": "major-outlet reporting on food-bank strain and SNAP-linked household pressure",
                "priority": "high",
                "source_family": "national_wire",
            }
        ],
    )

    pressure_review_path = tmp_path / "output" / "review" / "food-line" / date / "pressure_review.csv"
    pressure_review_path.parent.mkdir(parents=True, exist_ok=True)
    pressure_review_path.write_text("", encoding="utf-8")
    audit = food_line.run_food_line_source_collection_audit(
        tmp_path,
        date,
        gold_set_path=gold_set_path,
        sources=[],
        rejected_records=[],
        pressure_review_path=pressure_review_path,
        collect_result={"ok": True},
    )

    payload = json.loads(Path(audit["source_collection_audit_path"]).read_text(encoding="utf-8"))
    item = payload["items"][0]
    assert item["found"] is True
    assert item["matched_artifact"] == "collector_audit"
    assert item["highest_stage_reached"] == "rejected_with_reason"
    assert item["rejection_reason"] == "collector artifact reflects a pre-fix menu false positive on an article-like food-pressure candidate"
    assert item["matched_title"] == "Funding cuts threaten to deepen hunger crisis as rising costs send more families to food banks"


def test_food_line_source_collection_audit_markdown_no_longer_reports_menu_for_ap_candidate(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-25"
    collector_audit_path = tmp_path / "data" / "dispatches" / "food-line" / "sources" / date / "collector_audit.json"
    collector_audit_path.parent.mkdir(parents=True, exist_ok=True)
    collector_audit_path.write_text(
        json.dumps(
            [
                {
                    "source_id": "ap-food-bank-cuts",
                    "source_name": "Funding cuts threaten to deepen hunger crisis as rising costs send more families to food banks",
                    "source_family": "national_news",
                    "url": "https://apnews.com/article/665c19251b5d83bbed45a29958f79609",
                    "fetched": True,
                    "item_count": 1,
                    "accepted_pressure_count": 0,
                    "demoted_count": 0,
                    "rejected_count": 1,
                    "top_rejection_reasons": ["excluded by negative filter: menu"],
                    "extraction_basis_used": ["page_text_excerpt"],
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    gold_set_path = _write_source_collection_gold_set(
        tmp_path,
        date,
        [
            {
                "date": date,
                "query": "\"food banks\" funding cuts families after:2025-04-28",
                "url": "https://apnews.com/article/food-banks-campaign-against-hunger-snap-pantry-665c19251b5d83bbed45a29958f79609",
                "title": "Funding cuts threaten to deepen hunger crisis as rising costs send more families to food banks",
                "expected_status": "review_candidate",
                "expected_reason": "major-outlet reporting on food-bank strain and SNAP-linked household pressure",
                "priority": "high",
                "source_family": "national_wire",
            }
        ],
    )
    pressure_review_path = tmp_path / "output" / "review" / "food-line" / date / "pressure_review.csv"
    pressure_review_path.parent.mkdir(parents=True, exist_ok=True)
    pressure_review_path.write_text("", encoding="utf-8")

    audit = food_line.run_food_line_source_collection_audit(
        tmp_path,
        date,
        gold_set_path=gold_set_path,
        sources=[],
        rejected_records=[],
        pressure_review_path=pressure_review_path,
        collect_result={"ok": True},
    )

    markdown = Path(audit["source_collection_audit_markdown_path"]).read_text(encoding="utf-8")
    assert "excluded by negative filter: menu" not in markdown
    assert "pre-fix menu false positive" in markdown


def test_food_line_source_collection_audit_markdown_prefers_concrete_rejection_reason(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-25"
    manual_path = _manual_path(tmp_path, date)
    manual_path.parent.mkdir(parents=True, exist_ok=True)
    manual_path.write_text(
        json.dumps(
            [
                {
                    **_pressure_row(
                        31,
                        "Food banks continue to see increased need as SNAP requirements shift",
                        "Regional food banks continue to see increased need as SNAP requirements shift.",
                        family="public_radio",
                        state="MA",
                    ),
                    "url": "https://www.nepm.org/regional-news/2026-06-08/food-banks-continue-to-see-increased-need-as-snap-requirements-shift",
                    "source_family": "public_radio",
                    "source_purpose": "current_news",
                    "pressure_signal": False,
                    "pressure_verification_status": "demoted_context",
                    "source_freshness_status": "stale_outside_daily_window",
                    "source_freshness_disqualification_reason": "outside daily window",
                    "freshness_disqualification_reason": "outside daily window",
                    "primary_eligible": False,
                    "primary_disqualification_reason": "not a current public food-pressure signal",
                    "source_public_story_eligible": False,
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    gold_set_path = _write_source_collection_gold_set(
        tmp_path,
        date,
        [
            {
                "date": date,
                "query": "\"food banks\" SNAP requirements shift site:nepm.org after:2026-06-01",
                "url": "https://www.nepm.org/regional-news/2026-06-08/food-banks-continue-to-see-increased-need-as-snap-requirements-shift",
                "title": "Food banks continue to see increased need as SNAP requirements shift",
                "expected_status": "review_candidate",
                "expected_reason": "regional food-bank demand candidate tied to SNAP requirement changes",
                "priority": "high",
                "source_family": "public_radio",
            }
        ],
    )

    result = run_food_line_dispatch(
        tmp_path,
        date,
        generate_audio=False,
        audit_source_collection=True,
        gold_set_path=gold_set_path,
    )

    markdown = Path(result["source_collection_audit_markdown_path"]).read_text(encoding="utf-8")
    assert "| unknown |" not in markdown
    assert "outside daily window" in markdown
    assert "not a current public food-pressure signal" in markdown


def test_food_line_review_csv_is_written_and_includes_evidence_fields(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)
    csv_path = tmp_path / "output" / "review" / "food-line" / date / "pressure_review.csv"
    assert csv_path.exists()
    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    assert rows[0]["evidence_text"]
    assert rows[0]["pressure_match_terms"]
    assert rows[0]["pressure_verification_status"]
    assert rows[0]["source_family"] == "local_news"


def test_food_line_fetch_failures_are_counted_by_source_id(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    _write_pressure_registry(
        tmp_path,
        [
            {
                "source_id": "failing-feed",
                "source_name": "Failing Feed",
                "publisher": "Failing Publisher",
                "source_type": "rss",
                "url": "https://example.com/failing",
                "source_family": "national_news",
                "state": "US",
                "location_name": "United States",
                "location_scope": "national",
                "source_role_allowed": "pressure_evidence",
                "pressure_required": True,
                "pressure_verification_required": True,
                "freshness_mode": "pressure",
                "max_age_days": 7,
                "extraction_quality": "high",
                "expected_text_basis": "rss_summary",
                "positive_keywords": ["food bank", "SNAP"],
                "negative_keywords": ["recipe", "restaurant", "menu"],
                "affected_group_keywords": ["families"],
                "enabled": True,
                "notes": "Failure diagnostics feed.",
            },
            {
                "source_id": "healthy-feed",
                "source_name": "Healthy Feed",
                "publisher": "Healthy Publisher",
                "source_type": "rss",
                "url": "https://example.com/healthy",
                "source_family": "national_news",
                "state": "US",
                "location_name": "United States",
                "location_scope": "national",
                "source_role_allowed": "pressure_evidence",
                "pressure_required": True,
                "pressure_verification_required": True,
                "freshness_mode": "pressure",
                "max_age_days": 7,
                "extraction_quality": "high",
                "expected_text_basis": "rss_summary",
                "positive_keywords": ["food bank", "SNAP"],
                "negative_keywords": ["recipe", "restaurant", "menu"],
                "affected_group_keywords": ["families"],
                "enabled": True,
                "notes": "Healthy diagnostics feed.",
            },
        ],
    )
    rss_payload = _rss_payload(
        [
            {
                "title": "Food bank sees rising demand from families",
                "link": "https://example.com/healthy-item",
                "description": "Food bank demand increased and pantry lines grew.",
            }
        ]
    )

    def fetcher(url: str, timeout: int = 15):
        if "failing" in url:
            raise OSError("network failure")
        return rss_payload

    result = food_line.collect_food_line_auto_sources(tmp_path, date, fetcher=fetcher)
    assert result["fetch_failure_count_by_source_id"] == {"failing-feed": 1}
    assert result["source_count"] == 1
    assert result["collected_count_by_extraction_quality"] == {"high": 1}


@pytest.mark.parametrize(
    ("source_id", "exc", "expected_type", "expected_action"),
    [
        (
            "missing-page",
            urllib.error.HTTPError("https://example.com/missing", 404, "Not Found", hdrs=None, fp=None),
            "404",
            "update_url",
        ),
        (
            "blocked-page",
            urllib.error.HTTPError("https://example.com/blocked", 403, "Forbidden", hdrs=None, fp=None),
            "403",
            "mark_paywall_or_forbidden",
        ),
        (
            "slow-page",
            TimeoutError("The read operation timed out"),
            "timeout",
            "keep_retry_transient",
        ),
    ],
)
def test_food_line_fetch_failures_are_classified_for_source_health(
    tmp_path: Path,
    source_id: str,
    exc: Exception,
    expected_type: str,
    expected_action: str,
):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    url = f"https://example.com/{source_id}"
    _write_pressure_registry(
        tmp_path,
        [
            {
                "source_id": source_id,
                "source_name": source_id,
                "publisher": "Example Publisher",
                "source_type": "page",
                "url": url,
                "source_family": "national_news",
                "state": "US",
                "location_name": "United States",
                "location_scope": "national",
                "source_role_allowed": "pressure_evidence",
                "pressure_required": True,
                "pressure_verification_required": True,
                "freshness_mode": "pressure",
                "max_age_days": 7,
                "extraction_quality": "high",
                "expected_text_basis": "page_text",
                "positive_keywords": ["food bank", "SNAP"],
                "negative_keywords": ["recipe", "restaurant", "menu"],
                "affected_group_keywords": ["families"],
                "enabled": True,
                "notes": "Failure diagnostics page.",
            }
        ],
    )

    def fetcher(_url: str, timeout: int = 15):
        raise exc

    result = food_line.collect_food_line_auto_sources(tmp_path, date, fetcher=fetcher)
    assert result["fetch_failure_count_by_source_id"] == {source_id: 1}
    assert result["fetch_failure_count_by_type"] == {expected_type: 1}
    assert result["fetch_failure_type_by_source_id"] == {source_id: expected_type}
    assert result["fetch_failure_action_by_source_id"] == {source_id: expected_action}
    audit = json.loads(Path(result["collector_audit_path"]).read_text(encoding="utf-8"))
    assert audit[0]["fetch_failure_type"] == expected_type
    assert audit[0]["fetch_failure_action"] == expected_action
    assert audit[0]["fetched"] is False


def test_food_line_reused_collect_result_preserves_fetch_failure_classification(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    auto_path = tmp_path / "data" / "dispatches" / "food-line" / "sources" / date / "auto_sources.json"
    auto_path.parent.mkdir(parents=True, exist_ok=True)
    auto_path.write_text("[]", encoding="utf-8")
    collector_audit_path = tmp_path / "data" / "dispatches" / "food-line" / "sources" / date / "collector_audit.json"
    collector_audit_path.write_text(
        json.dumps(
            [
                {
                    "source_id": "blocked-page",
                    "url": "https://example.com/blocked",
                    "fetched": False,
                    "item_count": 0,
                    "accepted_pressure_count": 0,
                    "demoted_count": 0,
                    "rejected_count": 0,
                    "top_rejection_reasons": ["HTTPError: HTTP Error 403: Forbidden"],
                    "fetch_failure_type": "403",
                    "fetch_failure_action": "mark_paywall_or_forbidden",
                    "fetch_failure_transient": False,
                    "extraction_basis_used": [],
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )

    result = food_line._food_line_reused_collect_result(tmp_path, date)
    assert result["reused_existing_artifacts"] is True
    assert result["fetch_failure_count_by_source_id"] == {"blocked-page": 1}
    assert result["fetch_failure_count_by_type"] == {"403": 1}
    assert result["fetch_failure_type_by_source_id"] == {"blocked-page": "403"}
    assert result["fetch_failure_action_by_source_id"] == {"blocked-page": "mark_paywall_or_forbidden"}


def test_food_line_known_404_registry_entries_are_disabled() -> None:
    registry = json.loads(Path("data/dispatches/food-line/source_registry.json").read_text(encoding="utf-8"))
    by_id = {row["source_id"]: row for row in registry}

    for source_id in ("id-iccp-snap", "oh-jfs-snap"):
        row = by_id[source_id]
        assert row["enabled"] is False
        assert "verified 404" in str(row.get("notes") or "").lower()


def test_food_line_candidate_registry_loads(tmp_path: Path):
    _ensure_assets(tmp_path)
    _write_candidate_registry(
        tmp_path,
        [
            {
                "source_id": "candidate-one",
                "source_name": "Candidate One",
                "publisher": "Candidate Publisher",
                "candidate_url": "https://example.com/feed.rss",
                "source_family": "public_radio",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "Manual candidate.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": ["SNAP", "food bank"],
                "status": "candidate",
                "notes": "Seeded candidate.",
            }
        ],
    )
    candidates = load_food_line_candidate_registry(tmp_path)
    assert len(candidates) == 1
    assert candidates[0]["candidate_url"] == "https://example.com/feed.rss"
    assert candidates[0]["status"] == "candidate"


def test_food_line_production_collector_skips_candidate_registry(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-07"
    _write_candidate_registry(
        tmp_path,
        [
            {
                "source_id": "candidate-only",
                "source_name": "Candidate Only",
                "publisher": "Candidate Publisher",
                "candidate_url": "https://example.com/feed.rss",
                "source_family": "public_radio",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "Manual candidate.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": ["SNAP", "food bank"],
                "status": "candidate",
                "notes": "Seeded candidate.",
            }
        ],
    )
    (tmp_path / "data" / "dispatches" / "food-line").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "dispatches" / "food-line" / "source_registry.json").write_text("[]", encoding="utf-8")
    (tmp_path / "data" / "dispatches" / "food-line" / "pressure_source_registry.json").write_text("[]", encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    assert result["source_count"] == 0
    assert result["pressure_signal_count"] == 0


def test_food_line_candidate_tester_writes_review_and_audit(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-07"
    _write_candidate_registry(
        tmp_path,
        [
            {
                "source_id": "candidate-good",
                "source_name": "Candidate Good",
                "publisher": "Candidate Publisher",
                "candidate_url": "https://example.com/good.rss",
                "source_family": "local_news",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "Good candidate.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": ["SNAP", "food bank"],
                "status": "candidate",
                "notes": "Seeded candidate.",
            }
        ],
    )
    rss_payload = _rss_payload(
        [
            {
                "title": "Food bank sees rising demand from families",
                "link": "https://example.com/good-item",
                "description": "Food bank demand increased and pantry lines grew.",
            }
        ]
    )
    result = run_food_line_candidate_sources(tmp_path, date, fetcher=lambda _url, timeout=15: rss_payload)
    review_path = Path(result["candidate_review_path"])
    audit_path = Path(result["candidate_audit_path"])
    assert review_path.exists()
    assert audit_path.exists()
    with review_path.open(encoding="utf-8") as handle:
        review = list(csv.DictReader(handle))
    assert review and review[0]["recommendation"] == "enable"
    assert "noise_score" in review[0]
    assert "pressure_hit_rate" in review[0]
    assert "negative_hit_count" in review[0]
    assert "useful_text_available" in review[0]
    assert review[0]["useful_text_available"] == "true"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit and audit[0]["candidate_url"] == "https://example.com/good.rss"
    assert audit[0]["raw_diagnostics"]


def test_food_line_candidate_workflow_recommendations_cover_keep_reject_cases(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-07"
    _write_candidate_registry(
        tmp_path,
        [
            {
                "source_id": "candidate-quiet",
                "source_name": "Candidate Quiet",
                "publisher": "Candidate Publisher",
                "candidate_url": "https://example.com/quiet.rss",
                "source_family": "public_radio",
                "state": "ID",
                "location_name": "Idaho",
                "location_scope": "state_local",
                "candidate_reason": "Quiet working feed.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": ["SNAP", "food bank"],
                "status": "candidate",
                "notes": "No current pressure item.",
            },
            {
                "source_id": "candidate-broken",
                "source_name": "Candidate Broken",
                "publisher": "Candidate Publisher",
                "candidate_url": "https://example.com/broken.rss",
                "source_family": "public_radio",
                "state": "OR",
                "location_name": "Oregon",
                "location_scope": "state_local",
                "candidate_reason": "Broken feed.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": ["SNAP", "food bank"],
                "status": "candidate",
                "notes": "Broken.",
            },
            {
                "source_id": "candidate-recipes",
                "source_name": "Candidate Recipes",
                "publisher": "Candidate Publisher",
                "candidate_url": "https://example.com/recipes.rss",
                "source_family": "local_news",
                "state": "CA",
                "location_name": "California",
                "location_scope": "state_local",
                "candidate_reason": "Lifestyle-heavy feed.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": ["SNAP", "food bank"],
                "status": "candidate",
                "notes": "Lifestyle noise.",
            },
        ],
    )
    def fetcher(url: str, timeout: int = 15):
        if "broken" in url:
            raise OSError("network failure")
        if "recipes" in url:
            return _rss_payload(
                [
                    {
                        "title": "Recipe roundup for summer dinners",
                        "link": "https://example.com/recipe",
                        "description": "Lifestyle recipe ideas and cooking tips.",
                    }
                ]
            )
        return _rss_payload(
            [
                {
                    "title": "Community newsletter",
                    "link": "https://example.com/quiet-item",
                    "description": "Community updates and general information.",
                }
            ]
        )

    result = run_food_line_candidate_sources(tmp_path, date, fetcher=fetcher)
    review_path = Path(result["candidate_review_path"])
    with review_path.open(encoding="utf-8") as handle:
        review = {row["source_id"]: row for row in csv.DictReader(handle)}
    assert review["candidate-quiet"]["recommendation"] == "keep_candidate"
    assert review["candidate-broken"]["recommendation"] == "reject"
    assert review["candidate-recipes"]["recommendation"] == "reject"


def test_food_line_candidate_promotion_only_promotes_enable_and_is_idempotent(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-08"
    _write_candidate_registry(
        tmp_path,
        [
            {
                "source_id": "candidate-enable",
                "source_name": "Candidate Enable",
                "publisher": "Candidate Publisher",
                "candidate_url": "https://example.com/enable.rss",
                "source_family": "local_news",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "Enable candidate.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": ["SNAP", "food bank"],
                "status": "tested_good",
                "notes": "Seeded candidate.",
            },
            {
                "source_id": "candidate-keep",
                "source_name": "Candidate Keep",
                "publisher": "Candidate Publisher",
                "candidate_url": "https://example.com/keep.rss",
                "source_family": "public_radio",
                "state": "OR",
                "location_name": "Oregon",
                "location_scope": "state_local",
                "candidate_reason": "Keep candidate.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": ["SNAP", "food bank"],
                "status": "candidate",
                "notes": "Seeded candidate.",
            },
            {
                "source_id": "candidate-reject",
                "source_name": "Candidate Reject",
                "publisher": "Candidate Publisher",
                "candidate_url": "https://example.com/reject.rss",
                "source_family": "local_news",
                "state": "CA",
                "location_name": "California",
                "location_scope": "state_local",
                "candidate_reason": "Reject candidate.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": ["SNAP", "food bank"],
                "status": "candidate",
                "notes": "Seeded candidate.",
            },
        ],
    )
    rss_payloads = {
        "enable": _rss_payload(
            [
                {
                    "title": "Food bank sees rising demand from families",
                    "link": "https://example.com/enable-item",
                    "description": "Food bank demand increased and pantry lines grew.",
                }
            ]
        ),
        "keep": _rss_payload(
            [
                {
                    "title": "Community newsletter",
                    "link": "https://example.com/keep-item",
                    "description": "General community updates without pressure evidence.",
                }
            ]
        ),
        "reject": _rss_payload(
            [
                {
                    "title": "Recipe roundup for summer dinners",
                    "link": "https://example.com/reject-item",
                    "description": "Lifestyle recipe ideas and cooking tips.",
                }
            ]
        ),
    }

    def fetcher(url: str, timeout: int = 15):
        if "enable" in url:
            return rss_payloads["enable"]
        if "keep" in url:
            return rss_payloads["keep"]
        return rss_payloads["reject"]

    result = run_food_line_candidate_sources(tmp_path, date, fetcher=fetcher, promote_enabled=True)
    review_path = Path(result["candidate_review_path"])
    promotion_path = Path(result["candidate_promotion_report_path"])
    assert review_path.exists()
    assert promotion_path.exists()
    with review_path.open(encoding="utf-8") as handle:
        review = {row["source_id"]: row for row in csv.DictReader(handle)}
    assert "noise_score" in review["candidate-enable"]
    assert "pressure_hit_rate" in review["candidate-enable"]
    assert "negative_hit_count" in review["candidate-enable"]
    assert "useful_text_available" in review["candidate-enable"]
    assert "source_purpose" in review["candidate-enable"]
    assert "current_or_evergreen" in review["candidate-enable"]
    assert "promotable" in review["candidate-enable"]
    assert "non_promotable_reason" in review["candidate-enable"]
    assert review["candidate-enable"]["source_purpose"] == "current_news"
    with promotion_path.open(encoding="utf-8") as handle:
        promotion = {row["source_id"]: row for row in csv.DictReader(handle)}
    assert promotion["candidate-enable"]["promoted"] == "True"
    assert promotion["candidate-keep"]["promoted"] == "False"
    assert promotion["candidate-reject"]["promoted"] == "False"
    candidate_registry = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "candidate_source_registry.json").read_text(encoding="utf-8"))
    status_by_id = {row["source_id"]: row["status"] for row in candidate_registry}
    assert status_by_id["candidate-enable"] == "enabled"
    assert status_by_id["candidate-keep"] == "candidate"
    assert status_by_id["candidate-reject"] in {"rejected", "quarantined"}
    pressure_registry_path = tmp_path / "data" / "dispatches" / "food-line" / "pressure_source_registry.json"
    first_registry = json.loads(pressure_registry_path.read_text(encoding="utf-8"))
    assert sum(1 for row in first_registry if row["source_id"] == "candidate-enable") == 1

    second_result = run_food_line_candidate_sources(tmp_path, date, fetcher=fetcher, promote_enabled=True)
    second_registry = json.loads(pressure_registry_path.read_text(encoding="utf-8"))
    assert sum(1 for row in second_registry if row["source_id"] == "candidate-enable") == 1
    assert second_result["promoted_candidate_count"] == 1


def test_food_line_candidate_sources_block_non_promotable_source_purposes(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-10"
    donation_url = "https://www.feedingamerica.org/ways-to-give/monthly-giving"
    evergreen_url = "https://www.feedingamerica.org/research/hunger-and-poverty-united-states"
    resource_url = "https://www.example.org/find-food"
    current_url = "https://example.com/current.rss"
    _write_candidate_registry(
        tmp_path,
        [
            {
                "source_id": "candidate-donation",
                "source_name": "Monthly Giving & Recurring Donations",
                "publisher": "Feeding America",
                "candidate_url": donation_url,
                "source_family": "food_bank_provider",
                "state": "US",
                "location_name": "United States",
                "location_scope": "national",
                "candidate_reason": "Donation page.",
                "expected_text_basis": "page_text",
                "extraction_quality_guess": "medium",
                "pressure_topics_expected": ["donate"],
                "status": "candidate",
                "notes": "Donation page should stay blocked.",
            },
            {
                "source_id": "candidate-evergreen",
                "source_name": "Hunger & Poverty in the United States",
                "publisher": "Feeding America",
                "candidate_url": evergreen_url,
                "source_family": "food_bank_provider",
                "state": "US",
                "location_name": "United States",
                "location_scope": "national",
                "candidate_reason": "Evergreen explainer.",
                "expected_text_basis": "page_text",
                "extraction_quality_guess": "medium",
                "pressure_topics_expected": ["hunger facts"],
                "status": "candidate",
                "notes": "Evergreen context should stay blocked.",
            },
            {
                "source_id": "candidate-resource",
                "source_name": "Find food near you",
                "publisher": "Example Resource",
                "candidate_url": resource_url,
                "source_family": "food_bank_provider",
                "state": "US",
                "location_name": "United States",
                "location_scope": "national",
                "candidate_reason": "Resource page.",
                "expected_text_basis": "page_text",
                "extraction_quality_guess": "medium",
                "pressure_topics_expected": ["find food"],
                "status": "candidate",
                "notes": "Resource page should stay blocked.",
            },
            {
                "source_id": "candidate-current",
                "source_name": "KLTV current demand article",
                "publisher": "KLTV",
                "candidate_url": current_url,
                "source_family": "local_news",
                "state": "TX",
                "location_name": "East Texas, TX",
                "location_scope": "state_local",
                "candidate_reason": "Current pressure article.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": ["food bank", "demand"],
                "status": "candidate",
                "notes": "Current pressure story should remain promotable.",
            },
        ],
    )
    _write_pressure_registry(tmp_path, [])

    def fetcher(url: str, timeout: int = 15):
        if url == donation_url:
            return b"""<html><head><title>Monthly Giving &amp; Recurring Donations</title></head><body><p>Donate now and give monthly.</p></body></html>"""
        if url == evergreen_url:
            return b"""<html><head><title>Hunger &amp; Poverty in the United States</title></head><body><p>Hunger facts and research overview.</p></body></html>"""
        if url == resource_url:
            return b"""<html><head><title>Find food near you</title></head><body><p>Use our food bank locator and eligibility guide.</p></body></html>"""
        if url == current_url:
            return b"""<?xml version='1.0' encoding='UTF-8'?><rss version='2.0'><channel><item><title>Food bank sees rising demand from families</title><link>https://example.com/current-item</link><description>Food bank demand increased and pantry lines grew.</description></item></channel></rss>"""
        raise AssertionError(f"unexpected url: {url}")

    result = run_food_line_candidate_sources(tmp_path, date, fetcher=fetcher, promote_enabled=True)
    with Path(result["candidate_review_path"]).open(encoding="utf-8") as handle:
        review = {row["source_id"]: row for row in csv.DictReader(handle)}
    assert review["candidate-donation"]["source_purpose"] == "donation_page"
    assert review["candidate-donation"]["recommendation"] == "reject"
    assert review["candidate-evergreen"]["source_purpose"] == "evergreen_context"
    assert review["candidate-evergreen"]["recommendation"] == "keep_candidate"
    assert review["candidate-resource"]["source_purpose"] == "resource_page"
    assert review["candidate-resource"]["recommendation"] == "keep_candidate"
    assert review["candidate-current"]["source_purpose"] == "current_news"
    assert review["candidate-current"]["recommendation"] == "enable"
    assert result["promoted_blocked_by_source_purpose_count"] >= 3
    assert result["rejected_by_source_purpose_count"] >= 1
    with Path(result["candidate_promotion_report_path"]).open(encoding="utf-8") as handle:
        promotion = {row["source_id"]: row for row in csv.DictReader(handle)}
    assert promotion["candidate-donation"]["promoted"] == "False"
    assert promotion["candidate-evergreen"]["promoted"] == "False"
    assert promotion["candidate-resource"]["promoted"] == "False"
    assert promotion["candidate-current"]["promoted"] == "True"
    candidate_registry = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "candidate_source_registry.json").read_text(encoding="utf-8"))
    status_by_id = {row["source_id"]: row["status"] for row in candidate_registry}
    assert status_by_id["candidate-donation"] in {"rejected", "quarantined"}
    assert status_by_id["candidate-evergreen"] in {"candidate", "tested_weak"}
    assert status_by_id["candidate-resource"] in {"candidate", "tested_weak"}
    assert status_by_id["candidate-current"] == "enabled"


def test_food_line_candidate_discovery_notes_and_intake_template_exist():
    notes_path = Path("data") / "dispatches" / "food-line" / "candidate_source_discovery_notes.md"
    intake_path = Path("data") / "dispatches" / "food-line" / "candidate_source_intake_template.csv"
    notes = notes_path.read_text(encoding="utf-8")
    header = intake_path.read_text(encoding="utf-8").strip().splitlines()[0]
    assert notes_path.exists()
    assert intake_path.exists()
    assert "## A. National recurring sources" in notes
    assert "## B. State and local public media targets" in notes
    assert "## C. Food bank and provider targets" in notes
    assert "## D. Official pressure targets" in notes
    assert '"source_type": "rss"' in notes
    assert "Manual validation checklist" in notes
    assert "Invoke-WebRequest" in notes
    assert "python scripts\\test_food_line_candidate_sources.py --date 2026-06-08" in notes
    expected_columns = [
        "source_id",
        "source_name",
        "publisher",
        "candidate_url",
        "source_family",
        "source_type",
        "state",
        "location_name",
        "location_scope",
        "candidate_reason",
        "expected_text_basis",
        "extraction_quality_guess",
        "pressure_topics_expected",
        "status",
        "notes",
    ]
    assert header.split(",") == expected_columns


def test_food_line_source_purpose_blocks_donation_evergreen_and_resource_pages(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-10"
    donation_url = "https://www.feedingamerica.org/ways-to-give/monthly-giving"
    evergreen_url = "https://www.feedingamerica.org/research/hunger-and-poverty-united-states"
    resource_url = "https://www.example.org/find-food"
    valid_url = "https://www.kltv.com/food-bank-demand"
    _write_pressure_registry(
        tmp_path,
        [
            {
                "source_id": "fa-donation",
                "source_name": "Monthly Giving & Recurring Donations",
                "publisher": "Feeding America",
                "url": donation_url,
                "source_family": "food_bank_provider",
                "source_type": "page",
                "state": "US",
                "location_name": "United States",
                "location_scope": "national",
                "extraction_quality": "medium",
                "expected_text_basis": "page_text",
                "pressure_verification_required": True,
                "positive_keywords": ["donate", "monthly giving"],
                "negative_keywords": ["donate"],
                "affected_group_keywords": ["low-income households"],
                "enabled": True,
                "notes": "Donation page should not be mapped as pressure.",
            },
            {
                "source_id": "fa-evergreen",
                "source_name": "Hunger & Poverty in the United States",
                "publisher": "Feeding America",
                "url": evergreen_url,
                "source_family": "food_bank_provider",
                "source_type": "page",
                "state": "US",
                "location_name": "United States",
                "location_scope": "national",
                "extraction_quality": "medium",
                "expected_text_basis": "page_text",
                "pressure_verification_required": True,
                "positive_keywords": ["hunger facts"],
                "negative_keywords": ["hunger"],
                "affected_group_keywords": ["low-income households"],
                "enabled": True,
                "notes": "Evergreen context page should not be mapped as pressure.",
            },
            {
                "source_id": "fa-resource",
                "source_name": "Find food near you",
                "publisher": "Feeding America",
                "url": resource_url,
                "source_family": "food_bank_provider",
                "source_type": "page",
                "state": "US",
                "location_name": "United States",
                "location_scope": "national",
                "extraction_quality": "medium",
                "expected_text_basis": "page_text",
                "pressure_verification_required": True,
                "positive_keywords": ["find food"],
                "negative_keywords": ["find food"],
                "affected_group_keywords": ["low-income households"],
                "enabled": True,
                "notes": "Resource page should not be mapped as pressure.",
            },
            {
                "source_id": "kltv-valid",
                "source_name": "KLTV",
                "publisher": "KLTV",
                "url": valid_url,
                "source_family": "local_news",
                "source_type": "page",
                "state": "TX",
                "location_name": "East Texas, TX",
                "location_scope": "state_local",
                "extraction_quality": "medium",
                "expected_text_basis": "page_text",
                "pressure_verification_required": True,
                "positive_keywords": ["food bank", "demand"],
                "negative_keywords": ["recipe"],
                "affected_group_keywords": ["SNAP households"],
                "enabled": True,
                "notes": "Current local pressure article should remain mapped.",
            },
        ],
    )

    def fetcher(url: str, timeout: int = 15):
        if url == donation_url:
            return b"""<html><head><title>Monthly Giving &amp; Recurring Donations</title></head><body><p>Donate now and give monthly.</p></body></html>"""
        if url == evergreen_url:
            return b"""<html><head><title>Hunger &amp; Poverty in the United States</title></head><body><p>Hunger facts and research overview.</p></body></html>"""
        if url == resource_url:
            return b"""<html><head><title>Find food near you</title></head><body><p>Use our food bank locator and eligibility guide.</p></body></html>"""
        if url == valid_url:
            return b"""<html><head><title>KLTV reports rising food-bank demand</title><meta property='article:published_time' content='2026-06-10T12:00:00Z'><meta name='description' content='Food banks across Texas are working hard to keep up with rising demand.'></head><body><p>The government shutdown is now in its 4th week and food banks across Texas are working hard to keep up with rising demand. Michael Close, Chief Operating Officer at Swan Food Pantry, has seen a 17% increase in people asking for food assistance.</p></body></html>"""
        raise AssertionError(f"unexpected url: {url}")

    collect_result = food_line.collect_food_line_auto_sources(tmp_path, date, fetcher=fetcher)
    assert collect_result["rejected_by_source_purpose_count"] >= 3
    assert collect_result["demoted_by_source_purpose_count"] >= 3
    result = run_food_line_dispatch(tmp_path, date, collect=False)
    assert result["pressure_verified_count"] == 1
    assert result["pressure_marker_count"] == 1
    assert result["edition_mode"] == "no_public_edition"
    assert result["source_freshness_status"] == "blocked_insufficient_current_story_sources"
    pressure_registry = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "pressure_source_registry.json").read_text(encoding="utf-8"))
    by_id = {row["source_id"]: row for row in pressure_registry}
    assert by_id["fa-donation"]["enabled"] is False
    assert by_id["fa-donation"]["source_purpose"] == "donation_page"
    assert by_id["fa-evergreen"]["enabled"] is False
    assert by_id["fa-evergreen"]["source_purpose"] == "evergreen_context"
    assert by_id["fa-resource"]["enabled"] is False
    assert by_id["fa-resource"]["source_purpose"] == "resource_page"
    assert "not current pressure evidence" in by_id["fa-donation"]["notes"].lower()


def test_food_line_public_inclusion_rejects_wrapper_discovery_leads():
    row = {
        "source_id": "wrapper-lead",
        "source_role": "discovery_lead",
        "donation_wrapper": True,
        "public_eligible": False,
        "source_public_story_eligible": True,
        "supported_product_geography": True,
        "location_scope": "state_local",
        "pressure_signal": True,
        "pressure_verification_status": "source_text_verified",
        "pressure_type": "demand strain",
        "url": "https://example.com/wrapper",
        "source_family": "local_news",
    }
    reason = food_line._food_line_public_inclusion_reason(row)
    assert reason == "discovery lead only / not public eligible"
    assert food_line._food_line_qualifies_for_public_inclusion(row) is False


def test_food_line_discovery_prefilters_obvious_non_pressure_pages_and_dedupes(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-11"
    donation_url = "https://example.com/donate"
    recipe_url = "https://example.com/recipe"
    resource_url = "https://example.com/find-food"
    evergreen_url = "https://example.com/hunger-facts"
    valid_url = "https://example.com/pressure-story"
    _write_source_registry(
        tmp_path,
        [
            {"source_id": "donation-seed", "source_name": "Donate Monthly", "publisher": "Example Provider", "url": donation_url, "source_family": "food_bank_provider", "source_type": "page", "state": "US", "location_name": "United States", "location_scope": "national", "enabled": True},
            {"source_id": "recipe-seed", "source_name": "Recipe roundup", "publisher": "Example Media", "url": recipe_url, "source_family": "local_news", "source_type": "page", "state": "TX", "location_name": "Austin, TX", "location_scope": "state_local", "enabled": True},
            {"source_id": "resource-seed", "source_name": "Find food near you", "publisher": "Example Provider", "url": resource_url, "source_family": "food_bank_provider", "source_type": "page", "state": "US", "location_name": "United States", "location_scope": "national", "enabled": True},
            {"source_id": "evergreen-seed", "source_name": "Hunger & Poverty in the United States", "publisher": "Example Provider", "url": evergreen_url, "source_family": "food_bank_provider", "source_type": "page", "state": "US", "location_name": "United States", "location_scope": "national", "enabled": True},
            {"source_id": "valid-seed", "source_name": "Rising demand story", "publisher": "Example News", "url": valid_url, "source_family": "local_news", "source_type": "page", "state": "TX", "location_name": "Austin, TX", "location_scope": "state_local", "enabled": True},
        ],
    )
    _write_candidate_registry(tmp_path, [])
    _write_pressure_registry(tmp_path, [])
    _write_json(
        tmp_path / "data" / "dispatches" / "food-line" / "source_discovery_queries.json",
        [
            {
                "query": "food bank demand",
                "query_id": "q-food-bank-demand",
                "query_text": "food bank demand",
                "query_template": "food bank demand",
                "category": "local_news",
                "source_family": "local_news",
                "search_provider": "google_news",
                "discovery_channel": "search",
            }
        ],
    )

    def fetcher(url: str, timeout: int = 15):
        if url == donation_url:
            return b"<html><head><title>Donate Monthly</title></head><body><p>Donate now to support our work.</p></body></html>"
        if url == recipe_url:
            return b"<html><head><title>Recipe roundup</title></head><body><p>Recipe ideas and cooking tips.</p></body></html>"
        if url == resource_url:
            return b"<html><head><title>Find food near you</title></head><body><p>Use our food bank locator.</p></body></html>"
        if url == evergreen_url:
            return b"<html><head><title>Hunger &amp; Poverty in the United States</title></head><body><p>Hunger facts and statistics.</p></body></html>"
        if url == valid_url:
            return b"<html><head><title>Food bank sees rising demand</title></head><body><p>Food bank demand is rising and pantry lines grew.</p></body></html>"
        raise AssertionError(f"unexpected url: {url}")

    result = discover_food_line_sources(tmp_path, date, fetcher=fetcher, write_candidates=True)
    with Path(result["review_path"]).open(encoding="utf-8") as handle:
        review = {row["candidate_url"]: row for row in csv.DictReader(handle)}
    assert review[donation_url]["action"] == "rejected_discovery"
    assert review[donation_url]["rejected_by_prefilter"] == "true"
    assert review[recipe_url]["action"] == "rejected_discovery"
    assert review[recipe_url]["reason"]
    assert review[resource_url]["action"] == "rejected_discovery"
    assert review[evergreen_url]["action"] == "rejected_discovery"
    assert review[valid_url]["action"] == "inserted_candidate"
    assert "source_quality_score" in review[valid_url]
    registry = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "candidate_source_registry.json").read_text(encoding="utf-8"))
    assert len(registry) == 1

    result_again = discover_food_line_sources(tmp_path, date, fetcher=fetcher, write_candidates=True)
    registry_again = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "candidate_source_registry.json").read_text(encoding="utf-8"))
    assert len(registry_again) == 1
    assert result_again["updated_count"] >= 1


def test_food_line_discovery_preserves_query_attribution_and_history(tmp_path: Path):
    discovered = food_line_discovery._candidate_fields_from_discovery(  # type: ignore[attr-defined]
        discovered_url="https://example.com/food-bank-demand",
        source_name="Food bank demand story",
        publisher="Example News",
        source_family="local_news",
        source_type="page",
        state="NE",
        location_name="Omaha, NE",
        location_scope="state_local",
        reason="Seed page text supports manual review from Food bank demand story",
        pressure_terms=["food bank", "demand"],
        notes="Discovered from https://example.com/food-bank-demand",
        source_purpose="current_news",
        current_or_evergreen="current",
        promotable=True,
        non_promotable_reason="",
        source_quality_score=85,
        source_quality_tier="high",
        auto_discovered=True,
        first_discovered_at="2026-06-11T00:00:00Z",
        last_discovered_at="2026-06-11T00:00:00Z",
        discovery_count=1,
        last_recommendation="candidate",
        last_recommendation_reason="Seed page text supports manual review from Food bank demand story",
        source_seed_url="https://example.com/food-bank-demand",
        discovery_seed_url="https://example.com/food-bank-demand",
        discovered_from="seed_page",
        retrieved_at="2026-06-11T00:00:00Z",
        published_at="2026-06-11T00:00:00Z",
        page_metadata_date="2026-06-11T00:00:00Z",
        evidence_text="Food bank demand is rising and pantry lines grew.",
        evidence_text_basis="page_text_excerpt",
    )
    discovery_meta = {
        "discovery_method": "seed_page",
        "discovery_query": "food bank demand",
        "query_template": "food bank demand",
        "discovery_query_id": "q-food-bank-demand",
        "discovery_query_text": "food bank demand",
        "discovery_query_group": "local_news",
        "discovery_queries": ["food bank demand", "q-food-bank-demand"],
        "discovery_query_ids": ["q-food-bank-demand", "food bank demand"],
        "discovery_query_texts": ["food bank demand"],
        "discovery_query_groups": ["local_news"],
        "discovery_channels": ["page"],
        "discovery_providers": ["google_news"],
        "original_discovery_urls": ["https://news.google.com/rss/search?q=food+bank+demand"],
        "resolved_source_urls": ["https://example.com/food-bank-demand"],
        "collector_run_ids": ["2026-06-11T00:00:00Z"],
        "discovered_at": "2026-06-11T00:00:00Z",
    }
    merged = food_line_discovery._merge_candidate(  # type: ignore[attr-defined]
        {
            "source_id": "seed-food-bank-demand",
            "source_name": "Food bank demand story",
            "publisher": "Example News",
            "candidate_url": "https://example.com/food-bank-demand",
            "source_family": "local_news",
            "status": "candidate",
        },
        discovered,
        discovery_meta,
    )

    assert merged["discovery_query_id"] == ["q-food-bank-demand"]
    assert merged["discovery_query_text"] == ["food bank demand"]
    assert merged["discovery_query_group"] == ["local_news"]
    assert merged["discovery_queries"] == ["food bank demand", "q-food-bank-demand"]
    assert merged["discovery_query_ids"] == ["q-food-bank-demand", "food bank demand"]
    assert merged["discovery_query_texts"] == ["food bank demand"]
    assert merged["discovery_channels"] == ["page"]
    assert merged["discovery_providers"] == ["google_news"]
    assert merged["original_discovery_urls"] == ["https://news.google.com/rss/search?q=food+bank+demand"]
    assert merged["resolved_source_urls"] == ["https://example.com/food-bank-demand"]
    assert merged["collector_run_ids"] == ["2026-06-11T00:00:00Z"]


def test_food_line_candidate_quarantine_cleanup_and_include_quarantined(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-11"
    quarantined_url = "https://example.com/quarantined.rss"
    clean_url = "https://example.com/clean.rss"
    noisy_url = "https://example.com/noisy.rss"
    _write_candidate_registry(
        tmp_path,
        [
            {
                "source_id": "candidate-quarantined",
                "source_name": "Quarantined Candidate",
                "publisher": "Candidate Publisher",
                "candidate_url": quarantined_url,
                "source_family": "local_news",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "Quarantined candidate.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": ["SNAP", "food bank"],
                "status": "quarantined",
                "notes": "Should stay skipped unless explicitly included.",
                "reject_count": 3,
            },
            {
                "source_id": "candidate-clean",
                "source_name": "Clean Candidate",
                "publisher": "Candidate Publisher",
                "candidate_url": clean_url,
                "source_family": "local_news",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "Clean candidate.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": ["SNAP", "food bank"],
                "status": "candidate",
                "notes": "Clean candidate.",
            },
            {
                "source_id": "candidate-noisy",
                "source_name": "Noisy Candidate",
                "publisher": "Candidate Publisher",
                "candidate_url": noisy_url,
                "source_family": "local_news",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "Noisy candidate.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": ["SNAP", "food bank"],
                "status": "candidate",
                "notes": "Noisy candidate.",
            },
        ],
    )
    _write_pressure_registry(tmp_path, [])

    def fetcher(url: str, timeout: int = 15):
        if url == quarantined_url:
            return b"""<?xml version='1.0' encoding='UTF-8'?><rss version='2.0'><channel><item><title>Food bank sees rising demand from families</title><link>https://example.com/quarantined-item</link><description>Food bank demand increased and pantry lines grew.</description></item></channel></rss>"""
        if url == clean_url:
            return b"""<?xml version='1.0' encoding='UTF-8'?><rss version='2.0'><channel><item><title>Food bank sees rising demand from families</title><link>https://example.com/clean-item</link><description>Food bank demand increased and pantry lines grew.</description></item></channel></rss>"""
        if url == noisy_url:
            return b"""<?xml version='1.0' encoding='UTF-8'?><rss version='2.0'><channel><item><title>Food bank sees rising demand from families</title><link>https://example.com/noisy-item</link><description>Food bank demand increased and pantry lines grew.</description></item><item><title>Recipe roundup for summer dinners</title><link>https://example.com/noisy-recipe</link><description>Recipe ideas and cooking tips.</description></item><item><title>Menu ideas for the weekend</title><link>https://example.com/noisy-menu</link><description>Restaurant and menu coverage.</description></item><item><title>Festival guide</title><link>https://example.com/noisy-fest</link><description>Food festival coverage.</description></item></channel></rss>"""
        raise AssertionError(f"unexpected url: {url}")

    default_result = run_food_line_candidate_sources(tmp_path, date, fetcher=fetcher, promote_enabled=True)
    with Path(default_result["candidate_review_path"]).open(encoding="utf-8") as handle:
        default_review = {row["source_id"]: row for row in csv.DictReader(handle)}
    assert default_review["candidate-quarantined"]["recommendation"] == "skip_quarantined"
    assert default_result["quarantined_skipped_count"] == 1
    with Path(default_result["candidate_promotion_report_path"]).open(encoding="utf-8") as handle:
        promotion = {row["source_id"]: row for row in csv.DictReader(handle)}
    assert promotion["candidate-clean"]["promoted"] == "True"
    assert promotion["candidate-noisy"]["promoted"] == "False"
    registry_after_default = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "candidate_source_registry.json").read_text(encoding="utf-8"))
    by_id_default = {row["source_id"]: row for row in registry_after_default}
    assert by_id_default["candidate-quarantined"]["status"] == "quarantined"
    assert by_id_default["candidate-clean"]["status"] == "enabled"
    assert by_id_default["candidate-noisy"]["status"] == "tested_good"

    include_result = run_food_line_candidate_sources(tmp_path, date, fetcher=fetcher, promote_enabled=True, include_quarantined=True)
    with Path(include_result["candidate_review_path"]).open(encoding="utf-8") as handle:
        include_review = {row["source_id"]: row for row in csv.DictReader(handle)}
    assert include_review["candidate-quarantined"]["recommendation"] == "enable"
    with Path(include_result["candidate_promotion_report_path"]).open(encoding="utf-8") as handle:
        include_promotion = {row["source_id"]: row for row in csv.DictReader(handle)}
    assert include_promotion["candidate-quarantined"]["promoted"] == "False"
    assert include_promotion["candidate-noisy"]["promoted"] == "False"
    registry_after_include = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "candidate_source_registry.json").read_text(encoding="utf-8"))
    by_id_include = {row["source_id"]: row for row in registry_after_include}
    assert by_id_include["candidate-quarantined"]["status"] == "quarantined"
    assert by_id_include["candidate-clean"]["status"] == "enabled"
    assert by_id_include["candidate-noisy"]["status"] == "tested_good"


def test_food_line_candidate_cleanup_reports_and_changes_statuses(tmp_path: Path):
    _ensure_assets(tmp_path)
    _write_candidate_registry(
        tmp_path,
        [
            {
                "source_id": "candidate-reject-three",
                "source_name": "Reject Three",
                "publisher": "Publisher",
                "candidate_url": "https://example.com/reject-three.rss",
                "source_family": "local_news",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "Repeated rejects.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "low",
                "pressure_topics_expected": ["SNAP"],
                "status": "rejected",
                "notes": "Repeated rejects.",
                "reject_count": 3,
            },
            {
                "source_id": "candidate-archive",
                "source_name": "Archive Candidate",
                "publisher": "Publisher",
                "candidate_url": "https://example.com/archive.rss",
                "source_family": "local_news",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "Repeated failure.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "low",
                "pressure_topics_expected": ["SNAP"],
                "status": "tested_failed",
                "notes": "Repeated failure.",
                "reject_count": 1,
            },
            {
                "source_id": "candidate-enabled",
                "source_name": "Enabled Candidate",
                "publisher": "Publisher",
                "candidate_url": "https://example.com/enabled.rss",
                "source_family": "local_news",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "Enabled source.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": ["SNAP"],
                "status": "enabled",
                "notes": "Enabled source.",
            },
        ],
    )
    history_path = tmp_path / "data" / "dispatches" / "food-line" / "source_performance_history.json"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(
        json.dumps(
            {
                "candidate-archive": {
                    "runs_seen": 4,
                    "runs_fetched": 1,
                    "fetch_failures": 3,
                    "items_seen": 0,
                    "verified_pressure_records": 0,
                    "demoted_records": 0,
                    "rejected_records": 0,
                    "last_verified_pressure_at": "",
                    "last_fetch_error": "HTTPError: 403",
                    "rolling_quality_score": 5,
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    result = cleanup_food_line_candidates(tmp_path, mode="normal")
    report_path = Path(result["cleanup_report_path"])
    health_path = Path(result["source_registry_health_report_path"])
    assert report_path.exists()
    assert health_path.exists()
    with report_path.open(encoding="utf-8") as handle:
        report = {row["source_id"]: row for row in csv.DictReader(handle)}
    assert report["candidate-reject-three"]["new_status"] == "archived"
    assert report["candidate-archive"]["new_status"] == "archived"
    assert result["candidate_count_before"] == 3
    assert result["candidate_count_after"] == 3
    registry = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "candidate_source_registry.json").read_text(encoding="utf-8"))
    by_id = {row["source_id"]: row for row in registry}
    assert by_id["candidate-reject-three"]["status"] == "archived"
    assert by_id["candidate-archive"]["status"] == "archived"
    assert by_id["candidate-enabled"]["status"] == "enabled"


def test_food_line_candidate_cleanup_modes_and_dry_run(tmp_path: Path):
    _ensure_assets(tmp_path)
    _write_candidate_registry(
        tmp_path,
        [
            {
                "source_id": "candidate-reject-two",
                "source_name": "Reject Two",
                "publisher": "Publisher",
                "candidate_url": "https://example.com/reject-two.rss",
                "source_family": "local_news",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "Repeated rejects.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "low",
                "pressure_topics_expected": ["SNAP"],
                "status": "candidate",
                "notes": "Repeated rejects.",
                "reject_count": 2,
                "test_count": 2,
            },
            {
                "source_id": "candidate-enabled-two",
                "source_name": "Enabled Two",
                "publisher": "Publisher",
                "candidate_url": "https://example.com/enabled-two.rss",
                "source_family": "local_news",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "Enabled source.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": ["SNAP"],
                "status": "enabled",
                "notes": "Enabled source.",
            },
        ],
    )
    (tmp_path / "output" / "review" / "food-line" / "2026-06-11").mkdir(parents=True, exist_ok=True)
    review_path = tmp_path / "output" / "review" / "food-line" / "2026-06-11" / "candidate_source_review.csv"
    with review_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["source_id", "useful_text_available", "recommendation", "noise_score", "fetch_error"])
        writer.writeheader()
        writer.writerow({"source_id": "candidate-reject-two", "useful_text_available": "false", "recommendation": "reject", "noise_score": 95, "fetch_error": ""})
        writer.writerow({"source_id": "candidate-enabled-two", "useful_text_available": "true", "recommendation": "enable", "noise_score": 0, "fetch_error": ""})
    dry_run = cleanup_food_line_candidates(tmp_path, mode="conservative", dry_run=True)
    assert dry_run["dry_run"] is True
    assert dry_run["mode"] == "conservative"
    assert Path(dry_run["cleanup_report_path"]).exists()
    registry_after_dry_run = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "candidate_source_registry.json").read_text(encoding="utf-8"))
    by_id_dry_run = {row["source_id"]: row for row in registry_after_dry_run}
    assert by_id_dry_run["candidate-reject-two"]["status"] == "candidate"
    normal = cleanup_food_line_candidates(tmp_path, mode="normal")
    assert normal["quarantined_count"] == 0
    assert normal["archived_count"] >= 1
    registry_after_normal = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "candidate_source_registry.json").read_text(encoding="utf-8"))
    by_id_normal = {row["source_id"]: row for row in registry_after_normal}
    assert by_id_normal["candidate-reject-two"]["status"] == "archived"
    aggressive = cleanup_food_line_candidates(tmp_path, mode="aggressive")
    registry_after_aggressive = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "candidate_source_registry.json").read_text(encoding="utf-8"))
    by_id_aggressive = {row["source_id"]: row for row in registry_after_aggressive}
    assert by_id_aggressive["candidate-enabled-two"]["status"] == "enabled"


def test_food_line_candidate_cleanup_archives_repeated_broken_no_text_candidates(tmp_path: Path):
    _ensure_assets(tmp_path)
    _write_candidate_registry(
        tmp_path,
        [
            {
                "source_id": "candidate-broken",
                "source_name": "Broken Candidate",
                "publisher": "Publisher",
                "candidate_url": "https://example.com/broken.rss",
                "source_family": "local_news",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "Broken candidate.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "low",
                "pressure_topics_expected": ["SNAP"],
                "status": "tested_failed",
                "notes": "Broken candidate.",
                "reject_count": 3,
                "test_count": 3,
            }
        ],
    )
    history_path = tmp_path / "data" / "dispatches" / "food-line" / "source_performance_history.json"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(
        json.dumps(
            {
                "candidate-broken": {
                    "runs_seen": 4,
                    "runs_fetched": 1,
                    "fetch_failures": 3,
                    "items_seen": 0,
                    "verified_pressure_records": 0,
                    "demoted_records": 0,
                    "rejected_records": 0,
                    "last_verified_pressure_at": "",
                    "last_fetch_error": "HTTPError: 403",
                    "rolling_quality_score": 5,
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    review_dir = tmp_path / "output" / "review" / "food-line" / "2026-06-11"
    review_dir.mkdir(parents=True, exist_ok=True)
    with (review_dir / "candidate_source_review.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["source_id", "useful_text_available", "recommendation", "noise_score", "fetch_error"])
        writer.writeheader()
        writer.writerow({"source_id": "candidate-broken", "useful_text_available": "false", "recommendation": "reject", "noise_score": 100, "fetch_error": "HTTPError: 403"})
    result = cleanup_food_line_candidates(tmp_path, mode="normal")
    assert result["archived_count"] == 1
    registry = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "candidate_source_registry.json").read_text(encoding="utf-8"))
    by_id = {row["source_id"]: row for row in registry}
    assert by_id["candidate-broken"]["status"] == "archived"


def test_food_line_source_performance_history_updates_on_collection(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-11"
    _write_source_registry(
        tmp_path,
        [
            {
                "source_id": "history-source",
                "source_name": "History Source",
                "publisher": "Example News",
                "url": "https://example.com/history.rss",
                "source_family": "local_news",
                "source_type": "rss",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "enabled": True,
                "pressure_verification_required": True,
                "expected_text_basis": "rss_summary",
                "extraction_quality": "high",
            }
        ],
    )
    _write_candidate_registry(tmp_path, [])
    _write_pressure_registry(tmp_path, [])

    def fetcher(url: str, timeout: int = 15):
        if url == "https://example.com/history.rss":
            return b"""<?xml version='1.0' encoding='UTF-8'?><rss version='2.0'><channel><item><title>Food bank sees rising demand</title><link>https://example.com/history-item</link><description>Food bank demand increased and pantry lines grew.</description></item></channel></rss>"""
        raise AssertionError(f"unexpected url: {url}")

    result = food_line.collect_food_line_auto_sources(tmp_path, date, fetcher=fetcher)
    assert result["collector_audit_path"]
    history_path = tmp_path / "data" / "dispatches" / "food-line" / "source_performance_history.json"
    assert history_path.exists()
    history = json.loads(history_path.read_text(encoding="utf-8"))
    assert "history-source" in history
    assert history["history-source"]["runs_seen"] >= 1
    assert history["history-source"]["items_seen"] >= 1


def test_food_line_candidate_intake_imports_valid_rows_and_skips_templates(tmp_path: Path):
    _ensure_assets(tmp_path)
    existing_registry = _write_candidate_registry(
        tmp_path,
        [
            {
                "source_id": "candidate-update",
                "source_name": "Existing Candidate",
                "publisher": "Existing Publisher",
                "candidate_url": "https://example.com/existing.rss",
                "source_family": "public_radio",
                "state": "OR",
                "location_name": "Portland, OR",
                "location_scope": "state_local",
                "candidate_reason": "Existing candidate.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": ["SNAP"],
                "status": "candidate",
                "notes": "Keep this note.",
            }
        ],
    )
    _write_pressure_registry(tmp_path, [])
    csv_path = _write_intake_csv(
        tmp_path,
        [
            {
                "source_id": "",
                "source_name": "",
                "publisher": "",
                "candidate_url": "",
                "source_family": "",
                "source_type": "",
                "state": "",
                "location_name": "",
                "location_scope": "",
                "candidate_reason": "",
                "expected_text_basis": "",
                "extraction_quality_guess": "",
                "pressure_topics_expected": "",
                "status": "",
                "notes": "",
            },
            {
                "source_id": "candidate-new",
                "source_name": "Candidate New",
                "publisher": "New Publisher",
                "candidate_url": "https://example.com/new.rss",
                "source_family": "local_news",
                "source_type": "rss",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "New imported candidate.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": "SNAP|food bank",
                "status": "",
                "notes": "Imported candidate.",
            },
            {
                "source_id": "candidate-update",
                "source_name": "Candidate Updated",
                "publisher": "",
                "candidate_url": "https://example.com/existing.rss",
                "source_family": "public_radio",
                "source_type": "rss",
                "state": "",
                "location_name": "",
                "location_scope": "",
                "candidate_reason": "",
                "expected_text_basis": "",
                "extraction_quality_guess": "",
                "pressure_topics_expected": "",
                "status": "tested_good",
                "notes": "",
            },
        ],
    )
    result = import_food_line_candidate_intake(tmp_path, csv_path)
    assert result["imported_count"] == 1
    assert result["updated_count"] == 1
    assert result["skipped_count"] == 1
    assert result["rejected_count"] == 0
    report_path = Path(result["report_path"])
    assert report_path.exists()
    with report_path.open(encoding="utf-8") as handle:
        report = list(csv.DictReader(handle))
    actions = {row["source_id"]: row["action"] for row in report if row["source_id"]}
    assert actions["candidate-new"] == "inserted"
    assert actions["candidate-update"] == "updated"
    registry = json.loads(existing_registry.read_text(encoding="utf-8"))
    by_id = {row["source_id"]: row for row in registry}
    assert by_id["candidate-new"]["status"] == "candidate"
    assert by_id["candidate-new"]["source_type"] == "rss"
    assert by_id["candidate-update"]["source_name"] == "Candidate Updated"
    assert by_id["candidate-update"]["notes"] == "Keep this note."
    assert json.loads((tmp_path / "data" / "dispatches" / "food-line" / "pressure_source_registry.json").read_text(encoding="utf-8")) == []


def test_food_line_candidate_intake_rejects_invalid_rows(tmp_path: Path):
    _ensure_assets(tmp_path)
    _write_candidate_registry(tmp_path, [])
    _write_pressure_registry(tmp_path, [])
    csv_path = _write_intake_csv(
        tmp_path,
        [
            {
                "source_id": "candidate-dup",
                "source_name": "Candidate Dup",
                "publisher": "Publisher",
                "candidate_url": "https://example.com/dup.rss",
                "source_family": "public_radio",
                "source_type": "rss",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "Valid row.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": "SNAP",
                "status": "candidate",
                "notes": "",
            },
            {
                "source_id": "candidate-dup",
                "source_name": "Candidate Dup 2",
                "publisher": "Publisher",
                "candidate_url": "https://example.com/dup2.rss",
                "source_family": "public_radio",
                "source_type": "rss",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "Duplicate row.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": "SNAP",
                "status": "candidate",
                "notes": "",
            },
            {
                "source_id": "",
                "source_name": "Missing Id",
                "publisher": "Publisher",
                "candidate_url": "https://example.com/missing-id.rss",
                "source_family": "public_radio",
                "source_type": "rss",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "Missing source_id.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": "SNAP",
                "status": "candidate",
                "notes": "",
            },
            {
                "source_id": "candidate-bad",
                "source_name": "Candidate Bad",
                "publisher": "Publisher",
                "candidate_url": "ftp://example.com/bad.rss",
                "source_family": "public_radio",
                "source_type": "rss",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "Invalid URL.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": "SNAP",
                "status": "candidate",
                "notes": "",
            },
            {
                "source_id": "candidate-bad-type",
                "source_name": "Candidate Bad Type",
                "publisher": "Publisher",
                "candidate_url": "https://example.com/bad-type.rss",
                "source_family": "public_radio",
                "source_type": "invalid",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "Invalid source_type.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": "SNAP",
                "status": "candidate",
                "notes": "",
            },
            {
                "source_id": "candidate-bad-status",
                "source_name": "Candidate Bad Status",
                "publisher": "Publisher",
                "candidate_url": "https://example.com/bad-status.rss",
                "source_family": "public_radio",
                "source_type": "rss",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "Invalid status.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": "SNAP",
                "status": "bogus",
                "notes": "",
            },
        ],
    )
    result = import_food_line_candidate_intake(tmp_path, csv_path)
    assert result["imported_count"] == 1
    assert result["updated_count"] == 0
    assert result["skipped_count"] == 0
    assert result["rejected_count"] == 5
    with Path(result["report_path"]).open(encoding="utf-8") as handle:
        report = list(csv.DictReader(handle))
    reasons = {row["source_id"]: row["reason"] for row in report}
    assert reasons["candidate-dup"] == "duplicate source_id in CSV"
    assert "missing source_id" in " ".join(row["reason"] for row in report if not row["source_id"])
    assert reasons["candidate-bad"] == "candidate_url must use http or https"
    assert reasons["candidate-bad-type"].startswith("invalid source_type")
    assert reasons["candidate-bad-status"].startswith("invalid status")
    registry = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "candidate_source_registry.json").read_text(encoding="utf-8"))
    assert len(registry) == 1
    assert registry[0]["source_id"] == "candidate-dup"


def test_food_line_source_discovery_queries_load():
    queries = load_food_line_source_discovery_queries(Path(__file__).parent.parent)
    assert queries
    assert all("template" in row for row in queries)
    assert any("{state}" in row["template"] for row in queries)
    assert all("rolling_query_quality_score" in row for row in queries)
    assert any("Summer EBT" in row["template"] for row in queries)
    assert any("Feeding America" in row["template"] for row in queries)
    assert any("SNAP benefits delayed" in row["template"] for row in queries)
    assert any("{state} food banks" in row["template"] for row in queries)
    assert any("{state} food pantries" in row["template"] for row in queries)
    assert any("{state} pantry demand" in row["template"] for row in queries)
    assert any("{state} families turn to food banks" in row["template"] for row in queries)
    assert any("{state} food stamps OR SNAP cuts OR SNAP benefits OR SNAP rolls" in row["template"] for row in queries)
    assert any("{state} food distribution sites OR hunger relief OR emergency food assistance" in row["template"] for row in queries)
    assert any("{state} meal sites OR summer meals" in row["template"] for row in queries)
    assert any("food insecurity RSS" in row["template"] for row in queries)
    assert any("public radio food access RSS" in row["template"] for row in queries)
    assert any("local newspaper food access RSS" in row["template"] for row in queries)
    assert any("nepm.org/regional-news" in row["template"] for row in queries)
    assert any("themainemonitor.org giant freezer help Aroostook food pantries" in row["template"] for row in queries)
    assert any("miamiherald.com/news/local Miami food bank demand SNAP 60%" in row["template"] for row in queries)
    assert any("ALICE food costs" in row["template"] for row in queries)
    assert any("caregiver food insecurity hospitalization" in row["template"] for row in queries)
    assert any("hospitalized children food insecurity research" in row["template"] for row in queries)


def test_food_line_discovery_date_bounded_queries_cover_new_pressure_terms():
    rows = food_line_discovery._date_bounded_queries("2026-06-25")
    queries = {row["query"] for row in rows}

    assert any('"food banks"' in query for query in queries)
    assert any('"food pantries"' in query for query in queries)
    assert any('"pantry demand"' in query for query in queries)
    assert any('"families turn to food banks"' in query for query in queries)
    assert any('"food stamps" OR "SNAP cuts" OR "SNAP benefits" OR "SNAP rolls"' in query for query in queries)
    assert any('"food distribution sites" OR "hunger relief" OR "emergency food assistance"' in query for query in queries)
    assert any('"meal sites" OR "summer meals"' in query for query in queries)


def test_food_line_gold_set_2026_06_25_uses_real_reviewable_urls():
    payload = json.loads(
        (Path(__file__).parent.parent / "data" / "dispatches" / "food-line" / "source_collection_gold_sets" / "2026-06-25.json").read_text(encoding="utf-8")
    )
    assert payload
    assert all("example.com" not in str(row.get("url") or "") for row in payload)
    assert any("apnews.com" in str(row.get("url") or "") for row in payload)
    assert any("nepm.org" in str(row.get("url") or "") or "cascadepbs.org" in str(row.get("url") or "") for row in payload)


def test_food_line_discovery_source_configuration_includes_target_outlet_seeds():
    registry = json.loads((Path(__file__).parent.parent / "data" / "dispatches" / "food-line" / "source_registry.json").read_text(encoding="utf-8"))
    by_id = {row["source_id"]: row for row in registry}

    assert by_id["nepm-regional-news"]["source_family"] == "public_radio"
    assert by_id["nepm-regional-news"]["source_type"] == "page"
    assert by_id["nepm-regional-news"]["url"] == "https://www.nepm.org/regional-news"

    assert by_id["maine-monitor-post-sitemap"]["source_family"] == "nonprofit_news"
    assert by_id["maine-monitor-post-sitemap"]["source_type"] == "page"
    assert by_id["maine-monitor-post-sitemap"]["url"] == "https://themainemonitor.org/post-sitemap3.xml"

    assert by_id["miami-herald-local-news"]["source_family"] == "local_news"
    assert by_id["miami-herald-local-news"]["source_type"] == "rss"
    assert by_id["miami-herald-local-news"]["url"] == "https://www.miamiherald.com/news/local/?getXmlFeed=true&widgetContentId=712015&widgetName=rssfeed"

    priority = json.loads((Path(__file__).parent.parent / "data" / "dispatches" / "food-line" / "source_discovery_priority_domains.json").read_text(encoding="utf-8"))
    priority_domains = {str(item).strip().lower() for item in priority.get("priority_domains") or []}
    assert "nepm.org" in priority_domains
    assert "themainemonitor.org" in priority_domains
    assert "miamiherald.com" in priority_domains
    assert "wsls.com" in priority_domains


def test_food_line_pressure_registry_includes_wsls_roanoke_local_feed():
    registry = json.loads((Path(__file__).parent.parent / "data" / "dispatches" / "food-line" / "pressure_source_registry.json").read_text(encoding="utf-8"))
    by_id = {row["source_id"]: row for row in registry}

    wsls = by_id["wsls_roanoke_local"]
    assert wsls["publisher"] == "WSLS"
    assert wsls["source_family"] == "local_news"
    assert wsls["source_type"] == "rss"
    assert wsls["url"] == "https://www.wsls.com/arc/outboundfeeds/rss/category/news/?outputType=xml&size=10"
    assert wsls["state"] == "VA"
    assert wsls["location_name"] == "Roanoke, VA"
    assert wsls["location_scope"] == "state_local"
    assert wsls["source_role_allowed"] == "pressure_evidence"
    assert wsls["pressure_required"] is True
    assert wsls["freshness_mode"] == "pressure"
    assert wsls["max_age_days"] == 14
    assert wsls["enabled"] is True
    assert wsls["source_purpose"] == "current_news"
    assert wsls["current_or_evergreen"] == "current"
    assert wsls["promotable"] is True
    assert wsls["expected_text_basis"] == "rss_summary"
    assert "food pantry" in wsls["positive_keywords"]
    assert "St. Francis House" in wsls["positive_keywords"]
    assert "Feeding Southwest Virginia" in wsls["positive_keywords"]


def test_food_line_wsls_feed_source_qualifies_pressure_items_but_not_generic_local_news(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-11"
    feed_url = "https://www.wsls.com/arc/outboundfeeds/rss/category/news/?outputType=xml&size=10"
    _write_pressure_registry(
        tmp_path,
        [
            {
                "source_id": "wsls_roanoke_local",
                "source_name": "WSLS Roanoke and Southwest Virginia News",
                "publisher": "WSLS",
                "source_type": "rss",
                "url": feed_url,
                "source_family": "local_news",
                "state": "VA",
                "location_name": "Roanoke, VA",
                "location_scope": "state_local",
                "source_role_allowed": "pressure_evidence",
                "pressure_required": True,
                "freshness_mode": "pressure",
                "max_age_days": 14,
                "positive_keywords": [
                    "food pantry",
                    "food shortage",
                    "food insecurity",
                    "SNAP",
                    "USDA food",
                    "school meals",
                    "St. Francis House",
                    "Feeding Southwest Virginia",
                ],
                "negative_keywords": [
                    "recipe",
                    "restaurant",
                    "menu",
                    "chef",
                    "festival",
                    "gala",
                    "donation drive",
                ],
                "affected_group_keywords": [
                    "pantry clients",
                    "SNAP households",
                    "families",
                    "children",
                    "students",
                ],
                "enabled": True,
                "summary_fallback": "WSLS local-news feed for Roanoke and Southwest Virginia food access pressure.",
                "notes": "Verified official WSLS RSS feed.",
                "pressure_verification_required": True,
                "extraction_quality": "high",
                "expected_text_basis": "rss_summary",
                "source_purpose": "current_news",
                "current_or_evergreen": "current",
                "promotable": True,
                "non_promotable_reason": "",
            }
        ],
    )
    rss_payload = _rss_payload(
        [
                {
                    "title": "Why Roanoke's St. Francis House is facing its tightest food shortage ever this summer",
                    "link": "https://www.wsls.com/news/local/2026/06/10/why-roanokes-st-francis-house-is-facing-its-tightest-food-shortage-ever-this-summer/",
                    "description": "St. Francis House reported rising demand and reduced distribution after empty shelves in May. The June USDA delivery was smaller than May's, the pantry is handing out less food, food supply is down 64% compared with January, and summer school-meal gaps plus SNAP and USDA pressure are adding strain.",
                },
            {
                "title": "Roanoke city leaders discuss park improvements",
                "link": "https://www.wsls.com/news/local/2026/06/10/roanoke-park-update/",
                "description": "City leaders discussed a park project and traffic timing.",
            },
        ]
    )

    result = food_line.collect_food_line_auto_sources(tmp_path, date, fetcher=lambda _url, timeout=15: rss_payload)
    rows = json.loads(Path(result["auto_sources_path"]).read_text(encoding="utf-8"))
    by_title = {row["title"]: row for row in rows}

    qualifying_title = "Why Roanoke's St. Francis House is facing its tightest food shortage ever this summer"
    generic_title = "Roanoke city leaders discuss park improvements"

    assert result["source_count"] == 2
    assert result["collected_source_count_by_source_id"] == {"wsls_roanoke_local": 2}
    assert by_title[qualifying_title]["pressure_signal"] is True
    assert by_title[qualifying_title]["map_eligible"] is True
    assert by_title[qualifying_title]["source_role"] == "local_signal"
    assert by_title[qualifying_title]["pressure_verification_status"] == "source_text_verified"
    assert by_title[qualifying_title]["source_family"] == "local_news"
    assert by_title[qualifying_title]["state"] == "VA"
    assert by_title[qualifying_title]["url"] == "https://www.wsls.com/news/local/2026/06/10/why-roanokes-st-francis-house-is-facing-its-tightest-food-shortage-ever-this-summer/"
    assert by_title[generic_title]["pressure_signal"] is False
    assert by_title[generic_title]["map_eligible"] is False
    assert by_title[generic_title]["pressure_verification_status"] == "demoted_context"


def test_food_line_sitemap_xml_seed_discovers_article_urls(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-08"
    _write_pressure_registry(tmp_path, [])
    _write_candidate_registry(tmp_path, [])
    _write_source_registry(
        tmp_path,
        [
            {
                "source_id": "maine-monitor-post-sitemap",
                "source_name": "The Maine Monitor Post Sitemap",
                "publisher": "The Maine Monitor",
                "url": "https://themainemonitor.org/post-sitemap3.xml",
                "source_family": "nonprofit_news",
                "source_type": "page",
                "state": "ME",
                "location_name": "Maine",
                "location_scope": "state_local",
                "enabled": True,
                "notes": "WordPress sitemap seed.",
            }
        ],
    )

    def fetcher(url: str, timeout: int = 15):
        if url == "https://themainemonitor.org/post-sitemap3.xml":
            return b"""<?xml version=\"1.0\" encoding=\"UTF-8\"?><urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\"><url><loc>https://themainemonitor.org/giant-freezer-help-aroostook-food-pantries/</loc></url><url><loc>https://themainemonitor.org/another-story/</loc></url></urlset>"""
        raise AssertionError(f"unexpected fetch url: {url}")

    result = discover_food_line_sources(tmp_path, date, fetcher=fetcher, write_candidates=True, max_insertions=5, max_candidates_total=10)
    registry = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "candidate_source_registry.json").read_text(encoding="utf-8"))
    assert result["discovered_candidate_count"] >= 1
    assert any(row["candidate_url"] == "https://themainemonitor.org/giant-freezer-help-aroostook-food-pantries" for row in registry)
    assert any(row["candidate_url"] == "https://themainemonitor.org/giant-freezer-help-aroostook-food-pantries" for row in json.loads((Path(result["audit_path"])).read_text(encoding="utf-8")))


def test_food_line_sitemap_xml_expansion_discovers_fixture_article_with_metadata(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-08"
    fixture_rows = json.loads((Path(__file__).parent / "fixtures" / "food_line" / "regression_2026-06-08_sources.json").read_text(encoding="utf-8"))
    maine = next(row for row in fixture_rows if row["source_record_id"] == "food-line-src-002")
    _write_pressure_registry(tmp_path, [])
    _write_candidate_registry(tmp_path, [])
    _write_source_registry(
        tmp_path,
        [
            {
                "source_id": "maine-monitor-post-sitemap",
                "source_name": "The Maine Monitor Post Sitemap",
                "publisher": "The Maine Monitor",
                "url": "https://themainemonitor.org/post-sitemap3.xml",
                "source_family": "nonprofit_news",
                "source_type": "page",
                "state": "ME",
                "location_name": "Maine",
                "location_scope": "state_local",
                "enabled": True,
                "notes": "WordPress sitemap seed.",
            }
        ],
    )

    def fetcher(url: str, timeout: int = 15):
        if url == "https://themainemonitor.org/post-sitemap3.xml":
            return f"""<?xml version='1.0' encoding='UTF-8'?><urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'><url><loc>{maine['url']}</loc></url><url><loc>https://themainemonitor.org/another-story/</loc></url></urlset>""".encode("utf-8")
        if url.rstrip("/") == maine["url"].rstrip("/"):
            return b"""<html><head><title>Giant freezer helps Aroostook food pantries</title><meta property='article:published_time' content='2026-06-08T12:00:00Z'><meta name='description' content='Federal food assistance cuts are squeezing Aroostook County food pantries.'></head><body><p>Federal food assistance cuts are squeezing Aroostook County food pantries. Clients are receiving less and pantries are buying more food themselves.</p></body></html>"""
        raise AssertionError(f"unexpected fetch url: {url}")

    result = discover_food_line_sources(tmp_path, date, fetcher=fetcher, write_candidates=True, max_insertions=5, max_candidates_total=10)
    registry = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "candidate_source_registry.json").read_text(encoding="utf-8"))
    row = next(item for item in registry if item["candidate_url"] == maine["url"].rstrip("/"))
    assert row["source_seed_url"] == "https://themainemonitor.org/post-sitemap3.xml"
    assert row["discovery_seed_url"] == "https://themainemonitor.org/post-sitemap3.xml"
    assert row["discovered_from"] == "sitemap"
    assert row["retrieved_at"]
    assert row["published_at"] == "2026-06-08"
    assert row["page_metadata_date"] == "2026-06-08T12:00:00Z"
    assert row["evidence_text_basis"] == "page_text_excerpt"
    freshness = validate_food_line_source_freshness(date, row["published_at"], row["candidate_url"], "current_public_story", page_metadata_date=row["page_metadata_date"], freshness_window_days=3)
    assert freshness["source_freshness_date_basis"] == "published_at"
    assert freshness["public_story_eligible"] is True
    assert result["discovered_candidate_count"] >= 1


def test_food_line_nepm_index_page_expansion_discovers_fixture_article_with_metadata(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-08"
    fixture_rows = json.loads((Path(__file__).parent / "fixtures" / "food_line" / "regression_2026-06-08_sources.json").read_text(encoding="utf-8"))
    nepm = next(row for row in fixture_rows if row["source_record_id"] == "food-line-src-001")
    _write_pressure_registry(tmp_path, [])
    _write_candidate_registry(tmp_path, [])
    _write_source_registry(
        tmp_path,
        [
            {
                "source_id": "nepm-regional-news",
                "source_name": "NEPM Regional News",
                "publisher": "NEPM",
                "url": "https://www.nepm.org/regional-news",
                "source_family": "public_radio",
                "source_type": "page",
                "state": "MA",
                "location_name": "Massachusetts",
                "location_scope": "state_local",
                "enabled": True,
                "notes": "Regional news index.",
            }
        ],
    )

    article_slug = nepm["url"].rstrip("/").split("/")[-1]
    article_url = nepm["url"].rstrip("/")

    def fetcher(url: str, timeout: int = 15):
        if url == "https://www.nepm.org/regional-news":
            return f"""<html><head><title>Regional News | New England Public Media</title></head><body><a href='/regional-news/2026-06-08/{article_slug}'>Food banks continue</a><a href='/regional-news/2026-06-08/other-story'>Other story</a></body></html>""".encode("utf-8")
        if url.rstrip("/") == article_url.rstrip("/"):
            return b"""<html><head><title>Food banks continue to see increased need as SNAP requirements shift</title><meta property='article:published_time' content='2026-06-08T12:00:00Z'><meta name='description' content='Project Bread says food assistance call demand is up and dropped calls are creating friction.'></head><body><p>Food banks continue to see increased need as SNAP requirements shift. Project Bread says food assistance call demand is up and dropped calls are creating friction.</p></body></html>"""
        raise AssertionError(f"unexpected fetch url: {url}")

    result = discover_food_line_sources(tmp_path, date, fetcher=fetcher, write_candidates=True, max_insertions=5, max_candidates_total=10)
    registry = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "candidate_source_registry.json").read_text(encoding="utf-8"))
    row = next(item for item in registry if item["candidate_url"] == article_url)
    assert row["source_seed_url"] == "https://www.nepm.org/regional-news"
    assert row["discovery_seed_url"] == "https://www.nepm.org/regional-news"
    assert row["discovered_from"] == "link"
    assert row["retrieved_at"]
    assert row["published_at"] == "2026-06-08"
    assert row["page_metadata_date"] == "2026-06-08T12:00:00Z"
    assert row["evidence_text_basis"] == "page_text_excerpt"
    freshness = validate_food_line_source_freshness(date, row["published_at"], row["candidate_url"], "current_public_story", page_metadata_date=row["page_metadata_date"], freshness_window_days=3)
    assert freshness["source_freshness_date_basis"] == "published_at"
    assert freshness["public_story_eligible"] is True
    assert result["discovered_candidate_count"] >= 1


def test_food_line_nepm_and_maine_monitor_exact_articles_survive_discovery_promotion_and_manifest(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-08"
    fixture_rows = json.loads((Path(__file__).parent / "fixtures" / "food_line" / "regression_2026-06-08_sources.json").read_text(encoding="utf-8"))
    nepm_fixture = next(row for row in fixture_rows if row["source_record_id"] == "food-line-src-001")
    maine_fixture = next(row for row in fixture_rows if row["source_record_id"] == "food-line-src-002")
    _write_pressure_registry(tmp_path, [])
    _write_candidate_registry(tmp_path, [])
    _write_source_registry(
        tmp_path,
        [
            {
                "source_id": "nepm-regional-news",
                "source_name": "NEPM Regional News",
                "publisher": "NEPM",
                "url": "https://www.nepm.org/regional-news",
                "source_family": "public_radio",
                "source_type": "page",
                "state": "MA",
                "location_name": "Massachusetts",
                "location_scope": "state_local",
                "enabled": True,
                "notes": "Regional news index.",
            },
            {
                "source_id": "maine-monitor-post-sitemap",
                "source_name": "The Maine Monitor Post Sitemap",
                "publisher": "The Maine Monitor",
                "url": "https://themainemonitor.org/post-sitemap3.xml",
                "source_family": "nonprofit_news",
                "source_type": "page",
                "state": "ME",
                "location_name": "Maine",
                "location_scope": "state_local",
                "enabled": True,
                "notes": "WordPress sitemap seed.",
            },
        ],
    )

    def fetcher(url: str, timeout: int = 15):
        if url == "https://www.nepm.org/regional-news":
            return f"""<html><head><title>Regional News | New England Public Media</title></head><body><a href='/regional-news/2026-06-08/{nepm_fixture['url'].rstrip('/').split('/')[-1]}'>Food banks continue</a></body></html>""".encode("utf-8")
        if url.rstrip("/") == nepm_fixture["url"].rstrip("/"):
            return b"""<html><head><title>Food banks continue to see increased need as SNAP requirements shift</title><meta property='article:published_time' content='2026-06-08T12:00:00Z'><meta name='description' content='Project Bread says food assistance call demand is up and dropped calls are creating friction.'></head><body><p>Food banks continue to see increased need as SNAP requirements shift. Project Bread says food assistance call demand is up and dropped calls are creating friction.</p></body></html>"""
        if url == "https://themainemonitor.org/post-sitemap3.xml":
            return f"""<?xml version='1.0' encoding='UTF-8'?><urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'><url><loc>{maine_fixture['url']}</loc></url></urlset>""".encode("utf-8")
        if url.rstrip("/") == maine_fixture["url"].rstrip("/"):
            return b"""<html><head><title>Giant freezer may help get more food to Aroostook County pantries</title><meta property='article:published_time' content='2026-06-08T12:00:00Z'><meta name='description' content='Federal food assistance cuts are squeezing Aroostook County food pantries.'></head><body><p>Federal food assistance cuts are squeezing Aroostook County food pantries. Clients are receiving less and pantries are buying more food themselves.</p></body></html>"""
        raise AssertionError(f"unexpected fetch url: {url}")

    discovery = discover_food_line_sources(tmp_path, date, fetcher=fetcher, write_candidates=True, max_insertions=10, max_candidates_total=10)
    candidate_registry = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "candidate_source_registry.json").read_text(encoding="utf-8"))
    nepm_row = next(row for row in candidate_registry if row["candidate_url"] == nepm_fixture["url"])
    maine_url = maine_fixture["url"].rstrip("/")
    maine_row = next(row for row in candidate_registry if row["candidate_url"] == maine_url)
    assert nepm_row["source_seed_url"] == "https://www.nepm.org/regional-news"
    assert maine_row["source_seed_url"] == "https://themainemonitor.org/post-sitemap3.xml"
    assert discovery["discovered_candidate_count"] >= 2

    promotion = run_food_line_candidate_sources(tmp_path, date, fetcher=fetcher, promote_enabled=True)
    with Path(promotion["candidate_promotion_report_path"]).open(encoding="utf-8") as handle:
        promotion_rows = {row["source_id"]: row for row in csv.DictReader(handle)}
    assert promotion_rows[nepm_row["source_id"]]["promoted"] == "True"
    assert promotion_rows[maine_row["source_id"]]["promoted"] == "True"

    result = run_food_line_dispatch(tmp_path, date, collect=True, collect_fetcher=fetcher)
    assert result["pressure_verified_count"] >= 2
    assert result["edition_mode"] == "no_public_edition"
    assert result["source_freshness_status"] == "blocked_insufficient_current_story_sources"


def test_food_line_source_discovery_writes_review_and_audit_and_inserts_candidates(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-09"
    _write_candidate_registry(tmp_path, [])
    _write_pressure_registry(tmp_path, [])
    _write_source_registry(
        tmp_path,
        [
            {
                "source_id": "seed-home",
                "source_name": "Seed Home",
                "publisher": "Seed Publisher",
                "url": "https://example.com/home",
                "source_family": "local_news",
                "source_type": "page",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "notes": "Seed homepage.",
                "enabled": True,
            }
        ],
    )
    html_payload = b"""<html><head><title>Food bank demand in Austin</title><link rel=\"alternate\" type=\"application/rss+xml\" href=\"https://example.com/feeds/austin.rss\"></head><body><p>Food bank demand is rising and SNAP delays are affecting households.</p></body></html>"""

    def fetcher(url: str, timeout: int = 15):
        if url == "https://example.com/home":
            return html_payload
        if url == "https://example.com/feeds/austin.rss":
            return b"""<?xml version=\"1.0\" encoding=\"UTF-8\"?><rss version=\"2.0\"><channel><item><title>Food bank sees rising demand</title><link>https://example.com/story</link><description>Food bank demand increased and pantry lines grew.</description></item></channel></rss>"""
        raise AssertionError(f"unexpected fetch url: {url}")

    result = discover_food_line_sources(tmp_path, date, fetcher=fetcher, write_candidates=True)
    review_path = Path(result["review_path"])
    audit_path = Path(result["audit_path"])
    query_report_path = Path(result["query_performance_report_path"])
    assert review_path.exists()
    assert audit_path.exists()
    assert query_report_path.exists()
    with review_path.open(encoding="utf-8") as handle:
        review = list(csv.DictReader(handle))
    assert review
    assert any(row["action"] == "inserted_candidate" for row in review)
    assert any(row["candidate_url"] == "https://example.com/feeds/austin.rss" for row in review)
    assert "source_purpose" in review[0]
    assert "current_or_evergreen" in review[0]
    assert "promotable" in review[0]
    assert "non_promotable_reason" in review[0]
    registry = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "candidate_source_registry.json").read_text(encoding="utf-8"))
    assert any(row["candidate_url"] == "https://example.com/feeds/austin.rss" for row in registry)
    assert json.loads((tmp_path / "data" / "dispatches" / "food-line" / "pressure_source_registry.json").read_text(encoding="utf-8")) == []


def test_food_line_source_discovery_limits_skip_blocked_and_prioritize_quality(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-12"
    donation_url = "https://example.com/donate"
    recipe_url = "https://example.com/recipe"
    good_url = "https://www.kut.org/story"
    other_url = "https://example.com/other-story"
    _write_candidate_registry(tmp_path, [])
    _write_pressure_registry(tmp_path, [])
    _write_source_registry(
        tmp_path,
        [
            {"source_id": "donation-seed", "source_name": "Donate Monthly", "publisher": "Example Provider", "url": donation_url, "source_family": "food_bank_provider", "source_type": "page", "state": "US", "location_name": "United States", "location_scope": "national", "enabled": True},
            {"source_id": "recipe-seed", "source_name": "Recipe roundup", "publisher": "Example Media", "url": recipe_url, "source_family": "local_news", "source_type": "page", "state": "TX", "location_name": "Austin, TX", "location_scope": "state_local", "enabled": True},
            {"source_id": "good-seed", "source_name": "Rising demand story", "publisher": "KUT", "url": good_url, "source_family": "public_radio", "source_type": "page", "state": "TX", "location_name": "Austin, TX", "location_scope": "state_local", "enabled": True},
            {"source_id": "other-seed", "source_name": "Other demand story", "publisher": "Example News", "url": other_url, "source_family": "local_news", "source_type": "page", "state": "TX", "location_name": "Austin, TX", "location_scope": "state_local", "enabled": True},
        ],
    )

    def fetcher(url: str, timeout: int = 15):
        payloads = {
            donation_url: b"<html><head><title>Donate Monthly</title></head><body><p>Donate now.</p></body></html>",
            recipe_url: b"<html><head><title>Recipe roundup</title></head><body><p>Recipe ideas and cooking tips.</p></body></html>",
            good_url: b"<html><head><title>Food bank sees rising demand</title></head><body><p>Food bank demand is rising and pantry lines grew.</p></body></html>",
            other_url: b"<html><head><title>Food bank sees rising demand</title></head><body><p>Food bank demand is rising and pantry lines grew.</p></body></html>",
        }
        if url in payloads:
            return payloads[url]
        raise AssertionError(url)

    result = discover_food_line_sources(
        tmp_path,
        date,
        fetcher=fetcher,
        write_candidates=True,
        max_insertions=5,
        max_candidates_total=10,
        min_source_quality_score=0.45,
    )
    with Path(result["review_path"]).open(encoding="utf-8") as handle:
        review = {row["candidate_url"]: row for row in csv.DictReader(handle)}
    assert review[donation_url]["action"] == "rejected_discovery"
    assert review[recipe_url]["action"] == "rejected_discovery"
    assert review[good_url]["action"] == "inserted_candidate"
    assert int(review[good_url]["priority_bonus"]) > int(review[other_url]["priority_bonus"])
    assert result["inserted_count"] >= 1
    assert result["rejected_count"] >= 2
    assert result["query_performance_report_path"]
    query_report = Path(result["query_performance_report_path"])
    assert query_report.exists()
    with query_report.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows and "recommended_action" in rows[0]
    first_row = next(row for row in rows if row["query_template"].endswith("food bank demand RSS"))
    assert float(first_row["rolling_query_quality_score"]) >= 0
    registry = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "candidate_source_registry.json").read_text(encoding="utf-8"))
    assert any(row["candidate_url"] == good_url for row in registry)


def test_food_line_source_discovery_respects_max_insertions(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-12"
    first_url = "https://example.com/first"
    second_url = "https://example.com/second"
    _write_candidate_registry(tmp_path, [])
    _write_pressure_registry(tmp_path, [])
    _write_source_registry(
        tmp_path,
        [
            {"source_id": "first-seed", "source_name": "First Seed", "publisher": "Example News", "url": first_url, "source_family": "local_news", "source_type": "page", "state": "TX", "location_name": "Austin, TX", "location_scope": "state_local", "enabled": True},
            {"source_id": "second-seed", "source_name": "Second Seed", "publisher": "Example News", "url": second_url, "source_family": "local_news", "source_type": "page", "state": "TX", "location_name": "Austin, TX", "location_scope": "state_local", "enabled": True},
        ],
    )

    def fetcher(url: str, timeout: int = 15):
        payload = b"<html><head><title>Food bank sees rising demand</title></head><body><p>Food bank demand is rising and pantry lines grew.</p></body></html>"
        if url in {first_url, second_url}:
            return payload
        raise AssertionError(url)

    result = discover_food_line_sources(tmp_path, date, fetcher=fetcher, write_candidates=True, max_insertions=1, max_candidates_total=10)
    assert result["inserted_count"] == 1
    registry = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "candidate_source_registry.json").read_text(encoding="utf-8"))
    assert len(registry) == 1


def test_food_line_source_discovery_dedupes_and_preserves_final_status(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-09"
    discovered_url = "https://example.com/feeds/austin.rss"
    _write_source_registry(
        tmp_path,
        [
            {
                "source_id": "seed-home",
                "source_name": "Seed Home",
                "publisher": "Seed Publisher",
                "url": "https://example.com/home",
                "source_family": "local_news",
                "source_type": "page",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "notes": "Seed homepage.",
                "enabled": True,
            }
        ],
    )
    _write_candidate_registry(
        tmp_path,
        [
            {
                "source_id": "existing-final",
                "source_name": "Existing Final",
                "publisher": "Seed Publisher",
                "candidate_url": discovered_url,
                "source_family": "local_news",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "Previously reviewed.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": ["SNAP"],
                "status": "rejected",
                "notes": "Final status should remain.",
            }
        ],
    )
    _write_pressure_registry(tmp_path, [])

    def fetcher(url: str, timeout: int = 15):
        if url == "https://example.com/home":
            return b"""<html><head><title>Food bank demand in Austin</title><link rel=\"alternate\" type=\"application/rss+xml\" href=\"https://example.com/feeds/austin.rss\"></head><body><p>Food bank demand is rising.</p></body></html>"""
        if url == discovered_url:
            return b"""<?xml version=\"1.0\" encoding=\"UTF-8\"?><rss version=\"2.0\"><channel><item><title>Food bank sees rising demand</title><link>https://example.com/story</link><description>Food bank demand increased and pantry lines grew.</description></item></channel></rss>"""
        raise AssertionError(f"unexpected fetch url: {url}")

    result = discover_food_line_sources(tmp_path, date, fetcher=fetcher, write_candidates=True)
    assert result["discovered_candidate_count"] == 0
    assert result["skipped_count"] >= 1
    registry = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "candidate_source_registry.json").read_text(encoding="utf-8"))
    row = next(item for item in registry if item["candidate_url"] == discovered_url)
    assert row["status"] == "rejected"
    assert json.loads((tmp_path / "data" / "dispatches" / "food-line" / "pressure_source_registry.json").read_text(encoding="utf-8")) == []


def test_food_line_source_discovery_skips_quarantined_and_archived_candidates(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-12"
    source_url = "https://example.com/feeds/austin.rss"
    _write_source_registry(
        tmp_path,
        [
            {
                "source_id": "seed-home",
                "source_name": "Seed Home",
                "publisher": "Seed Publisher",
                "url": "https://example.com/home",
                "source_family": "local_news",
                "source_type": "page",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "notes": "Seed homepage.",
                "enabled": True,
            }
        ],
    )
    _write_candidate_registry(
        tmp_path,
        [
            {"source_id": "quarantined-one", "source_name": "Quarantined One", "publisher": "Seed Publisher", "candidate_url": source_url, "source_family": "local_news", "state": "TX", "location_name": "Austin, TX", "location_scope": "state_local", "candidate_reason": "Old candidate.", "expected_text_basis": "rss_summary", "extraction_quality_guess": "high", "pressure_topics_expected": ["SNAP"], "status": "quarantined", "notes": "Quarantined."},
            {"source_id": "archived-one", "source_name": "Archived One", "publisher": "Seed Publisher", "candidate_url": "https://example.com/feeds/archive.rss", "source_family": "local_news", "state": "TX", "location_name": "Austin, TX", "location_scope": "state_local", "candidate_reason": "Old candidate.", "expected_text_basis": "rss_summary", "extraction_quality_guess": "high", "pressure_topics_expected": ["SNAP"], "status": "archived", "notes": "Archived."},
        ],
    )
    _write_pressure_registry(tmp_path, [])

    def fetcher(url: str, timeout: int = 15):
        if url == "https://example.com/home":
            return b"""<html><head><title>Food bank demand in Austin</title><link rel=\"alternate\" type=\"application/rss+xml\" href=\"https://example.com/feeds/austin.rss\"></head><body><p>Food bank demand is rising.</p></body></html>"""
        if url == source_url:
            return b"""<?xml version=\"1.0\" encoding=\"UTF-8\"?><rss version=\"2.0\"><channel><item><title>Food bank sees rising demand</title><link>https://example.com/story</link><description>Food bank demand increased and pantry lines grew.</description></item></channel></rss>"""
        raise AssertionError(url)

    result = discover_food_line_sources(tmp_path, date, fetcher=fetcher, write_candidates=True, skip_quarantined=True, skip_archived=True)
    assert result["skipped_quarantined_count"] >= 1
    assert result["skipped_archived_count"] >= 1


def test_food_line_discovered_candidates_are_processed_by_candidate_tester(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-09"
    feed_url = "https://example.com/feeds/austin.rss"
    _write_pressure_registry(tmp_path, [])
    _write_source_registry(
        tmp_path,
        [
            {
                "source_id": "seed-home",
                "source_name": "Seed Home",
                "publisher": "Seed Publisher",
                "url": "https://example.com/home",
                "source_family": "local_news",
                "source_type": "page",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "notes": "Seed homepage.",
                "enabled": True,
            }
        ],
    )
    _write_candidate_registry(tmp_path, [])

    def discovery_fetcher(url: str, timeout: int = 15):
        if url == "https://example.com/home":
            return b"""<html><head><title>Food bank demand in Austin</title><link rel=\"alternate\" type=\"application/rss+xml\" href=\"https://example.com/feeds/austin.rss\"></head><body><p>Food bank demand is rising and SNAP delays are affecting households.</p></body></html>"""
        if url == feed_url:
            return b"""<?xml version=\"1.0\" encoding=\"UTF-8\"?><rss version=\"2.0\"><channel><item><title>Food bank sees rising demand from families</title><link>https://example.com/story</link><description>Food bank demand increased and pantry lines grew.</description></item></channel></rss>"""
        raise AssertionError(f"unexpected fetch url: {url}")

    discovery_result = discover_food_line_sources(tmp_path, date, fetcher=discovery_fetcher, write_candidates=True)
    assert discovery_result["inserted_count"] >= 1

    def candidate_fetcher(url: str, timeout: int = 15):
        if url == feed_url:
            return b"""<?xml version=\"1.0\" encoding=\"UTF-8\"?><rss version=\"2.0\"><channel><item><title>Food bank sees rising demand from families</title><link>https://example.com/story</link><description>Food bank demand increased and pantry lines grew.</description></item></channel></rss>"""
        raise AssertionError(f"unexpected fetch url: {url}")

    candidate_result = run_food_line_candidate_sources(tmp_path, date, fetcher=candidate_fetcher)
    with Path(candidate_result["candidate_review_path"]).open(encoding="utf-8") as handle:
        review = list(csv.DictReader(handle))
    assert any(row["recommendation"] == "enable" for row in review)
    assert any(row["candidate_url"] == feed_url for row in review)


def test_food_line_daily_wrapper_logs_before_python_and_supports_date_and_dry_run() -> None:
    wrapper_path = Path(__file__).resolve().parents[1] / "run_food_line_daily.ps1"
    wrapper_text = wrapper_path.read_text(encoding="utf-8")
    lower_text = wrapper_text.lower()

    assert "logs\\food-line\\daily_ops" in wrapper_text
    assert "--date" in wrapper_text
    assert "--collect" in wrapper_text
    assert "--include-discovery-gap-summary" in wrapper_text
    assert "--push" in wrapper_text
    assert "--post-bluesky" in wrapper_text
    assert "--generate-audio" in wrapper_text
    assert "--tts-provider" in wrapper_text
    assert "--audio-format" in wrapper_text
    assert "--audio-model" in wrapper_text
    assert "--audio-voice" in wrapper_text
    assert "--dry-run" in wrapper_text
    assert "start-process" in lower_text
    assert "redirectstandardoutput" in lower_text
    assert "redirectstandarderror" in lower_text
    assert "new-item -itemtype file" in lower_text
    assert "set-content" in lower_text
    assert lower_text.index("new-item -itemtype file") < lower_text.index('-label "dispatch"')
    assert "Food Line scheduled run status:" in wrapper_text
    assert "Food Line no public edition" in wrapper_text
    assert "Food Line no-current-update" in wrapper_text
    assert "Food Line dry-run completed" in wrapper_text
    assert "scripts\\publish_github_pages.py" not in wrapper_text
    assert '"git_push"' not in wrapper_text


def test_run_food_line_dispatch_help_executes_by_path_without_script_import_failure() -> None:
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "run_food_line_dispatch.py"
    completed = subprocess.run(
        [sys.executable, str(script_path), "--help"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    combined_output = completed.stdout + completed.stderr
    assert "usage:" in combined_output.lower()
    assert "ModuleNotFoundError" not in combined_output
    assert "No module named 'scripts'" not in combined_output


def _write_food_line_wrapper_fake_dispatch(project_root: Path, exit_code: int, payload: dict) -> None:
    scripts_dir = project_root / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    dispatch_script = scripts_dir / "run_food_line_dispatch.py"
    payload_json = json.dumps(payload)
    dispatch_script.write_text(
        textwrap.dedent(
            f"""
            import json

            payload = json.loads({payload_json!r})
            print(json.dumps(payload, indent=2))
            raise SystemExit({exit_code})
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )


def _resolve_powershell_executable() -> str:
    for candidate in ("powershell.exe", "pwsh"):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    pytest.skip("PowerShell is not available for wrapper execution tests")


def _run_food_line_wrapper(
    tmp_path: Path,
    payload: dict,
    exit_code: int = 0,
    *,
    dry_run: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    project_root = tmp_path / "project"
    log_root = project_root / "logs" / "food-line" / "daily_ops"
    log_root.mkdir(parents=True, exist_ok=True)
    _write_food_line_wrapper_fake_dispatch(project_root, exit_code=exit_code, payload=payload)
    wrapper_path = Path(__file__).resolve().parents[1] / "run_food_line_daily.ps1"
    powershell_exe = _resolve_powershell_executable()
    env = os.environ.copy()
    env["BLUEFERN_PROJECT_ROOT"] = str(project_root)
    env["BLUEFERN_FOOD_LINE_LOG_ROOT"] = str(log_root)
    env["BLUEFERN_PYTHON_EXE"] = sys.executable
    command = [
        powershell_exe,
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(wrapper_path),
        "-Date",
        "2026-06-23",
    ]
    if dry_run:
        command.append("-DryRun")
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        check=False,
    )
    return completed, log_root / "2026-06-23.log"


def test_food_line_daily_wrapper_succeeds_for_no_public_edition_without_pages_publish() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        completed, log_path = _run_food_line_wrapper(
            Path(tmpdir),
            payload={
                "ok": True,
                "edition_date": "2026-06-23",
                "source_count": 0,
                "public_rendered": False,
                "edition_mode": "no_public_edition",
                "publish_status": "no_public_edition",
                "skip_reason": "No new primary food-access signal qualified for public Food Line publication.",
                "pages_publish_copied": False,
                "pushed": False,
                "bluesky_status": "skipped",
                "audio_status": "skipped",
            },
            exit_code=1,
        )

        assert completed.returncode == 0
        assert (
            'Food Line no public edition 2026-06-23: source_count=0 reason="No new primary food-access signal qualified for public Food Line publication."'
            in completed.stdout
        )
        log_text = log_path.read_text(encoding="utf-8")
        assert "Food Line scheduled run status: NO_PUBLIC_EDITION" in log_text
        assert "summary.source_count: 0" in log_text
        assert "summary.public_rendered: false" in log_text
        assert "summary.edition_mode: no_public_edition" in log_text
        assert "summary.skip_reason: No new primary food-access signal qualified for public Food Line publication." in log_text
        assert "summary.pages_publish_copied: false" in log_text
        assert "summary.pushed: false" in log_text
        assert "summary.bluesky_status: skipped" in log_text
        assert "summary.audio_status: skipped" in log_text
        assert "publish command:" not in log_text
        assert "git_push command:" not in log_text


def test_food_line_daily_wrapper_dry_run_uses_safe_collection_only_flags() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        completed, log_path = _run_food_line_wrapper(
            Path(tmpdir),
            payload={
                "ok": True,
                "edition_date": "2026-06-23",
                "source_count": 35,
                "public_rendered": True,
                "edition_mode": "no_current_update",
                "source_freshness_status": "blocked_insufficient_fresh_current_stories",
                "food_line_publish_blocked_reason": "No fresh current-story Food Line sources remained after freshness filtering.",
                "pages_publish_copied": False,
                "pushed": False,
                "bluesky_status": "skipped",
                "audio_status": "audio_file_ready",
                "collector_result": {"ok": True, "source_count": 44},
                "discovery_gap_check": {"run": False},
            },
            dry_run=True,
        )

        assert completed.returncode == 0
        assert (
            "Food Line dry-run completed 2026-06-23: source_count=35 public_rendered=true edition_mode=no_current_update"
            in completed.stdout
        )
        log_text = log_path.read_text(encoding="utf-8")
        assert "--collect" in log_text
        assert "--include-discovery-gap-summary" in log_text
        assert "--dry-run" in log_text
        assert "--publish" not in log_text
        assert "--push" not in log_text
        assert "--post-bluesky" not in log_text
        assert "--generate-audio" not in log_text
        assert "Food Line scheduled run status: DRY_RUN_COMPLETED" in log_text
        assert "summary.source_count: 35" in log_text
        assert "summary.public_rendered: true" in log_text
        assert "summary.edition_mode: no_current_update" in log_text
        assert "summary.source_freshness_status: blocked_insufficient_fresh_current_stories" in log_text
        assert "summary.food_line_publish_blocked_reason: No fresh current-story Food Line sources remained after freshness filtering." in log_text
        assert "summary.pages_publish_copied: false" in log_text
        assert "summary.pushed: false" in log_text
        assert "summary.bluesky_status: skipped" in log_text
        assert "summary.audio_status: audio_file_ready" in log_text


def test_food_line_daily_wrapper_production_logs_full_workflow_flags_and_publish_summary() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        completed, log_path = _run_food_line_wrapper(
            Path(tmpdir),
            payload={
                "ok": True,
                "edition_date": "2026-06-23",
                "source_count": 12,
                "public_rendered": True,
                "edition_mode": "current_update",
                "public_url": "https://dispatches.thebluefernco.com/food-line/editions/2026-06-23/",
                "pages_publish_copied": True,
                "pushed": True,
                "bluesky_status": "success",
                "audio_status": "audio_file_ready",
            },
        )

        assert completed.returncode == 0
        assert (
            "Food Line published 2026-06-23: https://dispatches.thebluefernco.com/food-line/editions/2026-06-23/ pushed=true bluesky=success audio=audio_file_ready"
            in completed.stdout
        )
        log_text = log_path.read_text(encoding="utf-8")
        assert "--collect" in log_text
        assert "--include-discovery-gap-summary" in log_text
        assert "--publish" in log_text
        assert "--push" in log_text
        assert "--post-bluesky" in log_text
        assert "--generate-audio" in log_text
        assert "--tts-provider openai" in log_text
        assert "--audio-format mp3" in log_text
        assert "--audio-model gpt-4o-mini-tts" in log_text
        assert "--audio-voice alloy" in log_text
        assert "Food Line scheduled run status: PUBLISHED" in log_text


def test_food_line_daily_wrapper_preserves_real_dispatch_failures() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        completed, log_path = _run_food_line_wrapper(
            Path(tmpdir),
            payload={"ok": False, "public_rendered": False, "edition_mode": "no_public_edition"},
            exit_code=7,
        )

        assert completed.returncode != 0
        log_text = log_path.read_text(encoding="utf-8")
        assert "Food Line scheduled run failed" in log_text
        assert "Food Line dispatch run failed for 2026-06-23 (exit code 7)" in log_text



def test_food_line_discovery_registry_uses_the_maine_monitor_target_sitemap_shard() -> None:
    import json
    from pathlib import Path

    registry_path = Path("data/dispatches/food-line/source_registry.json")
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    source = next(
        item
        for item in registry
        if item.get("source_id") == "maine-monitor-post-sitemap"
    )
    assert source["url"] == "https://themainemonitor.org/post-sitemap3.xml"
