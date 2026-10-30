from __future__ import annotations

import os
import hashlib
import struct
from pathlib import Path
import stat
import subprocess
import sys

import pytest

from bluefern_dispatches import canonical_pages_checkout as cp


def _isolate_fixture_git(tmp_path, monkeypatch):
    """Give synthetic repositories their own Git config, not the CI host's.

    Only Git subprocesses confined to this test's tmp_path receive an explicit
    system-config file. Production _git/_config_guard and their environment
    checks are unchanged. Intentional local/HOME/XDG config remains observable;
    tests may also populate the synthetic system file to prove refusal.
    """
    settings = tmp_path / "git-environment"
    settings.mkdir()
    home, xdg = settings / "home", settings / "xdg"
    home.mkdir(); xdg.mkdir()
    system = settings / "system.gitconfig"
    system.write_text("")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    real_run = subprocess.run

    def fixture_run(command, *args, **kwargs):
        if isinstance(command, (list, tuple)) and command[0] == "git":
            assert len(command) > 2 and command[1] == "-C"
            assert Path(command[2]).resolve(strict=True).is_relative_to(tmp_path.resolve(strict=True))
            env = dict(kwargs.get("env") or os.environ)
            env["GIT_CONFIG_SYSTEM"] = str(system)
            kwargs["env"] = env
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fixture_run)


def git(root, *args, data=None):
    result = subprocess.run(["git", "-C", str(root), *args], input=data,
                            capture_output=True, check=True)
    return result.stdout


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    _isolate_fixture_git(tmp_path, monkeypatch)
    source, parent = tmp_path / "source", tmp_path / "private"
    source.mkdir(); parent.mkdir()
    template = parent.parent / "reviewed-template"; template.mkdir()
    (template / "hooks").mkdir()
    (template / "hooks/pre-commit.sample").write_text("# inert sample\n")
    git(source, "init", "-q", "--template", str(template))
    git(source, "config", "user.name", "Synthetic test")
    git(source, "config", "user.email", "test@example.invalid")
    git(source, "config", "core.autocrlf", "false")
    (source / "gaza").mkdir()
    (source / "gaza/index.html").write_bytes(b"<h1>Correction</h1>\n<p>Exact bytes</p>\n")
    (source / "gaza/rss.xml").write_bytes(b"<rss>\r\n<item>old</item>\r\n</rss>\n")
    (source / "image.bin").write_bytes(bytes(range(256)))
    git(source, "add", "--", "gaza", "image.bin")
    git(source, "commit", "-qm", "synthetic public baseline")
    oid = git(source, "rev-parse", "HEAD").decode().strip()
    return source, parent, parent / "new-pages", oid


def clone(fixture):
    source, parent, destination, oid = fixture
    return cp.create_canonical_pages_clone(source, destination,
        reviewed_template=parent.parent / "reviewed-template", approved_parent=parent, expected_commit=oid)


def test_raw_clone_preserves_lf_mixed_rss_and_binary_despite_source_autocrlf(fixture):
    source, parent, destination, oid = fixture
    git(source, "config", "core.autocrlf", "true")
    before = (source / ".git/config").read_bytes()
    # The source project's outer attributes do not govern the nested repo.
    (parent / ".gitattributes").write_text("* text eol=crlf\n")
    result = clone(fixture)
    assert result["ok"] and result["leaf_count"] == 3
    assert result["head"] == oid and result["publication_authorized"] is False
    assert git(destination, "config", "--local", "--get", "core.autocrlf") == b"false\n"
    assert git(destination, "remote") == b""
    assert subprocess.run(["git", "-C", str(destination), "symbolic-ref", "-q", "HEAD"]).returncode == 1
    for name in ("gaza/index.html", "gaza/rss.xml", "image.bin"):
        assert (destination / name).read_bytes() == (source / name).read_bytes()
    assert (source / ".git/config").read_bytes() == before
    assert not (destination / ".git/objects/info/alternates").exists()


