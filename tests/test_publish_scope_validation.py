from __future__ import annotations

import importlib.util
import hashlib
import json
import sys
from pathlib import Path

from bluefern_dispatches.food_line_approved_proposal import build_release_manifest, write_json_deterministic


def _load_validator_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "validate_publish_scope.py"
    spec = importlib.util.spec_from_file_location("validate_publish_scope", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_food_line_source_and_review_paths_pass_for_declared_date() -> None:
    module = _load_validator_module()
    errors = module.validate_publish_scope(
        dispatch="food-line",
        date_text="2026-06-19",
        source_changed_paths=[
            "output/site/food-line/editions/2026-06-19/index.html",
            "output/review/food-line/2026-06-19/daily_ops_report.json",
        ],
    )
    assert errors == []


def test_food_line_rejects_other_edition_date() -> None:
    module = _load_validator_module()
    errors = module.validate_publish_scope(
        dispatch="food-line",
        date_text="2026-06-19",
        source_changed_paths=["output/site/food-line/editions/2026-06-18/index.html"],
    )
    assert any("outside declared 2026-06-19" in error for error in errors)


def test_food_line_rejects_future_edition_date() -> None:
    module = _load_validator_module()
    errors = module.validate_publish_scope(
        dispatch="food-line",
        date_text="2026-06-19",
        source_changed_paths=["output/site/food-line/editions/2026-06-20/index.html"],
    )
    assert any("future edition date 2026-06-20" in error for error in errors)


def test_food_line_rejects_unrelated_gaza_path() -> None:
    module = _load_validator_module()
    errors = module.validate_publish_scope(
        dispatch="food-line",
        date_text="2026-06-19",
        source_changed_paths=["output/site/gaza/index.html"],
    )
    assert any("outside the declared food-line publish scope" in error for error in errors)


def test_pages_repo_root_requires_explicit_allow_flag() -> None:
    module = _load_validator_module()
    errors = module.validate_publish_scope(
        dispatch="food-line",
        date_text="2026-06-19",
        source_changed_paths=["output/site/food-line/editions/2026-06-19/index.html"],
        pages_repo_root=Path("bluefern-dispatches-pages"),
    )
    assert any("--pages-repo-root was provided without --allow-pages" in error for error in errors)


def test_allow_pages_requires_pages_repo_root() -> None:
    module = _load_validator_module()
    errors = module.validate_publish_scope(
        dispatch="food-line",
        date_text="2026-06-19",
        source_changed_paths=["output/site/food-line/editions/2026-06-19/index.html"],
        allow_pages=True,
    )
    assert any("--allow-pages requires --pages-repo-root" in error for error in errors)


def test_pages_scope_passes_when_explicitly_allowed() -> None:
    module = _load_validator_module()
    errors = module.validate_publish_scope(
        dispatch="food-line",
        date_text="2026-06-19",
        source_changed_paths=["output/site/food-line/editions/2026-06-19/index.html"],
        pages_repo_root=Path("bluefern-dispatches-pages"),
        allow_pages=True,
        pages_changed_paths=["food-line/editions/2026-06-19/index.html"],
    )
    assert errors == []


def test_audio_requires_explicit_allow_flag() -> None:
    module = _load_validator_module()
    errors = module.validate_publish_scope(
        dispatch="food-line",
        date_text="2026-06-19",
        source_changed_paths=["output/site/food-line/audio/2026-06-19.mp3"],
    )
    assert any("--allow-audio" in error for error in errors)


def test_audio_passes_when_explicitly_allowed() -> None:
    module = _load_validator_module()
    errors = module.validate_publish_scope(
        dispatch="food-line",
        date_text="2026-06-19",
        source_changed_paths=["output/site/food-line/audio/2026-06-19.mp3"],
        allow_audio=True,
    )
    assert errors == []


def test_map_requires_explicit_allow_flag() -> None:
    module = _load_validator_module()
    errors = module.validate_publish_scope(
        dispatch="food-line",
        date_text="2026-06-19",
        source_changed_paths=["output/site/food-line/editions/2026-06-19/map.html"],
    )
    assert any("--allow-map" in error for error in errors)


def test_bluesky_requires_explicit_allow_flag() -> None:
    module = _load_validator_module()
    errors = module.validate_publish_scope(
        dispatch="food-line",
        date_text="2026-06-19",
        source_changed_paths=["data/dispatches/food-line/editions/2026-06-19/bluesky_post.json"],
    )
    assert any("--allow-bluesky" in error for error in errors)


def test_invalid_date_argument_fails(capsys) -> None:
    module = _load_validator_module()
    exit_code = module.main(["--dispatch", "food-line", "--date", "2026-13-40"])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Invalid date '2026-13-40'" in captured.err


def _release_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    source = tmp_path / "source"
    pages = tmp_path / "pages"
    edition = source / "output/site/food-line/editions/2026-06-19"
    edition.mkdir(parents=True)
    pages.mkdir()
    site = source / "output/site/food-line"
    (site / "index.html").write_text("index", encoding="utf-8")
    (site / "archive.html").write_text("archive", encoding="utf-8")
    (site / "rss.xml").write_text("<?xml version=\"1.0\" encoding=\"utf-8\"?><rss><channel></channel></rss>", encoding="utf-8")
    for filename in ("index.html", "source_table.html", "claim_ledger.html", "sources_manifest.json", "curation_manifest.json", "edition_manifest.json"):
        (edition / filename).write_text(filename, encoding="utf-8")
    payload = build_release_manifest(
        root=source,
        pages_root=pages,
        edition_date="2026-06-19",
        source_commit="test",
        source_paths=[site / "index.html", site / "archive.html", *sorted(edition.iterdir())],
    )
    manifest = source / "release.json"
    write_json_deterministic(manifest, payload)
    return source, pages, manifest


def test_strict_release_manifest_validates_exact_delta_and_ignores_unrelated_dirt(tmp_path: Path, monkeypatch) -> None:
    module = _load_validator_module()
    source, pages, manifest = _release_fixture(tmp_path)
    (source / "unrelated.txt").write_text("dirty but not referenced", encoding="utf-8")
    monkeypatch.setattr(module, "_git_status_porcelain", lambda _root: [])
    errors = module.validate_publish_scope(
        dispatch="food-line",
        date_text="2026-06-19",
        source_repo_root=source,
        pages_repo_root=pages,
        allow_pages=True,
        strict=True,
        release_manifest_path=manifest,
    )
    assert errors == []


def test_strict_release_manifest_rejects_unexpected_cross_domain_path(tmp_path: Path, monkeypatch) -> None:
    module = _load_validator_module()
    source, pages, manifest = _release_fixture(tmp_path)
    gaza = source / "output/site/gaza/index.html"
    gaza.parent.mkdir(parents=True)
    gaza.write_text("gaza", encoding="utf-8")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["entries"].append(
        {
            "source_path": "output/site/gaza/index.html",
            "pages_path": "gaza/index.html",
            "action": "add",
            "source_sha256": __import__("hashlib").sha256(gaza.read_bytes()).hexdigest(),
            "pages_sha256_before": None,
        }
    )
    write_json_deterministic(manifest, payload)
    monkeypatch.setattr(module, "_git_status_porcelain", lambda _root: [])
    errors = module.validate_publish_scope(
        dispatch="food-line",
        date_text="2026-06-19",
        source_repo_root=source,
        pages_repo_root=pages,
        allow_pages=True,
        strict=True,
        release_manifest_path=manifest,
    )
    assert any("outside the declared food-line publish scope" in error for error in errors)
    assert any("unexpected Food Line publication files" in error for error in errors)


def test_strict_release_manifest_rejects_omitted_generated_file(tmp_path: Path, monkeypatch) -> None:
    module = _load_validator_module()
    source, pages, manifest = _release_fixture(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["entries"] = [entry for entry in payload["entries"] if not entry["source_path"].endswith("claim_ledger.html")]
    write_json_deterministic(manifest, payload)
    monkeypatch.setattr(module, "_git_status_porcelain", lambda _root: [])
    errors = module.validate_publish_scope(
        dispatch="food-line",
        date_text="2026-06-19",
        source_repo_root=source,
        pages_repo_root=pages,
        allow_pages=True,
        strict=True,
        release_manifest_path=manifest,
    )
    assert any("omits generated Food Line publication files" in error for error in errors)


def test_strict_retrospective_manifest_rejects_history_shrink(tmp_path: Path, monkeypatch) -> None:
    module = _load_validator_module()
    source, pages, manifest = _release_fixture(tmp_path)
    (source / "output/site/food-line/archive.html").write_text(
        '<a href="editions/2026-06-19/">new</a>', encoding="utf-8"
    )
    (source / "output/site/food-line/rss.xml").write_text(
        '<rss><channel><item><link>https://dispatches.thebluefernco.com/food-line/editions/2026-06-19/</link></item></channel></rss>',
        encoding="utf-8",
    )
    (pages / "food-line").mkdir(parents=True)
    (pages / "food-line/archive.html").write_text(
        '<a href="editions/2026-06-18/">old</a>', encoding="utf-8"
    )
    (pages / "food-line/rss.xml").write_text(
        '<rss><channel><item><link>https://dispatches.thebluefernco.com/food-line/editions/2026-06-18/</link></item></channel></rss>',
        encoding="utf-8",
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    for entry in payload["entries"]:
        entry["provenance_role"] = "approved_retrospective_generated_output"
        entry["source_sha256"] = hashlib.sha256((source / entry["source_path"]).read_bytes()).hexdigest()
    write_json_deterministic(manifest, payload)
    monkeypatch.setattr(module, "_validate_retrospective_release_authority", lambda **_kwargs: [])
    monkeypatch.setattr(module, "_git_status_porcelain", lambda _root: [])
    errors = module.validate_publish_scope(
        dispatch="food-line",
        date_text="2026-06-19",
        source_repo_root=source,
        pages_repo_root=pages,
        allow_pages=True,
        strict=True,
        release_manifest_path=manifest,
    )
    assert any("archive dropped: ['2026-06-18']" in error for error in errors)
    assert any("rss dropped: ['2026-06-18']" in error for error in errors)
