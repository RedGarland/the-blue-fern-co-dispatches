from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from bluefern_dispatches import gaza_edition_publication as publication
from bluefern_dispatches.gaza_edition_publication import GazaPublicationError


CORRECTION_ID = "gaza-edition-correction-v1-test-preservation"
EXPECTED_INDEX_UPDATE = '<li class="briefing-row briefing-row--no-update"><span class="edition-date">2026-10-20</span><span class="no-update-label">No qualifying update</span></li>'
EXPECTED_ARCHIVE_UPDATE = '<li class="archive-row archive-row--no-update"><span class="edition-date">2026-10-20</span><span class="no-update-label">No qualifying update</span></li>'

BASE_INDEX = f"""<!doctype html><html><body>
<article class="gaza-latest-card"><p class="edition-date">2026-10-19</p><h3>Latest Gaza update</h3><p>Latest summary and attribution.</p></article>
<ul>
<li class="briefing-row briefing-row--readable"><span class="edition-date">2026-10-19</span><span class="briefing-row-summary">Latest summary and attribution.</span></li>
<li class="briefing-row briefing-row--readable"><span class="edition-date">2026-10-06</span><span class="briefing-row-summary">Historical summary that must remain unchanged.</span></li>
</ul>
<section class="formal-correction" id="{CORRECTION_ID}"><h2>Correction notice</h2><p>Saved evidence remains preserved.</p></section>
</body></html>"""

BASE_ARCHIVE = f"""<!doctype html><html><body>
<section class="archive-latest"><p class="archive-latest-date">2026-10-19</p><h2>Latest Gaza update</h2><p>Latest archive summary.</p></section>
<ul class="archive-list">
<li class="archive-row archive-row--daily"><span class="edition-date">2026-10-19</span><span class="archive-row-note">Latest archive summary.</span></li>
<li class="archive-row archive-row--daily"><span class="edition-date">2026-10-06</span><span class="archive-row-note">Historical archive summary that must remain unchanged.</span></li>
</ul>
<section class="formal-correction" id="{CORRECTION_ID}"><h2>Correction notice</h2><p>Saved evidence remains preserved.</p></section>
</body></html>"""


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _insert_valid(index: str, archive: str) -> tuple[str, str]:
    index_row = '<li class="briefing-row briefing-row--no-update"><span class="edition-date">2026-10-20</span><span class="no-update-label">No qualifying update</span></li>'
    archive_row = '<li class="archive-row archive-row--no-update"><span class="edition-date">2026-10-20</span><span class="no-update-label">No qualifying update</span></li>'
    return index.replace("</ul>", index_row + "</ul>", 1), archive.replace("</ul>", archive_row + "</ul>", 1)


def _write_package(pkg: Path, pages: Path, index: str, archive: str) -> dict:
    (pkg / "gaza").mkdir(parents=True, exist_ok=True)
    (pkg / "gaza" / "index.html").write_text(index, encoding="utf-8")
    (pkg / "gaza" / "archive.html").write_text(archive, encoding="utf-8")
    paths = ("gaza/index.html", "gaza/archive.html")
    rows = [{"public_path": path, "role": "gaza_no_update", "sha256": _sha(pkg / path)} for path in paths]
    baseline_hashes = {path: _sha(pages / path) for path in paths}
    candidate_hashes = {row["public_path"]: row["sha256"] for row in rows}
    manifest = {
        "status": "staged_edition_correction",
        "package_type": "gaza_no_update",
        "publication_authorized": False,
        "approval_commit": "approval",
        "pages_head": _git(pages, "rev-parse", "HEAD"),
        "package_artifact_set_sha256": "sha256:artifact",
        "rendered_audio_sha256": "audio",
        "correction_id": CORRECTION_ID,
        "representations": rows,
        "no_update_preservation": {
            "edition_date": "2026-10-20",
            "allow_idempotent": True,
            "baseline_sha256": baseline_hashes,
            "candidate_sha256": candidate_hashes,
            "expected_new_row_html": {
                "gaza/index.html": EXPECTED_INDEX_UPDATE,
                "gaza/archive.html": EXPECTED_ARCHIVE_UPDATE,
            },
        },
    }
    manifest_path = pkg / "package_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return {
        "package_manifest_sha256": "sha256:" + _sha(manifest_path),
        "approval_commit": "approval",
        "pages_head": manifest["pages_head"],
        "package_artifact_set_sha256": manifest["package_artifact_set_sha256"],
        "rendered_audio_sha256": manifest["rendered_audio_sha256"],
        "correction_id": CORRECTION_ID,
    }


def _setup(tmp_path: Path, index: str, archive: str) -> tuple[Path, Path, dict]:
    pages = tmp_path / "pages"
    pkg = tmp_path / "pkg"
    pages.mkdir()
    _git(pages, "init", "-q")
    _git(pages, "config", "user.email", "test@example.com")
    _git(pages, "config", "user.name", "Test")
    (pages / "gaza").mkdir()
    (pages / "gaza" / "index.html").write_text(BASE_INDEX, encoding="utf-8")
    (pages / "gaza" / "archive.html").write_text(BASE_ARCHIVE, encoding="utf-8")
    _git(pages, "add", ".")
    _git(pages, "commit", "-qm", "baseline")
    pkg.mkdir()
    bindings = _write_package(pkg, pages, index, archive)
    request = {
        "package_manifest_sha256": bindings["package_manifest_sha256"],
        "approval_commit": bindings["approval_commit"],
        "pages_head": bindings["pages_head"],
        "package_artifact_set_sha256": bindings["package_artifact_set_sha256"],
        "rendered_audio_sha256": bindings["rendered_audio_sha256"],
        "correction_id": bindings["correction_id"],
    }
    return pages, pkg, request


