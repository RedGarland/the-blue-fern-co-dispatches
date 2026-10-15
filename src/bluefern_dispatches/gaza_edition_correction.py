"""Private, coordinated edition correction preparation. No approval or release API.

The legacy single-story contract is deliberately unchanged. This owner reuses
its concrete-syntax patching and audio ownership checks, but has a separate
preparation/proposal schema so an unreviewed packet cannot enter legacy staging.
"""
from __future__ import annotations

import html
import json
import os
import posixpath
import re
import shutil
import tempfile
from datetime import datetime
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from xml.etree import ElementTree

from . import gaza_historical_correction as legacy
from .historical_agent_archive import validate_gaza_published_story_lineage

Error = legacy.CorrectionValidationError
PACKET_SCHEMA = "gaza_edition_correction_preparation_v1"
PROPOSAL_SCHEMA = "gaza_edition_correction_proposal_v1"
REQUEST_SCHEMA = "gaza_edition_correction_request_v1"
REVIEW_SCHEMA = "gaza_edition_correction_review_v1"
REASONS = {"scope_error", "insufficient_saved_evidence"}
DECISIONS = {
    "scope_error": "confirmed_public_scope_contamination",
    "insufficient_saved_evidence": "confirmed_saved_evidence_insufficiency",
}
AUTHORITY_FIELDS = (
    "archive_authorized", "edition_authorized", "queue_authorized",
    "source_record_authorized", "cluster_authorized", "audio_authorized",
    "publication_authorized", "approval_authorized",
)
PREVIEW_PATHS = {
    **legacy.WITHDRAWAL_PREVIEW_PATHS,
    "dispatches_index": "dispatches/index.html",
}
DEPENDENCIES = {
    "source_quality_report": "gaza/editions/{date}/source_quality_report.md",
    "original_audio": "gaza/audio/{date}.mp3",
}


def _json(value: Any) -> bytes:
    return legacy._json_document(value)


def _hash(value: bytes) -> str:
    return legacy.sha256_bytes(value)


def _load(path: Path, label: str) -> Any:
    try:
        return legacy._load_json_document(path, label).root.value
    except (OSError, ValueError) as exc:
        raise Error(f"{label} is missing or invalid: {exc}") from exc


def _path(root: Path, relative: str) -> Path:
    return legacy._repo_path(root, relative, "edition correction artifact")


def _outside(root: Path, *repos: Path) -> None:
    for repo in repos:
        if root == repo or repo in root.parents:
            raise Error("correction output must be outside source and Pages")


def _heads(source: Path, pages: Path, request: dict[str, Any]) -> tuple[str, str]:
    actual = (legacy._git(source, "rev-parse", "HEAD"), legacy._git(pages, "rev-parse", "HEAD"))
    if actual != (request["expected_source_head"], request["expected_pages_head"]):
        raise Error("source or Pages head drifted")
    if legacy._git(source, "status", "--porcelain") or legacy._git(pages, "status", "--porcelain"):
        raise Error("source and Pages must be clean")
    if legacy._git(pages, "rev-parse", "--abbrev-ref", "HEAD") != "gh-pages":
        raise Error("Pages must be on gh-pages")
    return actual


def _array_remove(document: Any, node: Any, removed: set[int], owner: str) -> list[Any]:
    """Remove bounded comma spans, preserving every retained row's exact bytes."""
    if not removed:
        return []
    legacy._require_json_kind(node, "array", owner)
    if removed == set(range(len(node.items))):
        return [legacy._JsonEdit(node.items[0].start, node.items[-1].end, "", owner)]
    edits = []
    for first, last in _contiguous_groups(removed):
        if last + 1 < len(node.items):
            start, end = node.items[first].start, node.items[last + 1].start
        else:
            start, end = node.items[first - 1].end, node.items[last].end
        edits.append(legacy._JsonEdit(start, end, "", owner))
    return edits


def _patch_members(document: Any, node: Any, updates: dict[str, Any], owner: str) -> list[Any]:
    edits, additions = [], []
    for key, value in updates.items():
        member = node.member(key)
        if member is None:
            additions.append((key, value))
        elif key == "corrections" and member.value.kind == "array" and member.value.items:
            # Earlier correction records are immutable history, not current
            # dedupe candidates. Append without reserializing their lexemes.
            last = member.value.items[-1]
            indent = legacy._line_indent(document.artifact.text, last.start, owner)
            separator = "," + document.artifact.newline + indent
            appended = separator + separator.join(legacy._json_fragment(v, document.artifact, indent) for v in value)
            edits.append(legacy._JsonEdit(last.end, last.end, appended, owner))
        elif not legacy._json_values_equal(member.value.value, value):
            edits.append(legacy._replace_json_value(
                document, member, expected=member.value.value, replacement=value,
                owner=f"{owner}.{key}",
            ))
    insert = legacy._insert_json_members(document, node, additions, owner)
    if insert is not None:
        edits.append(insert)
    return edits


def _filter_story_collections(document: Any, node: Any, ids: set[str]) -> list[Any]:
    edits = []
    if node.kind == "array":
        removed = {i for i, row in enumerate(node.items)
                   if (row.kind == "object" and row.value.get("story_id") in ids)
                   or (row.kind == "string" and row.value in ids)}
        edits.extend(_array_remove(document, node, removed, "dedupe story collections"))
        for i, child in enumerate(node.items):
            if i not in removed:
                edits.extend(_filter_story_collections(document, child, ids))
    elif node.kind == "object":
        removed = {i for i, member in enumerate(node.members) if member.key != "corrections" and (
            member.key in ids or (member.value.kind == "object" and member.value.value.get("story_id") in ids))}
        for first, last in _contiguous_groups(removed):
            if len(removed) == len(node.members):
                start, end = node.members[0].key_start, node.members[-1].value.end
            elif last + 1 < len(node.members):
                start, end = node.members[first].key_start, node.members[last + 1].key_start
            else:
                start, end = node.members[first - 1].value.end, node.members[last].value.end
            edits.append(legacy._JsonEdit(start, end, "", "dedupe keyed story collections"))
        for i, member in enumerate(node.members):
            if member.key != "corrections" and i not in removed:
                edits.extend(_filter_story_collections(document, member.value, ids))
    return edits


def _contiguous_groups(indices: set[int]) -> list[tuple[int, int]]:
    groups = []
    for index in sorted(indices):
        if groups and groups[-1][1] + 1 == index:
            groups[-1] = (groups[-1][0], index)
        else:
            groups.append((index, index))
    return groups


def _story_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [row for item in value for row in _story_rows(item)]
    if isinstance(value, dict):
        return ([value] if "story_id" in value else []) + [
            row for key, item in value.items() if key != "corrections" for row in _story_rows(item)
        ]
    return []


def _story_references(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value} if re.fullmatch(r"gaza-story-20\d{2}-\d{2}-\d{2}-\d{3}", value) else set()
    if isinstance(value, list):
        return set().union(*(_story_references(item) for item in value)) if value else set()
    if isinstance(value, dict):
        return set().union(*(_story_references(key) | _story_references(item)
                             for key, item in value.items() if key != "corrections")) if value else set()
    return set()


