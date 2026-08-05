from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Mapping


REVIEW_ROOT = Path("data/dispatches/care-line/review")
REVIEW_SNAPSHOT_ROOT = REVIEW_ROOT / "signal-reviews"
PROPOSED_EDITION_ROOT = REVIEW_ROOT / "proposed-editions"
RELEASE_READINESS_ROOT = REVIEW_ROOT / "release-readiness"

REVIEW_SCHEMA_VERSION = "bluefern.care_line.editorial_review.v1"
SNAPSHOT_SCHEMA_VERSION = "bluefern.care_line.review_snapshot.v2"
PROPOSAL_SCHEMA_VERSION = "bluefern.care_line.proposed_edition.v1"
RELEASE_READINESS_SCHEMA_VERSION = "bluefern.care_line.release_readiness.v1"

REVIEW_DECISIONS = {
    "APPROVE",
    "APPROVE_WITH_CORRECTION",
    "HOLD_FOR_VERIFICATION",
    "EXCLUDE",
    "DUPLICATE",
    "SUPERSEDED",
    "CONTEXT_ONLY",
}
APPROVED_DECISIONS = {"APPROVE", "APPROVE_WITH_CORRECTION"}
BLOCKED_DECISIONS = {"HOLD_FOR_VERIFICATION", "EXCLUDE", "DUPLICATE", "SUPERSEDED", "CONTEXT_ONLY"}
READINESS_VERDICTS = {
    "READY_FOR_PRIVATE_DRAFT",
    "NO_CURRENT_UPDATE_SUPPORTED",
    "NOT_READY_INSUFFICIENT_REVIEW",
    "NOT_READY_PROVENANCE_GAP",
}
FINAL_PRIVATE_STATES = {
    "READY_FOR_HUMAN_APPROVAL_AS_PRIVATE_DRAFT",
    "NO_CURRENT_UPDATE_READY_FOR_HUMAN_APPROVAL",
    "NOT_READY",
}


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def payload_sha256(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical_json(payload))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _text(row: Mapping[str, Any], key: str, default: str = "") -> str:
    value = row.get(key, default)
    if value in (None, [], {}):
        return default
    return str(value).strip()


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value in (None, "", [], {}):
        return []
    return [str(value).strip()]


def review_snapshot_path(edition_date: str) -> Path:
    return REVIEW_SNAPSHOT_ROOT / f"{edition_date}.json"


def proposal_path(edition_date: str) -> Path:
    return PROPOSED_EDITION_ROOT / f"{edition_date}.json"


def release_readiness_path(edition_date: str) -> Path:
    return RELEASE_READINESS_ROOT / f"{edition_date}.json"


def validate_review_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    if _text(payload, "schema_version") != REVIEW_SCHEMA_VERSION:
        raise ValueError("unexpected Care Line editorial review schema version")
    edition_date = _text(payload, "edition_date")
    if not edition_date:
        raise ValueError("edition_date is required")
    reviewed_at = _text(payload, "reviewed_at")
    if not reviewed_at:
        raise ValueError("reviewed_at is required")
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("items must be a list")
    reviewed_ids: set[str] = set()
    approved_count = 0
    for index, item in enumerate(items, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"review item {index} must be an object")
        candidate_id = _text(item, "candidate_id")
        if not candidate_id:
            raise ValueError(f"review item {index} missing candidate_id")
        if candidate_id in reviewed_ids:
            raise ValueError(f"duplicate candidate_id in review payload: {candidate_id}")
        reviewed_ids.add(candidate_id)
        decision = _text(item, "review_decision")
        if decision not in REVIEW_DECISIONS:
            raise ValueError(f"invalid review_decision for {candidate_id}: {decision!r}")
        if not _text(item, "reviewer_rationale"):
            raise ValueError(f"reviewer_rationale is required for {candidate_id}")
        if not _text(item, "source_url").startswith("https://"):
            raise ValueError(f"source_url must be direct https publisher url for {candidate_id}")
        if not _text(item, "source_date"):
            raise ValueError(f"source_date is required for {candidate_id}")
        if decision in APPROVED_DECISIONS:
            approved_count += 1
            for field in (
                "approved_public_claim",
                "bounded_public_summary",
                "approved_event_type",
                "approved_service_line",
                "approved_access_consequence",
                "approved_geography",
                "exact_supporting_passage",
            ):
                if not _text(item, field):
                    raise ValueError(f"{field} is required for approved item {candidate_id}")
        else:
            if not _text(item, "exclusion_reason") and decision in {"EXCLUDE", "DUPLICATE", "SUPERSEDED", "CONTEXT_ONLY"}:
                raise ValueError(f"exclusion_reason is required for {candidate_id}")
    return {
        "edition_date": edition_date,
        "reviewed_at": reviewed_at,
        "approved_count": approved_count,
        "item_count": len(items),
    }


