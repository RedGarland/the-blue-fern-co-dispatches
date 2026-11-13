from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from bluefern_dispatches.gaza_pages_preservation import (
    GazaPagesPreservationError,
    assert_no_update_pages_preserve_baseline,
)

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


def _validate_no_update_preservation(
    *,
    manifest: dict[str, Any],
    package_root: Path,
    pages_root: Path,
    rows: list[dict[str, Any]],
    diff: list[dict[str, Any]],
) -> dict[str, dict[str, Any]] | None:
    """Run the whole-document guard for a Gaza no-update package.

    The contract is intentionally opt-in only when the manifest explicitly
    declares ``package_type=gaza_no_update``.  Ordinary correction fixtures
    and packages remain unaffected, while a no-update package cannot silently
    omit its landing/archive paths, baseline, candidate, edition-date, or hash
    bindings.
    """

    required_paths = {"gaza/index.html", "gaza/archive.html"}
    package_type = str(manifest.get("package_type") or "").strip()
    package_paths = {str(row.get("public_path") or "") for row in rows}
    no_update_paths = {
        str(row.get("public_path") or "")
        for row in rows
        if str(row.get("role") or "").strip().lower().startswith("gaza_no_update")
        or str(row.get("public_path") or "") in required_paths
    }
    if package_type != "gaza_no_update":
        if no_update_paths and (
            manifest.get("no_update_preservation") is not None
            or any(
                str(row.get("role") or "").strip().lower().startswith("gaza_no_update")
                for row in rows
            )
        ):
            raise GazaPublicationError(
                "staged no-update Gaza package must explicitly declare package_type=gaza_no_update"
            )
        return None
    if no_update_paths and not required_paths.issubset(package_paths):
        raise GazaPublicationError(
            "staged no-update Gaza package must include both landing and archive pages"
        )
    if not required_paths.issubset(package_paths):
        raise GazaPublicationError("staged no-update Gaza package must include both landing and archive pages")

    contract = manifest.get("no_update_preservation")
    if not isinstance(contract, dict):
        raise GazaPublicationError("staged no-update Gaza package is missing preservation contract")
    edition_date = str(contract.get("edition_date") or "").strip()
    if not edition_date:
        raise GazaPublicationError("staged no-update Gaza package is missing preservation edition_date")
    if contract.get("allow_idempotent") is not True:
        raise GazaPublicationError("staged no-update Gaza package must explicitly allow idempotent retry")

    baseline_hashes = contract.get("baseline_sha256")
    candidate_hashes = contract.get("candidate_sha256")
    if not isinstance(baseline_hashes, dict) or not isinstance(candidate_hashes, dict):
        raise GazaPublicationError("staged no-update Gaza package is missing preservation hash bindings")
    if set(baseline_hashes) != required_paths or set(candidate_hashes) != required_paths:
        raise GazaPublicationError("staged no-update Gaza preservation hash bindings are incomplete")
    expected_new_row_html = contract.get("expected_new_row_html")
    if not isinstance(expected_new_row_html, dict) or set(expected_new_row_html) != required_paths:
        raise GazaPublicationError("staged no-update Gaza package is missing exact new-row markup bindings")

    diff_by_path = {str(row["public_path"]): row for row in diff}
    for path in required_paths:
        if diff_by_path[path]["prior_sha256"] != str(baseline_hashes[path]).removeprefix("sha256:"):
            raise GazaPublicationError(f"staged no-update Gaza baseline hash binding differs: {path}")
        if diff_by_path[path]["staged_sha256"] != str(candidate_hashes[path]).removeprefix("sha256:"):
            raise GazaPublicationError(f"staged no-update Gaza candidate hash binding differs: {path}")

    baseline_pages: dict[str, str] = {}
    candidate_pages: dict[str, str] = {}
    for path in required_paths:
        live = pages_root / path
        staged = package_root / path
        if not live.is_file():
            raise GazaPublicationError(f"staged no-update Gaza baseline page is missing: {path}")
        # Decode bytes directly so the hash binding is over the exact staged
        # and live bytes; Path.read_text() may apply platform newline
        # translation on Windows before the guard hashes the text.
        baseline_pages[path] = live.read_bytes().decode("utf-8")
        candidate_pages[path] = staged.read_bytes().decode("utf-8")
    try:
        return assert_no_update_pages_preserve_baseline(
            baseline_pages=baseline_pages,
            candidate_pages=candidate_pages,
            edition_date=edition_date,
            allow_idempotent=True,
            expected_baseline_sha256=baseline_hashes,
            expected_candidate_sha256=candidate_hashes,
            expected_new_row_html=expected_new_row_html,
        )
    except GazaPagesPreservationError as exc:
        raise GazaPublicationError(str(exc)) from exc


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
    preservation = _validate_no_update_preservation(
        manifest=manifest,
        package_root=package_root,
        pages_root=pages_root,
        rows=rows,
        diff=diff,
    )
    if preservation is not None:
        # Keep the validation in the in-memory result for review and receipts;
        # it is not written back into the staged package or Pages.
        manifest["preservation_validation"] = preservation
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
        "proposal_source_commit": request["proposal_source_commit"],
        "preview_set_sha256": request["preview_set_sha256"],
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
    for field in ("source_approval_commit", "proposal_source_commit", "approval_commit", "approval_path", "approval_fingerprint", "proposal_sha256", "correction_id", "pages_head", "preview_set_sha256", "package_manifest_sha256", "package_artifact_set_sha256", "rendered_audio_sha256", "package_root"):
        _required(authorization, field)
    if _dirty_except(repo_root, authorization_path):
        raise GazaPublicationError("source working tree must be clean")
    request = {
        "source_approval_commit": authorization["source_approval_commit"],
        "proposal_source_commit": authorization["proposal_source_commit"],
        "approval_ref": authorization["approval_commit"],
        "approval_commit": authorization["approval_commit"],
        "approval_path": authorization["approval_path"],
        "approval_fingerprint": authorization["approval_fingerprint"],
        "proposal_sha256": authorization["proposal_sha256"],
        "correction_id": authorization["correction_id"],
        "pages_head": authorization["pages_head"],
        "preview_set_sha256": authorization["preview_set_sha256"],
        "package_manifest_sha256": authorization["package_manifest_sha256"],
        "package_artifact_set_sha256": authorization["package_artifact_set_sha256"],
        "rendered_audio_sha256": authorization["rendered_audio_sha256"],
        "package_root": authorization["package_root"],
    }
    try:
        subprocess.run(["git", "merge-base", "--is-ancestor", request["source_approval_commit"], "HEAD"], cwd=repo_root, check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        raise GazaPublicationError("source approval commit is not an ancestor of the current source HEAD") from exc
    approval, _ = _approval(repo_root, request)
    package_root = Path(_required(authorization, "package_root")).resolve(strict=True)
    manifest, rows, diff = _manifest(package_root, request, pages_root)
    if diff != authorization.get("affected_files"):
        raise GazaPublicationError("authorization path or hash bindings differ at execution time")
    if len(rows) != int(authorization.get("artifact_count") or 0):
        raise GazaPublicationError("authorization artifact count mismatch")
    expected: list[dict[str, Any]] = []
    for row in diff:
        staged = _repo_path(package_root, row["public_path"], "staged public path")
        live = _repo_path(pages_root, row["public_path"], "Pages public path")
        staged_bytes = staged.read_bytes()
        if _sha(staged_bytes) != row["staged_sha256"]:
            raise GazaPublicationError(f"staged artifact changed during execution validation: {row['public_path']}")
        prior_bytes = live.read_bytes() if live.is_file() else None
        if (None if prior_bytes is None else _sha(prior_bytes)) != row["prior_sha256"]:
            raise GazaPublicationError(f"Pages target changed during execution validation: {row['public_path']}")
        expected.append({"public_path": row["public_path"], "staged": staged, "live": live, "prior_bytes": prior_bytes, "prior_sha256": row["prior_sha256"], "staged_sha256": row["staged_sha256"], "action": row["action"]})
        expected[-1]["staged_bytes"] = staged_bytes
    return authorization, expected, request["pages_head"]


def _recheck_execution_inputs(pages_root: Path, files: list[dict[str, Any]], pages_head: str) -> None:
    """Fail closed if the validated Pages baseline or staged bytes drifted."""

    if _git(pages_root, "rev-parse", "HEAD") != pages_head:
        raise GazaPublicationError("Pages target head changed immediately before write")
    if _git(pages_root, "status", "--porcelain"):
        raise GazaPublicationError("Pages target became dirty immediately before write")
    for row in files:
        _recheck_execution_row(row)


def _recheck_execution_row(row: dict[str, Any]) -> None:
    public_path = str(row["public_path"])
    live = row["live"]
    current_live = live.read_bytes() if live.is_file() else None
    current_live_sha = None if current_live is None else _sha(current_live)
    if current_live_sha != row["prior_sha256"]:
        raise GazaPublicationError(f"Pages target changed immediately before write: {public_path}")
    staged = row["staged"]
    if not staged.is_file():
        raise GazaPublicationError(f"staged artifact disappeared immediately before write: {public_path}")
    if _sha(staged.read_bytes()) != row["staged_sha256"]:
        raise GazaPublicationError(f"staged artifact changed immediately before write: {public_path}")


def _write_validated_bytes(path: Path, content: bytes) -> None:
    """Write the immutable validation snapshot, never reread staged content."""

    path.write_bytes(content)


@contextmanager
def _pages_execution_lock(pages_root: Path):
    """Coordinate cooperating local publishers without changing repo permissions."""

    lock_name = "bluefern-gaza-pages-" + _sha(str(pages_root.resolve()).encode("utf-8")) + ".lock"
    lock_path = Path(tempfile.gettempdir()) / lock_name
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        locked = False
        try:
            if os.name == "nt":
                import msvcrt

                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise GazaPublicationError("Pages execution lock is held") from exc
            else:
                import fcntl

                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    raise GazaPublicationError("Pages execution lock is held") from exc
            locked = True
            yield
        finally:
            if locked:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def execute_authorization(repo_root: Path, pages_root: Path, authorization_path: Path, receipt_path: Path, *, dry_run: bool = True) -> dict[str, Any]:
    resolved_pages_root = pages_root.resolve(strict=True)
    with _pages_execution_lock(resolved_pages_root):
        return _execute_authorization_unlocked(repo_root, resolved_pages_root, authorization_path, receipt_path, dry_run=dry_run)


def _execute_authorization_unlocked(repo_root: Path, pages_root: Path, authorization_path: Path, receipt_path: Path, *, dry_run: bool = True) -> dict[str, Any]:
    receipt_path = receipt_path.resolve()
    if receipt_path.exists():
        prior = _read(receipt_path)
        if prior.get("local_application_executed") is True:
            raise GazaPublicationError(
                "publication authorization retry rejected: receipt records local_application_executed=true "
                "(already executed); no idempotent re-application is claimed"
            )
    authorization, files, pages_head = _validate_authorization(repo_root.resolve(strict=True), pages_root.resolve(strict=True), authorization_path)
    _recheck_execution_inputs(pages_root, files, pages_head)
    receipt = {
        "schema_version": "gaza_edition_correction_publication_receipt_v1",
        "correction_id": authorization["correction_id"],
        "authorization_fingerprint": authorization["authorization_fingerprint"],
        "pages_head_before": pages_head,
        "publication_executed": False,
        "local_application_executed": False,
        "pages_commit_created": False,
        "pages_pushed": False,
        "live_verified": False,
        "publication_state": "not_published",
        "rollback_evidence": [{"public_path": row["public_path"], "prior_sha256": row["prior_sha256"], "staged_sha256": row["staged_sha256"], "action": row["action"]} for row in files],
    }
    if dry_run:
        receipt["status"] = "dry_run_ready"
        return {"status": "dry_run_ready", "receipt": receipt, "authorization": authorization}
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt["status"] = "prepared"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    attempted: list[dict[str, Any]] = []
    try:
        # Recheck after preparing the receipt and immediately before applying
        # any Pages bytes. The staged path is checked for drift, but the
        # immutable snapshot captured during authorization is what gets used.
        _recheck_execution_inputs(pages_root, files, pages_head)
        for row in files:
            _recheck_execution_row(row)
            row["live"].parent.mkdir(parents=True, exist_ok=True)
            attempted.append(row)
            _write_validated_bytes(row["live"], row["staged_bytes"])
        receipt["local_application_executed"] = True
        receipt["status"] = "local_applied_uncommitted"
        receipt["publication_state"] = "local_applied_uncommitted"
    except Exception as exc:
        for row in attempted:
            if row["prior_bytes"] is None:
                row["live"].unlink(missing_ok=True)
            else:
                row["live"].write_bytes(row["prior_bytes"])
        receipt["status"] = "rolled_back"
        receipt["publication_state"] = "rolled_back_local_application"
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
        if args.mode == "authorize":
            if args.request is None:
                raise GazaPublicationError("--request is required for authorize")
            result = create_authorization(args.repo_root, args.pages_root, args.request, args.output)
        elif args.mode == "execute":
            if args.authorization is None or args.receipt is None:
                raise GazaPublicationError("--authorization and --receipt are required for execute")
            result = execute_authorization(args.repo_root, args.pages_root, args.authorization, args.receipt, dry_run=args.dry_run)
        else:
            if args.request is None:
                raise GazaPublicationError("--request is required for dry-run")
            result = validate_request(args.repo_root, args.pages_root, args.request)
            result["status"] = "dry_run_validated"
        print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
        if result.get("status") == "rolled_back":
            return 1
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}, indent=2))
        return 1