def _casualty_aggregates(original: dict[str, Any], retained: list[dict[str, Any]]) -> dict[str, Any]:
    updates = {}
    for key in ("casualty_totals", "casualty_counts", "aggregate_casualty_counts"):
        if key not in original:
            continue
        counters = original[key]
        if not isinstance(counters, dict) or any(type(v) is not int or v < 0 for v in counters.values()):
            raise Error("edition casualty aggregate shape is unsupported")
        totals = {}
        for field in counters:
            values = [s.get("casualty_counts", {}).get(field, 0) for s in retained]
            if any(type(v) is not int or v < 0 for v in values):
                raise Error("retained casualty claims do not support a numeric aggregate")
            totals[field] = sum(values)
        updates[key] = totals
    return updates


def _one(nodes: list[Any], label: str) -> Any:
    if len(nodes) != 1:
        raise Error(f"{label} is missing or ambiguous")
    return nodes[0]


def _text(node: Any) -> str:
    return legacy._normalized_html_text(node)


def _within(node: Any, owner: Any) -> bool:
    return legacy._is_descendant(node, owner)


def _inner(node: Any, text: str) -> tuple[int, int, str]:
    if node.end_tag_start is None:
        raise Error("text replacement has no closing boundary")
    return node.start_tag_end, node.end_tag_start, html.escape(text, quote=False)


def _apply(artifact: Any, edits: list[tuple[int, int, str]], role: str) -> bytes:
    result = legacy._apply_html_replacements(artifact.text, edits)
    # The same bounded spans must be reversible, including BOM/newline state.
    inverse, delta = [], 0
    for start, end, value in sorted(edits):
        inverse.append((start + delta, start + delta + len(value), artifact.text[start:end]))
        delta += len(value) - (end - start)
    cursor, reversed_parts = 0, []
    for start, end, original in inverse:
        reversed_parts.extend((result[cursor:start], original))
        cursor = end
    reversed_parts.append(result[cursor:])
    if artifact.encode("".join(reversed_parts), role) != artifact.encode(artifact.text, role):
        raise Error(f"{role} edits are not byte reversible")
    return artifact.encode(result, role)


def _insertion(nodes: list[Any], notice: str) -> tuple[int, int, str]:
    main = _one([n for n in nodes if n.tag == "main"], "main insertion owner")
    return main.end_tag_start, main.end_tag_start, notice


def _copy(context: dict[str, Any]) -> tuple[str, str, str]:
    date = legacy._natural_date(context["edition_date"], "owning edition")
    correction_date = legacy._natural_date(context["correction_date"], "correction date")
    proposed = not context["substantively_reviewed"]
    heading = ("Proposed correction" if proposed else "Correction") + f" — {correction_date}"
    paragraphs = []
    for target in context["targets"]:
        title = target["story"]["title"]
        publisher = target["lineage"]["source_attribution"]
        prefix = f"The {date} edition included a story from {publisher}: {title}. "
        if target["reason"] == "scope_error":
            reason = "The saved source describes an event outside Gaza and does not support its placement as a core in-Gaza development."
        else:
            reason = ("The saved evidence repeats a discovery headline without sufficient article detail for the published projection. "
                      "The underlying article’s accuracy and relevance remain undetermined.")
        treatment = "Its projection would be removed in this proposed correction." if proposed else "Its unsupported projection has been removed from the corrected edition."
        paragraphs.append(prefix + reason + " " + treatment)
    count = len(context["retained"])
    summary = f"{count} retained public {'story remains' if count == 1 else 'stories remain'} in the corrected selection."
    if not count:
        summary = "No qualified stories remain in the corrected saved-input selection. The saved evidence does not establish that no news occurred."
    script = heading + ".\n\n" + "\n\n".join(paragraphs) + "\n\n" + summary
    return heading, summary, script


def _notice(context: dict[str, Any]) -> str:
    heading, summary, _ = _copy(context)
    pieces = [f'<section class="formal-correction" id="{context["correction_id"]}"><h2>{html.escape(heading)}</h2>']
    for target in context["targets"]:
        single = {**context, "targets": [target]}
        _, _, script = _copy(single)
        paragraph = script.split("\n\n")[1]
        pieces.append(f"<p>{html.escape(paragraph)}</p><ul>")
        for source in target["lineage"]["source_records"]:
            label = source["publisher"] + (" saved discovery link" if "news.google.com" in source["url"] else " source")
            pieces.append(f'<li><a href="{html.escape(source["url"], quote=True)}">{html.escape(label)}</a></li>')
        pieces.append("</ul>")
    pieces.append(f"<p>{html.escape(summary)}</p></section>")
    return "".join(pieces)


def _edition_html(path: Path, context: dict[str, Any]) -> bytes:
    artifact = legacy._read_text_artifact(path, "edition HTML")
    nodes = legacy._StrictHtmlTreeParser(artifact.text).finish()
    today = _one([n for n in nodes if n.tag == "h2" and _text(n) == "Today’s Read"], "Today’s Read")
    glance = _one([n for n in nodes if n.tag == "h2" and _text(n) == "At A Glance"], "At A Glance")
    if today.parent is not glance.parent or today.start >= glance.start:
        raise Error("edition summary ownership drifted")
    summaries = [n for n in today.parent.children if n.tag == "p" and today.end <= n.start < glance.start]
    if len(summaries) != 1 + min(3, len(context["curation"])):
        raise Error("edition Today projection count contradicts curation")
    heading, summary, _ = _copy(context)
    edits = [_inner(summaries[0], summary)]
    for target in context["targets"]:
        story = target["story"]
        ordinal = context["curation"].index(story)
        if ordinal < 3:
            paragraph = summaries[ordinal + 1]
            if _text(paragraph) != " ".join(story["summary"].split()):
                raise Error("Today projection differs from exact story claim")
            edits.append((paragraph.start, paragraph.end, ""))
        urls = {s["url"] for s in target["lineage"]["source_records"]} | set(story["source_urls"])
        articles = [n for n in nodes if n.tag == "article" and any(
            link.tag == "a" and _within(link, n) and link.attrs.get("href") in urls for link in nodes)]
        article = _one(articles, "manifest-owned article")
        title = _one([n for n in nodes if n.tag == "h3" and _within(n, article)], "article title")
        if _text(title) != story["title"] or story["summary"] not in article.text:
            raise Error("article claim/title differs from lineage")
        edits.append((article.start, article.end, ""))
        glance_rows = [n for n in nodes if n.tag == "li" and glance.end <= n.start < article.start and _text(n) == story["title"]]
        row = _one(glance_rows, "At A Glance story")
        edits.append((row.start, row.end, ""))
    # These are edition-owned explanatory paragraphs, not historical timestamps.
    for node in nodes:
        if node.tag == "p" and node.parent is today.parent:
            if _text(node).startswith("Source mix:"):
                edits.append(_inner(node, summary))
            elif _text(node).startswith("Publishers:"):
                publishers = sorted({p for s in context["retained"] for p in s.get("publisher_names", [])})
                edits.append(_inner(node, "Retained-story publishers: " + (", ".join(publishers) or "none") + "."))
            elif _text(node).startswith("This edition is generated only from saved source"):
                edits.append(_inner(node, "Original saved evidence is preserved. This corrected selection and its reasons are recorded in the correction notice."))
            elif "limited-source update generated from" in _text(node):
                edits.append(_inner(node, summary))
            elif node.attrs.get("class") == "eyebrow":
                edits.append(_inner(node, heading))
    edits.append(_insertion(nodes, _notice(context)))
    return _apply(artifact, edits, "edition HTML")


