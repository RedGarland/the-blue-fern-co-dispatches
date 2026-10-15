from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA = "gaza_edition_correction_publication_authorization_v1"
REQUEST_SCHEMA = "gaza_edition_correction_publication_request_v1"
ROOT = Path("publication-authorizations") / "gaza" / "edition"


class GazaPublicationError(ValueError):
    pass


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _fingerprint(payload: Any) -> str:
    return "sha256:" + _sha(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GazaPublicationError(f"unable to read JSON: {path}") from exc


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True, encoding="utf-8").stdout.strip()


def _dirty_except(root: Path, allowed: Path | None = None) -> bool:
    allowed_rel = allowed.resolve().relative_to(root.resolve()).as_posix() if allowed is not None else None
    for line in _git(root, "status", "--porcelain", "--untracked-files=all").splitlines():
        path = line[3:].strip().strip('"').replace("\\", "/")
        if allowed_rel is None or path != allowed_rel:
            return True
    return False


def _required(payload: dict[str, Any], field: str) -> str:
    value = str(payload.get(field) or "").strip()
    if not value:
        raise GazaPublicationError(f"{field} is required")
    return value


def _repo_path(root: Path, value: str, field: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise GazaPublicationError(f"{field} must be repository-relative")
    resolved = (root / path).resolve()
    if root.resolve() not in resolved.parents:
        raise GazaPublicationError(f"{field} escapes repository")
    return resolved


def _approval(source_root: Path, request: dict[str, Any]) -> tuple[dict[str, Any], str]:
    for field in (
        "source_approval_commit", "proposal_source_commit", "approval_ref", "approval_path", "approval_commit",
        "approval_fingerprint", "proposal_sha256", "correction_id", "pages_head", "preview_set_sha256",
    ):
        _required(request, field)
    ref = _required(request, "approval_ref")
    path = _required(request, "approval_path")
    if ref != request["source_approval_commit"]:
        raise GazaPublicationError("approval reference and source approval commit mismatch")
    raw = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=source_root, check=True, capture_output=True).stdout
    try:
        approval = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GazaPublicationError("committed package approval is invalid") from exc
    commit = _git(source_root, "rev-parse", f"{ref}^{{commit}}")
    if commit != request["approval_commit"]:
        raise GazaPublicationError("resolved approval commit binding mismatch")
    parents = _git(source_root, "rev-list", "--parents", "-n", "1", commit).split()[1:]
    if parents != [str(approval.get("source_commit") or "")]:
        raise GazaPublicationError("approval lineage is stale or has more than one parent")
    changed = _git(source_root, "diff-tree", "--no-commit-id", "--name-only", "-r", commit).splitlines()
    if changed != [path]:
        raise GazaPublicationError("approval commit contains changes outside the approval artifact")
    approval_for_fingerprint = dict(approval)
    recorded_fingerprint = str(approval_for_fingerprint.pop("approval_fingerprint", ""))
    if recorded_fingerprint != _fingerprint(approval_for_fingerprint):
        raise GazaPublicationError("committed package approval fingerprint differs")
    if approval.get("publication_authorized") is not False or approval.get("package_authorized") is not True or approval.get("audio_authorized") is not True:
        raise GazaPublicationError("package approval carries invalid authority flags")
    if approval.get("source_commit") != request.get("proposal_source_commit"):
        raise GazaPublicationError("approval proposal source commit binding mismatch")
    for field in ("approval_fingerprint", "proposal_sha256", "correction_id", "pages_head", "preview_set_sha256"):
        if approval.get(field) != request.get(field):
            raise GazaPublicationError(f"approval binding mismatch: {field}")
    return approval, commit


