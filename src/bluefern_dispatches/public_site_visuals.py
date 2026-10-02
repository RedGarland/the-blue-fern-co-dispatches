from __future__ import annotations

import argparse
import contextlib
import hashlib
import http.server
import json
import re
import socket
import threading
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup


REJECTED_FOOD_TEXT = (
    "PA SNAP",
    "ThyBlackMan",
    "Reading Chronicle",
    "New Bedford Light",
    "Denver7",
)

CANONICAL_TOKENS = {
    "--bf-dark-blue": "#1E3F4F",
    "--bf-background-cream": "#EFE7DA",
    "--bf-accent-blue-grey": "#4E6B79",
    "--bf-soft-steel-grey": "#9BAEB5",
    "--bf-pale-beige": "#D9CEC0",
    "--bf-white": "#FFFFFF",
    "--bf-text": "#1E3F4F",
}

CANONICAL_RGB = {
    "--bf-dark-blue": "rgb(30, 63, 79)",
    "--bf-background-cream": "rgb(239, 231, 218)",
    "--bf-accent-blue-grey": "rgb(78, 107, 121)",
    "--bf-soft-steel-grey": "rgb(155, 174, 181)",
    "--bf-pale-beige": "rgb(217, 206, 192)",
    "--bf-white": "rgb(255, 255, 255)",
    "--bf-text": "rgb(30, 63, 79)",
}


class PublicSiteVisualValidationError(RuntimeError):
    pass


@dataclass
class VisualIssue:
    page: str
    check: str
    message: str


@dataclass
class VisualPageResult:
    path: str
    status: int | None = None
    screenshot: str | None = None
    metrics: dict[str, Any] | None = None


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _soup(path: Path) -> BeautifulSoup:
    return BeautifulSoup(_read_text(path), "html.parser")


def _find_latest_food_path(pages_root: Path) -> str:
    index = pages_root / "food-line" / "index.html"
    if index.exists():
        html = _read_text(index)
        match = re.search(r'href=["\'](?:/food-line/)?editions/(\d{4}-\d{2}-\d{2})/["\']', html)
        if match:
            return f"/food-line/editions/{match.group(1)}/"
    editions_root = pages_root / "food-line" / "editions"
    if not editions_root.exists():
        raise PublicSiteVisualValidationError("Food Line editions root is missing")
    dates = sorted(path.name for path in editions_root.iterdir() if path.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.name))
    if not dates:
        raise PublicSiteVisualValidationError("Food Line has no dated public edition folders")
    return f"/food-line/editions/{dates[-1]}/"


def _expected_food_story_count(food_edition_dir: Path) -> int:
    source_table = food_edition_dir / "source_table.html"
    if source_table.exists():
        soup = _soup(source_table)
        headers = [cell.get_text(" ", strip=True).lower() for cell in soup.find_all("th")]
        if "used on public page" in headers:
            used_index = headers.index("used on public page")
            count = 0
            for row in soup.find_all("tr")[1:]:
                cells = row.find_all(["td", "th"])
                if len(cells) > used_index and cells[used_index].get_text(" ", strip=True).lower() == "yes":
                    count += 1
            if count:
                return count
    manifest = food_edition_dir / "edition_manifest.json"
    if manifest.exists():
        payload = json.loads(_read_text(manifest))
        for key in ("public_story_count", "story_count", "source_count"):
            value = payload.get(key)
            if isinstance(value, int) and value > 0:
                return value
    return 0


def _required_paths(pages_root: Path) -> list[str]:
    paths = ["/", "/dispatches/", "/food-line/", _find_latest_food_path(pages_root), "/gaza/"]
    if (pages_root / "care-line" / "index.html").exists():
        paths.append("/care-line/")
    return paths