def _listing_html(path: Path, context: dict[str, Any], role: str) -> bytes:
    artifact = legacy._read_text_artifact(path, role)
    nodes = legacy._StrictHtmlTreeParser(artifact.text).finish()
    date = context["edition_date"]
    paths = {f"editions/{date}/", f"/gaza/editions/{date}/", f"https://dispatches.thebluefernco.com/gaza/editions/{date}/"}
    links = [n for n in nodes if n.tag == "a" and n.attrs.get("href") in paths]
    owners = []
    for link in links:
        owner = link.parent
        while owner and owner.tag not in {"article", "li"} and not (owner.tag == "section" and owner.attrs.get("class") == "archive-latest"):
            owner = owner.parent
        if owner is None:
            raise Error(f"{role} listing ownership is ambiguous")
        if owner not in owners:
            owners.append(owner)
    # The homepage's "latest editions" card can link to the Gaza desk rather
    # than the dated page. Bind it by desk, exact date and the original claim.
    natural_date = legacy._natural_date(date, "edition")
    desk_paths = {"/gaza/", "../gaza/", "https://dispatches.thebluefernco.com/gaza/"}
    for candidate in nodes:
        if (candidate.tag == "article" and candidate not in owners
                and (date in candidate.text or natural_date in candidate.text)
                and any(s["summary"] in candidate.text or s["title"] in candidate.text for s in context["curation"])
                and any(link.tag == "a" and link.attrs.get("href") in desk_paths and _within(link, candidate) for link in nodes)):
            owners.append(candidate)
    if not owners and role == "gaza_archive":
        raise Error("Gaza archive lacks the owning-edition history entry")
    _, summary, _ = _copy(context)
    title = f"Correction to the {legacy._natural_date(date, 'edition')} Gaza edition"
    edits = []
    for owner in owners:
        headlines = [n for n in nodes if _within(n, owner) and n.tag in {"h2", "h3"} and any(
            s["title"] == _text(n) for s in context["curation"])]
        headline_links = [n for n in links if _within(n, owner) and (n.attrs.get("class") == "briefing-row-title" or any(_within(n, h) for h in headlines))]
        for node in headline_links:
            edits.append(_inner(node, title))
        for node in headlines:
            if not any(_within(link, node) for link in headline_links):
                edits.append(_inner(node, title))
        for node in nodes:
            if not _within(node, owner):
                continue
            if node.tag in {"p", "span"} and not node.children and any(
                _text(node).startswith(s["summary"]) for s in context["curation"]):
                edits.append(_inner(node, summary))
            elif node.tag == "span" and node.attrs.get("class") == "briefing-row-summary":
                edits.append(_inner(node, summary))
            elif node.tag in {"p", "span"} and any(c in (node.attrs.get("class") or "").split() for c in ("briefing-row-meta", "topic-badge", "edition-meta")):
                edits.append(_inner(node, "Correction — " + summary))
            elif node.tag == "a" and node in links and _text(node) in {"Read briefing", "Read latest", "Read"}:
                edits.append(_inner(node, "Read corrected edition"))
    edits.append(_insertion(nodes, _notice(context)))
    return _apply(artifact, edits, role)


def _feed(path: Path, context: dict[str, Any], role: str) -> bytes:
    artifact = legacy._read_text_artifact(path, role)
    try:
        ElementTree.fromstring(path.read_bytes())
    except ElementTree.ParseError as exc:
        raise Error(f"{role} is malformed XML") from exc
    nodes = legacy._StrictHtmlTreeParser(artifact.text, void_tags=set()).finish()
    channel = _one([n for n in nodes if n.tag == "channel"], "feed channel")
    date, cid = context["edition_date"], context["correction_id"]
    target = f"https://dispatches.thebluefernco.com/gaza/editions/{date}/" if role == "rss" else f"https://dispatches.thebluefernco.com/gaza/audio/{date}-transcript.html"
    item = _one([n for n in nodes if n.tag == "item" and any(
        child.tag == "guid" and _text(child) == target and _within(child, n) for child in nodes)], "owning feed item")
    title = _one([n for n in nodes if n.tag == "title" and _within(n, item)], "feed title")
    desc = _one([n for n in nodes if n.tag == "description" and _within(n, item)], "feed description")
    heading, summary, _ = _copy(context)
    correction_url = f"https://dispatches.thebluefernco.com/gaza/corrections/{cid}/"
    existing_title = f"Corrected Gaza edition — {legacy._natural_date(date, 'edition')}" if role == "rss" else f"Superseded original Gaza recording — {legacy._natural_date(date, 'edition')}"
    edits = [_inner(title, existing_title), _inner(desc, summary + " Correction record: " + correction_url)]
    audio = "" if role == "rss" else f'<enclosure url="https://dispatches.thebluefernco.com/gaza/audio/corrections/{cid}.mp3" length="0" type="audio/mpeg" />'
    new = (f'<item><title>{html.escape(heading)}</title><link>{correction_url}</link>'
           f'<guid isPermaLink="false">{cid}</guid><description>{html.escape(summary)}'
           + (" Correction audio render is pending separate approval." if audio else "")
           + f'</description><pubDate>{format_datetime(datetime.fromisoformat(context["correction_at"].replace("Z", "+00:00")))}</pubDate>{audio}</item>')
    first_item = min([n.start for n in nodes if n.tag == "item" and n.parent is channel], default=channel.end_tag_start)
    edits.append((first_item, first_item, new + artifact.newline))
    return _apply(artifact, edits, role)


def _audio_index(path: Path, context: dict[str, Any]) -> bytes:
    artifact = legacy._read_text_artifact(path, "audio index")
    nodes = legacy._StrictHtmlTreeParser(artifact.text).finish()
    mp3 = f'/gaza/audio/{context["edition_date"]}.mp3'
    row = _one([n for n in nodes if n.tag == "li" and any(
        link.tag == "a" and link.attrs.get("href") == mp3 and _within(link, n) for link in nodes)], "original audio row")
    cid = context["correction_id"]
    treatment = "proposed correction" if not context["substantively_reviewed"] else "correction"
    notice = f'<span>Original recording superseded by the {treatment}. <a href="/gaza/corrections/{cid}/">Read correction</a></span>'
    heading, _, _ = _copy(context)
    new = f'<li><a href="/gaza/corrections/{cid}/">{html.escape(heading)}</a> — correction audio render pending.</li>'
    return _apply(artifact, [(row.end_tag_start, row.end_tag_start, notice), (row.end, row.end, new)], "audio index")


