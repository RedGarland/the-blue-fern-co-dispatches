from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


WATCH_DATA_ROOT = Path("data") / "dispatches" / "cascadia" / "detention_watch"
REVIEW_ROOT = Path("output") / "review" / "cascadia" / "detention_watch"
ALLOWED_SOURCE_FAMILIES = {
    "official_federal",
    "official_state",
    "court_record",
    "local_media",
    "advocacy_legal",
    "academic_data",
    "support_org",
}
ALLOWED_CHECK_FREQUENCY = {"weekly", "monthly", "manual"}
ALLOWED_CLAIM_CLASSES = {"documented", "reported", "alleged", "unknown"}
SOURCE_FAMILY_LABELS = {
    "official_federal": "Official Federal",
    "official_state": "Official State",
    "court_record": "Court Record",
    "local_media": "Local Media",
    "advocacy_legal": "Advocacy / Legal",
    "academic_data": "Academic Data",
    "support_org": "Support Organization",
}
STALE_REASON_LABELS = {
    "fetch_failed": "Fetch failed",
    "status_not_200": "HTTP status not 200",
    "content_hash_changed": "Content changed (hash)",
    "title_changed": "Title changed",
    "check_overdue": "Check overdue",
}


class _HTMLTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self.title: str = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if not text:
            return
        self._parts.append(text)
        if self._in_title and not self.title:
            self.title = text

    @property
    def text(self) -> str:
        return " ".join(self._parts)


@dataclass(frozen=True)
class FetchResult:
    status_code: int | None
    final_url: str
    retrieved_at: str
    title: str
    content_hash: str
    snippet: str
    detected_dates: list[str]
    changed: bool
    failed: bool
    notes: str


def _normalize_snippet(text: str, limit: int = 280) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def _extract_dates(text: str) -> list[str]:
    found = set(re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text))
    return sorted(found)[:5]


def _window_days(check_frequency: str) -> int:
    if check_frequency == "weekly":
        return 7
    if check_frequency == "monthly":
        return 31
    return 3650


def _diagnose_fetch_issue(notes: str, status_code: int | None) -> str:
    text = (notes or "").lower()
    if "certificate_verify_failed" in text or "ssl" in text:
        return "tls_or_certificate_error"
    if "timed out" in text or "timeout" in text:
        return "timeout"
    if "name or service not known" in text or "nodename nor servname provided" in text:
        return "dns_or_host_error"
    if status_code and status_code >= 400:
        return "http_error"
    if text:
        return "fetch_error"
    return ""