def _assert_html_basics(pages_root: Path, issues: list[VisualIssue]) -> dict[str, Any]:
    food_path = _find_latest_food_path(pages_root)
    food_dir = pages_root / food_path.strip("/")
    food_index = food_dir / "index.html"
    root_html = _read_text(pages_root / "index.html")
    food_html = _read_text(food_index)
    expected_count = _expected_food_story_count(food_dir)
    rendered_count = len(_soup(food_index).select("article.food-line-source-card"))

    if expected_count <= 0:
        issues.append(VisualIssue(food_path, "food_story_count", "latest Food edition has no positive expected public story count"))
    if rendered_count != expected_count:
        issues.append(
            VisualIssue(
                food_path,
                "food_story_count",
                f"rendered story cards {rendered_count} != expected public story count {expected_count}",
            )
        )
    for rejected in REJECTED_FOOD_TEXT:
        if rejected in food_html:
            issues.append(VisualIssue(food_path, "food_rejected_absent", f"rejected Food text is present: {rejected}"))

    if "/food-line/editions/" not in root_html:
        issues.append(VisualIssue("/", "latest_food_link", "root page does not link to a Food Line edition"))
    if "/gaza/editions/" not in root_html:
        issues.append(VisualIssue("/", "latest_gaza_link", "root page does not link to a Gaza edition"))

    return {
        "latest_food_path": food_path,
        "expected_food_story_count": expected_count,
        "rendered_food_story_count": rendered_count,
    }


def _assert_css_tokens(pages_root: Path, issues: list[VisualIssue]) -> None:
    css_path = pages_root / "assets" / "site.css"
    if not css_path.exists():
        issues.append(VisualIssue("/assets/site.css", "canonical_tokens", "shared stylesheet is missing"))
        return
    css = _read_text(css_path).lower()
    for token, value in CANONICAL_TOKENS.items():
        expected = f"{token.lower()}: {value.lower()}"
        compact_expected = expected.replace(" ", "")
        if expected not in css and compact_expected not in css.replace(" ", ""):
            issues.append(
                VisualIssue(
                    "/assets/site.css",
                    "canonical_tokens",
                    f"required token {token}: {value} is missing from shared stylesheet",
                )
            )


def _free_port() -> int:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return