def _render(pages: Path, context: dict[str, Any]) -> tuple[dict[str, bytes], dict[str, Any]]:
    date, cid = context["edition_date"], context["correction_id"]
    ids = {t["story"]["story_id"] for t in context["targets"]}
    retained_ids = {s["story_id"] for s in context["retained"]}
    source_ids = {sid for s in context["retained"] for sid in s["source_record_ids"]}
    publishers = sorted({s["publisher"] for s in context["sources"] if s["source_record_id"] in source_ids})
    payloads = {}
    for role in ("curation_manifest", "sources_manifest", "dedupe_report", "edition_manifest", "original_audio_metadata", "flash_briefing"):
        rel = PREVIEW_PATHS[role].format(date=date)
        doc = legacy._load_json_document(_path(pages, rel), role)
        edits = []
        if role == "curation_manifest":
            removed = {i for i, row in enumerate(doc.root.items) if row.value["story_id"] in ids}
            edits = _array_remove(doc, doc.root, removed, role)
        elif role == "sources_manifest":
            for row in doc.root.items:
                member = legacy._require_json_member(row, "used_in_story_ids", role)
                removed = {i for i, item in enumerate(member.value.items) if item.value in ids}
                edits.extend(_array_remove(doc, member.value, removed, role))
        elif role == "dedupe_report":
            edits = _filter_story_collections(doc, doc.root, ids)
            edits.extend(_patch_members(doc, doc.root, {"corrections": [context["record"]], "retained_story_count": len(retained_ids)}, role))
        elif role == "edition_manifest":
            _, summary, _ = _copy(context)
            updates = {
                "story_count": len(retained_ids), "source_count": len(source_ids),
                "publisher_count": len(publishers), "publishers": publishers,
                "source_adequacy_status": "no_qualified_stories" if not retained_ids else "corrected_source_update",
                "source_adequacy_label": "No qualified stories retained" if not retained_ids else "Corrected source update",
                "source_adequacy_warnings": [summary], "warnings": [summary],
                "public_exposed": False, "publication_authorized": False,
                "publication_state": "private_correction_preview", "prior_public_exposed": True,
                "saved_source_count": len(context["sources"]), "retained_story_ids": sorted(retained_ids),
                "corrections": [context["record"]], "selection_counts_recomputed_after_correction": True,
            }
            updates.update(_casualty_aggregates(doc.root.value, context["retained"]))
            for key in ("included_story_count", "qualified_story_count"):
                if doc.root.member(key):
                    updates[key] = len(retained_ids)
            edits = _patch_members(doc, doc.root, updates, role)
        elif role == "original_audio_metadata":
            edits = _patch_members(doc, doc.root, {
                "audio_status": "superseded_pending_correction_audio",
                "superseded_by_correction_id": cid, "supersession_notice": _copy(context)[1],
                "historical_recording": True, "current_briefing": False,
                "publication_authorized": False,
            }, role)
        elif context["flash_ownership"].edition_date == date:
            item = doc.root.items[0]
            edits = _patch_members(doc, item, {
                "uid": cid, "titleText": _copy(context)[0],
                "mainText": " ".join(_copy(context)[2].split()),
                "updateDate": context["correction_at"],
                "redirectionUrl": f"/gaza/audio/corrections/{cid}.mp3",
                "render_status": "pending_separate_approval", "publication_authorized": False,
            }, role)
        payloads[role] = doc.render(edits)
    payloads["edition_html"] = _edition_html(_path(pages, PREVIEW_PATHS["edition_html"].format(date=date)), context)
    for role in ("gaza_index", "gaza_archive", "root_index", "dispatches_index"):
        payloads[role] = _listing_html(_path(pages, PREVIEW_PATHS[role]), context, role)
    for role in ("rss", "podcast", "audio_podcast"):
        payloads[role] = _feed(_path(pages, PREVIEW_PATHS[role]), context, role)
    payloads["audio_index"] = _audio_index(_path(pages, PREVIEW_PATHS["audio_index"]), context)
    transcript = legacy._read_text_artifact(_path(pages, PREVIEW_PATHS["original_transcript"].format(date=date)), "original transcript")
    nodes = legacy._StrictHtmlTreeParser(transcript.text).finish()
    # Historical spoken text stays exact, visibly separated from current news.
    main = _one([n for n in nodes if n.tag == "main"], "transcript owner")
    treatment = "proposed correction" if not context["substantively_reviewed"] else "correction"
    disclaimer = f'<p><strong>Historical recording: its original transcript is preserved below. The {treatment} supersedes its current-news use.</strong></p>' + _notice(context)
    archive = '<details class="historical-recording"><summary>Original recording and transcript preserved as historical evidence</summary>'
    payloads["original_transcript"] = _apply(transcript, [(main.start_tag_end, main.start_tag_end, disclaimer + archive),
        (main.end_tag_start, main.end_tag_start, "</details>")], "original transcript")
    script = _copy(context)[2]
    audio_request = {
        "schema_version": "gaza_edition_correction_audio_request_v1", "correction_id": cid,
        "owning_edition_date": date, "correction_date": context["correction_date"],
        "prepared_at": context["correction_at"],
        "script_text": script, "script_sha256": _hash(script.encode("utf-8")),
        **context["tts"], "public_path": f"gaza/audio/corrections/{cid}.mp3",
        "render_authorized": False, "publication_authorized": False,
    }
    payloads["correction_audio_metadata"] = _json({
        **audio_request, "render_status": "pending_separate_approval", "rendered_audio_sha256": None,
        "audio_status": "correction_audio_request_only", "current_briefing": False,
    })
    payloads["correction_transcript"] = ("<!doctype html><html><body><main>" + _notice(context)
        + f'<p>{html.escape(script)}</p></main></body></html>').encode("utf-8")
    payloads["correction_page"] = ("<!doctype html><html><body><main>" + _notice(context) + "</main></body></html>").encode("utf-8")
    payloads["correction_manifest"] = _json(context["record"])
    if set(payloads) != set(PREVIEW_PATHS):
        raise Error("incomplete correction preview surface set")
    _validate_semantics(payloads, context, audio_request)
    return payloads, audio_request


def _render_assets(payloads: dict[str, bytes], pages: Path, date: str, cid: str) -> set[str]:
    """Discover local rendering dependencies without fetching external assets."""
    assets, pending = set(), []
    def discover(value: str, owner: str) -> None:
        parsed = urlsplit(value)
        if parsed.scheme or parsed.netloc:
            if parsed.scheme != "https" or parsed.netloc != legacy.PUBLIC_SITE_HOST:
                return
        path = parsed.path
        if not path or value.startswith("data:"):
            return
        relative = posixpath.normpath(path.lstrip("/") if path.startswith("/") else posixpath.join(posixpath.dirname(owner), path))
        if relative.startswith("../") or relative == "..":
            raise Error("render asset escapes Pages")
        if relative not in assets:
            if not _path(pages, relative).is_file():
                raise Error(f"missing render dependency: {relative}")
            assets.add(relative)
            pending.append(relative)
    for role, raw in payloads.items():
        relative = PREVIEW_PATHS[role].format(date=date, correction_id=cid)
        if not relative.endswith(".html"):
            continue
        text = raw.decode("utf-8-sig")
        for node in legacy._StrictHtmlTreeParser(text).finish():
            if node.tag in {"img", "script"} and node.attrs.get("src"):
                discover(node.attrs["src"], relative)
            elif (node.tag == "link" and node.attrs.get("href")
                  and set(str(node.attrs.get("rel", "")).lower().split()) & {"stylesheet", "icon", "apple-touch-icon", "mask-icon", "preload"}):
                discover(node.attrs["href"], relative)
    while pending:
        relative = pending.pop()
        if relative.endswith(".css"):
            css = _path(pages, relative).read_text(encoding="utf-8-sig")
            for value in re.findall(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)", css):
                discover(value.strip(), relative)
    return assets