def approved_items(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    validate_review_payload(payload)
    return [dict(item) for item in payload["items"] if _text(item, "review_decision") in APPROVED_DECISIONS]


def determine_readiness(payload: Mapping[str, Any]) -> dict[str, str]:
    meta = validate_review_payload(payload)
    items = list(payload["items"])
    approved = approved_items(payload)
    expected_count = int(payload.get("expected_review_item_count") or len(items))
    if len(items) < expected_count:
        verdict = "NOT_READY_INSUFFICIENT_REVIEW"
        edition_mode = "no_current_update"
        final_state = "NOT_READY"
    elif approved:
        verdict = "READY_FOR_PRIVATE_DRAFT"
        edition_mode = "current_update"
        final_state = "READY_FOR_HUMAN_APPROVAL_AS_PRIVATE_DRAFT"
    else:
        provenance_gap = any(_text(item, "review_decision") == "HOLD_FOR_VERIFICATION" for item in items)
        verdict = "NOT_READY_PROVENANCE_GAP" if provenance_gap else "NO_CURRENT_UPDATE_SUPPORTED"
        edition_mode = "no_current_update"
        final_state = "NOT_READY" if provenance_gap else "NO_CURRENT_UPDATE_READY_FOR_HUMAN_APPROVAL"
    return {
        "edition_date": meta["edition_date"],
        "edition_mode": edition_mode,
        "verdict": verdict,
        "final_state": final_state,
    }


def write_review_snapshot(root: Path, payload: Mapping[str, Any]) -> dict[str, str]:
    meta = validate_review_payload(payload)
    path = root / review_snapshot_path(meta["edition_date"])
    snapshot_payload = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "edition_date": meta["edition_date"],
        "reviewed_at": meta["reviewed_at"],
        "item_count": meta["item_count"],
        "approved_count": meta["approved_count"],
        "review_payload": payload,
    }
    write_json_atomic(path, snapshot_payload)
    return {"snapshot_path": review_snapshot_path(meta["edition_date"]).as_posix(), "snapshot_sha256": sha256_file(path)}


