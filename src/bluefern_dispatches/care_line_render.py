from __future__ import annotations

import html
from collections import Counter
from typing import Any

from bluefern_dispatches.care_line_sources import (
    DISPATCH_SLUG,
    POSITIONING_NOTE,
    PUBLIC_BUCKETS,
    DISPATCH_NAME,
    DISPATCH_TAGLINE,
    care_line_public_card_copy,
    public_bucket_note_labels,
    public_claim_rows,
    record_is_public,
    source_table_rows,
    summary_for_records,
)


def _document_shell(title: str, canonical: str, body: str, description: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <link rel="canonical" href="{html.escape(canonical)}">
  <meta name="description" content="{html.escape(description)}">
  <meta property="og:description" content="{html.escape(description)}">
  <meta name="twitter:description" content="{html.escape(description)}">
  <link rel="icon" href="/assets/favicon.ico" sizes="any">
  <link rel="icon" type="image/png" sizes="32x32" href="/assets/favicon-32x32.png">
  <link rel="icon" type="image/png" sizes="16x16" href="/assets/favicon-16x16.png">
  <link rel="apple-touch-icon" href="/assets/apple-touch-icon.png">
  <link rel="stylesheet" href="../../assets/site.css">
</head>
<body>
{body}
</body>
</html>
"""


def _section_cards(records: list[dict[str, Any]], bucket: str) -> str:
    rows = [record for record in records if str(record.get("public_inclusion_bucket") or "") == bucket and record_is_public(record)]
    if not rows:
        return ""
    cards: list[str] = []
    for record in rows:
        copy = care_line_public_card_copy(record)
        cards.append(
            f"""      <article class="care-line-signal-card">
        <p class="eyebrow">{html.escape(copy["pressure_label"])}</p>
        <h3>{html.escape(copy["source_title"])}</h3>
        <p class="source-meta">{html.escape(copy["source_meta"])}</p>
        <p><strong>What changed:</strong> {html.escape(copy["what_changed"])}</p>
        <p><strong>Who may be affected:</strong> {html.escape(copy["who_may_be_affected"])}</p>
        <p><strong>Why it matters:</strong> {html.escape(copy["why_it_matters"])}</p>
        <p><strong>Limit:</strong> {html.escape(copy["limit"])}</p>
        <p><a href="{html.escape(str(record.get("url") or ""))}" target="_blank" rel="noopener noreferrer">Open source</a></p>
      </article>"""
        )
    return "\n".join(cards)


def render_care_line_edition_body(records: list[dict[str, Any]], edition_date: str) -> str:
    public_rows = [record for record in records if record_is_public(record)]
    claim_rows = public_claim_rows(records)
    if not public_rows:
        body = f"""<section class="hero">
      <p class="eyebrow">Edition | {html.escape(edition_date)}</p>
      <h1>{html.escape(DISPATCH_NAME)}</h1>
      <p class="lede">{html.escape(DISPATCH_TAGLINE)}</p>
      <p>{html.escape(POSITIONING_NOTE)}</p>
      <p><a href="source_table.html">Open the source table</a> | <a href="claim_ledger.html">Open the claim ledger</a></p>
    </section>
    <section class="section">
      <h2>Plain-English Summary</h2>
      <p>No current Care Line update was published because no fresh source-backed healthcare-access pressure signal qualified from the reviewed source records.</p>
    </section>
    <section class="section">
      <h2>At A Glance</h2>
      <ul class="edition-list">
      <li>No public claims qualified for this edition.</li>
      </ul>
    </section>
    <section class="section">
      <h2>Source Mix</h2>
      <p>No current public signals qualified.</p>
      <ul>
        <li>No public claims were published.</li>
      </ul>
    </section>
    <section class="section">
      <h2>Source Note</h2>
      <p>Each public claim is tied to saved source records. The source table preserves all edition records, including those excluded from public inclusion.</p>
      <p>Care Line does not publish a map in this release. The source table and claim ledger preserve the traceable record for readers and researchers even when no public claims qualify.</p>
      <p><a href="source_table.html">source table</a> | <a href="claim_ledger.html">claim ledger</a> | <a href="../">Archive</a></p>
    </section>"""
        return body

    at_a_glance = "\n".join(
        f"      <li>{html.escape(row['supporting_source'])} - {html.escape(row['publisher'])}</li>" for row in claim_rows
    )
    public_summary = summary_for_records(records)
    public_story_count = len(public_rows)
    source_family_count = len({str(record.get("source_family") or "") for record in public_rows if str(record.get("source_family") or "").strip()})
    source_family_label = "source family" if source_family_count == 1 else "source families"
    sections = []
    for bucket in PUBLIC_BUCKETS:
        cards = _section_cards(records, bucket)
        if not cards:
            continue
        sections.append(
            f"""    <section class="section">
      <h2>{html.escape(bucket)}</h2>
      <div class="signal-grid">
{cards}
      </div>
    </section>"""
        )

    empty_bucket_labels = public_bucket_note_labels(records)
    empty_bucket_note = ""
    if empty_bucket_labels:
        empty_bucket_note = (
            "\n    <section class=\"section\">"
            f"<p>Other monitored categories had no qualifying public signal in this edition: {html.escape(', '.join(empty_bucket_labels))}.</p>"
            "</section>"
        )

    bucket_summary = Counter(
        str(record.get("source_family") or "") for record in records if record_is_public(record)
    )
    family_mix = ", ".join(f"{html.escape(key)} ({value})" for key, value in sorted(bucket_summary.items()) if key) or "No qualified public sources"
    source_mix_html = f"""
    <section class="section">
      <h2>Source Mix</h2>
      <p>{html.escape(family_mix)}</p>
      <p>{public_story_count} public signal{'s' if public_story_count != 1 else ''} from {source_family_count} {source_family_label}.</p>
      <ul>
{''.join(f'        <li>{html.escape(row["publisher"])} - {html.escape(row["freshness_role"] or "current")}</li>' for row in claim_rows) or '        <li>No qualified public claims were published.</li>'}
      </ul>
    </section>"""

    body = f"""<section class="hero">
      <p class="eyebrow">Edition | {html.escape(edition_date)}</p>
      <h1>{html.escape(DISPATCH_NAME)}</h1>
      <p class="lede">{html.escape(DISPATCH_TAGLINE)}</p>
      <p>{html.escape(POSITIONING_NOTE)}</p>
      <p><a href="source_table.html">Open the source table</a> | <a href="claim_ledger.html">Open the claim ledger</a></p>
    </section>
    <section class="section">
      <h2>Today's Read</h2>
      <p>{html.escape(public_summary)}</p>
      <p>The source table preserves excluded and context-only records alongside public signals so readers can trace why each record was or was not used.</p>
    </section>
    <section class="section">
      <h2>Plain-English Summary</h2>
      <p>{html.escape(public_summary)}</p>
    </section>
    <section class="section">
      <h2>At A Glance</h2>
      <ul class="edition-list">
{at_a_glance}
      </ul>
    </section>
    {''.join(sections)}
    {empty_bucket_note}
    {source_mix_html}
    <section class="section">
      <h2>Source Note</h2>
      <p>Each public claim is tied to saved source records. The source table shows all edition records, including those excluded from public inclusion.</p>
      <p>Care Line does not publish a map in this release. The source table and claim ledger preserve the traceable record for readers and researchers.</p>
      <p><a href="source_table.html">source table</a> | <a href="claim_ledger.html">claim ledger</a> | <a href="../">Archive</a></p>
    </section>"""
    return body


def render_care_line_source_table_html(records: list[dict[str, Any]], edition_date: str) -> str:
    rows = source_table_rows(records)
    body_rows = []
    for row in rows:
        body_rows.append(
            f"""      <tr>
        <th scope="row">{html.escape(row['record_id'])}</th>
        <td>{html.escape(row['title'])}</td>
        <td>{html.escape(row['publisher'])}</td>
        <td>{html.escape(row['location'])}</td>
        <td><a href="{html.escape(row['source_link'])}" target="_blank" rel="noopener noreferrer">Open source</a></td>
        <td>{html.escape(row['source_family'])}</td>
        <td>{html.escape(row['how_used'])}</td>
        <td>{html.escape(row['issue'])}</td>
        <td>{html.escape(row['what_happened'])}</td>
        <td>{html.escape(row['what_the_source_says'])}</td>
        <td>{html.escape(row['verification_status'])}</td>
        <td>{html.escape(row['who_may_be_affected'])}</td>
        <td>{html.escape(row['used_on_public_page'])}</td>
        <td>{html.escape(row['freshness_status'])}</td>
        <td>{html.escape(row['date_basis'])}</td>
        <td>{html.escape(row['public_story_eligible'])}</td>
      </tr>"""
        )
    table_rows = "\n".join(body_rows) or "      <tr><td colspan=\"16\">No current Care Line update was published for this edition.</td></tr>"
    body = f"""<section class="hero">
      <p class="eyebrow">Source Table | {html.escape(edition_date)}</p>
      <h1>{html.escape(DISPATCH_NAME)} Source Table</h1>
      <p>{html.escape(POSITIONING_NOTE)}</p>
      <p>This table preserves the public signals, excluded context, and stale records that informed the edition.</p>
      <p><a href="./">Back to edition</a> | <a href="claim_ledger.html">Open claim ledger</a></p>
    </section>
    <section class="section">
      <table>
        <thead>
          <tr>
            <th scope="col">Record ID</th>
            <th scope="col">Title</th>
            <th scope="col">Publisher</th>
            <th scope="col">Location</th>
            <th scope="col">Source Link</th>
            <th scope="col">Source Family</th>
            <th scope="col">How It Was Used</th>
            <th scope="col">Issue</th>
            <th scope="col">What Happened</th>
            <th scope="col">What the Source Says</th>
            <th scope="col">Verification Status</th>
            <th scope="col">Who May Be Affected</th>
            <th scope="col">Used on Public Page</th>
            <th scope="col">Freshness Status</th>
            <th scope="col">Date Basis</th>
            <th scope="col">Public Story Eligible</th>
          </tr>
        </thead>
        <tbody>
{table_rows}
        </tbody>
      </table>
    </section>"""
    return _document_shell(
        f"{DISPATCH_NAME} Source Table",
        f"https://dispatches.thebluefernco.com/{DISPATCH_SLUG}/editions/{edition_date}/source_table.html",
        body,
        POSITIONING_NOTE,
    )


def render_care_line_claim_ledger_html(records: list[dict[str, Any]], edition_date: str) -> str:
    rows = public_claim_rows(records)
    body_rows = []
    for row in rows:
        body_rows.append(
            f"""      <tr>
        <td>{html.escape(row['claim'])}</td>
        <td>{html.escape(row['interpretation'])}</td>
        <td>{html.escape(row['supporting_source'])}</td>
        <td>{html.escape(row['publisher'])}</td>
        <td><a href="{html.escape(row['url'])}" target="_blank" rel="noopener noreferrer">Open source</a></td>
        <td>{html.escape(row['published_at'])}</td>
        <td>{html.escape(row['retrieved_at'])}</td>
        <td>{html.escape(row['evidence_level'])}</td>
        <td>{html.escape(row['confidence'])}</td>
        <td>{html.escape(row['freshness_role'])}</td>
        <td>{html.escape(row['location_scope'])}</td>
        <td>{html.escape(row['limitation'])}</td>
      </tr>"""
    )
    table_rows = "\n".join(body_rows) or "      <tr><td colspan=\"12\">No current Care Line update was published for this edition.</td></tr>"
    body = f"""<section class="hero">
      <p class="eyebrow">Claim Ledger | {html.escape(edition_date)}</p>
      <h1>{html.escape(DISPATCH_NAME)} Claim Ledger</h1>
      <p>{html.escape(POSITIONING_NOTE)}</p>
      <p>This ledger keeps the public claims, supporting interpretations, and traceability limits in one place.</p>
      <p><a href="./">Back to edition</a> | <a href="source_table.html">Open source table</a></p>
    </section>
    <section class="section">
      <table>
        <thead>
          <tr>
            <th scope="col">Claim</th>
            <th scope="col">Interpretation / why it matters</th>
            <th scope="col">Supporting Source</th>
            <th scope="col">Publisher</th>
            <th scope="col">Source URL</th>
            <th scope="col">Published Date</th>
            <th scope="col">Retrieved Date</th>
            <th scope="col">Evidence Level</th>
            <th scope="col">Confidence</th>
            <th scope="col">Freshness Role</th>
            <th scope="col">Location Scope</th>
            <th scope="col">Limitation</th>
          </tr>
        </thead>
        <tbody>
{table_rows}
        </tbody>
      </table>
    </section>"""
    return _document_shell(
        f"{DISPATCH_NAME} Claim Ledger",
        f"https://dispatches.thebluefernco.com/{DISPATCH_SLUG}/editions/{edition_date}/claim_ledger.html",
        body,
        POSITIONING_NOTE,
    )