def _validate_semantics(payloads: dict[str, bytes], context: dict[str, Any], audio: dict[str, Any]) -> None:
    if set(payloads) != set(PREVIEW_PATHS):
        raise Error("missing correction preview surfaces")
    curation = json.loads(payloads["curation_manifest"])
    retained = {s["story_id"] for s in context["retained"]}
    if {s["story_id"] for s in curation} != retained:
        raise Error("corrected curation is contradictory")
    removed = {t["story"]["story_id"] for t in context["targets"]}
    sources = json.loads(payloads["sources_manifest"])
    if any(set(s["used_in_story_ids"]) - retained for s in sources):
        raise Error("corrected source-to-story links are contradictory")
    dedupe = json.loads(payloads["dedupe_report"])
    if _story_references(dedupe) & removed:
        raise Error("dedupe retains a removed story in another collection")
    edition = json.loads(payloads["edition_manifest"])
    used = {s["source_record_id"] for s in sources if s["used_in_story_ids"]}
    if edition["story_count"] != len(retained) or edition["source_count"] != len(used):
        raise Error("corrected edition counts are contradictory")
    if edition["public_exposed"] is not False or edition["publication_authorized"] is not False:
        raise Error("preview improperly changes publication authority")
    if any(edition[key] != value for key, value in _casualty_aggregates(edition, context["retained"]).items()):
        raise Error("corrected casualty aggregates are contradictory")
    if not retained and (edition["source_adequacy_status"] != "no_qualified_stories" or edition["publishers"]):
        raise Error("zero-story adequacy is contradictory")
    for role in ("rss", "podcast", "audio_podcast"):
        root = ElementTree.fromstring(payloads[role])
        matches = [i for i in root.findall('.//item') if i.findtext('guid') == context["correction_id"]]
        if len(matches) != 1 or "/corrections/" not in matches[0].findtext("link", ""):
            raise Error("correction feed identity is not distinct")
    if _hash(audio["script_text"].encode("utf-8")) != audio["script_sha256"]:
        raise Error("audio script hash drifted")
    if audio["render_authorized"] is not False or audio["publication_authorized"] is not False:
        raise Error("audio request improperly grants authority")
    original_metadata = json.loads(payloads["original_audio_metadata"])
    if (original_metadata.get("current_briefing") is not False
            or original_metadata.get("historical_recording") is not True
            or original_metadata.get("superseded_by_correction_id") != context["correction_id"]):
        raise Error("original audio lacks explicit supersession")


