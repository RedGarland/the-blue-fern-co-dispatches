from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


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
