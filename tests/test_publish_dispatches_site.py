from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "publish_dispatches_site.py"
SPEC = importlib.util.spec_from_file_location("publish_dispatches_site", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_copy_excludes_review_outputs_and_includes_detention_watch(tmp_path: Path):
    site = tmp_path / "output" / "site"
    pages = tmp_path / "pages"
    (site / "cascadia" / "detention-watch" / "index.html").parent.mkdir(parents=True, exist_ok=True)
    (site / "cascadia" / "detention-watch" / "index.html").write_text("ok", encoding="utf-8")
    (site / "review_dashboard_2026-05-26.html").write_text("review", encoding="utf-8")
    (site / "source_refresh_2026-05-26.json").write_text("{}", encoding="utf-8")
    (site / "__pycache__").mkdir(parents=True, exist_ok=True)
    (site / "__pycache__" / "x.pyc").write_bytes(b"x")
    pages.mkdir(parents=True, exist_ok=True)

    result = MODULE.copy_public_site_to_pages(site, pages, dry_run=False)

    assert result["copied"] is True
    assert (pages / "cascadia" / "detention-watch" / "index.html").exists()
    assert not (pages / "review_dashboard_2026-05-26.html").exists()
    assert not (pages / "source_refresh_2026-05-26.json").exists()
    assert not (pages / "__pycache__").exists()


def test_dry_run_copy_does_not_modify_pages_repo(tmp_path: Path):
    site = tmp_path / "output" / "site"
    pages = tmp_path / "pages"
    site.mkdir(parents=True)
    pages.mkdir(parents=True)
    before = list(pages.rglob("*"))

    result = MODULE.copy_public_site_to_pages(site, pages, dry_run=True)

    after = list(pages.rglob("*"))
    assert result["dry_run"] is True
    assert before == after


def test_no_changes_exits_cleanly(monkeypatch, tmp_path: Path):
    calls: list[list[str]] = []

    class Proc:
        def __init__(self, rc: int, out: str = ""):
            self.returncode = rc
            self.stdout = out
            self.stderr = ""

    def fake_run(command, cwd=None, check=True, verbose=False):
        calls.append(command)
        if command[:3] == ["git", "status", "--porcelain"]:
            return Proc(0, "")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(MODULE, "run_command", fake_run)
    result = MODULE.stage_commit_sync_push(tmp_path, "msg", dry_run=False, no_push=True)

    assert result["changed"] is False
    assert "No publish changes detected." in result["message"]
    assert calls == [["git", "status", "--porcelain"]]


def test_no_push_commits_without_push(monkeypatch, tmp_path: Path):
    calls: list[list[str]] = []

    class Proc:
        def __init__(self, rc: int, out: str = ""):
            self.returncode = rc
            self.stdout = out
            self.stderr = ""

    def fake_run(command, cwd=None, check=True, verbose=False):
        calls.append(command)
        if command[:3] == ["git", "status", "--porcelain"]:
            return Proc(0, " M index.html")
        if command[:3] == ["git", "add", "-A"]:
            return Proc(0)
        if command[:4] == ["git", "diff", "--cached", "--name-only"]:
            return Proc(0, "index.html")
        if command[:3] == ["git", "commit", "-m"]:
            return Proc(0)
        if command[:4] == ["git", "fetch", "origin", "gh-pages"]:
            return Proc(0)
        if command[:3] == ["git", "rev-parse", "HEAD"]:
            return Proc(0, "abc")
        if command[:3] == ["git", "rev-parse", "origin/gh-pages"]:
            return Proc(0, "abc")
        if command[:2] == ["git", "push"]:
            return Proc(0)
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(MODULE, "run_command", fake_run)
    result = MODULE.stage_commit_sync_push(tmp_path, "msg", dry_run=False, no_push=True)

    assert result["committed"] is True
    assert result["pushed"] is False
    assert not any(cmd[:2] == ["git", "fetch"] for cmd in calls)
    assert not any(cmd[:3] == ["git", "pull", "--rebase"] for cmd in calls)
    assert not any(cmd[:2] == ["git", "push"] for cmd in calls)


def test_normal_publish_calls_fetch_and_push(monkeypatch, tmp_path: Path):
    calls: list[list[str]] = []

    class Proc:
        def __init__(self, rc: int, out: str = ""):
            self.returncode = rc
            self.stdout = out
            self.stderr = ""

    def fake_run(command, cwd=None, check=True, verbose=False):
        calls.append(command)
        if command[:3] == ["git", "status", "--porcelain"]:
            return Proc(0, " M index.html")
        if command[:3] == ["git", "add", "-A"]:
            return Proc(0)
        if command[:4] == ["git", "diff", "--cached", "--name-only"]:
            return Proc(0, "index.html")
        if command[:3] == ["git", "commit", "-m"]:
            return Proc(0)
        if command[:4] == ["git", "fetch", "origin", "gh-pages"]:
            return Proc(0)
        if command[:3] == ["git", "rev-parse", "HEAD"]:
            return Proc(0, "abc")
        if command[:3] == ["git", "rev-parse", "origin/gh-pages"]:
            return Proc(0, "abc")
        if command[:2] == ["git", "push"]:
            return Proc(0)
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(MODULE, "run_command", fake_run)
    result = MODULE.stage_commit_sync_push(tmp_path, "msg", dry_run=False, no_push=False)

    assert result["pushed"] is True
    assert any(cmd[:2] == ["git", "fetch"] for cmd in calls)
    assert any(cmd[:2] == ["git", "push"] for cmd in calls)


def test_git_commands_never_use_force_push(monkeypatch, tmp_path: Path):
    calls: list[list[str]] = []

    class Proc:
        def __init__(self, rc: int, out: str = ""):
            self.returncode = rc
            self.stdout = out
            self.stderr = ""

    def fake_run(command, cwd=None, check=True, verbose=False):
        calls.append(command)
        if command[:3] == ["git", "status", "--porcelain"]:
            return Proc(0, " M index.html")
        if command[:3] == ["git", "add", "-A"]:
            return Proc(0)
        if command[:4] == ["git", "diff", "--cached", "--name-only"]:
            return Proc(0, "index.html")
        if command[:3] == ["git", "commit", "-m"]:
            return Proc(0)
        if command[:4] == ["git", "fetch", "origin", "gh-pages"]:
            return Proc(0)
        if command[:3] == ["git", "rev-parse", "HEAD"]:
            return Proc(0, "abc")
        if command[:3] == ["git", "rev-parse", "origin/gh-pages"]:
            return Proc(0, "abc")
        if command[:2] == ["git", "push"]:
            return Proc(0)
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(MODULE, "run_command", fake_run)
    MODULE.stage_commit_sync_push(tmp_path, "msg", dry_run=False, no_push=False)

    flat = " ".join(" ".join(cmd) for cmd in calls)
    assert "--force" not in flat
    assert "-f" not in flat


def test_verify_marker_logic_with_mocked_http(monkeypatch):
    class FakeResponse:
        def __init__(self, status: int, body: str):
            self.status = status
            self._body = body.encode("utf-8")

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(url, timeout=20):
        if url.endswith("/cascadia/"):
            return FakeResponse(200, "missing marker")
        if "editions" in url:
            return FakeResponse(503, "Method note")
        return FakeResponse(200, "Cascadia Detention Watch cascadia/detention-watch Method note")

    monkeypatch.setattr(MODULE.urllib.request, "urlopen", fake_urlopen)
    result = MODULE.verify_urls()

    assert result["ok"] is False
    failures = [c for c in result["checks"] if not c["ok"]]
    assert any(f.get("failure") == "content_marker_missing" for f in failures)
    assert any(f.get("failure") == "status_non_200" for f in failures)


def test_verify_local_output_markers(tmp_path: Path, monkeypatch):
    site = tmp_path / "output" / "site"
    (site / "cascadia" / "detention-watch" / "editions" / "2026-05-26").mkdir(parents=True, exist_ok=True)
    (site / "index.html").write_text("Cascadia Detention Watch", encoding="utf-8")
    (site / "cascadia" / "index.html").parent.mkdir(parents=True, exist_ok=True)
    (site / "cascadia" / "index.html").write_text('/cascadia/detention-watch/', encoding="utf-8")
    (site / "cascadia" / "detention-watch" / "index.html").write_text("Cascadia Detention Watch", encoding="utf-8")
    (site / "cascadia" / "detention-watch" / "editions" / "2026-05-26" / "index.html").write_text("Method note", encoding="utf-8")
    monkeypatch.setattr(MODULE, "ROOT", tmp_path)
    monkeypatch.setattr(
        MODULE,
        "LOCAL_VERIFY_TARGETS",
        [
            (tmp_path / "output" / "site" / "index.html", "Cascadia Detention Watch"),
            (tmp_path / "output" / "site" / "cascadia" / "index.html", "/cascadia/detention-watch/"),
            (tmp_path / "output" / "site" / "cascadia" / "detention-watch" / "index.html", "Cascadia Detention Watch"),
            (tmp_path / "output" / "site" / "cascadia" / "detention-watch" / "editions" / "2026-05-26" / "index.html", "Method note"),
        ],
    )
    result = MODULE.verify_local_output()
    assert result["ok"] is True


def test_no_push_verify_uses_local_check_not_network(monkeypatch, tmp_path: Path):
    pages = tmp_path / "pages"
    pages.mkdir(parents=True)

    def fake_copy(site_root, pages_repo, dry_run=False):
        return {"copied": False, "dry_run": dry_run}

    def fake_stage(*args, **kwargs):
        return {"changed": False, "committed": False, "pushed": False, "message": "No publish changes detected."}

    called = {"local": False, "live": False}

    def fake_local():
        called["local"] = True
        return {"ok": True, "checks": []}

    def fake_live():
        called["live"] = True
        return {"ok": True, "checks": []}

    monkeypatch.setattr(MODULE, "copy_public_site_to_pages", fake_copy)
    monkeypatch.setattr(MODULE, "stage_commit_sync_push", fake_stage)
    monkeypatch.setattr(MODULE, "verify_local_output", fake_local)
    monkeypatch.setattr(MODULE, "verify_urls", fake_live)
    args = MODULE.parse_args(["--pages-repo", str(pages), "--dry-run", "--no-push", "--verify"])
    result = MODULE.run_publish_workflow(args)
    assert result["ok"] is True
    assert result["verify_mode"] == "local"
    assert called["local"] is True
    assert called["live"] is False


def test_workflow_dry_run_skips_build_and_git_changes(monkeypatch, tmp_path: Path):
    pages = tmp_path / "pages"
    pages.mkdir(parents=True)

    called = {"build": False, "detention": False, "stage": False}

    def fake_build(*args, **kwargs):
        called["build"] = True

    def fake_detention(*args, **kwargs):
        called["detention"] = True

    def fake_copy(site_root, pages_repo, dry_run=False):
        return {"copied": False, "dry_run": dry_run}

    def fake_stage(*args, **kwargs):
        called["stage"] = True
        return {"changed": False, "committed": False, "pushed": False}

    monkeypatch.setattr(MODULE, "build_public_site", fake_build)
    monkeypatch.setattr(MODULE, "build_detention_watch_baseline", fake_detention)
    monkeypatch.setattr(MODULE, "copy_public_site_to_pages", fake_copy)
    monkeypatch.setattr(MODULE, "stage_commit_sync_push", fake_stage)

    args = MODULE.parse_args(["--pages-repo", str(pages), "--dry-run"])
    result = MODULE.run_publish_workflow(args)

    assert result["ok"] is True
    assert called["build"] is False
    assert called["detention"] is False
    assert called["stage"] is True