@contextlib.contextmanager
def _serve_pages(pages_root: Path):
    handler = lambda *args, **kwargs: _QuietHandler(*args, directory=str(pages_root), **kwargs)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", _free_port()), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _visual_checks_with_playwright(
    pages_root: Path,
    paths: list[str],
    screenshot_dir: Path | None,
    issues: list[VisualIssue],
) -> list[VisualPageResult]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - exercised when dependency is absent.
        raise PublicSiteVisualValidationError(f"Playwright is required for rendered visual validation: {exc}") from exc

    results: list[VisualPageResult] = []
    stylesheet_loaded_by_path: dict[str, bool] = {}
    screenshot_dir = screenshot_dir.resolve() if screenshot_dir else None
    if screenshot_dir:
        screenshot_dir.mkdir(parents=True, exist_ok=True)

    with _serve_pages(pages_root) as base_url:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                context = browser.new_context(viewport={"width": 1366, "height": 900}, device_scale_factor=1)
                for path in paths:
                    page = context.new_page()
                    css_loaded = False

                    def on_response(response: Any) -> None:
                        nonlocal css_loaded
                        if response.url.endswith("/assets/site.css") and response.status == 200:
                            css_loaded = True

                    page.on("response", on_response)
                    response = page.goto(f"{base_url}{path}", wait_until="networkidle")
                    status = response.status if response else None
                    metrics = page.evaluate(
                        """
                        () => {
                          const box = (selector) => {
                            const el = document.querySelector(selector);
                            if (!el) return null;
                            const r = el.getBoundingClientRect();
                            const cs = window.getComputedStyle(el);
                            return {x: r.x, y: r.y, width: r.width, height: r.height, display: cs.display, gridTemplateColumns: cs.gridTemplateColumns};
                          };
                          const parseRgb = (value) => {
                            const match = String(value || '').match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)/i);
                            return match ? [Number(match[1]), Number(match[2]), Number(match[3])] : null;
                          };
                          const luminance = (rgb) => {
                            const channel = (value) => {
                              const normalized = value / 255;
                              return normalized <= 0.03928 ? normalized / 12.92 : Math.pow((normalized + 0.055) / 1.055, 2.4);
                            };
                            return (0.2126 * channel(rgb[0])) + (0.7152 * channel(rgb[1])) + (0.0722 * channel(rgb[2]));
                          };
                          const contrast = (foreground, background) => {
                            const fg = parseRgb(foreground);
                            const bg = parseRgb(background);
                            if (!fg || !bg) return null;
                            const a = luminance(fg);
                            const b = luminance(bg);
                            return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
                          };
                          const style = (selector) => {
                            const el = document.querySelector(selector);
                            if (!el) return null;
                            const cs = window.getComputedStyle(el);
                            return {
                              color: cs.color,
                              backgroundColor: cs.backgroundColor,
                              backgroundImage: cs.backgroundImage,
                              display: cs.display,
                              gap: cs.gap,
                              columnGap: cs.columnGap,
                              rowGap: cs.rowGap,
                              fontWeight: cs.fontWeight,
                              textDecorationLine: cs.textDecorationLine,
                              contrast: contrast(cs.color, cs.backgroundColor)
                            };
                          };
                          const styleForElement = (el) => {
                            const cs = window.getComputedStyle(el);
                            return {
                              color: cs.color,
                              backgroundColor: cs.backgroundColor,
                              contrast: contrast(cs.color, cs.backgroundColor),
                              text: el.textContent ? el.textContent.trim() : ''
                            };
                          };
                          const rootStyle = window.getComputedStyle(document.documentElement);
                          const canonicalTokens = {
                            '--bf-dark-blue': rootStyle.getPropertyValue('--bf-dark-blue').trim(),
                            '--bf-background-cream': rootStyle.getPropertyValue('--bf-background-cream').trim(),
                            '--bf-accent-blue-grey': rootStyle.getPropertyValue('--bf-accent-blue-grey').trim(),
                            '--bf-soft-steel-grey': rootStyle.getPropertyValue('--bf-soft-steel-grey').trim(),
                            '--bf-pale-beige': rootStyle.getPropertyValue('--bf-pale-beige').trim(),
                            '--bf-white': rootStyle.getPropertyValue('--bf-white').trim(),
                            '--bf-text': rootStyle.getPropertyValue('--bf-text').trim()
                          };
                          const buttons = Array.from(document.querySelectorAll('.button')).map(styleForElement);
                          const cardActions = document.querySelector('.active-grid .dispatch-card .card-actions, .dispatch-grid .dispatch-card .card-actions');
                          const cardActionsStyle = cardActions ? window.getComputedStyle(cardActions) : null;
                          return {
                            canonicalTokens,
                            paperToken: window.getComputedStyle(document.documentElement).getPropertyValue('--paper').trim(),
                            html: style('html'),
                            body: style('body'),
                            buttons,
                            primaryButton: style('.active-grid .dispatch-card .card-actions .button, .dispatch-grid .dispatch-card .card-actions .button, .actions .button, .button'),
                            hero: box('.hero'),
                            latestDesk: box('.edition-grid'),
                            activeGrid: box('.active-grid, .dispatch-grid'),
                            activeCardCount: document.querySelectorAll('.dispatch-card--featured, .dispatch-card').length,
                            activeCardActionCount: document.querySelectorAll('.active-grid .dispatch-card .card-actions a, .dispatch-grid .dispatch-card .card-actions a').length,
                            activeCardPrimaryButtonCount: document.querySelectorAll('.active-grid .dispatch-card .card-actions .button, .dispatch-grid .dispatch-card .card-actions .button').length,
                            activeCardSupportLinkCount: document.querySelectorAll('.active-grid .dispatch-card .card-actions .support-link, .active-grid .dispatch-card .card-actions .text-link, .dispatch-grid .dispatch-card .card-actions .support-link, .dispatch-grid .dispatch-card .card-actions .text-link').length,
                            activeCardActionGapPx: cardActionsStyle ? Number.parseFloat(cardActionsStyle.columnGap || cardActionsStyle.gap || '0') : null,
                            editionCardCount: document.querySelectorAll('.edition-card').length,
                            foodHero: box('.food-line-hero'),
                            foodLogo: box('.food-line-logo--edition, .food-line-logo--home, .hero-logo'),
                            foodStoryCardCount: document.querySelectorAll('article.food-line-source-card').length,
                            bodyText: document.body ? document.body.innerText : ''
                          };
                        }
                        """
                    )
                    safe_name = path.strip("/").replace("/", "__") or "root"
                    screenshot_path: str | None = None
                    if screenshot_dir:
                        target = screenshot_dir / f"{safe_name}.png"
                        page.screenshot(path=str(target), full_page=False)
                        screenshot_path = str(target)
                    results.append(VisualPageResult(path=path, status=status, screenshot=screenshot_path, metrics=metrics))
                    stylesheet_loaded_by_path[path] = css_loaded

                    if status != 200:
                        issues.append(VisualIssue(path, "http_status", f"expected HTTP 200, got {status}"))
                    if not css_loaded:
                        issues.append(VisualIssue(path, "stylesheet", "shared /assets/site.css did not load with HTTP 200"))
                    tokens = metrics.get("canonicalTokens") or {}
                    for token, expected_rgb in CANONICAL_RGB.items():
                        observed = str(tokens.get(token) or "").lower()
                        if observed not in {expected_rgb.lower(), CANONICAL_TOKENS[token].lower()}:
                            issues.append(VisualIssue(path, "canonical_tokens", f"{token} computed as {tokens.get(token)!r}, expected {expected_rgb}"))
                    html_style = metrics.get("html") or {}
                    body = metrics.get("body") or {}
                    html_background = str(html_style.get("backgroundColor") or "").lower()
                    body_background = str(body.get("backgroundColor") or "").lower()
                    if html_background != CANONICAL_RGB["--bf-background-cream"]:
                        issues.append(VisualIssue(path, "body_background", f"html background is {html_style.get('backgroundColor')}, expected {CANONICAL_RGB['--bf-background-cream']}"))
                    if body_background != CANONICAL_RGB["--bf-background-cream"]:
                        issues.append(VisualIssue(path, "body_background", f"body background is {body.get('backgroundColor')}, expected {CANONICAL_RGB['--bf-background-cream']}"))
                    for button in metrics.get("buttons") or []:
                        contrast_ratio = button.get("contrast")
                        if contrast_ratio is None:
                            issues.append(VisualIssue(path, "primary_button_contrast", f"button contrast could not be measured: {button.get('text')}"))
                        elif float(contrast_ratio) < 4.5:
                            issues.append(VisualIssue(path, "primary_button_contrast", f"button contrast is below 4.5:1: {contrast_ratio:.2f} ({button.get('text')})"))
                        if str(button.get("backgroundColor") or "").lower() == CANONICAL_RGB["--bf-dark-blue"] and str(button.get("color") or "").lower() != CANONICAL_RGB["--bf-white"]:
                            issues.append(VisualIssue(path, "primary_button_contrast", f"dark primary button text is not white: {button.get('color')} ({button.get('text')})"))
                    if path == "/":
                        primary_button = metrics.get("primaryButton") or {}
                        hero = metrics.get("hero") or {}
                        latest = metrics.get("latestDesk") or {}
                        active = metrics.get("activeGrid") or {}
                        paper_token = str(metrics.get("paperToken") or "").lower()
                        if paper_token not in {"#efe7da", CANONICAL_RGB["--bf-background-cream"]}:
                            issues.append(VisualIssue(path, "body_background", f"expected warm Dispatches paper token #EFE7DA, got {metrics.get('paperToken')!r}"))
                        contrast_ratio = primary_button.get("contrast")
                        if contrast_ratio is None:
                            issues.append(VisualIssue(path, "primary_button_contrast", "primary button contrast could not be measured"))
                        elif float(contrast_ratio) < 4.5:
                            issues.append(VisualIssue(path, "primary_button_contrast", f"primary button contrast is below 4.5:1: {contrast_ratio:.2f}"))
                        if str(primary_button.get("color") or "").lower() not in {CANONICAL_RGB["--bf-white"], "#ffffff", "white"}:
                            issues.append(VisualIssue(path, "primary_button_contrast", f"primary button text color is not the approved light tone: {primary_button.get('color')}"))
                        if not latest:
                            issues.append(VisualIssue(path, "latest_desk", "homepage latest desk/grid was not found"))
                        elif float(latest.get("y") or 9999) > 1200:
                            issues.append(VisualIssue(path, "latest_desk", f"homepage latest desk starts below first viewport: y={latest.get('y')}"))
                        if hero and float(hero.get("height") or 0) > 720:
                            issues.append(VisualIssue(path, "hero_height", f"homepage hero is too tall: {hero.get('height')}px"))
                        if not active or int(metrics.get("activeCardCount") or 0) < 3:
                            issues.append(VisualIssue(path, "active_dispatch_cards", "active dispatch cards/grid did not render"))
                        elif str(active.get("display")) not in {"grid", "flex"}:
                            issues.append(VisualIssue(path, "active_dispatch_cards", f"active dispatch container display is {active.get('display')}"))
                        if int(metrics.get("activeCardPrimaryButtonCount") or 0) < 3:
                            issues.append(VisualIssue(path, "active_dispatch_action_links", "active dispatch cards are missing primary button class links"))
                        if int(metrics.get("activeCardSupportLinkCount") or 0) < 3:
                            issues.append(VisualIssue(path, "active_dispatch_action_links", "active dispatch cards are missing secondary text/support links"))
                        gap = metrics.get("activeCardActionGapPx")
                        if gap is None:
                            issues.append(VisualIssue(path, "active_dispatch_action_links", "active dispatch card action cluster was not found"))
                        elif float(gap) < 10:
                            issues.append(VisualIssue(path, "active_dispatch_action_links", f"active dispatch action link gap is too tight: {gap}px"))
                    if path == "/dispatches/" and (int(metrics.get("editionCardCount") or 0) + int(metrics.get("activeCardCount") or 0)) < 3:
                        issues.append(VisualIssue(path, "edition_cards", "dispatch directory rendered fewer than three cards"))
                    if path == "/food-line/":
                        hero = metrics.get("foodHero") or metrics.get("hero") or {}
                        if hero and float(hero.get("height") or 0) > 760:
                            issues.append(VisualIssue(path, "food_hero_height", f"Food landing hero is too tall: {hero.get('height')}px"))
                    if path.startswith("/food-line/editions/"):
                        hero = metrics.get("foodHero") or {}
                        logo = metrics.get("foodLogo") or {}
                        if hero and float(hero.get("height") or 0) > 760:
                            issues.append(VisualIssue(path, "food_edition_hero_height", f"Food edition hero is too tall: {hero.get('height')}px"))
                        if logo and (float(logo.get("height") or 0) > 520 or float(logo.get("width") or 0) > 620):
                            issues.append(VisualIssue(path, "food_edition_logo", f"Food edition logo/header is oversized: {logo.get('width')}x{logo.get('height')}"))
            finally:
                browser.close()

    return results


