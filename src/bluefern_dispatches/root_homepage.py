from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from bluefern_dispatches.dispatch_catalog import active_dispatch_slugs, dispatch_is_active

BASE_URL = "https://dispatches.thebluefernco.com"
LATEST_DEVELOPMENTS_HEADING = "Latest published developments"
ACTIVE_PRODUCTS = active_dispatch_slugs()
CARD_LIMIT = 7
SECTION_RE = re.compile(
    r'<section class="section-block"><div class="section-heading"><p class="eyebrow">The current edition desk</p><h2>Latest published developments</h2></div><div class="edition-grid">.*?</div></section>',
    re.DOTALL,
)
DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
INVALID_RELEASE_MARKERS = {
    "failed",
    "not_published",
    "not_synced",
    "rejected",
    "suppressed",
    "unpublished",
    "withheld",
    "withdrawn",
}
INVALID_RELEASE_BOOLEAN_FIELDS = (
    "failed",
    "suppressed",
    "unpublished",
    "withheld",
    "withdrawn",
)

PRODUCT_META: dict[str, dict[str, str]] = {
    "gaza": {
        "badge": "GAZA",
        "badge_class": "gaza",
        "publication_name": "Dispatches From Gaza",
    },
    "food-line": {
        "badge": "FOOD LINE",
        "badge_class": "food-line",
        "publication_name": "Food Line Dispatch",
    },
    "care-line": {
        "badge": "CARE LINE",
        "badge_class": "care-line",
        "publication_name": "Care Line",
    },
    "cascadia": {
        "badge": "CASCADIA",
        "badge_class": "cascadia",
        "publication_name": "The Cascadia Briefing",
    },
    "american-pressure": {
        "badge": "AMERICAN PRESSURE",
        "badge_class": "american-pressure",
        "publication_name": "The American Pressure Dispatch",
    },
}


@dataclass(frozen=True)
class PublicRelease:
    slug: str
    edition_date: str
    title: str
    public_url: str
    relative_url: str
    source_count: int
    publication_name: str
    badge_label: str
    badge_class: str
    release_status: str
    pages_status: str
    represented_on_homepage: bool
    age_days: int
    publication_time_text: str | None
    manifest_path: str | None
    authorized: bool

    @property
    def sort_key(self) -> tuple[datetime, int, str]:
        exact = _publication_datetime(self.edition_date, self.publication_time_text)
        return (exact, 1 if self.publication_time_text else 0, self.slug)