def _context(source: Path, pages: Path, request: dict[str, Any], review_packet: Path, *, formal: bool) -> dict[str, Any]:
    if request.get("schema_version") != REQUEST_SCHEMA:
        raise Error("unsupported edition correction request")
    legacy._exact_fields(request, {"schema_version", "expected_source_head", "expected_pages_head",
        "review_packet_sha256", "edition_date", "correction_date", "correction_at", "targets",
        "tts_provider", "tts_model", "tts_voice"}, "edition correction request")
    for key in ("expected_source_head", "expected_pages_head"):
        if not re.fullmatch(r"[0-9a-f]{40}", str(request.get(key, ""))):
            raise Error(f"{key} is not an exact Git commit")
    legacy._require_sha(request.get("review_packet_sha256"), "review packet hash")
    _heads(source, pages, request)
    if legacy.sha256_file(review_packet) != request["review_packet_sha256"]:
        raise Error("bound review packet hash drifted")
    bound = _load(review_packet, "bound review packet")
    if bound["pages_head"] != request["expected_pages_head"]:
        raise Error("bound review packet Pages head differs")
    if legacy._git(source, "merge-base", "--is-ancestor", bound["source_head"], request["expected_source_head"]):
        raise Error("source does not descend from the reviewed checkpoint")
    # Every supplied immutable snapshot must still match; runtime originals may
    # advance, but are never substituted for the saved review evidence.
    for rel, row in bound["evidence"].items():
        if "snapshot_path" in row and legacy.sha256_file(Path(row["snapshot_path"])) != row["sha256"]:
            raise Error("bound review evidence hash drifted")
        if "snapshot_path" in row and (rel.startswith("gaza/") or rel == "index.html"):
            if legacy.sha256_file(_path(pages, rel)) != row["sha256"]:
                raise Error("original public bytes differ from bound review evidence")
    date = str(request.get("edition_date", ""))
    correction_date = str(request.get("correction_date", ""))
    for value in (date, correction_date):
        legacy._natural_date(value, "correction date")
    if correction_date < date:
        raise Error("correction date predates edition")
    try:
        correction_at = datetime.fromisoformat(request["correction_at"].replace("Z", "+00:00"))
        if correction_at.tzinfo is None or correction_at.date().isoformat() != correction_date:
            raise ValueError("correction timestamp/date mismatch")
    except (KeyError, ValueError) as exc:
        raise Error("an exact, timezone-aware correction timestamp is required") from exc
    curation = _load(_path(pages, PREVIEW_PATHS["curation_manifest"].format(date=date)), "curation")
    sources = _load(_path(pages, PREVIEW_PATHS["sources_manifest"].format(date=date)), "sources")
    dedupe = _load(_path(pages, PREVIEW_PATHS["dedupe_report"].format(date=date)), "dedupe")
    edition = _load(_path(pages, PREVIEW_PATHS["edition_manifest"].format(date=date)), "edition")
    if not isinstance(curation, list) or not isinstance(sources, list) or not isinstance(dedupe, dict):
        raise Error("original manifests have invalid shapes")
    ids = [s["story_id"] for s in curation]
    if len(set(ids)) != len(ids) or edition.get("edition_date") != date or edition.get("story_count") != len(ids):
        raise Error("original edition/curation manifest contradiction")
    if edition.get("source_count") != len(sources) or dedupe.get("edition_date") != date:
        raise Error("original source/dedupe count contradiction")
    source_ids = [s["source_record_id"] for s in sources]
    if len(set(source_ids)) != len(source_ids):
        raise Error("source identities are duplicated")
    for s in sources:
        if not isinstance(s.get("used_in_story_ids"), list) or set(s["used_in_story_ids"]) - set(ids):
            raise Error("original source links contradict curation")
    for story in curation:
        actual_sources = {s["source_record_id"] for s in sources if story["story_id"] in s["used_in_story_ids"]}
        if actual_sources != set(story["source_record_ids"]):
            raise Error("original story-to-source relationship contradicts sources")
    active_dedupe = {s["story_id"] for s in _story_rows(dedupe) if s.get("public_rendered") is True}
    if active_dedupe != set(ids):
        raise Error("original dedupe collections contradict curation")
    entries = request.get("targets")
    if not isinstance(entries, list) or not entries:
        raise Error("correction targets are required")
    targets = []
    for entry in entries:
        if (not isinstance(entry, dict) or not {"story_id", "reason"} <= set(entry)
                or set(entry) - {"story_id", "reason", "review_path", "decision_audit_path"}):
            raise Error("correction target fields are invalid")
        sid = entry.get("story_id")
        if sid not in ids or entry.get("reason") not in REASONS:
            raise Error("target story or correction reason is invalid")
        lineage_path = _path(source, f"data/agent-history/gaza/lineage/published-stories/{sid}.json")
        lineage = _load(lineage_path, "published lineage")
        validate_gaza_published_story_lineage(lineage)
        if lineage["edition_date"] != date or lineage["pages_provenance"]["pinned_commit"] != request["expected_pages_head"]:
            raise Error("target lineage head/edition drifted")
        for a in lineage["pages_provenance"]["artifacts"]:
            b = legacy._git_blob(pages, request["expected_pages_head"], a["path"])
            if _hash(b) != a["sha256"] or len(b) != a["byte_length"] or legacy._git(pages, "rev-parse", f'{request["expected_pages_head"]}:{a["path"]}') != a["git_blob_oid"]:
                raise Error("lineage public Git blob evidence drifted")
        story = curation[ids.index(sid)]
        if story["title"] != lineage["story_title"] or story["summary"] != lineage["prior_claim"]["text"]:
            raise Error("lineage story claim contradicts curation")
        targets.append({**entry, "story": story, "lineage": lineage,
                        "lineage_path": str(lineage_path.relative_to(source).as_posix()),
                        "lineage_sha256": legacy.sha256_file(lineage_path),
                        "saved_input_sha256": (bound["evidence"].get("saved_inputs") or {}).get("sha256")})
        if entry["reason"] == "insufficient_saved_evidence" and not targets[-1]["saved_input_sha256"]:
            raise Error("saved-evidence insufficiency requires the original saved-input snapshot")
    if len({t["story_id"] for t in targets}) != len(targets):
        raise Error("duplicate correction target")
    targets.sort(key=lambda t: t["story_id"])
    tts_keys = ("tts_provider", "tts_model", "tts_voice")
    if any(not isinstance(request.get(k), str) or not request[k].strip() for k in tts_keys):
        raise Error("deterministic audio configuration is required")
    tts = {k: request[k].strip() for k in tts_keys}
    absent = [f'{t["story_id"]}: independently reviewed decision and exact audit' for t in targets if not t.get("review_path") or not t.get("decision_audit_path")]
    if formal and absent:
        raise Error("formal proposal blocked: " + "; ".join(absent))
    missing = [] if formal else [f'{t["story_id"]}: independently reviewed decision and exact committed audit' for t in targets]
    if formal:
        for target in targets:
            _validate_review(source, target, request)
    removed = {t["story_id"] for t in targets}
    retained = [s for s in curation if s["story_id"] not in removed]
    identity = {"edition_date": date, "correction_date": correction_date,
                "targets": [{"story_id": t["story_id"], "reason": t["reason"], "target_fingerprint": t["lineage"]["withdrawal_target_identity"]["fingerprint"]} for t in targets]}
    cid = "gaza-edition-correction-v1-" + _hash(legacy._canonical_bytes(identity))[:20]
    context = {"edition_date": date, "correction_date": correction_date, "correction_id": cid,
               "correction_at": request["correction_at"],
               "targets": targets, "curation": curation, "sources": sources, "retained": retained,
               "tts": tts, "missing_inputs": missing, "substantively_reviewed": formal}
    context["record"] = {
        "schema_version": "gaza_edition_correction_record_v1", "correction_id": cid,
        "owning_edition_date": date, "correction_date": correction_date,
        "review_state": "substantively_reviewed" if formal else "pending_substantive_review",
        "retained_story_ids": sorted(s["story_id"] for s in retained),
        "saved_source_count": len(sources), "retained_story_count": len(retained),
        "corrections": [{"story_id": t["story_id"], "reason": t["reason"],
                         "source_attribution": t["lineage"]["source_attribution"],
                         "source_record_ids": t["story"]["source_record_ids"],
                         "source_urls": t["story"]["source_urls"],
                         "source_published_at": [s["published_at"] for s in t["lineage"]["source_records"]],
                         "event_date": t["story"]["event_date"],
                         "prior_claim": t["lineage"]["prior_claim"],
                         "prior_claim_fingerprint": t["lineage"]["prior_claim_identity"]["fingerprint"],
                         "lineage_record_fingerprint": t["lineage"]["record_fingerprint"],
                         "target_fingerprint": t["lineage"]["withdrawal_target_identity"]["fingerprint"],
                         "saved_input_sha256": t["saved_input_sha256"],
                         "review_sha256": t.get("review_sha256"), "audit_sha256": t.get("audit_sha256"),
                         "underlying_article_verdict": "undetermined" if t["reason"] == "insufficient_saved_evidence" else None,
                         "substantive_decision": DECISIONS[t["reason"]] if formal else None} for t in targets],
        **{k: False for k in AUTHORITY_FIELDS},
    }
    legacy._validate_edition_audio_ownership(pages, {"owning_edition_date": date}, json_loader=_load)
    flash = legacy._load_json_document(_path(pages, "gaza/flash-briefing.json"), "flash")
    context["flash_ownership"] = legacy._validate_flash_briefing_audio_ownership(pages, flash, {"owning_edition_date": date}, json_loader=_load)
    flash_date = context["flash_ownership"].edition_date
    flash_metadata = _load(_path(pages, f"gaza/audio/{flash_date}.json"), "flash audio metadata")
    if " ".join(str(flash.root.items[0].value.get("mainText", "")).split()) != " ".join(str(flash_metadata.get("script_text", "")).split()):
        raise Error("flash text contradicts its owning audio script")
    return context