def load_validated_review_snapshot(root: Path, *, snapshot_path: str, snapshot_sha256: str) -> dict[str, Any]:
    path = root / snapshot_path
    if not path.exists():
        raise ValueError("unable to read review snapshot")
    actual = sha256_file(path)
    if actual != snapshot_sha256:
        raise ValueError("review snapshot SHA-256 is stale")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if _text(payload, "schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise ValueError("unexpected review snapshot schema version")
    validate_review_payload(payload["review_payload"])
    return payload


def _headline_for_items(items: list[dict[str, Any]], edition_mode: str) -> str:
    if edition_mode != "current_update" or not items:
        return "Care Line private draft: no current approved access update"
    lead = items[0]
    geography = _text(lead, "approved_geography") or _text(lead, "jurisdiction")
    claim = _text(lead, "approved_public_claim").rstrip(".")
    if geography and claim:
        return f"{geography} healthcare-access update: {claim}"
    if claim:
        return claim
    return "Care Line private draft"


def _summary_for_items(items: list[dict[str, Any]], edition_mode: str) -> str:
    if edition_mode != "current_update" or not items:
        return "No reviewed candidate cleared the current Care Line publication threshold for this edition date."
    return f"This private draft uses {len(items)} human-approved, source-traceable healthcare-access signal{'s' if len(items) != 1 else ''}."


def _source_mix(items: list[dict[str, Any]]) -> str:
    if not items:
        return "No approved public sources."
    counts = Counter(_text(item, "source_name") or "Unknown source" for item in items)
    return ", ".join(f"{name} ({count})" for name, count in sorted(counts.items()))


def build_proposed_edition(
    payload: Mapping[str, Any],
    *,
    snapshot_path: str,
    snapshot_sha256: str,
    proposal_created_at: str,
) -> dict[str, Any]:
    meta = validate_review_payload(payload)
    readiness = determine_readiness(payload)
    approved = approved_items(payload)
    source_urls = sorted({_text(item, "source_url") for item in approved if _text(item, "source_url")})
    source_names = sorted({_text(item, "source_name") for item in approved if _text(item, "source_name")})
    coverage = sorted({_text(item, "approved_geography") or _text(item, "jurisdiction") for item in approved if _text(item, "approved_geography") or _text(item, "jurisdiction")})
    proposed = {
        "schema_version": PROPOSAL_SCHEMA_VERSION,
        "edition_date": meta["edition_date"],
        "edition_mode": readiness["edition_mode"],
        "approved_signal_ids": [_text(item, "candidate_id") for item in approved],
        "excluded_count": sum(1 for item in payload["items"] if _text(item, "review_decision") in {"EXCLUDE", "DUPLICATE", "SUPERSEDED", "CONTEXT_ONLY"}),
        "held_count": sum(1 for item in payload["items"] if _text(item, "review_decision") == "HOLD_FOR_VERIFICATION"),
        "review_snapshot_path": snapshot_path,
        "review_snapshot_sha256": snapshot_sha256,
        "proposal_created_at": proposal_created_at,
        "source_count": len(source_urls),
        "signal_count": len(approved),
        "geographic_coverage": coverage,
        "headline": _headline_for_items(approved, readiness["edition_mode"]),
        "edition_summary": _summary_for_items(approved, readiness["edition_mode"]),
        "source_mix": _source_mix(approved),
        "claim_ledger_reference": "claim_ledger.html",
        "source_table_reference": "source_table.html",
        "generation_status": "private_draft_ready" if readiness["verdict"] == "READY_FOR_PRIVATE_DRAFT" else "no_current_update_private_draft_ready" if readiness["verdict"] == "NO_CURRENT_UPDATE_SUPPORTED" else "blocked",
        "publication_authorization_status": False,
        "publication_datetime": None,
        "live_release_status": "not_published",
        "pages_sync_status": "not_synced",
        "review_decision_counts": {decision: sum(1 for item in payload["items"] if _text(item, "review_decision") == decision) for decision in sorted(REVIEW_DECISIONS)},
        "source_names": source_names,
        "source_urls": source_urls,
        "final_state": readiness["final_state"],
        "readiness_verdict": readiness["verdict"],
    }
    return proposed


def _escape_html(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _markdown_lines_for_items(items: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in items:
        lines.extend(
            [
                f"### {_text(item, 'facility') or _text(item, 'facility_name') or _text(item, 'approved_geography')}",
                "",
                f"- Claim: {_text(item, 'bounded_public_summary')}",
                f"- Attribution: {_text(item, 'source_name')} reports {_text(item, 'approved_public_claim')}",
                f"- Geography: {_text(item, 'approved_geography')}",
                f"- Event type: {_text(item, 'approved_event_type')}",
                f"- Service line: {_text(item, 'approved_service_line')}",
                f"- Access consequence: {_text(item, 'approved_access_consequence')}",
                f"- Source: {_text(item, 'source_url')}",
                "",
            ]
        )
    return lines


def render_private_markdown(proposal: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    approved = approved_items(payload)
    lines = [
        "# Care Line private draft",
        "",
        f"Edition date: {proposal['edition_date']}",
        "",
        "Edition status: Private draft only. Not authorized for publication.",
        "",
        f"# {proposal['headline']}",
        "",
        "## Edition summary",
        "",
        proposal["edition_summary"],
        "",
        "## Today’s Read",
        "",
        proposal["edition_summary"],
        "",
        "## At A Glance",
        "",
    ]
    if approved:
        lines.extend([f"- {_text(item, 'bounded_public_summary')}" for item in approved])
    else:
        lines.append("- No current approved public signal cleared the Care Line threshold.")
    lines.extend(["", "## Core Healthcare Access Signals", ""])
    if approved:
        lines.extend(_markdown_lines_for_items(approved))
    else:
        lines.extend(["No approved current signal.", ""])
    lines.extend(
        [
            "## Source Mix",
            "",
            proposal["source_mix"],
            "",
            "## Source Note",
            "",
            "Every public-facing claim in this private draft is bound to an exact supporting passage and a direct publisher URL.",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _html_shell(title: str, body: str) -> str:
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n"
        "  <meta charset=\"utf-8\">\n"
        "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"  <title>{_escape_html(title)}</title>\n"
        "</head>\n<body>\n"
        f"{body}\n"
        "</body>\n</html>\n"
    )


def _private_index_html(proposal: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    approved = approved_items(payload)
    bullets = "".join(f"<li>{_escape_html(_text(item, 'bounded_public_summary'))}</li>" for item in approved) or "<li>No approved current signal.</li>"
    cards = []
    for item in approved:
        cards.append(
            "<article>"
            f"<h3>{_escape_html(_text(item, 'facility') or _text(item, 'approved_geography'))}</h3>"
            f"<p><strong>Claim:</strong> {_escape_html(_text(item, 'bounded_public_summary'))}</p>"
            f"<p><strong>Source:</strong> <a href=\"{_escape_html(_text(item, 'source_url'))}\">{_escape_html(_text(item, 'source_name'))}</a></p>"
            f"<p><strong>Evidence:</strong> {_escape_html(_text(item, 'exact_supporting_passage'))}</p>"
            "</article>"
        )
    cards_html = "".join(cards) or "<p>No approved current signal.</p>"
    return _html_shell(
        str(proposal["headline"]),
        (
            "<main>"
            "<p>Private draft only. Not authorized for publication.</p>"
            f"<h1>{_escape_html(str(proposal['headline']))}</h1>"
            f"<p>{_escape_html(str(proposal['edition_summary']))}</p>"
            "<p><a href=\"source_table.html\">Source table</a> | <a href=\"claim_ledger.html\">Claim ledger</a></p>"
            "<h2>At A Glance</h2>"
            f"<ul>{bullets}</ul>"
            "<h2>Core Healthcare Access Signals</h2>"
            f"{cards_html}"
            "<h2>Source Mix</h2>"
            f"<p>{_escape_html(str(proposal['source_mix']))}</p>"
            "<h2>Source Note</h2>"
            "<p>Every public-facing claim in this private draft is bound to an exact supporting passage and a direct publisher URL.</p>"
            "</main>"
        ),
    )


def _table(headers: list[str], rows: list[list[str]]) -> str:
    header_html = "".join(f"<th scope=\"col\">{_escape_html(header)}</th>" for header in headers)
    body_rows = []
    for row in rows:
        body_rows.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>")
    body_html = "".join(body_rows) or f"<tr><td colspan=\"{len(headers)}\">No rows.</td></tr>"
    return f"<table><thead><tr>{header_html}</tr></thead><tbody>{body_html}</tbody></table>"


def _source_table_html(payload: Mapping[str, Any]) -> str:
    approved = approved_items(payload)
    rows = []
    for item in approved:
        rows.append(
            [
                _escape_html(_text(item, "source_name")),
                _escape_html(_text(item, "source_title")),
                f"<a href=\"{_escape_html(_text(item, 'source_url'))}\">{_escape_html(_text(item, 'source_url'))}</a>",
                _escape_html(_text(item, "source_date")),
                _escape_html(_text(item, "source_type", "article")),
                _escape_html(_text(item, "authority_level", "secondary")),
                _escape_html(_text(item, "role_in_edition", "core_access_signal")),
                _escape_html(_text(item, "candidate_id")),
                _escape_html(_text(item, "lineage_note")),
                _escape_html(_text(item, "notes")),
            ]
        )
    return _html_shell(
        "Care Line private draft source table",
        "<main><h1>Source table</h1>"
        + _table(
            ["Publisher", "Title", "URL", "Source date", "Source type", "Authority level", "Role in edition", "Event IDs", "Lineage note", "Limitation / notes"],
            rows,
        )
        + "</main>",
    )


def _claim_ledger_html(payload: Mapping[str, Any], edition_date: str) -> str:
    approved = approved_items(payload)
    rows = []
    for index, item in enumerate(approved, start=1):
        rows.append(
            [
                _escape_html(f"claim-{edition_date}-{index:02d}"),
                _escape_html(edition_date),
                _escape_html(_text(item, "candidate_id")),
                _escape_html(_text(item, "approved_public_claim")),
                f"<a href=\"{_escape_html(_text(item, 'source_url'))}\">{_escape_html(_text(item, 'source_url'))}</a>",
                _escape_html(_text(item, "source_name")),
                _escape_html(_text(item, "source_date")),
                _escape_html(_text(item, "exact_supporting_passage")),
                _escape_html(_text(item, "evidence_level")),
                _escape_html(_text(item, "review_decision")),
                _escape_html(_text(item, "approved_geography")),
                _escape_html(_text(item, "approved_event_type")),
                _escape_html(_text(item, "approved_service_line")),
                _escape_html(_text(item, "approved_access_consequence")),
                "private",
            ]
        )
    return _html_shell(
        "Care Line private draft claim ledger",
        "<main><h1>Claim ledger</h1>"
        + _table(
            [
                "Claim ID",
                "Edition date",
                "Candidate ID",
                "Public claim text",
                "Source URL",
                "Source publisher",
                "Source date",
                "Supporting passage",
                "Evidence level",
                "Review decision",
                "Geography",
                "Event type",
                "Service line",
                "Access consequence",
                "Status",
            ],
            rows,
        )
        + "</main>",
    )


def _write_csv(path: Path, headers: list[str], rows: list[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({header: row.get(header, "") for header in headers})
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def build_private_draft_artifacts(
    root: Path,
    *,
    payload: Mapping[str, Any],
    proposal: Mapping[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    validate_review_payload(payload)
    output_dir.mkdir(parents=True, exist_ok=True)
    edition_date = _text(proposal, "edition_date")
    markdown = render_private_markdown(proposal, payload)
    index_html = _private_index_html(proposal, payload)
    source_table_html = _source_table_html(payload)
    claim_ledger_html = _claim_ledger_html(payload, edition_date)

    (output_dir / "index.html").write_text(index_html, encoding="utf-8")
    (output_dir / "source_table.html").write_text(source_table_html, encoding="utf-8")
    (output_dir / "claim_ledger.html").write_text(claim_ledger_html, encoding="utf-8")
    (output_dir / "proposed-edition.md").write_text(markdown, encoding="utf-8")
    write_json_atomic(output_dir / "proposed-edition.json", proposal)

    approved = approved_items(payload)
    source_rows = [
        {
            "publisher": _text(item, "source_name"),
            "title": _text(item, "source_title"),
            "url": _text(item, "source_url"),
            "source_date": _text(item, "source_date"),
            "source_type": _text(item, "source_type", "article"),
            "authority_level": _text(item, "authority_level", "secondary"),
            "role_in_edition": _text(item, "role_in_edition", "core_access_signal"),
            "event_ids_supported": _text(item, "candidate_id"),
            "lineage_note": _text(item, "lineage_note"),
            "access_or_extraction_limitation": _text(item, "notes"),
        }
        for item in approved
    ]
    claim_rows = [
        {
            "claim_id": f"claim-{edition_date}-{index:02d}",
            "edition_date": edition_date,
            "candidate_id": _text(item, "candidate_id"),
            "public_claim_text": _text(item, "approved_public_claim"),
            "source_url": _text(item, "source_url"),
            "source_publisher": _text(item, "source_name"),
            "source_date": _text(item, "source_date"),
            "supporting_passage": _text(item, "exact_supporting_passage"),
            "evidence_level": _text(item, "evidence_level"),
            "review_decision": _text(item, "review_decision"),
            "geography": _text(item, "approved_geography"),
            "event_type": _text(item, "approved_event_type"),
            "service_line": _text(item, "approved_service_line"),
            "access_consequence": _text(item, "approved_access_consequence"),
            "public_private_status": "private",
        }
        for index, item in enumerate(approved, start=1)
    ]
    write_json_atomic(output_dir / "sources_manifest.json", {"schema_version": "bluefern.care_line.private_sources_manifest.v1", "sources": source_rows})
    write_json_atomic(output_dir / "curation_manifest.json", {"schema_version": "bluefern.care_line.private_curation_manifest.v1", "approved_candidate_ids": [_text(item, "candidate_id") for item in approved]})
    write_json_atomic(
        output_dir / "edition_manifest.json",
        {
            "schema_version": "bluefern.care_line.private_edition_manifest.v1",
            "edition_date": edition_date,
            "edition_mode": proposal["edition_mode"],
            "draft_only": True,
            "publication_datetime": None,
            "approved_signal_count": len(approved),
            "source_count": len(source_rows),
            "headline": proposal["headline"],
            "public_authorization": False,
        },
    )
    write_json_atomic(
        output_dir / "draft-manifest.json",
        {
            "schema_version": "bluefern.care_line.private_draft_manifest.v1",
            "edition_date": edition_date,
            "draft_only": True,
            "artifacts": {
                name: sha256_file(output_dir / name)
                for name in (
                    "index.html",
                    "source_table.html",
                    "claim_ledger.html",
                    "sources_manifest.json",
                    "curation_manifest.json",
                    "edition_manifest.json",
                    "proposed-edition.json",
                    "proposed-edition.md",
                )
            },
            "publication_authorization": False,
        },
    )
    (output_dir / "editorial-checklist.md").write_text(
        "\n".join(
            [
                "# Care Line editorial checklist",
                "",
                "- Traceability: pass only if every claim row has a direct https source URL and exact passage.",
                "- Headline accuracy: manual approval required.",
                "- Geography: based on approved reviewed geography only.",
                "- Date basis: based on source publication or clearly bounded announcement/effective date only.",
                "- Pressure classification: based on approved event type and access consequence only.",
                "- Affected groups: do not expand beyond the approved record.",
                "- Duplication: duplicate/superseded/context-only rows excluded from approved claims.",
                "- Attribution: every public-facing claim retains explicit source attribution.",
                "- Unsupported generalization: not allowed.",
                "- Completeness limitations: this is a bounded private draft, not a national census.",
                "- Readiness for human approval: required before any public artifact or release record.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return {
        "approved_count": len(approved),
        "source_count": len(source_rows),
        "claim_count": len(claim_rows),
    }


def build_release_readiness_record(
    *,
    proposal: Mapping[str, Any],
    proposal_path_value: str,
    proposal_sha256: str,
    snapshot_path_value: str,
    snapshot_sha256: str,
    draft_dir: Path,
    validation_results: Mapping[str, Any],
    blocking_issues: list[str],
) -> dict[str, Any]:
    hashes = {}
    for name in (
        "index.html",
        "source_table.html",
        "claim_ledger.html",
        "sources_manifest.json",
        "curation_manifest.json",
        "edition_manifest.json",
        "proposed-edition.json",
        "proposed-edition.md",
        "draft-manifest.json",
        "editorial-checklist.md",
    ):
        path = draft_dir / name
        if path.exists():
            hashes[name] = sha256_file(path)
    return {
        "schema_version": RELEASE_READINESS_SCHEMA_VERSION,
        "edition_date": _text(proposal, "edition_date"),
        "proposal_path": proposal_path_value,
        "proposal_sha256": proposal_sha256,
        "review_snapshot_path": snapshot_path_value,
        "review_snapshot_sha256": snapshot_sha256,
        "private_draft_artifact_hashes": hashes,
        "validation_results": dict(validation_results),
        "public_path_plan": {
            "output_site_target": f"output/site/care-line/editions/{_text(proposal, 'edition_date')}/",
            "pages_sync_status": "not_synced",
        },
        "publication_authorization": False,
        "pages_synchronization_status": "not_synced",
        "blocking_issues": list(blocking_issues),
        "required_human_approval": True,
    }
