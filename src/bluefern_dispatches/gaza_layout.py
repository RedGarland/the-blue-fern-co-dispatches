"""Durable presentation layer for the Gaza public homepage and archive.

The generator remains the source of truth for records, links, dates, and
publication gates.  This module only rearranges the already-rendered Gaza
blocks and adds presentation metadata/controls; it never invents or filters
records.
"""

from __future__ import annotations

import html
import re
from datetime import datetime


_FOOTER_RE = re.compile(r'<footer class="site-footer">.*?</footer>', re.DOTALL)
_LATEST_RE = re.compile(r'<article class="gaza-latest-card">.*?</article>', re.DOTALL)
_CHECKS_RE = re.compile(r'<ul class="edition-list gaza-check-list">.*?</ul>', re.DOTALL)
_READABLE_RE = re.compile(r'<ul class="edition-list gaza-readable-list">.*?</ul>', re.DOTALL)
_AUDIO_RE = re.compile(r'<p><a href="/gaza/audio/index\.html">.*?</a></p>', re.DOTALL)
_MONTH_RE = re.compile(r'<section class="archive-month">.*?</section>', re.DOTALL)
_ARCHIVE_ROW_RE = re.compile(r'<li class="(?P<attrs>archive-row[^"]*)">(?P<body>.*?)</li>', re.DOTALL)
_DATE_RE = re.compile(r'<(?:p|span) class="edition-date">(\d{4})-(\d{2})-(\d{2})</(?:p|span)>')


GAZA_ARCHIVE_FILTERS_JS = r'''(() => {
  "use strict";
  const archive = document.querySelector(".archive--gaza");
  if (!archive) return;
  const records = Array.from(archive.querySelectorAll(".archive-record"));
  const groups = Array.from(archive.querySelectorAll(".archive-month"));
  const filters = Array.from(archive.querySelectorAll("[data-filter]"));
  const monthSelect = archive.querySelector("#archive-month");
  const result = archive.querySelector("#archive-result");
  if (!monthSelect || !result) return;
  const counts = records.reduce((map, record) => {
    const kind = record.dataset.recordType || "briefing";
    map[kind] = (map[kind] || 0) + 1;
    return map;
  }, {});
  let activeType = "briefing";
  let activeMonth = "";

  const months = Array.from(new Set(records.map((record) => record.dataset.month).filter(Boolean))).sort().reverse();
  months.forEach((month) => {
    const option = document.createElement("option");
    option.value = month;
    option.textContent = new Intl.DateTimeFormat("en", { month: "long", year: "numeric", timeZone: "UTC" })
      .format(new Date(`${month}-01T00:00:00Z`));
    monthSelect.appendChild(option);
  });

  const labels = { briefing: "Briefings", correction: "Corrections", monitoring: "Monitoring", all: "All records" };
  filters.forEach((button) => {
    const kind = button.dataset.filter || "briefing";
    button.textContent = `${labels[kind]} ${kind === "all" ? records.length : (counts[kind] || 0)}`;
    button.addEventListener("click", () => {
      activeType = kind;
      filters.forEach((candidate) => {
        const selected = candidate === button;
        candidate.classList.toggle("is-active", selected);
        candidate.setAttribute("aria-pressed", String(selected));
      });
      apply();
    });
  });
  monthSelect.addEventListener("change", () => {
    activeMonth = monthSelect.value;
    apply();
  });

  function apply() {
    let shown = 0;
    records.forEach((record) => {
      const typeMatches = activeType === "all" || record.dataset.recordType === activeType;
      const monthMatches = !activeMonth || record.dataset.month === activeMonth;
      const visible = typeMatches && monthMatches;
      record.hidden = !visible;
      if (visible) shown += 1;
    });
    groups.forEach((group) => {
      group.hidden = !group.querySelector(".archive-record:not([hidden])");
    });
    result.textContent = `${shown} record${shown === 1 ? "" : "s"} shown`;
  }
  apply();
})();
'''


def _display_date(iso_date: str) -> str:
    try:
        parsed = datetime.strptime(iso_date, "%Y-%m-%d")
    except ValueError:
        return iso_date
    return parsed.strftime("%A, %B %d, %Y").replace(" 0", " ")


def _header(*, archive: bool) -> str:
    active_index = "" if archive else ' class="is-active"'
    active_archive = ' class="is-active"' if archive else ""
    target = "archive" if archive else "briefing"
    return f'''  <a class="skip-link" href="#main">Skip to {target}</a>
  <header class="site-header">
    <a class="brand" href="index.html">
      <img class="brand-mark" src="assets/gaza-logo.png" alt="">
      <span class="brand-text"><span class="brand-title">Dispatches From Gaza</span><span class="brand-kicker">Facts. Context. Humanity.</span></span>
    </a>
    <nav aria-label="Main navigation"><a href="index.html"{active_index}>Latest briefing</a><a href="archive.html"{active_archive}>Archive</a><a href="rss.xml">RSS</a></nav>
  </header>'''


def _footer(body: str) -> str:
    match = _FOOTER_RE.search(body)
    if match:
        return match.group(0)
    return '''  <footer class="site-footer">
    <div class="publisher">
      <a href="https://thebluefernco.com/" target="_blank" rel="noopener noreferrer"><img class="publisher-mark" src="/assets/bluefern.png" alt="The Blue Fern Co."></a>
      <p class="publisher-label">Published by <a href="https://thebluefernco.com/" target="_blank" rel="noopener noreferrer">The Blue Fern Company</a></p>
    </div>
  </footer>'''


def _document_parts(document: str) -> tuple[str, str]:
    head_end = document.find("</head>")
    body_start = document.find("<body>")
    if head_end < 0 or body_start < 0:
        raise ValueError("Gaza document is missing its HTML head/body boundary")
    return document[: head_end + len("</head>")], document[body_start + len("<body>") :]