def _manifest(package_root: Path, request: dict[str, Any], pages_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    manifest_path = package_root / "package_manifest.json"
    if not manifest_path.is_file():
        raise GazaPublicationError("staged package manifest is missing")
    manifest_raw = manifest_path.read_bytes()
    manifest_sha = _sha(manifest_raw)
    if manifest_sha != request["package_manifest_sha256"].removeprefix("sha256:"):
        raise GazaPublicationError("package manifest hash mismatch")
    manifest = _read(manifest_path)
    if manifest.get("status") != "staged_edition_correction" or manifest.get("publication_authorized") is not False:
        raise GazaPublicationError("staged package is not an unpublished verified package")
    for field in ("approval_commit", "pages_head", "package_artifact_set_sha256", "rendered_audio_sha256", "correction_id"):
        expected = _required(request, field)
        if manifest.get(field) != expected:
            raise GazaPublicationError(f"staged manifest binding mismatch: {field}")
    rows = manifest.get("representations")
    if not isinstance(rows, list) or not rows:
        raise GazaPublicationError("staged package representations are missing")
    expected: set[str] = {"package_manifest.json"}
    for row in rows:
        public_path = str(row.get("public_path") or "").strip()
        digest = str(row.get("sha256") or "").strip()
        if not public_path or not digest or public_path in expected:
            raise GazaPublicationError("staged representation is malformed or duplicated")
        expected.add(public_path)
        path = _repo_path(package_root, public_path, "staged public path")
        if not path.is_file() or _sha(path.read_bytes()) != digest:
            raise GazaPublicationError(f"staged artifact hash mismatch: {public_path}")
    actual = {p.relative_to(package_root).as_posix() for p in package_root.rglob("*") if p.is_file()}
    if actual != expected:
        raise GazaPublicationError("staged package contains missing or unauthorized files")
    if _git(pages_root, "rev-parse", "HEAD") != request["pages_head"]:
        raise GazaPublicationError("Pages target head is stale")
    if _git(pages_root, "status", "--porcelain"):
        raise GazaPublicationError("Pages target is dirty")
    diff: list[dict[str, Any]] = []
    for row in rows:
        public_path = row["public_path"]
        live = pages_root / public_path
        prior = _sha(live.read_bytes()) if live.is_file() else None
        diff.append({"public_path": public_path, "role": row.get("role"), "prior_sha256": prior, "staged_sha256": row["sha256"], "action": "add" if prior is None else "replace"})
    return manifest, rows, diff


def validate_request(repo_root: Path, pages_root: Path, request_path: Path, *, allowed_dirty_path: Path | None = None) -> dict[str, Any]:
    repo_root = repo_root.resolve(strict=True)
    pages_root = pages_root.resolve(strict=True)
    request = _read(request_path.resolve(strict=True))
    if request.get("schema_version") != REQUEST_SCHEMA:
        raise GazaPublicationError("publication request schema_version is invalid")
    if request.get("publication_authorized") is not True or request.get("pages_authorized") is not True:
        raise GazaPublicationError("publication request must explicitly authorize publication and Pages")
    for field in ("publication_authorized", "pages_authorized", "social_authorized", "source_changes_authorized", "audio_authorized"):
        if field not in request:
            raise GazaPublicationError(f"{field} is required")
    if any(request[field] is not False for field in ("social_authorized", "source_changes_authorized", "audio_authorized")):
        raise GazaPublicationError("publication request grants an unrelated authority")
    if _dirty_except(repo_root, allowed_dirty_path):
        raise GazaPublicationError("source working tree must be clean")
    try:
        subprocess.run(["git", "merge-base", "--is-ancestor", request["source_approval_commit"], "HEAD"], cwd=repo_root, check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        raise GazaPublicationError("source approval commit is not an ancestor of the current source HEAD") from exc
    if _git(pages_root, "rev-parse", "HEAD") != request["pages_head"]:
        raise GazaPublicationError("Pages target head is stale")
    approval, approval_commit = _approval(repo_root, request)
    package_root = Path(_required(request, "package_root")).resolve(strict=True)
    manifest, rows, diff = _manifest(package_root, request, pages_root)
    auth = {
        "schema_version": SCHEMA,
        "publication_type": "gaza_edition_correction_publication_authorization",
        "correction_id": request["correction_id"],
        "authorized_by": request["authorized_by"],
        "authorized_at": request["authorized_at"],
        "source_approval_commit": request["source_approval_commit"],
        "approval_commit": approval_commit,
        "approval_path": request["approval_path"],
        "approval_fingerprint": request["approval_fingerprint"],
        "proposal_sha256": request["proposal_sha256"],
        "pages_head": request["pages_head"],
        "package_manifest_sha256": request["package_manifest_sha256"],
        "package_artifact_set_sha256": request["package_artifact_set_sha256"],
        "rendered_audio_sha256": request["rendered_audio_sha256"],
        "package_root": str(Path(_required(request, "package_root")).resolve()),
        "publication_authorized": True,
        "pages_authorized": True,
        "social_authorized": False,
        "source_changes_authorized": False,
        "audio_authorized": False,
        "publication_executed": False,
        "artifact_count": len(rows),
        "affected_files": diff,
        "authorization_fingerprint": "",
    }
    _required(auth, "authorized_by")
    datetime.fromisoformat(_required(auth, "authorized_at").replace("Z", "+00:00"))
    auth["authorization_fingerprint"] = _fingerprint({key: value for key, value in auth.items() if key != "authorization_fingerprint"})
    return {"status": "validated_gaza_publication_authorization", "authorization": auth, "approval": approval, "manifest": manifest, "diff": diff}


def create_authorization(repo_root: Path, pages_root: Path, request_path: Path, output_path: Path | None = None) -> dict[str, Any]:
    requested = _read(request_path.resolve(strict=True))
    if output_path is None and requested.get("correction_id"):
        output_path = repo_root / ROOT / f"{requested['correction_id']}-publication-v1.json"
    result = validate_request(repo_root, pages_root, request_path, allowed_dirty_path=output_path if output_path and output_path.exists() else None)
    if output_path is None:
        output_path = repo_root / ROOT / f"{result['authorization']['correction_id']}-publication-v1.json"
    output_path = output_path.resolve()
    if repo_root.resolve() not in output_path.parents:
        raise GazaPublicationError("authorization output must be inside the source repository")
    payload = result["authorization"]
    if output_path.exists():
        if _read(output_path) == payload:
            return {"status": "idempotent_noop", "authorization_path": str(output_path), "authorization": payload}
        raise GazaPublicationError("publication authorization already exists with different content")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"status": "publication_authorization_written", "authorization_path": str(output_path), "authorization": payload}


def _validate_authorization(repo_root: Path, pages_root: Path, authorization_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    authorization = _read(authorization_path.resolve(strict=True))
    if authorization.get("schema_version") != SCHEMA or authorization.get("publication_type") != "gaza_edition_correction_publication_authorization":
        raise GazaPublicationError("publication authorization schema is invalid")
    recorded = str(authorization.get("authorization_fingerprint") or "")
    unsigned = {key: value for key, value in authorization.items() if key != "authorization_fingerprint"}
    if recorded != _fingerprint(unsigned):
        raise GazaPublicationError("publication authorization fingerprint differs")
    if authorization.get("publication_authorized") is not True or authorization.get("pages_authorized") is not True:
        raise GazaPublicationError("publication authorization does not grant Pages publication")
    if any(authorization.get(field) is not False for field in ("social_authorized", "source_changes_authorized", "audio_authorized")):
        raise GazaPublicationError("publication authorization grants an unrelated authority")
    if authorization.get("publication_executed") is not False:
        raise GazaPublicationError("publication_executed is receipt state, not execution permission")
    if _dirty_except(repo_root, authorization_path):
        raise GazaPublicationError("source working tree must be clean")
    if _git(pages_root, "status", "--porcelain"):
        raise GazaPublicationError("Pages target is dirty")
    pages_head = _git(pages_root, "rev-parse", "HEAD")
    if pages_head != authorization.get("pages_head"):
        raise GazaPublicationError("Pages target head is stale")
    package_root = Path(_required(authorization, "package_root")).resolve(strict=True)
    expected: list[dict[str, Any]] = []
    for row in authorization.get("affected_files") or []:
        if not isinstance(row, dict):
            raise GazaPublicationError("authorization affected_files is malformed")
        public_path = _required(row, "public_path")
        staged_sha = _required(row, "staged_sha256")
        staged = _repo_path(package_root, public_path, "staged public path")
        if not staged.is_file() or _sha(staged.read_bytes()) != staged_sha:
            raise GazaPublicationError(f"staged artifact hash mismatch: {public_path}")
        live = _repo_path(pages_root, public_path, "Pages public path")
        prior_bytes = live.read_bytes() if live.is_file() else None
        prior_sha = _sha(prior_bytes) if prior_bytes is not None else None
        if prior_sha != row.get("prior_sha256"):
            raise GazaPublicationError(f"Pages prior hash mismatch: {public_path}")
        expected.append({"public_path": public_path, "staged": staged, "live": live, "prior_bytes": prior_bytes, "prior_sha256": prior_sha, "staged_sha256": staged_sha, "action": row.get("action")})
    if len(expected) != int(authorization.get("artifact_count") or 0):
        raise GazaPublicationError("authorization artifact count mismatch")
    return authorization, expected, pages_head


def execute_authorization(repo_root: Path, pages_root: Path, authorization_path: Path, receipt_path: Path, *, dry_run: bool = True) -> dict[str, Any]:
    receipt_path = receipt_path.resolve()
    if receipt_path.exists():
        prior = _read(receipt_path)
        if prior.get("publication_executed") is True:
            raise GazaPublicationError("publication authorization was already executed")
    authorization, files, pages_head = _validate_authorization(repo_root.resolve(strict=True), pages_root.resolve(strict=True), authorization_path)
    receipt = {
        "schema_version": "gaza_edition_correction_publication_receipt_v1",
        "correction_id": authorization["correction_id"],
        "authorization_fingerprint": authorization["authorization_fingerprint"],
        "pages_head_before": pages_head,
        "publication_executed": False,
        "rollback_evidence": [{"public_path": row["public_path"], "prior_sha256": row["prior_sha256"], "staged_sha256": row["staged_sha256"], "action": row["action"]} for row in files],
    }
    if dry_run:
        receipt["status"] = "dry_run_ready"
        return {"status": "dry_run_ready", "receipt": receipt, "authorization": authorization}
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt["status"] = "prepared"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        for row in files:
            row["live"].parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(row["staged"], row["live"])
        receipt["publication_executed"] = True
        receipt["status"] = "executed"
    except Exception as exc:
        for row in files:
            if row["prior_bytes"] is None:
                row["live"].unlink(missing_ok=True)
            else:
                row["live"].write_bytes(row["prior_bytes"])
        receipt["status"] = "rolled_back"
        receipt["error"] = str(exc)
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"status": receipt["status"], "receipt": receipt, "authorization": authorization}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Authorize a verified Gaza correction package for Pages publication.")
    parser.add_argument("--mode", choices=("dry-run", "authorize", "execute"), required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--pages-root", type=Path, required=True)
    parser.add_argument("--request", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--authorization", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.mode == "execute":
            if args.authorization is None or args.receipt is None:
                raise GazaPublicationError("--authorization and --receipt are required for execute")
            result = execute_authorization(args.repo_root, args.pages_root, args.authorization, args.receipt, dry_run=args.dry_run)
        else:
            if args.request is None:
                raise GazaPublicationError("--request is required for dry-run and authorize")
            result = validate_request(args.repo_root, args.pages_root, args.request)
        if args.mode == "authorize":
            result = create_authorization(args.repo_root, args.pages_root, args.request, args.output)
        else:
            result["status"] = "dry_run_validated"
        print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}, indent=2))
        return 1