def _parse_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _parse_datetime(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _publication_datetime(edition_date: str, time_text: str | None) -> datetime:
    day = _parse_date(edition_date) or date(1900, 1, 1)
    if not time_text:
        return datetime(day.year, day.month, day.day)
    match = re.fullmatch(r"(\d{1,2}):(\d{2}) ([AP]M) PT", time_text)
    if not match:
        return datetime(day.year, day.month, day.day)
    hour = int(match.group(1)) % 12
    minute = int(match.group(2))
    if match.group(3) == "PM":
        hour += 12
    return datetime(day.year, day.month, day.day, hour, minute)


def _format_long_date(edition_date: str) -> str:
    parsed = _parse_date(edition_date)
    if parsed is None:
        return edition_date
    return parsed.strftime("%B") + f" {parsed.day}, {parsed.year}"


def _format_source_count(count: int) -> str:
    return f"{count} public source" if count == 1 else f"{count} public sources"


def _current_homepage_links(homepage_html: str) -> set[str]:
    return set(re.findall(r'<article class="edition-card[^"]*">.*?<h3><a href="([^"]+)">', homepage_html, re.DOTALL))


def _dispatch_listing_mentions(public_root: Path, slug: str, edition_date: str) -> bool:
    relative = f"editions/{edition_date}/"
    for filename in ("index.html", "archive.html", "rss.xml"):
        path = public_root / slug / filename
        if path.exists() and relative in path.read_text(encoding="utf-8", errors="replace"):
            return True
    return False


def _public_archive_mentions(public_root: Path, slug: str, edition_date: str) -> bool:
    archive = public_root / slug / "archive.html"
    return archive.exists() and f"editions/{edition_date}/" in archive.read_text(encoding="utf-8", errors="replace")


def _published_status(manifest: dict[str, Any]) -> tuple[str, str]:
    release_status = str(
        manifest.get("public_release_status")
        or manifest.get("publication_status")
        or manifest.get("public_release")
        or ""
    ).strip()
    pages_status = str(
        manifest.get("pages_release_status")
        or manifest.get("pages_status")
        or ""
    ).strip()
    return release_status, pages_status


def _authorized_publication_time(manifest: dict[str, Any]) -> str | None:
    # A correction or deterministic rebuild can run after the public edition date.
    # Prefer the public/scheduled release time so root metadata cannot inherit the
    # correction job's wall-clock time.
    for key in ("public_published_at", "scheduled_run_local_time", "actual_run_local_time"):
        parsed = _parse_datetime(str(manifest.get(key) or ""))
        if parsed is not None:
            hour = parsed.strftime("%I").lstrip("0") or "0"
            return f"{hour}:{parsed.strftime('%M %p')} PT"
    return None


def _extract_title_from_html(slug: str, html_text: str) -> str:
    patterns: list[str] = []
    if slug == "american-pressure":
        patterns.extend(
            [
                r"<em>Source:\s*<a [^>]*>([^<]+)</a>",
                r"<h3>([^<]+)</h3>",
            ]
        )
    else:
        patterns.extend(
            [
                r"<article[^>]*>\s*<h3>([^<]+)</h3>",
                r"<h3>([^<]+)</h3>",
                r"<li><a [^>]*>([^<]+)</a>",
            ]
        )
    for pattern in patterns:
        match = re.search(pattern, html_text, re.DOTALL)
        if match:
            return html.unescape(re.sub(r"\s+", " ", match.group(1))).strip()
    return ""


def _resolve_title(slug: str, edition_dir: Path, manifest: dict[str, Any]) -> str:
    for key in ("public_archive_title", "headline", "lead_headline", "title"):
        value = str(manifest.get(key) or "").strip()
        if value:
            return value
    index_path = edition_dir / "index.html"
    if index_path.exists():
        return _extract_title_from_html(slug, index_path.read_text(encoding="utf-8", errors="replace"))
    return ""


def _positive_int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _selected_supporting_source_count(edition_dir: Path) -> int:
    curation_manifest = edition_dir / "curation_manifest.json"
    if not curation_manifest.exists():
        return 0
    try:
        rows = json.loads(curation_manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return 0
    if not isinstance(rows, list):
        return 0
    source_ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or row.get("public_rendered") is not True:
            continue
        for source_id in row.get("source_record_ids") or []:
            normalized = str(source_id or "").strip()
            if normalized:
                source_ids.add(normalized)
    return len(source_ids)


def _resolve_source_count(slug: str, edition_dir: Path, manifest: dict[str, Any]) -> int:
    if slug == "gaza":
        selected = _positive_int(manifest.get("selected_supporting_source_count"))
        if selected:
            return selected
        selected = _selected_supporting_source_count(edition_dir)
        if selected:
            return selected
    raw = manifest.get("source_count")
    if _positive_int(raw):
        return int(raw)
    source_manifest = edition_dir / "sources_manifest.json"
    if source_manifest.exists():
        try:
            rows = json.loads(source_manifest.read_text(encoding="utf-8"))
            if isinstance(rows, list):
                return len(rows)
        except json.JSONDecodeError:
            return 0
    return 0


def _relative_url(slug: str, edition_date: str) -> str:
    return f"/{slug}/editions/{edition_date}/"


def _public_url(slug: str, edition_date: str, manifest: dict[str, Any]) -> str:
    value = str(manifest.get("public_url") or "").strip()
    if value.startswith("https://dispatches.thebluefernco.com/"):
        return value
    return f"{BASE_URL}{_relative_url(slug, edition_date)}"


def _release_is_eligible(
    *,
    public_root: Path,
    verify_root: Path | None,
    slug: str,
    edition_date: str,
    manifest: dict[str, Any],
) -> tuple[bool, str, str, bool]:
    release_status, pages_status = _published_status(manifest)
    status_values = [
        release_status,
        pages_status,
        str(manifest.get("release_status") or ""),
        str(manifest.get("status") or ""),
        str(manifest.get("disposition") or ""),
    ]
    normalized_statuses = {
        re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
        for value in status_values
        if value.strip()
    }
    has_negative_status = any(
        marker == status or marker in status.split("_") or marker in status
        for status in normalized_statuses
        for marker in INVALID_RELEASE_MARKERS
    ) or any(manifest.get(field) is True for field in INVALID_RELEASE_BOOLEAN_FIELDS)
    has_positive_status = release_status == "published" or pages_status == "synced"
    live_verified = False
    if verify_root is not None:
        live_verified = (verify_root / slug / "editions" / edition_date / "index.html").exists()
    archive_listed = _public_archive_mentions(public_root, slug, edition_date)
    legacy_ok = not release_status and not pages_status and _dispatch_listing_mentions(public_root, slug, edition_date)
    if has_negative_status:
        return False, release_status, pages_status, live_verified
    if not archive_listed:
        return False, release_status, pages_status, live_verified
    if not (has_positive_status or live_verified or legacy_ok):
        return False, release_status, pages_status, live_verified
    return True, release_status, pages_status, live_verified


def discover_public_releases(
    public_root: Path,
    *,
    verify_root: Path | None = None,
    as_of: date | None = None,
    homepage_html: str | None = None,
) -> list[PublicRelease]:
    today = as_of or date.today()
    represented = _current_homepage_links(homepage_html or "")
    releases: list[PublicRelease] = []
    for slug in ACTIVE_PRODUCTS:
        if not dispatch_is_active(slug):
            continue
        editions_root = public_root / slug / "editions"
        if not editions_root.exists():
            continue
        for edition_dir in sorted((p for p in editions_root.iterdir() if p.is_dir() and DATE_DIR_RE.match(p.name)), reverse=True):
            edition_date = edition_dir.name
            parsed_date = _parse_date(edition_date)
            if parsed_date is None or parsed_date > today:
                continue
            if not (edition_dir / "index.html").exists():
                continue
            manifest = _parse_json(edition_dir / "edition_manifest.json") if (edition_dir / "edition_manifest.json").exists() else {}
            eligible, release_status, pages_status, authorized = _release_is_eligible(
                public_root=public_root,
                verify_root=verify_root,
                slug=slug,
                edition_date=edition_date,
                manifest=manifest,
            )
            if not eligible:
                continue
            title = _resolve_title(slug, edition_dir, manifest)
            source_count = _resolve_source_count(slug, edition_dir, manifest)
            if not title or source_count <= 0:
                continue
            metadata = PRODUCT_META[slug]
            relative = _relative_url(slug, edition_date)
            releases.append(
                PublicRelease(
                    slug=slug,
                    edition_date=edition_date,
                    title=title,
                    public_url=_public_url(slug, edition_date, manifest),
                    relative_url=relative,
                    source_count=source_count,
                    publication_name=metadata["publication_name"],
                    badge_label=metadata["badge"],
                    badge_class=metadata["badge_class"],
                    release_status=release_status,
                    pages_status=pages_status,
                    represented_on_homepage=relative in represented,
                    age_days=(today - parsed_date).days,
                    publication_time_text=_authorized_publication_time(manifest),
                    manifest_path=str(edition_dir / "edition_manifest.json") if (edition_dir / "edition_manifest.json").exists() else None,
                    authorized=authorized,
                )
            )
    return releases


def select_homepage_cards(releases: list[PublicRelease], *, limit: int = CARD_LIMIT) -> list[PublicRelease]:
    by_slug: dict[str, list[PublicRelease]] = {slug: [] for slug in ACTIVE_PRODUCTS}
    for release in sorted(releases, key=lambda item: item.sort_key, reverse=True):
        by_slug.setdefault(release.slug, []).append(release)
    selected: list[PublicRelease] = []
    chosen = {(release.slug, release.edition_date) for release in selected}
    for slug in ACTIVE_PRODUCTS:
        if by_slug.get(slug):
            release = by_slug[slug][0]
            selected.append(release)
            chosen.add((release.slug, release.edition_date))
    remaining = [
        release
        for release in sorted(releases, key=lambda item: item.sort_key, reverse=True)
        if (release.slug, release.edition_date) not in chosen
    ]
    for release in remaining:
        if len(selected) >= limit:
            break
        selected.append(release)
    return sorted(selected, key=lambda item: item.sort_key, reverse=True)


def select_effective_latest(releases: list[PublicRelease]) -> dict[str, PublicRelease]:
    latest: dict[str, PublicRelease] = {}
    for release in sorted(releases, key=lambda item: item.sort_key, reverse=True):
        latest.setdefault(release.slug, release)
    return latest


def render_latest_developments_section(cards: list[PublicRelease]) -> str:
    card_html = "".join(
        f'<article class="edition-card edition-card--{html.escape(card.badge_class)}">'
        f'<p class="topic-badge topic-badge--{html.escape(card.badge_class)}">{html.escape(card.badge_label)}</p>'
        f'<h3><a href="{html.escape(card.relative_url)}">{html.escape(card.title)}</a></h3>'
        f'<p class="edition-source">{html.escape(card.publication_name)} &middot; {html.escape(_format_long_date(card.edition_date))}'
        f'{f" &middot; {html.escape(card.publication_time_text)}" if card.publication_time_text else ""}</p>'
        f'<p class="edition-provenance">Based on public source reporting</p>'
        f'<p class="edition-meta">{html.escape(_format_source_count(card.source_count))}</p>'
        f"</article>"
        for card in cards
    )
    return (
        '<section class="section-block"><div class="section-heading"><p class="eyebrow">The current edition desk</p>'
        f"<h2>{LATEST_DEVELOPMENTS_HEADING}</h2></div><div class=\"edition-grid\">{card_html}</div></section>"
    )


def render_homepage_from_template(template_html: str, cards: list[PublicRelease]) -> str:
    replacement = render_latest_developments_section(cards)
    if not SECTION_RE.search(template_html):
        raise ValueError("Latest published developments section not found in template")
    return SECTION_RE.sub(replacement, template_html, count=1)


def _release_date_line(release: PublicRelease) -> str:
    suffix = f" &middot; {html.escape(release.publication_time_text)}" if release.publication_time_text else ""
    return f"{html.escape(release.publication_name)} &middot; {html.escape(_format_long_date(release.edition_date))}{suffix}"


def _replace_release_fields(card_html: str, release: PublicRelease, *, include_source_count: bool) -> str:
    headline = (
        f'<h3 class="latest-headline"><a href="{html.escape(release.relative_url)}">'
        f"{html.escape(release.title)}</a></h3>"
    )
    updated, headline_count = re.subn(
        r'<h3 class="latest-headline"><a href="[^"]+">.*?</a></h3>',
        headline,
        card_html,
        count=1,
        flags=re.DOTALL,
    )
    if headline_count != 1:
        raise ValueError(f"Latest headline field not found for {release.slug}")
    updated, date_count = re.subn(
        r'<p class="date-line">.*?</p>',
        f'<p class="date-line">{_release_date_line(release)}</p>',
        updated,
        count=1,
        flags=re.DOTALL,
    )
    if date_count != 1:
        raise ValueError(f"Latest date field not found for {release.slug}")
    updated, button_count = re.subn(
        r'(<a class="button" href=")[^"]+(">Read latest</a>)',
        rf'\g<1>{html.escape(release.relative_url)}\g<2>',
        updated,
        count=1,
    )
    if button_count != 1:
        raise ValueError(f"Read latest link not found for {release.slug}")
    if include_source_count:
        updated, source_count = re.subn(
            r'<p class="edition-meta">.*?</p>',
            f'<p class="edition-meta">{html.escape(_format_source_count(release.source_count))}</p>',
            updated,
            count=1,
            flags=re.DOTALL,
        )
        if source_count != 1:
            raise ValueError(f"Source-count field not found for {release.slug}")
    return updated


def _replace_latest_edition_card(template_html: str, release: PublicRelease) -> str:
    pattern = re.compile(
        rf'<article class="edition-card edition-card--{re.escape(release.badge_class)}">.*?</article>',
        re.DOTALL,
    )
    match = pattern.search(template_html)
    if match is None:
        raise ValueError(f"Latest edition card not found for {release.slug}")
    card = match.group(0)
    headline = (
        f'<h3><a href="{html.escape(release.relative_url)}">'
        f"{html.escape(release.title)}</a></h3>"
    )
    updated, headline_count = re.subn(r'<h3><a href="[^"]+">.*?</a></h3>', headline, card, count=1, flags=re.DOTALL)
    if headline_count != 1:
        raise ValueError(f"Edition-grid headline field not found for {release.slug}")
    updated, date_count = re.subn(
        r'<p class="edition-source">.*?</p>',
        f'<p class="edition-source">{_release_date_line(release)}</p>',
        updated,
        count=1,
        flags=re.DOTALL,
    )
    if date_count != 1:
        raise ValueError(f"Edition-grid date field not found for {release.slug}")
    updated, source_count = re.subn(
        r'<p class="edition-meta">.*?</p>',
        f'<p class="edition-meta">{html.escape(_format_source_count(release.source_count))}</p>',
        updated,
        count=1,
        flags=re.DOTALL,
    )
    if source_count != 1:
        raise ValueError(f"Edition-grid source-count field not found for {release.slug}")
    return template_html[: match.start()] + updated + template_html[match.end() :]


def _replace_active_dispatch_card(template_html: str, release: PublicRelease) -> str:
    product_name = PRODUCT_META[release.slug]["publication_name"]
    if release.slug == "care-line":
        product_name = "The Care Line Dispatch"
    pattern = re.compile(
        rf'<article class="dispatch-card dispatch-card--featured">(?:(?!</article>).)*?'
        rf'<h2>{re.escape(product_name)}</h2>(?:(?!</article>).)*?</article>',
        re.DOTALL,
    )
    match = pattern.search(template_html)
    if match is None:
        raise ValueError(f"Active dispatch card not found for {release.slug}")
    updated = _replace_release_fields(match.group(0), release, include_source_count=False)
    return template_html[: match.start()] + updated + template_html[match.end() :]


def render_sitewide_homepage_from_template(template_html: str, release: PublicRelease) -> str:
    return _replace_active_dispatch_card(_replace_latest_edition_card(template_html, release), release)


def render_dispatch_directory_from_template(template_html: str, release: PublicRelease) -> str:
    return _replace_active_dispatch_card(template_html, release)
