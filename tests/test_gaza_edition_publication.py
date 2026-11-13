import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from bluefern_dispatches import gaza_edition_publication as publication
from bluefern_dispatches.gaza_edition_publication import GazaPublicationError, _fingerprint, create_authorization, execute_authorization, main, validate_request


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


def test_cli_authorize_retry_is_idempotent_with_untracked_authorization(tmp_path: Path) -> None:
    src, pages, request_path, _ = setup_case(tmp_path)
    output = src / "publication-authorizations/gaza/edition/test.json"
    argv = ["--mode", "authorize", "--repo-root", str(src), "--pages-root", str(pages), "--request", str(request_path), "--output", str(output)]
    assert main(argv) == 0
    assert main(argv) == 0
    assert output.is_file()


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

    receipt_path.write_text(json.dumps({"local_application_executed": True}), encoding="utf-8")
    with pytest.raises(GazaPublicationError, match="already executed.*no idempotent re-application"):
        execute_authorization(src, pages, authorization_path, receipt_path)


def test_executor_applies_only_bound_files_and_records_rollback_evidence(tmp_path: Path) -> None:
    src, pages, request_path, _ = setup_case(tmp_path)
    authorization_path = src / "publication-authorizations/gaza/edition/test.json"
    create_authorization(src, pages, request_path, authorization_path)
    receipt_path = tmp_path / "receipt.json"
    result = execute_authorization(src, pages, authorization_path, receipt_path, dry_run=False)
    assert result["status"] == "local_applied_uncommitted"
    assert (pages / "index.html").read_text(encoding="utf-8") == "corrected\n"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["publication_executed"] is False
    assert receipt["local_application_executed"] is True
    assert receipt["pages_commit_created"] is False
    assert receipt["pages_pushed"] is False
    assert receipt["live_verified"] is False
    assert receipt["rollback_evidence"][0]["public_path"] == "index.html"
    assert receipt["rollback_evidence"][0]["prior_sha256"] is None


def test_cli_buffer_write_failure_returns_nonzero_and_preserves_receipt_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    src, pages, request_path, _ = setup_case(tmp_path)
    authorization_path = src / "publication-authorizations/gaza/edition/test.json"
    create_authorization(src, pages, request_path, authorization_path)
    receipt_path = tmp_path / "receipt.json"

    def fail_write(*args: object, **kwargs: object) -> None:
        raise OSError("injected buffer write failure")

    monkeypatch.setattr(publication, "_write_validated_bytes", fail_write)
    code = main(["--mode", "execute", "--repo-root", str(src), "--pages-root", str(pages), "--authorization", str(authorization_path), "--receipt", str(receipt_path)])
    assert code == 1
    output = capsys.readouterr().out
    assert '"status": "rolled_back"' in output
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["publication_executed"] is False
    assert receipt["local_application_executed"] is False
    assert receipt["publication_state"] == "rolled_back_local_application"
    assert not (pages / "index.html").exists()


def test_executor_rejects_staged_path_drift_before_any_page_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src, pages, request_path, request = setup_case(tmp_path)
    authorization_path = src / "publication-authorizations/gaza/edition/test.json"
    create_authorization(src, pages, request_path, authorization_path)
    staged = Path(request["package_root"]) / "index.html"
    receipt_path = tmp_path / "receipt.json"
    calls = 0
    writes: list[Path] = []
    original_recheck = publication._recheck_execution_inputs

    def mutate_after_receipt(pages_root: Path, files: list[dict[str, object]], pages_head: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            staged.write_text("tampered after authorization\n", encoding="utf-8")
        original_recheck(pages_root, files, pages_head)

    monkeypatch.setattr(publication, "_recheck_execution_inputs", mutate_after_receipt)
    monkeypatch.setattr(publication, "_write_validated_bytes", lambda path, content: writes.append(path))
    result = execute_authorization(src, pages, authorization_path, receipt_path, dry_run=False)

    assert result["status"] == "rolled_back"
    assert writes == []
    assert not (pages / "index.html").exists()
    assert "staged artifact changed immediately before write" in result["receipt"]["error"]


def test_executor_rechecks_target_baseline_before_each_buffer_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src, pages, request_path, request = setup_case(tmp_path)
    authorization_path = src / "publication-authorizations/gaza/edition/test.json"
    create_authorization(src, pages, request_path, authorization_path)
    receipt_path = tmp_path / "receipt.json"
    calls = 0
    writes: list[Path] = []
    original_recheck = publication._recheck_execution_row

    def mutate_target_before_row_check(row: dict[str, object]) -> None:
        nonlocal calls
        calls += 1
        if calls == 3:
            Path(row["live"]).write_text("external target drift\n", encoding="utf-8")
        original_recheck(row)

    monkeypatch.setattr(publication, "_recheck_execution_row", mutate_target_before_row_check)
    monkeypatch.setattr(publication, "_write_validated_bytes", lambda path, content: writes.append(path))
    result = execute_authorization(src, pages, authorization_path, receipt_path, dry_run=False)

    assert result["status"] == "rolled_back"
    assert writes == []
    assert (pages / "index.html").read_text(encoding="utf-8") == "external target drift\n"
    assert "Pages target changed immediately before write" in result["receipt"]["error"]


def test_executor_rolls_back_all_attempted_files_after_multi_file_partial_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src, pages, request_path, request = setup_case(tmp_path)
    pkg = Path(request["package_root"])
    (pkg / "archive.html").write_text("archive corrected\n", encoding="utf-8")
    manifest_path = pkg / "package_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    archive_sha = hashlib.sha256((pkg / "archive.html").read_bytes()).hexdigest()
    manifest["representations"].append({"public_path": "archive.html", "role": "archive", "sha256": archive_sha})
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    request["package_manifest_sha256"] = "sha256:" + hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    request_path.write_text(json.dumps(request), encoding="utf-8")
    authorization_path = src / "publication-authorizations/gaza/edition/test.json"
    create_authorization(src, pages, request_path, authorization_path)
    receipt_path = tmp_path / "receipt.json"
    writes = 0

    def fail_on_second_write(path: Path, content: bytes) -> None:
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("injected second-file failure")
        path.write_bytes(content)

    monkeypatch.setattr(publication, "_write_validated_bytes", fail_on_second_write)
    result = execute_authorization(src, pages, authorization_path, receipt_path, dry_run=False)

    assert result["status"] == "rolled_back"
    assert writes == 2
    assert not (pages / "index.html").exists()
    assert not (pages / "archive.html").exists()
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["local_application_executed"] is False
    assert receipt["publication_state"] == "rolled_back_local_application"


def test_executor_revalidates_package_manifest_before_copy(tmp_path: Path) -> None:
    src, pages, request_path, request = setup_case(tmp_path)
    authorization_path = src / "publication-authorizations/gaza/edition/test.json"
    create_authorization(src, pages, request_path, authorization_path)
    package_manifest = Path(request["package_root"]) / "package_manifest.json"
    manifest = json.loads(package_manifest.read_text(encoding="utf-8"))
    manifest["approval_commit"] = "0" * 40
    package_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(GazaPublicationError, match="package manifest hash mismatch"):
        execute_authorization(src, pages, authorization_path, tmp_path / "receipt.json")


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




