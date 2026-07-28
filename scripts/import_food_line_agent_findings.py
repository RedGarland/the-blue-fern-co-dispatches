from __future__ import annotations
import argparse, hashlib, json, os, shutil, tempfile
from collections import Counter
from pathlib import Path
from typing import Any
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from bluefern_dispatches.adapters.food_line_agent import adapt_food_line_agent_output, map_finding_to_food_line_candidate

def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle: json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2); handle.write("\n")
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)

ENVELOPE_FIELDS = ("schema_version", "agent_name", "agent_run_id", "started_at", "completed_at", "search_window", "findings", "coverage_notes")

def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""): digest.update(block)
    return digest.hexdigest()

def validate_input(input_path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"valid": False, "input_sha256": _sha256(input_path), "finding_count": 0, "invalid_urls": [], "missing_evidence": [], "missing_publication_dates": [], "duplicate_findings": [], "fields_requiring_human_review": []}
    try: payload = json.loads(input_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        result["error"] = f"invalid_json: {exc}"; return result
    if not isinstance(payload, dict) or any(field not in payload for field in ENVELOPE_FIELDS):
        result["error"] = "invalid_envelope: required top-level fields are missing"; return result
    if not isinstance(payload["findings"], list): result["error"] = "invalid_envelope: findings must be a list"; return result
    result["finding_count"] = len(payload["findings"])
    seen: dict[str, int] = {}
    for index, row in enumerate(payload["findings"]):
        if not isinstance(row, dict): result["fields_requiring_human_review"].append(f"findings[{index}]"); continue
        url = str(row.get("canonical_source_url") or row.get("source_url") or row.get("url") or "")
        if not url.lower().startswith("https://"): result["invalid_urls"].append(index)
        if not str(row.get("exact_supporting_passage") or row.get("passage") or "").strip(): result["missing_evidence"].append(index)
        if not str(row.get("source_published_at") or row.get("published_at") or row.get("publication_date") or "").strip(): result["missing_publication_dates"].append(index)
        identity = json.dumps({"url": url.split("?")[0].lower().rstrip("/"), "title": str(row.get("title") or row.get("headline") or "").strip().lower(), "publisher": str(row.get("publisher") or "").strip().lower()}, sort_keys=True)
        if identity in seen: result["duplicate_findings"].append({"first": seen[identity], "duplicate": index})
        else: seen[identity] = index
        result["fields_requiring_human_review"].append(index)
    result["valid"] = not result["error"] if "error" in result else not (result["invalid_urls"] or result["missing_evidence"] or result["missing_publication_dates"] or result["duplicate_findings"])
    return result

def process(root: Path, input_path: Path, *, edition_date: str, agent_name: str, agent_run_id: str, dry_run: bool) -> dict[str, Any]:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    envelope = payload if isinstance(payload, dict) and "findings" in payload else {}
    effective_agent = agent_name or str(envelope.get("agent_name") or "")
    effective_run = agent_run_id or str(envelope.get("agent_run_id") or "")
    input_hash = _sha256(input_path)
    artifact_path = root / "data/dispatches/food-line/agent-intake" / edition_date / f"{effective_run}.json"
    if artifact_path.exists():
        previous = json.loads(artifact_path.read_text(encoding="utf-8"))
        if previous.get("input_sha256") == input_hash:
            return {"status": "idempotent_noop", "input_sha256": input_hash, "artifact_path": str(artifact_path), "finding_ids": [row.get("finding_id") for row in previous.get("findings", [])], "would_write": False}
        raise ValueError("refusing reimport: run artifact exists for a different input hash")
    findings = adapt_food_line_agent_output(payload, agent_name=effective_agent, agent_run_id=effective_run)
    candidates = [map_finding_to_food_line_candidate(item, edition_date=edition_date) for item in findings]
    seen: set[str] = set(); deduped = []
    for row in candidates:
        if row["agent_duplicate_key"] in seen: row["exclusion_reason"] = "duplicate_article_within_run"; continue
        seen.add(row["agent_duplicate_key"]); deduped.append(row)
    counts = Counter("eligible_for_review" if row["eligible_for_review"] else "excluded" for row in candidates)
    artifact = {"schema_version": "food_line_agent_intake_v1", "agent_name": effective_agent, "agent_run_id": effective_run, "started_at": envelope.get("started_at", ""), "completed_at": envelope.get("completed_at", ""), "search_window": envelope.get("search_window", {"edition_date": edition_date}), "findings": [item.to_dict() for item in findings], "candidate_rows": deduped, "counts": dict(counts), "coverage_notes": envelope.get("coverage_notes", "Private intake only; review is required before publication."), "input_sha256": input_hash, "input_filename": input_path.name}
    report = {"schema_version": "food_line_agent_intake_report_v1", "agent_run_id": effective_run, "edition_date": edition_date, "dry_run": dry_run, "counts": dict(counts), "finding_ids": [item.finding_id for item in findings], "duplicate_keys": sorted(seen), "input_sha256": input_hash}
    if not dry_run:
        _atomic_json(artifact_path, artifact)
        _atomic_json(root / "data/dispatches/food-line/agent-intake/reports" / edition_date / f"{effective_run}.json", report)
        inbox = root / "data/dispatches/food-line/agent-inbox"
        try:
            input_path.resolve().relative_to(inbox.resolve())
        except ValueError:
            pass
        else:
            archive = inbox / "processed" / edition_date / input_path.name
            if archive.exists() and _sha256(archive) != input_hash: archive = archive.with_name(f"{archive.stem}-{input_hash[:12]}{archive.suffix}")
            if not archive.exists():
                archive.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(input_path, archive)
    return report | {"artifact": artifact, "would_write": not dry_run}

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect or privately import Food Line agent findings")
    parser.add_argument("operation", choices=["inspect", "validate", "import", "dry-run", "report"]); parser.add_argument("--input", required=True, type=Path); parser.add_argument("--edition-date"); parser.add_argument("--agent-name", default=""); parser.add_argument("--agent-run-id", default=""); parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    if args.operation == "validate":
        result = validate_input(args.input)
    else:
        if not args.edition_date or not args.agent_run_id: parser.error("--edition-date and --agent-run-id are required for import operations")
        result = process(args.repo_root, args.input, edition_date=args.edition_date, agent_name=args.agent_name, agent_run_id=args.agent_run_id, dry_run=args.operation in {"inspect", "dry-run", "report"})
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)); return 0

if __name__ == "__main__": raise SystemExit(main())
