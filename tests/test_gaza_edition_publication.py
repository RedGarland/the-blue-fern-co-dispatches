import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from bluefern_dispatches.gaza_edition_publication import GazaPublicationError, _fingerprint, create_authorization, execute_authorization, validate_request


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def setup_case(tmp_path: Path) -> tuple[Path, Path, Path, dict]:
    src = tmp_path / "src"; pages = tmp_path / "pages"; pkg = tmp_path / "pkg"
    for root in (src, pages):
        root.mkdir(); git(root, "init", "-q"); git(root, "config", "user.email", "test@example.com"); git(root, "config", "user.name", "Test")
        (root / "seed.txt").write_text("seed\n", encoding="utf-8"); git(root, "add", "."); git(root, "commit", "-qm", "seed")
    source_commit = git(src, "rev-parse", "HEAD"); pages_head = git(pages, "rev-parse", "HEAD")
    pkg.mkdir(); (pkg / "index.html").write_text("corrected\n", encoding="utf-8")
    staged_sha = hashlib.sha256((pkg / "index.html").read_bytes()).hexdigest()
    manifest = {"status":"staged_edition_correction", "publication_authorized":False, "approval_commit":"APPROVAL", "pages_head":pages_head, "package_artifact_set_sha256":"sha256:artifact", "rendered_audio_sha256":"audio", "correction_id":"gaza-edition-correction-v1-test", "representations":[{"public_path":"index.html","role":"root_index","sha256":staged_sha}]}
    (pkg / "package_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    manifest_sha = hashlib.sha256((pkg / "package_manifest.json").read_bytes()).hexdigest()
    approval = {"schema_version":"gaza_edition_correction_release_approval_v1", "scope":"gaza_edition_correction", "approval_id":"a", "proposal_sha256":"sha256:proposal", "correction_id":manifest["correction_id"], "source_commit":source_commit, "pages_head":pages_head, "preview_set_sha256":"sha256:preview", "audio_request_sha256":"sha256:audio-request", "approved_at":"2026-10-15T00:00:00Z", "approver":"William Patton", "package_authorized":True, "audio_authorized":True, "publication_authorized":False}
    approval["approval_fingerprint"] = _fingerprint(approval)
    approval_path = "approvals/gaza/edition/a.json"; full = src / approval_path; full.parent.mkdir(parents=True); full.write_text(json.dumps(approval), encoding="utf-8")
    git(src, "add", "."); git(src, "commit", "-qm", "approval")
    approval_commit = git(src, "rev-parse", "HEAD"); manifest["approval_commit"] = approval_commit; (pkg / "package_manifest.json").write_text(json.dumps(manifest), encoding="utf-8"); manifest_sha = hashlib.sha256((pkg / "package_manifest.json").read_bytes()).hexdigest()
    request = {"schema_version":"gaza_edition_correction_publication_request_v1", "correction_id":manifest["correction_id"], "authorized_by":"William Patton", "authorized_at":"2026-10-15T01:00:00Z", "source_approval_commit":approval_commit, "proposal_source_commit":source_commit, "approval_ref":approval_commit, "approval_commit":approval_commit, "approval_path":approval_path, "approval_fingerprint":approval["approval_fingerprint"], "preview_set_sha256":approval["preview_set_sha256"], "proposal_sha256":approval["proposal_sha256"], "pages_head":pages_head, "package_manifest_sha256":"sha256:"+manifest_sha, "package_artifact_set_sha256":manifest["package_artifact_set_sha256"], "rendered_audio_sha256":manifest["rendered_audio_sha256"], "package_root":str(pkg), "publication_authorized":True, "pages_authorized":True, "social_authorized":False, "source_changes_authorized":False, "audio_authorized":False}
    request_path = tmp_path / "request.json"; request_path.write_text(json.dumps(request), encoding="utf-8")
    return src, pages, request_path, request


def test_dry_run_validates_exact_bindings_and_diff(tmp_path: Path) -> None:
    src, pages, request_path, _ = setup_case(tmp_path)
    result = validate_request(src, pages, request_path)
    assert result["status"] == "validated_gaza_publication_authorization"
    assert result["diff"][0]["action"] == "add"
    assert result["authorization"]["pages_authorized"] is True


def test_rejects_stale_pages_head(tmp_path: Path) -> None:
    src, pages, request_path, request = setup_case(tmp_path)
    request["pages_head"] = "0" * 40; request_path.write_text(json.dumps(request), encoding="utf-8")
    with pytest.raises(GazaPublicationError, match="Pages target head is stale"):
        validate_request(src, pages, request_path)


def test_rejects_altered_or_extra_staged_file(tmp_path: Path) -> None:
    src, pages, request_path, request = setup_case(tmp_path)
    pkg = Path(request["package_root"]); (pkg / "index.html").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(GazaPublicationError, match="staged artifact hash mismatch"):
        validate_request(src, pages, request_path)
    (pkg / "index.html").write_text("corrected\n", encoding="utf-8"); (pkg / "extra.txt").write_text("x", encoding="utf-8")
    with pytest.raises(GazaPublicationError, match="unauthorized files"):
        validate_request(src, pages, request_path)


def test_authorize_is_idempotent_and_keeps_nonpublication_channels_false(tmp_path: Path) -> None:
    src, pages, request_path, _ = setup_case(tmp_path)
    output = src / "publication-authorizations/gaza/edition/test.json"
    first = create_authorization(src, pages, request_path, output)
    assert first["status"] == "publication_authorization_written"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["publication_authorized"] is True
    assert payload["pages_authorized"] is True
    assert payload["social_authorized"] is False
    assert payload["publication_executed"] is False
    second = create_authorization(src, pages, request_path, output)
    assert second["status"] == "idempotent_noop"
    (src / "unrelated.txt").write_text("drift\n", encoding="utf-8")
    with pytest.raises(GazaPublicationError, match="source working tree must be clean"):
        create_authorization(src, pages, request_path, output)


def test_rejects_mismatched_approval_commit(tmp_path: Path) -> None:
    src, pages, request_path, request = setup_case(tmp_path)
    request["approval_ref"] = git(src, "rev-parse", "HEAD^")
    request_path.write_text(json.dumps(request), encoding="utf-8")
    with pytest.raises(GazaPublicationError, match="approval reference and source approval commit mismatch"):
        validate_request(src, pages, request_path)


def test_rejects_omitted_approval_binding(tmp_path: Path) -> None:
    src, pages, request_path, request = setup_case(tmp_path)
    del request["preview_set_sha256"]
    request_path.write_text(json.dumps(request), encoding="utf-8")
    with pytest.raises(GazaPublicationError, match="preview_set_sha256 is required"):
        validate_request(src, pages, request_path)


def test_rejects_omitted_manifest_approval_commit_binding(tmp_path: Path) -> None:
    src, pages, request_path, request = setup_case(tmp_path)
    del request["approval_commit"]
    request_path.write_text(json.dumps(request), encoding="utf-8")
    with pytest.raises(GazaPublicationError, match="approval_commit is required"):
        validate_request(src, pages, request_path)


def test_rejects_extra_changes_in_approval_commit(tmp_path: Path) -> None:
    src, pages, request_path, request = setup_case(tmp_path)
    (src / "unrelated.txt").write_text("not approval\n", encoding="utf-8")
    git(src, "add", "unrelated.txt")
    git(src, "commit", "--amend", "--no-edit", "-q")
    approval_commit = git(src, "rev-parse", "HEAD")
    request["source_approval_commit"] = approval_commit
    request["approval_ref"] = approval_commit
    request["approval_commit"] = approval_commit
    request_path.write_text(json.dumps(request), encoding="utf-8")
    with pytest.raises(GazaPublicationError, match="changes outside the approval artifact"):
        validate_request(src, pages, request_path)


def test_rejects_authorization_field_tampering(tmp_path: Path) -> None:
    src, pages, request_path, _ = setup_case(tmp_path)
    authorization_path = src / "publication-authorizations/gaza/edition/test.json"
    create_authorization(src, pages, request_path, authorization_path)
    payload = json.loads(authorization_path.read_text(encoding="utf-8"))
    payload["publication_executed"] = True
    authorization_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(GazaPublicationError, match="authorization fingerprint differs"):
        execute_authorization(src, pages, authorization_path, tmp_path / "receipt.json")


def test_executor_dry_run_has_exact_diff_and_repeat_receipt_guard(tmp_path: Path) -> None:
    src, pages, request_path, _ = setup_case(tmp_path)
    authorization_path = src / "publication-authorizations/gaza/edition/test.json"
    create_authorization(src, pages, request_path, authorization_path)
    receipt_path = tmp_path / "receipt.json"
    result = execute_authorization(src, pages, authorization_path, receipt_path)
    assert result["status"] == "dry_run_ready"
    assert result["receipt"]["publication_executed"] is False
    assert not receipt_path.exists()

    receipt_path.write_text(json.dumps({"publication_executed": True}), encoding="utf-8")
    with pytest.raises(GazaPublicationError, match="already executed"):
        execute_authorization(src, pages, authorization_path, receipt_path)


def test_executor_applies_only_bound_files_and_records_rollback_evidence(tmp_path: Path) -> None:
    src, pages, request_path, _ = setup_case(tmp_path)
    authorization_path = src / "publication-authorizations/gaza/edition/test.json"
    create_authorization(src, pages, request_path, authorization_path)
    receipt_path = tmp_path / "receipt.json"
    result = execute_authorization(src, pages, authorization_path, receipt_path, dry_run=False)
    assert result["status"] == "executed"
    assert (pages / "index.html").read_text(encoding="utf-8") == "corrected\n"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["publication_executed"] is True
    assert receipt["rollback_evidence"][0]["public_path"] == "index.html"
    assert receipt["rollback_evidence"][0]["prior_sha256"] is None


def test_executor_rejects_target_head_drift_before_mutation(tmp_path: Path) -> None:
    src, pages, request_path, _ = setup_case(tmp_path)
    authorization_path = src / "publication-authorizations/gaza/edition/test.json"
    create_authorization(src, pages, request_path, authorization_path)
    (pages / "drift.txt").write_text("drift\n", encoding="utf-8")
    git(pages, "add", "drift.txt"); git(pages, "commit", "-qm", "drift")
    before = (pages / "seed.txt").read_text(encoding="utf-8")
    with pytest.raises(GazaPublicationError, match="Pages target head is stale"):
        execute_authorization(src, pages, authorization_path, tmp_path / "receipt.json")
    assert (pages / "seed.txt").read_text(encoding="utf-8") == before