def validate_public_site_visuals(
    pages_root: Path,
    *,
    screenshot_dir: Path | None = None,
) -> dict[str, Any]:
    pages_root = pages_root.resolve()
    if not pages_root.exists():
        raise PublicSiteVisualValidationError(f"pages root does not exist: {pages_root}")
    issues: list[VisualIssue] = []
    _assert_css_tokens(pages_root, issues)
    html_metrics = _assert_html_basics(pages_root, issues)
    paths = _required_paths(pages_root)
    pages = _visual_checks_with_playwright(pages_root, paths, screenshot_dir, issues)
    screenshots = [page.screenshot for page in pages if page.screenshot]
    return {
        "ok": not issues,
        "pages_root": str(pages_root),
        "paths_checked": paths,
        "html_metrics": html_metrics,
        "pages": [asdict(page) for page in pages],
        "screenshots": screenshots,
        "screenshot_hashes": {path: _hash_file(Path(path)) for path in screenshots},
        "issues": [asdict(issue) for issue in issues],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate rendered Dispatches public Pages surfaces.")
    parser.add_argument("--pages-root", type=Path, default=Path("bluefern-dispatches-pages"), help="Local Pages checkout/static root to validate.")
    parser.add_argument("--screenshot-dir", type=Path, help="Optional directory for first-viewport review screenshots.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = validate_public_site_visuals(args.pages_root, screenshot_dir=args.screenshot_dir)
    except Exception as exc:
        result = {"ok": False, "error": str(exc), "issues": [{"page": "", "check": "exception", "message": str(exc)}]}
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print("public site visual validation:", "PASS" if result.get("ok") else "FAIL")
        for issue in result.get("issues") or []:
            print(f"- {issue.get('page')}: {issue.get('check')}: {issue.get('message')}")
        if result.get("screenshots"):
            print("screenshots:")
            for path in result["screenshots"]:
                print(f"- {path}")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