def _validate_review(source: Path, target: dict[str, Any], request: dict[str, Any]) -> None:
    review_path = _path(source, target["review_path"])
    audit_path = _path(source, target["decision_audit_path"])
    review = _load(review_path, "substantive review")
    legacy._exact_fields(review, {"schema_version", "story_id", "owning_edition_date", "reason",
        "decision", "resulting_review_state", "lineage_sha256", "review_packet_sha256", "reviewer",
        "reviewed_at", "decision_reason", "evidence_references", "assessment", *AUTHORITY_FIELDS}, "substantive review")
    if review_path.read_bytes() != audit_path.read_bytes():
        raise Error("decision audit does not exactly bind substantive review")
    for path in (review_path, audit_path):
        rel = path.relative_to(source).as_posix()
        if legacy._git_blob(source, "HEAD", rel) != path.read_bytes():
            raise Error("substantive review/audit must be committed at the exact source head")
    expected = {"schema_version": REVIEW_SCHEMA, "story_id": target["story_id"],
                "owning_edition_date": request["edition_date"], "reason": target["reason"],
                "decision": DECISIONS[target["reason"]], "resulting_review_state": "substantively_reviewed",
                "lineage_sha256": target["lineage_sha256"], "review_packet_sha256": request["review_packet_sha256"]}
    if any(review.get(k) != v for k, v in expected.items()):
        raise Error("substantive review identity, reason, or evidence binding drifted")
    if any(review.get(k) is not False for k in AUTHORITY_FIELDS):
        raise Error("review improperly grants authority")
    if not str(review.get("reviewer", "")).strip() or not str(review.get("decision_reason", "")).strip():
        raise Error("substantive reviewer and reason are required")
    try:
        if datetime.fromisoformat(review["reviewed_at"].replace("Z", "+00:00")).tzinfo is None:
            raise ValueError("timezone missing")
    except (KeyError, ValueError) as exc:
        raise Error("substantive review timestamp is invalid") from exc
    refs = review.get("evidence_references")
    urls = {s["url"] for s in target["lineage"]["source_records"]}
    evidence_type = "source_scope" if target["reason"] == "scope_error" else "saved_input"
    if not isinstance(refs, list):
        raise Error("substantive evidence references must be a list")
    for ref in refs:
        legacy._exact_fields(ref, {"role", "url", "supporting_passage", "evidence_type"}, "review evidence reference")
        if (ref["role"] not in {"principal", "corroborating"} or ref["evidence_type"] != evidence_type
                or not isinstance(ref["url"], str) or not ref["url"].startswith("https://")
                or not isinstance(ref["supporting_passage"], str) or not ref["supporting_passage"].strip()):
            raise Error("substantive evidence reference has invalid role, type or passage")
    if not isinstance(refs, list) or not any(r.get("role") == "principal" and r.get("evidence_type") == evidence_type
        and r.get("url") in urls and str(r.get("supporting_passage", "")).strip() for r in refs if isinstance(r, dict)):
        raise Error("substantive review lacks principal source evidence")
    assessment = review.get("assessment", {})
    if target["reason"] == "scope_error":
        legacy._exact_fields(assessment, {"materially_concerns_gaza"}, "scope assessment")
        if assessment.get("materially_concerns_gaza") is not False:
            raise Error("scope-error review does not prove a scope error")
    else:
        legacy._exact_fields(assessment, {"materially_concerns_gaza", "saved_article_evidence_sufficient",
            "underlying_article_verdict", "saved_input_sha256"}, "saved evidence assessment")
        if (assessment.get("saved_article_evidence_sufficient") is not False
              or assessment.get("underlying_article_verdict") != "undetermined"
              or assessment.get("saved_input_sha256") != target["saved_input_sha256"]
              or assessment.get("materially_concerns_gaza") is not None):
            raise Error("saved-evidence insufficiency cannot assert article falsity or non-Gaza scope")
    target["review_sha256"] = legacy.sha256_file(review_path)
    target["audit_sha256"] = legacy.sha256_file(audit_path)


def prepare_edition_correction(*, source_root: Path, pages_root: Path, request_path: Path,
                               review_packet_path: Path, output_root: Path, formal: bool = False) -> dict[str, Any]:
    source, pages, output = source_root.resolve(), pages_root.resolve(), output_root.resolve()
    _outside(output, source, pages)
    request = _load(request_path, "edition correction request")
    date = str(request.get("edition_date", ""))
    legacy._natural_date(date, "owning edition")
    for role, template in {**PREVIEW_PATHS, **DEPENDENCIES}.items():
        if role not in legacy.NEW_ROLES:
            rel = template.format(date=date)
            if not _path(pages, rel).is_file():
                raise Error(f"missing affected surface or immutable dependency: {rel}")
    context = _context(source, pages, request, review_packet_path, formal=formal)
    payloads, audio = _render(pages, context)
    date, cid = context["edition_date"], context["correction_id"]
    assets = _render_assets(payloads, pages, date, cid)
    original_paths = {template.format(date=date) for role, template in PREVIEW_PATHS.items()
                      if role not in legacy.NEW_ROLES} | {p.format(date=date) for p in DEPENDENCIES.values()}
    # Later singleton-flash ownership is an immutable dependency, never re-dated.
    if context["flash_ownership"].edition_date != date:
        later = context["flash_ownership"].edition_date
        original_paths.update({f"gaza/audio/{later}.json", f"gaza/audio/{later}.mp3", f"gaza/audio/{later}-transcript.html"})
    original_paths.update(assets)
    known = original_paths | {a["path"] for t in context["targets"] for a in t["lineage"]["pages_provenance"]["artifacts"]}
    grep = legacy._git(pages, "grep", "-l", "-I", "-F", *[part for t in context["targets"] for part in ("-e", t["story"]["title"])], "HEAD", "--")
    discovered = {line.removeprefix("HEAD:") for line in grep.splitlines()}
    if discovered & assets:
        raise Error("claim-bearing render dependency requires its own corrected representation")
    if discovered - known:
        raise Error("uninventoried claim-bearing surfaces: " + ", ".join(sorted(discovered - known)))
    originals = []
    files = {"request.json": _json(request), "audio_request.json": _json(audio)}
    for rel in sorted(original_paths):
        path = _path(pages, rel)
        if not path.is_file():
            raise Error(f"missing affected surface or immutable dependency: {rel}")
        raw = path.read_bytes()
        git_blob = legacy._git_blob(pages, request["expected_pages_head"], rel)
        originals.append({"public_path": rel, "sha256": _hash(raw), "git_blob_sha256": _hash(git_blob),
                          "git_blob_oid": legacy._git(pages, "rev-parse", f'{request["expected_pages_head"]}:{rel}'),
                          "snapshot_path": "evidence/public/" + rel})
        files["evidence/public/" + rel] = raw
    for target in context["targets"]:
        files["evidence/lineage/" + target["story_id"] + ".json"] = _path(source, target["lineage_path"]).read_bytes()
        if formal:
            files["evidence/reviews/" + target["story_id"] + ".json"] = _path(source, target["review_path"]).read_bytes()
            files["evidence/audits/" + target["story_id"] + ".json"] = _path(source, target["decision_audit_path"]).read_bytes()
    for rel in sorted(assets):
        files["preview/" + rel] = _path(pages, rel).read_bytes()
    files["evidence/bound-review-packet.json"] = review_packet_path.read_bytes()
    bound = _load(review_packet_path, "bound review packet")
    if "saved_inputs" in bound["evidence"]:
        files["evidence/saved-inputs.json"] = Path(bound["evidence"]["saved_inputs"]["snapshot_path"]).read_bytes()
    decisions = {"schema_version": "gaza_edition_correction_decision_request_v1", "review_packet_sha256": request["review_packet_sha256"],
                 "targets": [{"story_id": t["story_id"], "reason": t["reason"], "required_decision": DECISIONS[t["reason"]],
                              "decision": DECISIONS[t["reason"]] if formal else None,
                              "lineage_sha256": t["lineage_sha256"], "review_path": t.get("review_path"),
                              "decision_audit_path": t.get("decision_audit_path"),
                              "review_sha256": t.get("review_sha256"), "audit_sha256": t.get("audit_sha256")} for t in context["targets"]],
                 **{k: False for k in AUTHORITY_FIELDS}}
    files["decision_request.json"] = _json(decisions)
    representations = []
    for role, template in sorted(PREVIEW_PATHS.items()):
        rel = template.format(date=date, correction_id=cid)
        files["preview/" + rel] = payloads[role]
        representations.append({"role": role, "public_path": rel, "input_path": "preview/" + rel,
                                "sha256": _hash(payloads[role]), "prior_sha256": None if role in legacy.NEW_ROLES else legacy.sha256_file(_path(pages, rel))})
    packet = {"schema_version": PROPOSAL_SCHEMA if formal else PACKET_SCHEMA,
              "state": "formal_proposal" if formal else "awaiting_substantive_review",
              "source_commit": request["expected_source_head"], "pages_head": request["expected_pages_head"],
              "review_packet_sha256": request["review_packet_sha256"], "correction_id": cid,
              "correction": context["record"], "missing_inputs": context["missing_inputs"],
              "representations": representations, "original_artifacts": originals,
              "immutable_dependencies": [p.format(date=date) for p in DEPENDENCIES.values()],
              "render_asset_dependencies": sorted(assets),
              "discovered_claim_surfaces": sorted(discovered),
              "prospective_staged_surface_count": len(PREVIEW_PATHS) + 1,
              "audio_request_sha256": _hash(files["audio_request.json"]),
              "preview_set_sha256": _hash(legacy._canonical_bytes(representations + [{"role": "audio_request", "sha256": _hash(files["audio_request.json"])}])),
              "artifact_hashes": {rel: _hash(raw) for rel, raw in sorted(files.items())},
              **{k: False for k in AUTHORITY_FIELDS}, "packet_sha256": ""}
    packet["packet_sha256"] = legacy.fingerprint_payload(packet, "packet_sha256")
    main_name = "proposal.json" if formal else "packet.json"
    files[main_name] = _json(packet)
    _heads(source, pages, request)
    target = output / cid
    if target.exists():
        actual = {p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()}
        if actual != set(files) or any(_path(target, rel).read_bytes() != raw for rel, raw in files.items()):
            raise Error("existing packet conflicts with exact deterministic output")
        status = "idempotent_noop"
    else:
        output.mkdir(parents=True, exist_ok=True)
        temp = Path(tempfile.mkdtemp(prefix=".edition-correction-", dir=output))
        try:
            for rel, raw in files.items():
                path = _path(temp, rel)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            _heads(source, pages, request)
            os.replace(temp, target)
        except Exception:
            shutil.rmtree(temp, ignore_errors=True)
            raise
        status = "formal_proposal_created" if formal else "preparation_packet_created"
    return {"status": status, "packet_path": str(target / main_name),
            "packet_sha256": packet["packet_sha256"], "packet_file_sha256": _hash(files[main_name]),
            "missing_inputs": packet["missing_inputs"], "preview_surface_count": len(PREVIEW_PATHS),
            "proposal_created": formal, "pages_mutation": False, **{k: False for k in AUTHORITY_FIELDS}}