def test_inherited_global_autocrlf_true_is_overridden_only_in_new_clone(fixture, monkeypatch):
    source, parent, destination, oid = fixture
    home = parent.parent / "synthetic-home"; home.mkdir()
    config = home / ".gitconfig"; config.write_text("[core]\n\tautocrlf = true\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    before = config.read_bytes()
    assert clone(fixture)["ok"]
    assert config.read_bytes() == before
    assert git(destination, "config", "--get", "core.autocrlf") == b"false\n"


def test_dirty_source_working_bytes_are_not_used_or_repaired(fixture):
    source, _, destination, oid = fixture
    file = source / "gaza/index.html"
    file.write_bytes(file.read_bytes().replace(b"\n", b"\r\n"))
    dirty = file.read_bytes()
    assert clone(fixture)["ok"]
    assert file.read_bytes() == dirty
    assert (destination / "gaza/index.html").read_bytes() == git(source, "show", f"{oid}:gaza/index.html")


@pytest.mark.parametrize("existing", ["empty", "dirty", "symlink"])
def test_never_reuses_existing_destination(fixture, existing):
    source, parent, destination, oid = fixture
    if existing == "symlink":
        destination.symlink_to(source, target_is_directory=True)
    else:
        destination.mkdir()
        if existing == "dirty": (destination / "keep").write_bytes(b"unrelated")
    with pytest.raises(cp.CheckoutRefused, match="already exists"):
        clone(fixture)
    if existing == "dirty": assert (destination / "keep").read_bytes() == b"unrelated"
    assert destination.exists()


@pytest.mark.parametrize("oid", ["HEAD", "a" * 39, "A" * 40, "a" * 41, "--all", "" , None])
def test_requires_exact_commit_without_destination_write(fixture, oid):
    source, parent, destination, _ = fixture
    with pytest.raises(cp.CheckoutRefused):
        cp.create_canonical_pages_clone(source, destination, reviewed_template=parent.parent / "reviewed-template", approved_parent=parent, expected_commit=oid)
    assert not destination.exists()


def test_missing_commit_refused_before_destination(fixture):
    source, parent, destination, _ = fixture
    with pytest.raises(cp.CheckoutRefused):
        cp.create_canonical_pages_clone(source, destination, reviewed_template=parent.parent / "reviewed-template", approved_parent=parent, expected_commit="f" * 40)
    assert not destination.exists()


@pytest.mark.parametrize("mutation", ["newline", "extra", "ignored", "empty_dir", "missing", "mode", "symlink", "hardlink"])
def test_full_verifier_rejects_every_worktree_drift(fixture, mutation):
    _, _, destination, oid = fixture
    clone(fixture)
    file = destination / "gaza/index.html"
    if mutation == "newline": file.write_bytes(file.read_bytes().replace(b"\n", b"\r\n"))
    elif mutation == "extra": (destination / "extra.txt").write_text("unexpected")
    elif mutation == "ignored":
        (destination / ".git/info").mkdir(exist_ok=True)
        (destination / ".git/info/exclude").write_text("ignored\n")
        (destination / "ignored").write_text("still unexpected")
    elif mutation == "empty_dir": (destination / "empty").mkdir()
    elif mutation == "missing": file.unlink()
    elif mutation == "mode": file.chmod(file.stat().st_mode | stat.S_IXUSR)
    elif mutation == "symlink": file.unlink(); file.symlink_to(destination / "image.bin")
    elif mutation == "hardlink":
        extra = destination.parent / "link"; os.link(file, extra)
    with pytest.raises(cp.CheckoutRefused): cp.verify_canonical_checkout(destination, oid)


def test_attributes_conversion_fails_retaining_new_checkout(fixture):
    source, parent, destination, oid = fixture
    (source / ".gitattributes").write_text("*.html text eol=crlf\n")
    git(source, "add", ".gitattributes"); git(source, "commit", "-qm", "conversion attribute")
    oid = git(source, "rev-parse", "HEAD").decode().strip()
    with pytest.raises(cp.CheckoutRefused, match="raw working bytes differ"):
        cp.create_canonical_pages_clone(source, destination, reviewed_template=parent.parent / "reviewed-template", approved_parent=parent, expected_commit=oid)
    assert destination.is_dir()
    assert b"\r\n" in (destination / "gaza/index.html").read_bytes()


@pytest.mark.parametrize("key,value", [
    ("filter.test.smudge", "never execute me"), ("core.hooksPath", "/not-used"),
    ("core.fsmonitor", "never execute me"), ("uploadpack.packObjectsHook", "never execute me"),
    ("url.https://example.invalid/.insteadOf", "/tmp/"), ("remote.origin.promisor", "true"),
    ("remote.origin.receivepack", "never execute me"), ("remote.origin.proxy", "never use me"),
    ("remote.origin.partialclonefilter", "blob:none"), ("core.sshCommand", "never execute me"),
    ("core.gitProxy", "never execute me"), ("core.alternateRefsCommand", "never execute me"),
    ("fetch.bundleURI", "https://example.invalid/bundle"),
])
def test_execution_redirect_config_refused_not_disabled(fixture, key, value):
    source, _, destination, _ = fixture
    git(source, "config", key, value)
    before = (source / ".git/config").read_bytes()
    with pytest.raises(cp.CheckoutRefused, match="unsupported Git execution/redirect configuration"):
        clone(fixture)
    assert not destination.exists() and (source / ".git/config").read_bytes() == before


def test_git_environment_is_refused_not_silently_reconfigured(fixture, monkeypatch):
    monkeypatch.setenv("GIT_DIR", str(fixture[0] / ".git"))
    with pytest.raises(cp.CheckoutRefused, match="environment overrides"):
        clone(fixture)
    assert not fixture[2].exists()


def test_credential_in_config_key_is_not_echoed(fixture):
    source, _, destination, _ = fixture
    git(source, "config", "url.https://user:PRIVATE_TOKEN@example.invalid/.insteadOf", "/tmp/")
    with pytest.raises(cp.CheckoutRefused) as error: clone(fixture)
    assert "PRIVATE_TOKEN" not in str(error.value)
    assert "user:" not in str(error.value) and not destination.exists()


@pytest.mark.parametrize("name", ["hooks/reference-transaction", "hooks/post-checkout", "config", "HEAD", "info/attributes"])
def test_unreviewed_active_template_refused_before_any_clone(fixture, name, monkeypatch):
    _, parent, destination, _ = fixture
    template = parent.parent / "reviewed-template"
    file = template / name; file.parent.mkdir(exist_ok=True)
    marker = parent.parent / "MUST_NOT_EXECUTE"
    file.write_text(f"#!/bin/sh\nprintf unsafe > '{marker}'\n")
    file.chmod(0o755)
    original = cp._git; calls = []
    def record(root, *args):
        calls.append(args[0]); return original(root, *args)
    monkeypatch.setattr(cp, "_git", record)
    with pytest.raises(cp.CheckoutRefused, match="requires separate review"):
        clone(fixture)
    assert "clone" not in calls and not marker.exists() and not destination.exists()


def test_explicit_template_is_bound_in_clone_command(fixture, monkeypatch):
    original = cp._git; clones = []
    def record(root, *args):
        if args[0] == "clone": clones.append(args)
        return original(root, *args)
    monkeypatch.setattr(cp, "_git", record)
    proof = clone(fixture)
    assert len(clones) == 1
    assert clones[0][clones[0].index("--template") + 1] == proof["reviewed_template"]
    assert len(proof["template_inventory_sha256"]) == 64
    assert proof["existing_worktree_writes_requested"] is False


def test_preexisting_template_configuration_not_overridden(fixture):
    source, parent, destination, _ = fixture
    git(source, "config", "init.templateDir", str(parent.parent / "reviewed-template"))
    with pytest.raises(cp.CheckoutRefused, match="execution/redirect configuration"):
        clone(fixture)
    assert not destination.exists()


def test_template_symlink_refused(fixture):
    _, parent, destination, _ = fixture
    template = parent.parent / "reviewed-template"
    (template / "description").symlink_to(template / "hooks/pre-commit.sample")
    with pytest.raises(cp.CheckoutRefused, match="link/reparse"):
        clone(fixture)
    assert not destination.exists()


def test_unreadable_inventory_refuses_instead_of_silent_skip(fixture, monkeypatch):
    def denied(root, *, followlinks, onerror):
        onerror(PermissionError("synthetic denied subtree"))
        return iter(())
    monkeypatch.setattr(cp.os, "walk", denied)
    with pytest.raises(cp.CheckoutRefused, match="inventory could not be read"):
        clone(fixture)
    assert not fixture[2].exists()


def test_storage_refusal_keeps_reserve_and_no_destination(fixture, monkeypatch):
    monkeypatch.setattr(cp.shutil, "disk_usage", lambda _: type("Usage", (), {"free": cp.RESERVE_BYTES})())
    with pytest.raises(cp.CheckoutRefused, match="storage admission"):
        clone(fixture)
    assert not fixture[2].exists()


def test_ancestor_symlink_refused_before_git(fixture):
    source, parent, destination, oid = fixture
    alias = parent.parent / "alias"; alias.symlink_to(parent, target_is_directory=True)
    with pytest.raises(cp.CheckoutRefused, match="link/reparse"):
        cp.create_canonical_pages_clone(source, alias / "new", reviewed_template=parent.parent / "reviewed-template", approved_parent=alias, expected_commit=oid)


def test_mock_windows_reparse_guard(fixture, monkeypatch):
    path = fixture[1]
    real = path.lstat()
    class Reparse:
        st_mode = real.st_mode
        st_file_attributes = 0x400
    original = Path.lstat
    monkeypatch.setattr(Path, "lstat", lambda self: Reparse() if self == path else original(self))
    with pytest.raises(cp.CheckoutRefused, match="link/reparse"):
        cp._ordinary(path, directory=True)


@pytest.mark.parametrize("path", ["../escape", "/absolute", "a//b", "a/.git/config", "a/CON.txt", "NUL", "a:b", "a\\b", "a.", "a ", "a\n", "git~1/config", "cafe\u0301", "COM¹", "LPT².txt", "COM³.test", "LPT³", "COM².txt", "LPT¹", "CONIN$", "CONOUT$", "a/conIn$", "a/conOut$"])
def test_portable_path_refusal(path):
    with pytest.raises(cp.CheckoutRefused): cp._safe_tree_path(path)


def test_case_collision_refused_before_checkout(fixture):
    source, parent, destination, _ = fixture
    (source / "Case").write_text("a"); (source / "case").write_text("b")
    git(source, "add", "Case", "case"); git(source, "commit", "-qm", "bad case pair")
    oid = git(source, "rev-parse", "HEAD").decode().strip()
    with pytest.raises(cp.CheckoutRefused, match="case-colliding"):
        cp.create_canonical_pages_clone(source, destination, reviewed_template=parent.parent / "reviewed-template", approved_parent=parent, expected_commit=oid)
    assert not destination.exists()


@pytest.mark.parametrize("mode", ["120000", "160000"])
def test_symlink_and_gitlink_tree_modes_refused(fixture, mode):
    source, parent, destination, oid = fixture
    obj = oid if mode == "160000" else git(source, "hash-object", "-w", "--stdin", data=b"image.bin").decode().strip()
    git(source, "update-index", "--add", "--cacheinfo", f"{mode},{obj},unsafe")
    git(source, "commit", "-qm", "unsupported mode")
    oid = git(source, "rev-parse", "HEAD").decode().strip()
    with pytest.raises(cp.CheckoutRefused, match="unsupported tree mode"):
        cp.create_canonical_pages_clone(source, destination, reviewed_template=parent.parent / "reviewed-template", approved_parent=parent, expected_commit=oid)
    assert not destination.exists()


def test_wrong_head_and_index_drift_refused(fixture):
    source, _, destination, oid = fixture
    clone(fixture)
    with pytest.raises(cp.CheckoutRefused, match="HEAD differs"):
        cp.verify_canonical_checkout(destination, "f" * 40)
    git(destination, "update-index", "--chmod=+x", "gaza/index.html")
    with pytest.raises(cp.CheckoutRefused, match="index modes"):
        cp.verify_canonical_checkout(destination, oid)


def test_shallow_selected_commit_is_allowed_no_complete_history_claim(fixture):
    source, _, destination, oid = fixture
    # A declared shallow boundary at this genuine commit; the full selected
    # tree remains present. No synthetic object header or fabricated history.
    (source / ".git/shallow").write_text(oid + "\n")
    proof = cp.verify_canonical_checkout(source, oid)
    assert proof["ok"] and "complete_history_verified" not in proof
    assert clone(fixture)["ok"]


def test_subprocess_failure_retains_claimed_destination(fixture, monkeypatch):
    original = cp._git
    def fail_clone(root, *args):
        if args[0] == "clone": raise cp.CheckoutRefused("synthetic clone failure")
        return original(root, *args)
    monkeypatch.setattr(cp, "_git", fail_clone)
    with pytest.raises(cp.CheckoutRefused, match="synthetic clone failure"):
        clone(fixture)
    assert fixture[2].is_dir() and not list(fixture[2].iterdir())


def test_cli_requires_explicit_create_and_reports_retained_refusal(fixture):
    script = Path(__file__).parents[1] / "scripts/prepare_canonical_pages_checkout.py"
    source, parent, destination, oid = fixture
    command = [sys.executable, "-B", str(script), "--source-repo", str(source),
               "--approved-parent", str(parent), "--destination", str(destination),
               "--expected-commit", oid, "--reviewed-template", str(parent.parent / "reviewed-template")]
    assert subprocess.run(command, capture_output=True).returncode == 2
    destination.mkdir()
    result = subprocess.run([*command, "--create-private-clone"], capture_output=True)
    assert result.returncode == 1
    assert b'"publication_authorized": false' in result.stdout


def test_ci_selects_new_safety_suite_without_dropping_existing_guards():
    import shlex
    import yaml

    workflow = yaml.safe_load((Path(__file__).parents[1] / ".github/workflows/dispatch-validation.yml").read_text())
    commands = [step["run"] for step in workflow["jobs"]["validate_core"]["steps"]
                if step.get("name") == "Run focused validation tests"]
    assert len(commands) == 1
    selected = shlex.split(commands[0])
    assert selected[:3] == ["python", "-m", "pytest"]
    for name in ("test_canonical_pages_checkout.py", "test_gaza_public_history_reconciliation.py",
                 "test_gaza_correction_activation.py", "test_compact_pytest_basetemp.py",
                 "test_validation_profiles.py", "test_fixture_lifecycle.py"):
        assert selected.count("tests/" + name) == 1


def test_duplicate_real_stage_zero_index_entry_must_refuse(fixture):
    source, _, _, oid = fixture
    path = source / ".git/index"
    data = path.read_bytes()
    magic, version, count = struct.unpack("!4sII", data[:12])
    assert magic == b"DIRC" and version == 2
    entries, pos = [], 12
    for _ in range(count):
        end = data.index(b"\0", pos + 62) + 1
        end += (-(end - pos)) % 8
        entries.append(data[pos:end]); pos = end
    payload = struct.pack("!4sII", magic, version, count + 1)
    payload += entries[0] + b"".join(entries) + data[pos:-20]
    path.write_bytes(payload + hashlib.sha1(payload).digest())
    rows = git(source, "ls-files", "--stage", "-z").rstrip(b"\0").split(b"\0")
    assert len(rows) == count + 1 and len(set(rows)) == count
    with pytest.raises(cp.CheckoutRefused, match="index"):
        cp.verify_canonical_checkout(source, oid)


def test_git_executable_mode_requires_owner_execute_bit(fixture):
    source, parent, destination, _ = fixture
    target = source / "image.bin"; target.chmod(0o755)
    git(source, "add", "image.bin"); git(source, "commit", "-qm", "synthetic executable")
    oid = git(source, "rev-parse", "HEAD").decode().strip()
    clone((source, parent, destination, oid))
    (destination / "image.bin").chmod(0o654)  # Group executable, owner not executable.
    assert b"mode change 100755 => 100644 image.bin" in git(destination, "diff", "--summary")
    with pytest.raises(cp.CheckoutRefused, match="working mode"):
        cp.verify_canonical_checkout(destination, oid)


@pytest.mark.parametrize("key", ["remote.origin.uploadpack", "remote.origin.vcs"])
def test_inherited_remote_command_refuses_before_execution_or_destination(fixture, monkeypatch, key):
    _, parent, destination, _ = fixture
    home = parent.parent / "synthetic-home"; home.mkdir()
    marker = home / "COMMAND_EXECUTED"
    helper = home / ("git-remote-reviewprobe" if key.endswith(".vcs") else "upload-pack-reviewprobe")
    helper.write_text('#!/bin/sh\nprintf invoked > "' + str(marker) + '"\nexit 1\n')
    helper.chmod(0o755)
    value = "reviewprobe" if key.endswith(".vcs") else str(helper)
    (home / ".gitconfig").write_text('[remote "origin"]\n\t' + key.rsplit(".", 1)[1] + ' = ' + value + '\n')
    monkeypatch.setenv("HOME", str(home)); monkeypatch.setenv("USERPROFILE", str(home))
    if key.endswith(".vcs"):
        monkeypatch.setenv("PATH", str(home) + os.pathsep + os.environ["PATH"])
    with pytest.raises(cp.CheckoutRefused, match="execution/redirect configuration"):
        clone(fixture)
    assert not marker.exists() and not destination.exists()


def test_inherited_remote_metadata_also_refuses_before_clone(fixture, monkeypatch):
    _, parent, destination, _ = fixture
    home = parent.parent / "synthetic-home"; home.mkdir()
    (home / ".gitconfig").write_text('[remote "origin"]\n\turl = /unused/local/source\n')
    monkeypatch.setenv("HOME", str(home)); monkeypatch.setenv("USERPROFILE", str(home))
    with pytest.raises(cp.CheckoutRefused, match="execution/redirect configuration"):
        clone(fixture)
    assert not destination.exists()


def test_ordinary_source_remote_metadata_remains_supported(fixture):
    source, _, _, _ = fixture
    git(source, "remote", "add", "origin", "https://example.invalid/never-contacted.git")
    assert clone(fixture)["ok"]


def test_redirected_git_config_refuses_before_git_reads_it(fixture, monkeypatch):
    source, parent, destination, _ = fixture
    config = source / ".git/config"
    held = parent.parent / "config-elsewhere"; config.rename(held); config.symlink_to(held)
    def forbidden(*_args):
        raise AssertionError("Git must not read redirected repository metadata")
    monkeypatch.setattr(cp, "_git", forbidden)
    with pytest.raises(cp.CheckoutRefused, match="link/reparse"):
        clone(fixture)
    assert not destination.exists()


def test_fixture_system_config_remains_visible_to_production_guard(fixture):
    _, parent, destination, _ = fixture
    system = parent.parent / "git-environment/system.gitconfig"
    system.write_text('[filter "synthetic"]\n\tsmudge = must-not-execute\n')
    with pytest.raises(cp.CheckoutRefused, match="execution/redirect configuration"):
        clone(fixture)
    assert not destination.exists()


def test_fixture_xdg_config_remains_visible_to_production_guard(fixture):
    _, parent, destination, _ = fixture
    xdg = Path(os.environ["XDG_CONFIG_HOME"]) / "git"
    xdg.mkdir()
    (xdg / "config").write_text('[filter "synthetic"]\n\tsmudge = must-not-execute\n')
    with pytest.raises(cp.CheckoutRefused, match="execution/redirect configuration"):
        clone(fixture)
    assert not destination.exists()


def test_fixture_git_adapter_never_runs_outside_its_synthetic_root(fixture):
    source, _, _, _ = fixture
    with pytest.raises(AssertionError):
        git(source.parent.parent, "config", "--list")