def _replace_one(pattern: re.Pattern[str], document: str, replacement: str, label: str) -> str:
    match = pattern.search(document)
    if not match:
        raise ValueError(f"Gaza generated document is missing {label}")
    return document[: match.start()] + replacement + document[match.end() :]


def apply_gaza_home_layout(document: str) -> str:
    """Apply the approved Gaza homepage presentation to generated HTML."""
    head, body = _document_parts(document)
    latest_match = _LATEST_RE.search(body)
    checks_match = _CHECKS_RE.search(body)
    readable_match = _READABLE_RE.search(body)
    audio_match = _AUDIO_RE.search(body)
    if not all((latest_match, checks_match, readable_match, audio_match)):
        # Empty/bootstrap runs may intentionally have no readable record yet.
        # Keep the generator's conservative legacy output rather than inventing
        # an empty presentation surface.
        return document
    latest = latest_match.group(0)
    date_match = _DATE_RE.search(latest)
    latest_date = date_match.group(0).split(">", 1)[1].split("<", 1)[0] if date_match else ""
    readable = readable_match.group(0)
    checks = checks_match.group(0)
    footer = _footer(body)
    body_html = f'''<body>
{_header(archive=False)}
  <main id="main" class="home home--gaza">
    <section class="gaza-briefing-hero" aria-labelledby="briefing-title">
      <p class="eyebrow">The daily briefing</p>
      <h1 id="briefing-title">Traceable sources. A lasting record.</h1>
      <p class="publication-date">{html.escape(_display_date(latest_date))}</p>
    </section>
{latest}
    <section class="gaza-recent" aria-labelledby="recent-title">
      <div class="section-heading">
        <p class="eyebrow">Recent briefings</p>
        <h2 id="recent-title">The public record, in order</h2>
      </div>
      <p class="gaza-section-note">Briefings, corrections, and historical entries remain available in the full archive.</p>
{readable}
      <p class="gaza-archive-link"><a class="text-link" href="archive.html">Open the full archive</a></p>
    </section>
    <details class="gaza-monitoring">
      <summary>Monitoring &amp; source notes</summary>
      <p>Checks without a published briefing, and how to read these updates.</p>
{checks}
    </details>
    {audio_match.group(0)}
  </main>
{footer}
</body>
</html>
'''
    return head + body_html


def _record_type(row: str) -> str:
    if "archive-row--no-update" in row:
        return "monitoring"
    if re.search(r">Correction(?:\s|<)|Correction\s*\|", row):
        return "correction"
    return "briefing"


def _add_record_attributes(month: str, fallback_month: str = "") -> str:
    def replace(match: re.Match[str]) -> str:
        attrs = match.group("attrs")
        if "archive-record" in attrs:
            return match.group(0)
        body = match.group("body")
        date_match = _DATE_RE.search(body)
        record_month = f"{date_match.group(1)}-{date_match.group(2)}" if date_match else fallback_month
        kind = _record_type(match.group(0))
        return (
            f'<li class="{attrs} archive-record archive-record--{kind}" '
            f'data-record-type="{kind}" data-month="{html.escape(record_month)}">{body}</li>'
        )

    return _ARCHIVE_ROW_RE.sub(replace, month)


def apply_gaza_archive_layout(document: str) -> str:
    """Apply the approved Gaza archive presentation without changing rows."""
    head, body = _document_parts(document)
    latest_match = re.search(r'<section class="archive-latest".*?</section>', body, re.DOTALL)
    months = _MONTH_RE.findall(body)
    audio_match = _AUDIO_RE.search(body)
    if not latest_match or not months or not audio_match:
        # Preserve the existing bootstrap/empty archive contract.
        return document
    dated_months = []
    for month in months:
        month_match = _DATE_RE.search(month)
        month_name = f"{month_match.group(1)}-{month_match.group(2)}" if month_match else ""
        dated_months.append(_add_record_attributes(month, month_name))
    footer = _footer(body)
    controls = '''    <section class="archive-controls" aria-label="Filter archive records">
      <div class="archive-filter-group" role="group" aria-label="Record type">
        <button type="button" class="archive-filter is-active" data-filter="briefing" aria-pressed="true">Briefings</button>
        <button type="button" class="archive-filter" data-filter="correction" aria-pressed="false">Corrections</button>
        <button type="button" class="archive-filter" data-filter="monitoring" aria-pressed="false">Monitoring</button>
        <button type="button" class="archive-filter" data-filter="all" aria-pressed="false">All records</button>
      </div>
      <label class="archive-month-filter" for="archive-month">Month
        <select id="archive-month" name="month">
          <option value="">All months</option>
        </select>
      </label>
    </section>
    <p class="archive-result" id="archive-result" role="status" aria-live="polite"></p>'''
    body_html = f'''<body>
{_header(archive=True)}
  <main id="main" class="archive archive--gaza">
    <section class="archive-intro" aria-labelledby="archive-title">
      <p class="eyebrow">The lasting record</p>
      <h1 id="archive-title">Edition archive</h1>
      <p class="lede">Past briefings, in order. Corrections and monitoring remain part of the record - without getting in the way of the news.</p>
    </section>
    {audio_match.group(0)}
{latest_match.group(0)}
{controls}
    <div class="archive-context" role="note">
      <p><strong>A record, not a feed.</strong><br>Browse by publication date. Historical catch-ups retain their original event dates within each edition.</p>
      <p>Limited-source updates are partial coverage, not full daily briefings.</p>
    </div>
{chr(10).join(dated_months)}
  </main>
{footer}
  <script src="assets/archive-filters.js" defer></script>
</body>
</html>
'''
    return head + body_html

