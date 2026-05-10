import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "clean_local_generated.py"


def _run_git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def _write(path: Path, text: str = "x\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _run_git(root, "init")
    _run_git(root, "config", "user.email", "tests@example.test")
    _run_git(root, "config", "user.name", "Tests")
    for path in [
        "assets/logo.txt",
        "docs/readme.md",
        "scripts/tool.py",
        "src/pkg/__init__.py",
        "tests/test_sample.py",
        "data/records/records.json",
        "output/site/cascadia/index.html",
    ]:
        _write(root / path)
    _run_git(root, "add", ".")
    _run_git(root, "commit", "-m", "initial")
    return root


def _run_clean(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


def test_dry_run_does_not_delete(tmp_path):
    root = _make_repo(tmp_path)
    _write(root / "data/dispatches/cascadia/cache/cache.json")

    result = _run_clean(root)

    assert "Dry run" in result.stdout
    assert (root / "data/dispatches/cascadia/cache/cache.json").exists()


def test_apply_removes_only_allowed_generated_paths(tmp_path):
    root = _make_repo(tmp_path)
    _write(root / "data/dispatches/cascadia/cache/cache.json")
    _write(root / ".pytest_tmp/temp.txt")
    _write(root / "src/bluefern_dispatches.egg-info/PKG-INFO")
    _write(root / "docs/generated-note.md")

    _run_clean(root, "--apply")

    assert not (root / "data/dispatches/cascadia/cache").exists()
    assert not (root / ".pytest_tmp").exists()
    assert not (root / "src/bluefern_dispatches.egg-info").exists()
    assert (root / "docs/generated-note.md").exists()


def test_env_and_nested_pages_repo_are_never_touched(tmp_path):
    root = _make_repo(tmp_path)
    _write(root / ".env", "SECRET=1\n")
    _write(root / "bluefern-dispatches-pages/output.html")

    _run_clean(root, "--apply", "--include-site-output")

    assert (root / ".env").read_text(encoding="utf-8") == "SECRET=1\n"
    assert (root / "bluefern-dispatches-pages/output.html").exists()


def test_source_test_doc_assets_are_never_touched(tmp_path):
    root = _make_repo(tmp_path)
    for path in ["assets/new.png", "docs/new.md", "scripts/new.py", "src/new.py", "tests/new_test.py"]:
        _write(root / path)

    _run_clean(root, "--apply")

    for path in ["assets/new.png", "docs/new.md", "scripts/new.py", "src/new.py", "tests/new_test.py"]:
        assert (root / path).exists()


def test_output_site_requires_explicit_flag(tmp_path):
    root = _make_repo(tmp_path)
    _write(root / "output/site/cascadia/index.html", "changed\n")

    _run_clean(root, "--apply")
    assert (root / "output/site/cascadia/index.html").read_text(encoding="utf-8") == "changed\n"

    _run_clean(root, "--apply", "--include-site-output")
    assert (root / "output/site/cascadia/index.html").read_text(encoding="utf-8") == "x\n"