def test_real_publication_manifest_preflight_rejects_incident_and_accepts_exact_preview(tmp_path: Path) -> None:
    valid_index, valid_archive = _insert_valid(BASE_INDEX, BASE_ARCHIVE)
    faulty_index = valid_index.replace(
        '<li class="briefing-row briefing-row--readable"><span class="edition-date">2026-10-06</span><span class="briefing-row-summary">Historical summary that must remain unchanged.</span></li>',
        '<li class="briefing-row briefing-row--readable"><span class="edition-date">2026-10-06</span><span class="briefing-row-summary">Rewritten historical summary.</span></li>',
    ).replace(f'<section class="formal-correction" id="{CORRECTION_ID}"><h2>Correction notice</h2><p>Saved evidence remains preserved.</p></section>', "")
    faulty_archive = valid_archive.replace(
        '<li class="archive-row archive-row--daily"><span class="edition-date">2026-10-06</span><span class="archive-row-note">Historical archive summary that must remain unchanged.</span></li>',
        '<li class="archive-row archive-row--daily"><span class="edition-date">2026-10-06</span><span class="archive-row-note">Rewritten historical archive summary.</span></li>',
    ).replace(f'<section class="formal-correction" id="{CORRECTION_ID}"><h2>Correction notice</h2><p>Saved evidence remains preserved.</p></section>', "")
    pages, pkg, request = _setup(tmp_path, faulty_index, faulty_archive)

    with pytest.raises(GazaPublicationError, match="no-update Pages preservation failed"):
        publication._manifest(pkg, request, pages)

    # Replace the staged files with the exact minimal candidate and rebind the
    # package manifest. No Pages file is changed by this private preflight.
    for path, content in (("gaza/index.html", valid_index), ("gaza/archive.html", valid_archive)):
        (pkg / path).write_text(content, encoding="utf-8")
    request = _setup_package_rebind(pkg, pages)
    manifest, rows, diff = publication._manifest(pkg, request, pages)
    assert len(rows) == 2
    assert all(item["action"] == "replace" for item in diff)
    assert manifest["preservation_validation"]["gaza/index.html"]["ok"] is True
    assert (pages / "gaza" / "index.html").read_text(encoding="utf-8") == BASE_INDEX


def test_real_preflight_rejects_single_gaza_page_instead_of_bypassing_guard(tmp_path: Path) -> None:
    valid_index, valid_archive = _insert_valid(BASE_INDEX, BASE_ARCHIVE)
    pages, pkg, request = _setup(tmp_path, valid_index, valid_archive)
    (pkg / "gaza" / "archive.html").unlink()
    manifest_path = pkg / "package_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["representations"] = [manifest["representations"][0]]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    request["package_manifest_sha256"] = "sha256:" + _sha(manifest_path)

    with pytest.raises(GazaPublicationError, match="must include both landing and archive"):
        publication._manifest(pkg, request, pages)


def test_real_preflight_does_not_apply_no_update_guard_to_normal_correction_package(tmp_path: Path) -> None:
    valid_index, valid_archive = _insert_valid(BASE_INDEX, BASE_ARCHIVE)
    pages, pkg, request = _setup(tmp_path, valid_index, valid_archive)
    request = _setup_package_rebind(pkg, pages)
    manifest_path = pkg / "package_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("no_update_preservation")
    manifest["package_type"] = "gaza_edition_correction"
    manifest["representations"] = [
        {**row, "role": "correction_html"}
        for row in manifest["representations"]
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    request["package_manifest_sha256"] = "sha256:" + _sha(manifest_path)

    validated, rows, diff = publication._manifest(pkg, request, pages)
    assert validated["package_type"] == "gaza_edition_correction"
    assert len(rows) == 2
    assert all(item["action"] == "replace" for item in diff)


def test_no_update_role_without_explicit_package_type_is_rejected(tmp_path: Path) -> None:
    valid_index, valid_archive = _insert_valid(BASE_INDEX, BASE_ARCHIVE)
    pages, pkg, request = _setup(tmp_path, valid_index, valid_archive)
    manifest_path = pkg / "package_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("package_type")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    request["package_manifest_sha256"] = "sha256:" + _sha(manifest_path)

    with pytest.raises(GazaPublicationError, match="explicitly declare package_type=gaza_no_update"):
        publication._manifest(pkg, request, pages)


def _setup_package_rebind(pkg: Path, pages: Path) -> dict:
    manifest_path = pkg / "package_manifest.json"
    old = json.loads(manifest_path.read_text(encoding="utf-8"))
    paths = ("gaza/index.html", "gaza/archive.html")
    old["representations"] = [{"public_path": path, "role": "gaza_no_update", "sha256": _sha(pkg / path)} for path in paths]
    old["no_update_preservation"]["candidate_sha256"] = {path: _sha(pkg / path) for path in paths}
    manifest_path.write_text(json.dumps(old), encoding="utf-8")
    return {
        "package_manifest_sha256": "sha256:" + _sha(manifest_path),
        "approval_commit": old["approval_commit"],
        "pages_head": old["pages_head"],
        "package_artifact_set_sha256": old["package_artifact_set_sha256"],
        "rendered_audio_sha256": old["rendered_audio_sha256"],
        "correction_id": old["correction_id"],
    }