def load_registry(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("source registry must be a JSON array")
    return payload


def validate_registry(registry: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for item in registry:
        if not isinstance(item, dict):
            errors.append("registry item must be an object")
            continue
        source_id = str(item.get("source_id") or "").strip()
        if not source_id:
            errors.append("registry source_id is required")
            continue
        if source_id in seen:
            errors.append(f"duplicate source_id: {source_id}")
        seen.add(source_id)
        if str(item.get("source_family") or "").strip() not in ALLOWED_SOURCE_FAMILIES:
            errors.append(f"invalid source_family for {source_id}")
        if str(item.get("check_frequency") or "").strip() not in ALLOWED_CHECK_FREQUENCY:
            errors.append(f"invalid check_frequency for {source_id}")
        url = str(item.get("url") or "").strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            errors.append(f"invalid url for {source_id}")
        if not isinstance(item.get("enabled"), bool):
            errors.append(f"enabled must be boolean for {source_id}")
    return errors


def _fetch_metadata(url: str, fallback_title: str) -> FetchResult:
    now = datetime.now(timezone.utc).isoformat()
    req = Request(url, headers={"User-Agent": "BlueFernDetentionWatch/1.0"})
    try:
        with urlopen(req, timeout=20) as response:
            status = int(getattr(response, "status", 200))
            final_url = str(getattr(response, "url", url))
            body = response.read(65536)
            decoded = body.decode("utf-8", errors="ignore")
            parser = _HTMLTextParser()
            parser.feed(decoded)
            visible = parser.text
            title = parser.title or fallback_title
            snippet = _normalize_snippet(visible)
            content_hash = hashlib.sha256(decoded.encode("utf-8", errors="ignore")).hexdigest()
            detected_dates = _extract_dates(decoded)
            return FetchResult(
                status_code=status,
                final_url=final_url,
                retrieved_at=now,
                title=title,
                content_hash=content_hash,
                snippet=snippet,
                detected_dates=detected_dates,
                changed=False,
                failed=status != 200,
                notes="" if status == 200 else f"HTTP {status}",
            )
    except HTTPError as exc:
        return FetchResult(
            status_code=int(exc.code),
            final_url=url,
            retrieved_at=now,
            title=fallback_title,
            content_hash="",
            snippet="",
            detected_dates=[],
            changed=False,
            failed=True,
            notes=f"HTTPError: {exc.code}",
        )
    except URLError as exc:
        return FetchResult(
            status_code=None,
            final_url=url,
            retrieved_at=now,
            title=fallback_title,
            content_hash="",
            snippet="",
            detected_dates=[],
            changed=False,
            failed=True,
            notes=f"URLError: {exc.reason}",
        )
    except Exception as exc:  # pragma: no cover
        return FetchResult(
            status_code=None,
            final_url=url,
            retrieved_at=now,
            title=fallback_title,
            content_hash="",
            snippet="",
            detected_dates=[],
            changed=False,
            failed=True,
            notes=f"Fetch error: {exc}",
        )


def _load_latest_refresh(review_root: Path, before_date: str) -> dict[str, Any] | None:
    if not review_root.exists():
        return None
    candidates = sorted(review_root.glob("source_refresh_*.json"))
    for path in reversed(candidates):
        token = path.stem.replace("source_refresh_", "", 1)
        if token < before_date:
            return json.loads(path.read_text(encoding="utf-8"))
    return None


def run_refresh(root: Path, as_of: str | None = None, fetcher: Any | None = None) -> dict[str, Any]:
    as_of_date = as_of or date.today().isoformat()
    registry_path = root / WATCH_DATA_ROOT / "source_registry.json"
    review_root = root / REVIEW_ROOT
    registry = load_registry(registry_path)
    errors = validate_registry(registry)
    if errors:
        raise ValueError("; ".join(errors))
    previous = _load_latest_refresh(review_root, as_of_date) or {}
    previous_sources = {str(item.get("source_id") or ""): item for item in previous.get("sources", []) if isinstance(item, dict)}
    fetch = fetcher or _fetch_metadata
    rows: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    stale_sources: list[dict[str, Any]] = []
    for source in registry:
        source_id = str(source["source_id"])
        if not bool(source.get("enabled")):
            continue
        url = str(source["url"])
        if not (url.startswith("http://") or url.startswith("https://")):
            continue
        result: FetchResult = fetch(url, str(source.get("title") or source_id))
        prev = previous_sources.get(source_id, {})
        prev_hash = str(prev.get("content_hash") or "")
        prev_title = str(prev.get("title") or "")
        changed = bool(prev_hash and result.content_hash and prev_hash != result.content_hash)
        title_changed = bool(prev_title and result.title and prev_title != result.title)
        stale_reasons: list[str] = []
        if result.failed:
            stale_reasons.append("fetch_failed")
        if result.status_code != 200:
            stale_reasons.append("status_not_200")
        if changed:
            stale_reasons.append("content_hash_changed")
        if title_changed:
            stale_reasons.append("title_changed")
        retrieved_dt = datetime.fromisoformat(result.retrieved_at.replace("Z", "+00:00"))
        age_days = (datetime.now(timezone.utc) - retrieved_dt).days
        if age_days > _window_days(str(source.get("check_frequency") or "manual")):
            stale_reasons.append("check_overdue")
        changed_flag = bool(changed or title_changed)
        failure_hint = _diagnose_fetch_issue(result.notes, result.status_code)
        row = {
            "source_id": source_id,
            "source_family": source["source_family"],
            "source_family_label": SOURCE_FAMILY_LABELS.get(str(source["source_family"]), str(source["source_family"])),
            "check_frequency": source["check_frequency"],
            "status_code": result.status_code,
            "final_url": result.final_url,
            "retrieved_at": result.retrieved_at,
            "title": result.title,
            "content_hash": result.content_hash,
            "snippet": result.snippet,
            "detected_dates": result.detected_dates,
            "stale": bool(stale_reasons),
            "stale_reasons": stale_reasons,
            "stale_reason_labels": [STALE_REASON_LABELS.get(reason, reason) for reason in stale_reasons],
            "changed": changed_flag,
            "fetch_failed": bool(result.failed),
            "fetch_notes": result.notes,
            "fetch_diagnostic": failure_hint,
        }
        rows.append(row)
        if stale_reasons:
            stale_sources.append({"source_id": source_id, "reasons": stale_reasons})
        if changed or title_changed:
            candidates.append(
                {
                    "source_id": source_id,
                    "source_url": result.final_url or url,
                    "source_title": result.title or str(source.get("title") or source_id),
                    "retrieved_at": result.retrieved_at,
                    "source_family": source["source_family"],
                    "proposed_claim_class": "unknown",
                    "proposed_claim_text": "",
                    "review_status": "candidate",
                    "confidence": "low",
                    "notes": "Source changed; manual review required.",
                }
            )
    payload = {
        "review_type": "cascadia_detention_watch_source_refresh",
        "as_of_date": as_of_date,
        "registry_path": str(registry_path),
        "source_count": len(rows),
        "sources": rows,
        "stale_sources": stale_sources,
        "candidate_claims": candidates,
    }
    output_path = review_root / f"source_refresh_{as_of_date}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {"ok": True, "output_path": str(output_path), "source_count": len(rows), "candidate_count": len(candidates)}


def render_review_dashboard(refresh_path: Path) -> Path:
    payload = json.loads(refresh_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("refresh payload must be a JSON object")
    as_of_date = str(payload.get("as_of_date") or "")
    sources = [item for item in payload.get("sources", []) if isinstance(item, dict)]
    candidates = [item for item in payload.get("candidate_claims", []) if isinstance(item, dict)]
    failed_fetches = sum(1 for row in sources if row.get("status_code") != 200)
    changed_sources = sum(
        1
        for row in sources
        if ("content_hash_changed" in list(row.get("stale_reasons") or [])) or ("title_changed" in list(row.get("stale_reasons") or []))
    )
    stale_sources = sum(1 for row in sources if bool(row.get("stale")))
    def _stale_labels_for_row(row: dict[str, Any]) -> list[str]:
        labeled = [str(x) for x in list(row.get("stale_reason_labels") or []) if str(x)]
        if labeled:
            return labeled
        return [STALE_REASON_LABELS.get(str(reason), str(reason)) for reason in list(row.get("stale_reasons") or [])]

    source_rows_html = "".join(
        f"<tr>"
        f"<td>{html.escape(str(row.get('source_id') or ''))}</td>"
        f"<td>{html.escape(str(row.get('source_family_label') or row.get('source_family') or ''))}</td>"
        f"<td>{html.escape(str(row.get('title') or ''))}</td>"
        f"<td>{html.escape(str(row.get('status_code') if row.get('status_code') is not None else 'n/a'))}</td>"
        f"<td>{html.escape(', '.join(_stale_labels_for_row(row)) or 'none')}</td>"
        f"<td>{'changed' if bool(row.get('changed')) or ('content_hash_changed' in list(row.get('stale_reasons') or [])) or ('title_changed' in list(row.get('stale_reasons') or [])) else 'not changed'}</td>"
        f"<td>{html.escape(str(row.get('retrieved_at') or ''))}</td>"
        f"<td>{html.escape(str(row.get('fetch_diagnostic') or ''))}</td>"
        f"<td><a href=\"{html.escape(str(row.get('final_url') or ''))}\" target=\"_blank\" rel=\"noopener noreferrer\">{html.escape(str(row.get('final_url') or ''))}</a></td>"
        f"</tr>"
        for row in sources
    )
    candidate_rows_html = "".join(
        f"<tr>"
        f"<td>{html.escape(str(row.get('source_id') or ''))}</td>"
        f"<td>{html.escape(str(row.get('proposed_claim_class') or ''))}</td>"
        f"<td>{html.escape(str(row.get('proposed_claim_text') or ''))}</td>"
        f"<td>{html.escape(str(row.get('review_status') or ''))}</td>"
        f"<td>{html.escape(str(row.get('confidence') or ''))}</td>"
        f"<td>{html.escape(str(row.get('notes') or ''))}</td>"
        f"</tr>"
        for row in candidates
    )
    dashboard_html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Cascadia Detention Watch Source Refresh Review - {html.escape(as_of_date)}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 20px; line-height: 1.4; }}
    table {{ border-collapse: collapse; width: 100%; margin-bottom: 24px; }}
    th, td {{ border: 1px solid #bbb; padding: 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f1f1f1; }}
    .warning {{ background: #fff3cd; border: 1px solid #d9b86a; padding: 10px; margin-bottom: 20px; font-weight: 700; }}
  </style>
</head>
<body>
  <div class="warning">Local editorial review only - not for publication</div>
  <h1>Cascadia Detention Watch Source Refresh Review</h1>
  <p><strong>Review date:</strong> {html.escape(as_of_date)}</p>
  <h2>Summary counts</h2>
  <ul>
    <li>Total sources checked: {len(sources)}</li>
    <li>Failed fetches: {failed_fetches}</li>
    <li>Changed sources: {changed_sources}</li>
    <li>Stale sources: {stale_sources}</li>
    <li>Candidate claims requiring review: {len(candidates)}</li>
  </ul>
  <h2>Source table</h2>
  <table>
    <thead><tr><th>source_id</th><th>source_family</th><th>title</th><th>status_code</th><th>stale flags</th><th>changed</th><th>retrieved_at</th><th>diagnostic</th><th>final_url</th></tr></thead>
    <tbody>{source_rows_html}</tbody>
  </table>
  <h2>Candidate claim review table</h2>
  <table>
    <thead><tr><th>source_id</th><th>proposed_claim_class</th><th>proposed_claim_text</th><th>review_status</th><th>confidence</th><th>notes</th></tr></thead>
    <tbody>{candidate_rows_html}</tbody>
  </table>
  <h2>Manual review instructions</h2>
  <ul>
    <li>Editors must manually inspect original sources before approving any candidate claims.</li>
    <li>Edit candidate rows in the refresh JSON file, then re-open this dashboard to verify changes.</li>
    <li>Empty candidate claims cannot be promoted.</li>
    <li>Only <code>review_status: "approved"</code> candidates can be promoted.</li>
    <li>If diagnostics show TLS/certificate errors, resolve local trust-store/network settings before assuming source outages.</li>
  </ul>
</body>
</html>
"""
    output_path = refresh_path.parent / f"review_dashboard_{as_of_date}.html"
    output_path.write_text(dashboard_html, encoding="utf-8")
    return output_path


def promote_candidates(input_path: Path, edition_date: str, output_path: Path) -> dict[str, Any]:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("review payload must be a JSON object")
    source_rows = [item for item in payload.get("sources", []) if isinstance(item, dict)]
    source_ids = {str(item.get("source_id") or "") for item in source_rows}
    claims = [item for item in payload.get("candidate_claims", []) if isinstance(item, dict)]
    promoted: list[dict[str, Any]] = []
    errors: list[str] = []
    for idx, claim in enumerate(claims):
        claim_errors: list[str] = []
        source_id = str(claim.get("source_id") or "").strip()
        source_url = str(claim.get("source_url") or "").strip()
        claim_text = str(claim.get("proposed_claim_text") or "").strip()
        claim_class = str(claim.get("proposed_claim_class") or "").strip().lower()
        review_status = str(claim.get("review_status") or "").strip().lower()
        if not source_id or source_id not in source_ids:
            claim_errors.append(f"candidate[{idx}] has missing or unknown source_id")
        if not source_url:
            claim_errors.append(f"candidate[{idx}] missing source_url")
        if review_status != "approved":
            claim_errors.append(f"candidate[{idx}] review_status must be approved")
        if claim_class not in ALLOWED_CLAIM_CLASSES:
            claim_errors.append(f"candidate[{idx}] has unsupported claim class")
        if not claim_text:
            claim_errors.append(f"candidate[{idx}] proposed_claim_text is empty")
        if claim_errors:
            errors.extend(claim_errors)
        else:
            promoted.append(
                {
                    "source_id": source_id,
                    "source_url": source_url,
                    "source_title": str(claim.get("source_title") or source_id),
                    "retrieved_at": str(claim.get("retrieved_at") or ""),
                    "source_family": str(claim.get("source_family") or ""),
                    "claim_class": claim_class,
                    "text": claim_text,
                    "confidence": str(claim.get("confidence") or "low"),
                    "notes": str(claim.get("notes") or ""),
                }
            )
    if errors:
        raise ValueError("; ".join(errors))
    update_payload = {
        "date": edition_date,
        "title": "Cascadia Detention Watch Update",
        "summary": "Approved source-backed update claims for manual editorial integration.",
        "sources": source_rows,
        "changed_this_week": [],
        "current_indicators_delta": [],
        "timeline_additions": [],
        "claims": promoted,
        "open_questions": [],
        "method_note": f"Promoted from reviewed candidates in {input_path}.",
        "review_status": "approved",
        "review_input": str(input_path),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(update_payload, indent=2), encoding="utf-8")
    return {"ok": True, "output_path": str(output_path), "claim_count": len(promoted)}
