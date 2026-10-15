"""Edition-level package approval, planning, and private staging.

This consumer is deliberately separate from the legacy single-story workflow.
It validates a formally reviewed edition proposal, binds a separately committed
package/audio approval, and stages a private package outside both repositories.
It never publishes, mutates Pages, or grants publication authority.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from . import gaza_edition_correction as edition
from . import gaza_historical_correction as legacy

Error = edition.Error
APPROVAL_REQUEST_SCHEMA = "gaza_edition_correction_approval_request_v1"
APPROVAL_SCHEMA = "gaza_edition_correction_release_approval_v1"
PLAN_SCHEMA = "gaza_edition_correction_validated_plan_v1"
PACKAGE_SCHEMA = "gaza_edition_correction_staged_package_v1"


def _load(path: Path, label: str) -> Any:
    return edition._load(path, label)


def _json(value: Any) -> bytes:
    return legacy._json_document(value)


def _hash(value: bytes) -> str:
    return legacy.sha256_bytes(value)


def _path(root: Path, relative: str, label: str) -> Path:
    return legacy._repo_path(root, relative, label)


def _require_false(value: dict[str, Any], keys: tuple[str, ...], label: str) -> None:
    if any(value.get(key) is not False for key in keys):
        raise Error(f"{label} improperly grants authority")


def _proposal(proposal_path: Path) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    proposal_path = proposal_path.resolve()
    if proposal_path.name != "proposal.json":
        raise Error("edition package consumer requires proposal.json")
    root = proposal_path.parent
    proposal = _load(proposal_path, "edition proposal")
    if proposal.get("schema_version") != edition.PROPOSAL_SCHEMA:
        raise Error("edition proposal schema is unsupported")
    if proposal.get("state") != "formal_proposal" or proposal.get("missing_inputs") != []:
        raise Error("only a formally reviewed edition proposal may be packaged")
    if legacy.fingerprint_payload(proposal, "packet_sha256") != proposal.get("packet_sha256"):
        raise Error("edition proposal fingerprint differs")
    _require_false(proposal, edition.AUTHORITY_FIELDS, "edition proposal")
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    expected = set(proposal.get("artifact_hashes", {})) | {"proposal.json"}
    if actual != expected:
        raise Error("edition proposal files are missing or unexpected")
    for relative, expected_hash in proposal["artifact_hashes"].items():
        if legacy.sha256_file(_path(root, relative, "edition proposal artifact")) != expected_hash:
            raise Error(f"edition proposal artifact hash drifted: {relative}")
    request = _load(_path(root, "request.json", "edition request"), "edition request")
    if request.get("schema_version") != edition.REQUEST_SCHEMA:
        raise Error("edition request schema is unsupported")
    if request.get("expected_source_head") != proposal.get("source_commit"):
        raise Error("edition request and proposal source heads differ")
    if request.get("expected_pages_head") != proposal.get("pages_head"):
        raise Error("edition request and proposal Pages heads differ")
    if request.get("review_packet_sha256") != proposal.get("review_packet_sha256"):
        raise Error("edition request and proposal review packet differ")
    return root, proposal, request


def _approval_request(proposal: dict[str, Any]) -> dict[str, Any]:
    correction_id = proposal.get("correction_id")
    audio_sha = proposal.get("audio_request_sha256")
    values = {
        "schema_version": APPROVAL_REQUEST_SCHEMA,
        "scope": "gaza_edition_correction_package_and_audio",
        "proposal_sha256": proposal["packet_sha256"],
        "correction_id": correction_id,
        "source_commit": proposal["source_commit"],
        "pages_head": proposal["pages_head"],
        "preview_set_sha256": proposal["preview_set_sha256"],
        "audio_request_sha256": audio_sha,
        "package_authorized": False,
        "audio_authorized": False,
        "publication_authorized": False,
        "request_sha256": "",
    }
    values["request_sha256"] = legacy.fingerprint_payload(values, "request_sha256")
    return values


def create_edition_approval_request(*, proposal_path: Path, output_path: Path) -> dict[str, Any]:
    _, proposal, _ = _proposal(proposal_path)
    output_path = output_path.resolve()
    content = _json(_approval_request(proposal))
    if output_path.exists():
        if output_path.read_bytes() == content:
            return {"status": "idempotent_noop", "approval_request_path": str(output_path)}
        raise Error("conflicting edition approval request already exists")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.tmp")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, output_path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return {
        "status": "edition_approval_request_created",
        "approval_request_path": str(output_path),
        "publication_authorized": False,
    }


def create_edition_package_approval(
    *,
    source_root: Path,
    proposal_path: Path,
    approval_request_path: Path,
    output_path: Path,
    approval_id: str,
    approver: str,
    approved_at: str,
) -> dict[str, Any]:
    source_root = source_root.resolve()
    output_path = output_path.resolve()
    approvals_root = (source_root / "approvals" / "gaza" / "edition").resolve()
    try:
        output_path.relative_to(approvals_root)
    except ValueError as exc:
        raise Error("edition approval must be under approvals/gaza/edition") from exc
    _, proposal, _ = _proposal(proposal_path)
    if legacy._git(source_root, "rev-parse", "HEAD") != proposal["source_commit"]:
        raise Error("source history drifted before edition package approval")
    request = _load(approval_request_path, "edition approval request")
    expected = _approval_request(proposal)
    if request != expected:
        raise Error("edition approval request is not validator-produced for this proposal")
    if not all(str(value).strip() for value in (approval_id, approver, approved_at)):
        raise Error("approval ID, approver, and approval time are required")
    approval = {
        "schema_version": APPROVAL_SCHEMA,
        "scope": "gaza_edition_correction",
        "approval_id": approval_id,
        "proposal_sha256": request["proposal_sha256"],
        "correction_id": request["correction_id"],
        "source_commit": request["source_commit"],
        "pages_head": request["pages_head"],
        "preview_set_sha256": request["preview_set_sha256"],
        "audio_request_sha256": request["audio_request_sha256"],
        "approved_at": approved_at,
        "approver": approver,
        "package_authorized": True,
        "audio_authorized": True,
        "publication_authorized": False,
        "approval_fingerprint": "",
    }
    approval["approval_fingerprint"] = legacy.fingerprint_payload(approval, "approval_fingerprint")
    content = _json(approval)
    if output_path.exists():
        if output_path.read_bytes() == content:
            return {"status": "idempotent_noop", "approval_path": str(output_path)}
        raise Error("conflicting edition package approval already exists")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.tmp")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, output_path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return {
        "status": "edition_package_approval_created",
        "approval_path": str(output_path),
        "approval_fingerprint": approval["approval_fingerprint"],
        "publication_authorized": False,
    }


def _load_committed_approval(
    source_root: Path,
    proposal: dict[str, Any],
    approval_ref: str,
    approval_path: str,
) -> tuple[dict[str, Any], str, bytes]:
    if not approval_ref or not approval_path:
        raise Error("a separately committed edition approval ref and path are required")
    approval_commit = legacy._git(source_root, "rev-parse", f"{approval_ref}^{{commit}}")
    raw = legacy._git_blob(source_root, approval_commit, approval_path)
    if legacy._commit_parents(source_root, approval_commit) != [proposal["source_commit"]]:
        raise Error("edition approval commit must have only the proposal source commit as parent")
    if not legacy._approval_only_diff(source_root, proposal["source_commit"], approval_commit, approval_path):
        raise Error("edition approval commit must contain only the approval artifact")
    try:
        approval = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise Error("committed edition approval is missing or invalid") from exc
    required = {
        "schema_version", "scope", "approval_id", "proposal_sha256", "correction_id",
        "source_commit", "pages_head", "preview_set_sha256", "audio_request_sha256",
        "approved_at", "approver", "package_authorized", "audio_authorized",
        "publication_authorized", "approval_fingerprint",
    }
    legacy._exact_fields(approval, required, "edition release approval")
    if approval["schema_version"] != APPROVAL_SCHEMA or approval["scope"] != "gaza_edition_correction":
        raise Error("edition release approval schema or scope is invalid")
    if approval["approval_fingerprint"] != legacy.fingerprint_payload(approval, "approval_fingerprint"):
        raise Error("edition release approval fingerprint differs")
    expected = _approval_request(proposal)
    bindings = ("proposal_sha256", "correction_id", "source_commit", "pages_head",
                "preview_set_sha256", "audio_request_sha256")
    if any(approval[key] != expected[key] for key in bindings):
        raise Error("edition release approval binds different proposal evidence")
    if approval["package_authorized"] is not True or approval["audio_authorized"] is not True:
        raise Error("edition release approval does not authorize package and audio construction")
    if approval["publication_authorized"] is not False:
        raise Error("edition package approval must not grant publication authority")
    if not str(approval["approver"]).strip() or not str(approval["approved_at"]).strip():
        raise Error("edition release approval lacks authority identity or timestamp")
    return approval, approval_commit, raw


def _validate_public_bindings(pages_root: Path, proposal: dict[str, Any]) -> None:
    pages_root = pages_root.resolve()
    if legacy._git(pages_root, "status", "--porcelain"):
        raise Error("Pages repository is dirty")
    if legacy._git(pages_root, "rev-parse", "HEAD") != proposal["pages_head"]:
        raise Error("Pages history drifted from edition proposal")
    for row in proposal["original_artifacts"]:
        current = _path(pages_root, row["public_path"], "edition public evidence")
        if not current.is_file() or legacy.sha256_file(current) != row["sha256"]:
            raise Error("edition original public evidence drifted")
        blob = legacy._git_blob(pages_root, proposal["pages_head"], row["public_path"])
        if _hash(blob) != row["git_blob_sha256"] or legacy._git(
            pages_root, "rev-parse", f'{proposal["pages_head"]}:{row["public_path"]}'
        ) != row["git_blob_oid"]:
            raise Error("edition original public Git binding drifted")


def plan_edition_correction(
    *,
    source_root: Path,
    pages_root: Path,
    proposal_path: Path,
    approval_ref: str,
    approval_path: str,
) -> dict[str, Any]:
    root, proposal, _ = _proposal(proposal_path)
    source_root = source_root.resolve()
    current_source = legacy._git(source_root, "rev-parse", "HEAD")
    if current_source != proposal["source_commit"] and legacy._git(
        source_root, "merge-base", "--is-ancestor", proposal["source_commit"], current_source
    ) != "":
        raise Error("source history does not contain the edition proposal commit")
    _validate_public_bindings(pages_root, proposal)
    approval, approval_commit, approval_blob = _load_committed_approval(
        source_root, proposal, approval_ref, approval_path
    )
    topology = legacy._validate_approval_source_topology(
        source_root,
        source_head=current_source,
        proposal_source_commit=proposal["source_commit"],
        approval_commit=approval_commit,
        approval_path=approval_path,
        approval_blob=approval_blob,
    )
    audio_request = _load(_path(root, "audio_request.json", "edition audio request"), "edition audio request")
    if _hash(_json(audio_request)) != proposal["audio_request_sha256"]:
        raise Error("edition audio request differs from proposal")
    rows = []
    for row in proposal["representations"]:
        input_path = _path(root, row["input_path"], "edition proposal preview")
        if not input_path.is_file() or legacy.sha256_file(input_path) != row["sha256"]:
            raise Error(f"edition proposal preview differs: {row['role']}")
        rows.append({
            "role": row["role"],
            "public_path": row["public_path"],
            "input_path": row["input_path"],
            "corrected_sha256": row["sha256"],
        })
    return {
        "schema_version": PLAN_SCHEMA,
        "status": "validated_edition_plan",
        "operation": "gaza_edition_correction",
        "correction_id": proposal["correction_id"],
        "proposal_sha256": proposal["packet_sha256"],
        "source_commit": proposal["source_commit"],
        "pages_head": proposal["pages_head"],
        "validated_source_commit": current_source,
        "approval_id": approval["approval_id"],
        "approval_commit": approval_commit,
        "approval_topology": topology,
        "audio_request": audio_request,
        "representations": rows,
        "package_authorized": True,
        "audio_authorized": True,
        "publication_authorized": False,
        "persistent_mutation": False,
        "pages_mutation": False,
    }


def _rendered_content(
    role: str,
    content: bytes,
    rendered_audio_length: int,
    rendered_audio_sha256: str,
    correction_id: str,
) -> bytes:
    if role == "correction_audio_metadata":
        metadata = json.loads(content.decode("utf-8"))
        metadata["render_status"] = "rendered_for_private_review"
        metadata["rendered_audio_sha256"] = rendered_audio_sha256
        return _json(metadata)
    if role in {"podcast", "audio_podcast"}:
        correction_bytes = re.escape(correction_id.encode("utf-8"))
        item_pattern = re.compile(
            rb"<item\b[^>]*>.*?<guid\b[^>]*>\s*"
            + correction_bytes
            + rb"\s*</guid>.*?</item>",
            re.S,
        )
        items = list(item_pattern.finditer(content))
        if len(items) != 1:
            raise Error(f"{role} lacks one correction feed item")
        item_match = items[0]
        item = item_match.group(0)
        enclosure_pattern = re.compile(
            rb'(<enclosure\b[^>]*\blength=")\d+(")',
            re.S,
        )
        enclosures = list(enclosure_pattern.finditer(item))
        if len(enclosures) != 1:
            raise Error(f"{role} correction feed item lacks one enclosure")
        enclosure = enclosures[0]
        rendered = (
            item[:enclosure.start(2)]
            + str(rendered_audio_length).encode("ascii")
            + item[enclosure.end(2):]
        )
        return content[:item_match.start()] + rendered + content[item_match.end():]
    return content


def stage_edition_correction(
    *,
    plan: dict[str, Any],
    input_root: Path,
    rendered_audio_path: Path,
    staging_root: Path,
    source_root: Path,
    pages_root: Path,
) -> dict[str, Any]:
    input_root = input_root.resolve()
    staging_root = staging_root.resolve()
    source_root = source_root.resolve()
    pages_root = pages_root.resolve()
    for forbidden, label in ((source_root, "source"), (pages_root, "Pages")):
        try:
            staging_root.relative_to(forbidden)
        except ValueError:
            pass
        else:
            raise Error(f"staging root must be outside the {label} repository")
    if plan.get("status") != "validated_edition_plan" or plan.get("package_authorized") is not True:
        raise Error("only a package-authorized edition plan may be staged")
    if plan.get("audio_authorized") is not True or plan.get("publication_authorized") is not False:
        raise Error("edition plan carries invalid audio or publication authority")
    if legacy._git(source_root, "rev-parse", "HEAD") != plan.get("validated_source_commit"):
        raise Error("source history drifted before edition package staging")
    _validate_public_bindings(pages_root, {
        "pages_head": plan["pages_head"],
        "original_artifacts": [],
    })
    rendered_audio_path = rendered_audio_path.resolve()
    if not rendered_audio_path.is_file() or rendered_audio_path.read_bytes()[:3] != b"ID3":
        raise Error("approved edition audio render is missing or is not an MP3 asset")
    audio_content = rendered_audio_path.read_bytes()
    audio_sha = _hash(audio_content)
    final_content: dict[str, bytes] = {}
    final_rows = []
    for row in plan["representations"]:
        source = _path(input_root, row["input_path"], "approved edition preview")
        if not source.is_file() or legacy.sha256_file(source) != row["corrected_sha256"]:
            raise Error(f"approved edition preview is missing for {row['role']}")
        content = _rendered_content(
            row["role"],
            source.read_bytes(),
            len(audio_content),
            audio_sha,
            plan["correction_id"],
        )
        final_content[row["role"]] = content
        final_rows.append({"role": row["role"], "public_path": row["public_path"], "sha256": _hash(content)})
    audio_public_path = plan["audio_request"]["public_path"]
    final_content["correction_audio"] = audio_content
    final_rows.append({"role": "correction_audio", "public_path": audio_public_path, "sha256": audio_sha})
    final_rows.sort(key=lambda row: row["role"])
    artifact_set = "sha256:" + _hash(legacy._canonical_bytes(final_rows))
    manifest = {
        "schema_version": PACKAGE_SCHEMA,
        "status": "staged_edition_correction",
        "operation": "gaza_edition_correction",
        "correction_id": plan["correction_id"],
        "proposal_sha256": plan["proposal_sha256"],
        "approval_id": plan["approval_id"],
        "approval_commit": plan["approval_commit"],
        "pages_head": plan["pages_head"],
        "validated_source_commit": plan["validated_source_commit"],
        "audio_request": plan["audio_request"],
        "preview_representations": plan["representations"],
        "representations": final_rows,
        "rendered_audio_sha256": audio_sha,
        "package_artifact_set_sha256": artifact_set,
        "publication_authorized": False,
        "package_manifest_sha256": "",
    }
    manifest["package_manifest_sha256"] = legacy.fingerprint_payload(manifest, "package_manifest_sha256")
    target = staging_root / plan["correction_id"]
    if target.exists():
        existing = target / "package_manifest.json"
        if existing.is_file() and _load(existing, "existing edition package") == manifest:
            return {**manifest, "status": "idempotent_noop", "package_path": str(target)}
        raise Error("conflicting edition correction package already exists")
    staging_root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{plan['correction_id']}-", dir=staging_root))
    try:
        for row in manifest["representations"]:
            destination = _path(temporary, row["public_path"], "staged edition representation")
            destination.parent.mkdir(parents=True, exist_ok=True)
            if row["role"] == "correction_audio":
                shutil.copyfile(rendered_audio_path, destination)
            elif row["role"] in {"correction_audio_metadata", "podcast", "audio_podcast"}:
                destination.write_bytes(final_content[row["role"]])
            else:
                source = _path(input_root, next(
                    item["input_path"] for item in plan["representations"] if item["role"] == row["role"]
                ), "staged edition preview")
                shutil.copyfile(source, destination)
        (temporary / "package_manifest.json").write_bytes(_json(manifest))
        os.replace(temporary, target)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return {**manifest, "package_path": str(target)}


def verify_staged_edition_package(
    *,
    plan: dict[str, Any],
    package_root: Path,
    source_root: Path,
    pages_root: Path,
) -> dict[str, Any]:
    package_root = package_root.resolve()
    manifest = _load(package_root / "package_manifest.json", "staged edition package")
    if manifest.get("schema_version") != PACKAGE_SCHEMA:
        raise Error("staged edition package schema is unsupported")
    if manifest.get("correction_id") != plan.get("correction_id") or manifest.get("proposal_sha256") != plan.get("proposal_sha256"):
        raise Error("staged edition package binds another proposal")
    if manifest.get("publication_authorized") is not False:
        raise Error("staged edition package improperly grants publication authority")
    if manifest.get("package_manifest_sha256") != legacy.fingerprint_payload(manifest, "package_manifest_sha256"):
        raise Error("staged edition package fingerprint differs")
    expected_roles = {row["role"] for row in plan["representations"]} | {"correction_audio"}
    rows = manifest.get("representations")
    if not isinstance(rows, list) or {row.get("role") for row in rows if isinstance(row, dict)} != expected_roles:
        raise Error("staged edition package representation set is incomplete")
    for row in rows:
        path = _path(package_root, row["public_path"], "staged edition representation")
        if not path.is_file() or legacy.sha256_file(path) != row["sha256"]:
            raise Error(f"staged edition representation differs: {row['role']}")
    if manifest.get("package_artifact_set_sha256") != "sha256:" + _hash(legacy._canonical_bytes(sorted(rows, key=lambda row: row["role"]))):
        raise Error("staged edition package artifact-set fingerprint differs")
    source_root = source_root.resolve()
    if legacy._git(source_root, "rev-parse", "HEAD") != plan.get("validated_source_commit"):
        raise Error("source history drifted after edition package staging")
    _validate_public_bindings(pages_root, {"pages_head": plan["pages_head"], "original_artifacts": []})
    return {
        "status": "staged_edition_package_verified",
        "correction_id": plan["correction_id"],
        "package_manifest_sha256": manifest["package_manifest_sha256"],
        "package_artifact_set_sha256": manifest["package_artifact_set_sha256"],
        "publication_authorized": False,
        "persistent_mutation": False,
    }