def verify_edition_correction_packet(*, packet_path: Path, source_root: Path, pages_root: Path) -> dict[str, Any]:
    root = packet_path.resolve().parent
    packet = _load(packet_path, "edition packet")
    if packet.get("schema_version") not in {PACKET_SCHEMA, PROPOSAL_SCHEMA}:
        raise Error("unsupported edition packet schema")
    if legacy.fingerprint_payload(packet, "packet_sha256") != packet.get("packet_sha256"):
        raise Error("edition packet fingerprint drifted")
    if any(packet.get(k) is not False for k in AUTHORITY_FIELDS):
        raise Error("edition packet improperly grants authority")
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    if actual != set(packet["artifact_hashes"]) | {packet_path.name}:
        raise Error("packet files are missing or unexpected")
    for rel, expected in packet["artifact_hashes"].items():
        if legacy.sha256_file(_path(root, rel)) != expected:
            raise Error(f"packet artifact hash drifted: {rel}")
    request = _load(_path(root, "request.json"), "request")
    _heads(source_root, pages_root, request)
    for row in packet["original_artifacts"]:
        current = _path(pages_root, row["public_path"])
        blob = legacy._git_blob(pages_root, packet["pages_head"], row["public_path"])
        snapshot_rel = "evidence/public/" + row["public_path"]
        if (row["snapshot_path"] != snapshot_rel or legacy.sha256_file(_path(root, snapshot_rel)) != row["sha256"]
                or legacy.sha256_file(current) != row["sha256"] or _hash(blob) != row["git_blob_sha256"]
                or legacy._git(pages_root, "rev-parse", f'{packet["pages_head"]}:{row["public_path"]}') != row["git_blob_oid"]):
            raise Error("original public evidence drifted")
    context = _context(source_root, pages_root, request, _path(root, "evidence/bound-review-packet.json"),
                       formal=packet["schema_version"] == PROPOSAL_SCHEMA)
    payloads, audio = _render(pages_root, context)
    if (len(packet["representations"]) != len(PREVIEW_PATHS)
            or {r["role"] for r in packet["representations"]} != set(PREVIEW_PATHS)):
        raise Error("missing preview surface binding")
    if (packet["correction"] != context["record"] or packet["correction_id"] != context["correction_id"]
            or packet["source_commit"] != request["expected_source_head"]
            or packet["pages_head"] != request["expected_pages_head"]
            or packet["review_packet_sha256"] != request["review_packet_sha256"]
            or packet["missing_inputs"] != context["missing_inputs"]):
        raise Error("packet decision/identity metadata is contradictory")
    if (packet["immutable_dependencies"] != [p.format(date=context["edition_date"]) for p in DEPENDENCIES.values()]
            or packet["prospective_staged_surface_count"] != len(PREVIEW_PATHS) + 1):
        raise Error("immutable dependency inventory is contradictory")
    expected_originals = {p.format(date=context["edition_date"]) for role, p in PREVIEW_PATHS.items() if role not in legacy.NEW_ROLES}
    expected_originals.update(p.format(date=context["edition_date"]) for p in DEPENDENCIES.values())
    assets = _render_assets(payloads, pages_root, context["edition_date"], context["correction_id"])
    expected_originals.update(assets)
    if packet["render_asset_dependencies"] != sorted(assets):
        raise Error("render asset inventory is contradictory")
    for rel in assets:
        if _path(root, "preview/" + rel).read_bytes() != _path(pages_root, rel).read_bytes():
            raise Error("render asset bytes drifted")
    if context["flash_ownership"].edition_date != context["edition_date"]:
        later = context["flash_ownership"].edition_date
        expected_originals.update({f"gaza/audio/{later}.json", f"gaza/audio/{later}.mp3", f"gaza/audio/{later}-transcript.html"})
    if (len(packet["original_artifacts"]) != len(expected_originals)
            or {r["public_path"] for r in packet["original_artifacts"]} != expected_originals):
        raise Error("affected surface or immutable dependency binding is missing")
    for row in packet["representations"]:
        expected_path = PREVIEW_PATHS[row["role"]].format(date=context["edition_date"], correction_id=context["correction_id"])
        if row["public_path"] != expected_path or row["input_path"] != "preview/" + expected_path or _hash(payloads[row["role"]]) != row["sha256"]:
            raise Error("preview differs from validator-owned deterministic output")
    if _hash(_json(audio)) != packet["audio_request_sha256"]:
        raise Error("audio request differs from deterministic output")
    expected_set = _hash(legacy._canonical_bytes(packet["representations"] + [{"role": "audio_request", "sha256": packet["audio_request_sha256"]}]))
    if packet["preview_set_sha256"] != expected_set:
        raise Error("complete preview-set fingerprint drifted")
    return {"status": "verified", "packet_sha256": packet["packet_sha256"],
            "proposal_created": packet["schema_version"] == PROPOSAL_SCHEMA,
            "persistent_mutation": False, **{k: False for k in AUTHORITY_FIELDS}}
