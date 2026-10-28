from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import pytest


pytest_plugins = ("pytester",)


def _run_case(pytester, case: str):
    repo_tests = Path(__file__).resolve().parent
    pytester.makeconftest(
        f"import sys; sys.path.insert(0, {str(repo_tests.parent)!r}); "
        f"sys.path.insert(0, {str(repo_tests)!r}); pytest_plugins = ('fixture_lifecycle',)"
    )
    setup = "" if case != "setup_failure" else "raise RuntimeError('setup failure')"
    test_body = {
        "success": "assert root.joinpath('evidence.txt').exists()",
        "setup_failure": "assert False, 'setup failure'",
        "test_failure": "assert False, 'test failure'",
        "skip": "pytest.skip('skip')",
        "xfail": "pytest.xfail('xfail')",
        "keep": "assert root.joinpath('evidence.txt').exists()",
        "teardown_failure": "assert root.joinpath('evidence.txt').exists()",
        "cleanup_failure": "assert root.joinpath('evidence.txt').exists()",
    }[case]
    mark = "@pytest.mark.keep_fixture\n" if case == "keep" else ""
    cleanup = "cleanup=lambda path: path.joinpath('cleanup-called.txt').write_text('must not run', encoding='utf-8')" if case == "cleanup_failure" else ""
    teardown = "raise RuntimeError('teardown failure')" if case == "teardown_failure" else ""
    pytester.makepyfile(test_case=f"""
import pytest
from fixture_lifecycle import mark_fixture_setup_complete, register_fixture_root

@pytest.fixture
def root(request, tmp_path_factory):
    root = tmp_path_factory.mktemp('fixture')
    print(f'FIXTURE_ROOT={{root}}')
    register_fixture_root(request, root, {cleanup})
    (root / 'evidence.txt').write_text('evidence', encoding='utf-8')
    {setup}
    mark_fixture_setup_complete(request, root)
    yield root
    {teardown}

{mark}def test_case(root):
    {test_body}
""")
    result = pytester.runpytest_subprocess('-s', '--strict-markers', '--basetemp', str(pytester.path / 'child-basetemp'))
    output = result.stdout.str() + result.stderr.str()
    roots = [Path(value.strip()) for value in re.findall(r'FIXTURE_ROOT=(.*child-basetemp[^\r\n]*)', output)]
    return result, output, roots


def test_success_is_retained_without_verified_cleanup_backend(pytester):
    result, output, roots = _run_case(pytester, 'success')
    assert result.ret == 0
    assert len(roots) == 1 and roots[0].exists()
    assert (roots[0] / 'evidence.txt').read_text(encoding='utf-8') == 'evidence'
    assert 'automatic cleanup deferred (no verified object-bound backend)' in output


@pytest.mark.parametrize('case', ['setup_failure', 'test_failure', 'teardown_failure', 'skip', 'xfail', 'keep'])
def test_incomplete_or_non_successful_runs_are_retained(pytester, case):
    result, _, roots = _run_case(pytester, case)
    assert len(roots) == 1 and roots[0].exists()
    assert (roots[0] / 'evidence.txt').exists()
    assert (result.ret == 0) if case in {'skip', 'xfail', 'keep'} else (result.ret != 0)


def test_unverified_cleanup_callback_is_not_invoked(pytester):
    result, output, roots = _run_case(pytester, 'cleanup_failure')
    assert result.ret == 0 and len(roots) == 1 and roots[0].exists()
    assert (roots[0] / 'evidence.txt').read_text(encoding='utf-8') == 'evidence'
    assert not (roots[0] / 'cleanup-called.txt').exists()
    assert 'automatic cleanup deferred' in output and 'retained' in output


def test_shared_template_is_retained_when_child_cleanup_is_deferred(pytester):
    repo_tests = Path(__file__).resolve().parent
    pytester.makeconftest(
        f"import sys; sys.path.insert(0, {str(repo_tests.parent)!r}); "
        f"sys.path.insert(0, {str(repo_tests)!r}); pytest_plugins = ('fixture_lifecycle',)"
    )
    pytester.makepyfile(test_shared="""
import shutil
import pytest
from fixture_lifecycle import mark_fixture_setup_complete, register_fixture_root

@pytest.fixture(scope='session')
def template(request, tmp_path_factory):
    root = tmp_path_factory.mktemp('template')
    register_fixture_root(request, root, group='shared')
    (root / 'template.txt').write_text('template', encoding='utf-8')
    mark_fixture_setup_complete(request, root)
    print(f'TEMPLATE_ROOT={root}')
    return root

@pytest.fixture
def child(request, tmp_path_factory, template):
    root = tmp_path_factory.mktemp('child')
    register_fixture_root(request, root, parent_group='shared', cleanup=lambda path: path.joinpath('cleanup-called.txt').write_text('must not run', encoding='utf-8'))
    shutil.copy2(template / 'template.txt', root / 'child.txt')
    mark_fixture_setup_complete(request, root)
    print(f'CHILD_ROOT={root}')
    return root

def test_child(child):
    assert child.exists()
""")
    result = pytester.runpytest_subprocess('-s', '--strict-markers', '--basetemp', str(pytester.path / 'child-basetemp'))
    output = result.stdout.str() + result.stderr.str()
    template = Path(re.search(r'TEMPLATE_ROOT=(.+)', output).group(1).strip())
    child = Path(re.search(r'CHILD_ROOT=(.+)', output).group(1).strip())
    assert result.ret == 0 and child.exists() and template.exists()
    assert not (child / 'cleanup-called.txt').exists()
    assert (child / 'child.txt').read_text(encoding='utf-8') == 'template'
    assert (template / 'template.txt').read_text(encoding='utf-8') == 'template'
    assert 'shared template' in output


def test_root_replacement_and_ancestor_redirection_are_refused(tmp_path, monkeypatch):
    import fixture_lifecycle
    from fixture_lifecycle import FixtureRunRegistry

    basetemp = tmp_path / 'base'
    basetemp.mkdir()
    root = basetemp / 'root'
    root.mkdir()

    class Config:
        _tmp_path_factory = type('Factory', (), {'getbasetemp': lambda self: basetemp})()

    class Request:
        config = Config()
        node = type('Node', (), {'nodeid': 'test', 'get_closest_marker': lambda self, name: None})()

    (root / 'evidence.txt').write_text('original fixture', encoding='utf-8')
    registry = FixtureRunRegistry()
    record = registry.register(Request(), root)
    # Keep the original inode allocated: remove/recreate may recycle its
    # identity tuple and cannot deterministically exercise identity-mismatch
    # detection. Inode-reuse acceptance is outside this test's guarantee.
    original_root = basetemp / 'original-root'
    root.rename(original_root)
    root.mkdir()
    assert fixture_lifecycle._identity(original_root) == record.root_identity
    assert fixture_lifecycle._identity(root) != record.root_identity
    assert not registry._boundary_ok(record)
    assert (original_root / 'evidence.txt').read_text(encoding='utf-8') == 'original fixture'

    ancestor_base = basetemp / 'ancestor-base'
    ancestor_base.mkdir()
    ancestor_root = ancestor_base / 'root'
    ancestor_root.mkdir()
    ancestor_record = registry.register(Request(), ancestor_root)
    redirected = tmp_path / 'redirected'
    redirected.mkdir()
    original_ancestor = tmp_path / 'ancestor-original'
    os.rename(ancestor_base, original_ancestor)
    try:
        os.symlink(redirected, ancestor_base, target_is_directory=True)
    except (OSError, NotImplementedError):
        os.rename(original_ancestor, ancestor_base)
        monkeypatch.setattr(fixture_lifecycle, '_is_redirect', lambda path: path == ancestor_base)
    assert not registry._boundary_ok(ancestor_record)


def test_preexisting_redirected_root_is_rejected_and_target_survives(tmp_path, monkeypatch):
    import fixture_lifecycle
    from fixture_lifecycle import FixtureRunRegistry

    basetemp = tmp_path / 'base'
    basetemp.mkdir()
    target = tmp_path / 'target'
    target.mkdir()
    evidence = target / 'evidence.txt'
    evidence.write_text('keep', encoding='utf-8')
    redirected = basetemp / 'fixture'
    try:
        os.symlink(target, redirected, target_is_directory=True)
    except (OSError, NotImplementedError):
        monkeypatch.setattr(fixture_lifecycle, '_is_redirect', lambda path: path == redirected)

    class Config:
        _tmp_path_factory = type('Factory', (), {'getbasetemp': lambda self: basetemp})()

    class Request:
        config = Config()
        node = type('Node', (), {'nodeid': 'test', 'get_closest_marker': lambda self, name: None})()

    with pytest.raises(ValueError, match='redirect'):
        FixtureRunRegistry().register(Request(), redirected)
    assert evidence.read_text(encoding='utf-8') == 'keep'


def test_session_error_retains_template_after_successful_child(pytester):
    repo_tests = Path(__file__).resolve().parent
    pytester.makeconftest(
        f"import sys; sys.path.insert(0, {str(repo_tests.parent)!r}); "
        f"sys.path.insert(0, {str(repo_tests)!r}); pytest_plugins = ('fixture_lifecycle',)"
    )
    pytester.makepyfile(test_interrupt="""
import pytest
from fixture_lifecycle import mark_fixture_setup_complete, register_fixture_root

@pytest.fixture(scope='session', autouse=True)
def late_session_error(request):
    def fail():
        raise RuntimeError('late session teardown failure')
    request.addfinalizer(fail)

@pytest.fixture(scope='session')
def template(request, tmp_path_factory):
    root = tmp_path_factory.mktemp('template')
    register_fixture_root(request, root, group='shared')
    (root / 'template.txt').write_text('template', encoding='utf-8')
    mark_fixture_setup_complete(request, root)
    print(f'TEMPLATE_ROOT={root}')
    return root

@pytest.fixture
def child(request, tmp_path_factory, template):
    root = tmp_path_factory.mktemp('child')
    register_fixture_root(request, root, parent_group='shared')
    (root / 'child.txt').write_text('child', encoding='utf-8')
    mark_fixture_setup_complete(request, root)
    print(f'CHILD_ROOT={root}')
    return root

def test_child_passes_before_late_session_error(child):
    assert child.exists()
""")
    result = pytester.runpytest_subprocess('-s', '--strict-markers', '--basetemp', str(pytester.path / 'child-basetemp'))
    output = result.stdout.str() + result.stderr.str()
    template = Path(re.search(r'TEMPLATE_ROOT=(.+)', output).group(1).strip())
    assert result.ret != 0 and template.exists()
    assert 'late session teardown failure' in output


def test_missing_teardown_is_retained():
    from fixture_lifecycle import FixtureRun, FixtureRunRegistry

    record = FixtureRun(root=Path('fixture'), nodeid='test', basetemp=Path('base'), setup_complete=True)
    record.reports = {
        'setup': type('Report', (), {'outcome': 'passed', 'wasxfail': False})(),
        'call': type('Report', (), {'outcome': 'passed', 'wasxfail': False})(),
    }
    assert FixtureRunRegistry._must_retain(record)


def _retention_registry(tmp_path):
    from types import SimpleNamespace
    from fixture_lifecycle import FixtureRunRegistry

    base = tmp_path / 'base'
    base.mkdir()
    request = SimpleNamespace(
        config=SimpleNamespace(_tmp_path_factory=SimpleNamespace(getbasetemp=lambda: base)),
        node=SimpleNamespace(nodeid='retention-case', get_closest_marker=lambda _: None),
    )
    return FixtureRunRegistry(), request, base


def _mark_retention_case_passed(record):
    from types import SimpleNamespace

    record.setup_complete = True
    record.reports = {
        phase: SimpleNamespace(outcome='passed', wasxfail=False)
        for phase in ('setup', 'call', 'teardown')
    }


def test_successful_record_never_invokes_path_callback(tmp_path):
    registry, request, base = _retention_registry(tmp_path)
    root = base / 'root'
    root.mkdir()
    evidence = root / 'evidence.txt'
    evidence.write_text('retain', encoding='utf-8')
    calls = []
    record = registry.register(request, root, cleanup=calls.append)
    _mark_retention_case_passed(record)

    registry.finalize()
    registry.finalize()

    assert calls == []
    assert record.cleanup_deferred
    assert not record.cleanup_failed and not record.boundary_failed
    assert evidence.read_text(encoding='utf-8') == 'retain'


def test_default_cleanup_is_deferred_for_successful_root(tmp_path):
    registry, request, base = _retention_registry(tmp_path)
    root = base / 'root'
    root.mkdir()
    evidence = root / 'evidence.txt'
    evidence.write_text('retain', encoding='utf-8')
    record = registry.register(request, root)
    _mark_retention_case_passed(record)

    registry.finalize()

    assert record.cleanup_deferred and not record.cleanup_failed
    assert evidence.read_text(encoding='utf-8') == 'retain'


def test_complete_standalone_template_is_deferred(tmp_path):
    registry, request, base = _retention_registry(tmp_path)
    root = base / 'template'
    root.mkdir()
    calls = []
    record = registry.register(request, root, group='standalone', cleanup=calls.append)
    record.setup_complete = True

    registry.finalize()

    assert record.cleanup_deferred and root.is_dir()
    assert calls == []


def test_existing_retention_reasons_do_not_attempt_cleanup(tmp_path):
    registry, request, base = _retention_registry(tmp_path)
    calls = []
    records = []
    for reason in ('setup_incomplete', 'missing_teardown', 'setup_failed', 'call_failed',
                   'teardown_failed', 'skipped', 'xfail', 'keep'):
        root = base / reason
        root.mkdir()
        (root / 'evidence.txt').write_text(reason, encoding='utf-8')
        record = registry.register(request, root, keep=reason == 'keep', cleanup=calls.append)
        _mark_retention_case_passed(record)
        if reason == 'setup_incomplete':
            record.setup_complete = False
        elif reason == 'missing_teardown':
            record.reports.pop('teardown')
        elif reason.endswith('_failed'):
            record.reports[reason.removesuffix('_failed')].outcome = 'failed'
        elif reason == 'skipped':
            record.reports['call'].outcome = 'skipped'
        elif reason == 'xfail':
            record.reports['call'].wasxfail = True
        records.append((reason, record))

    registry.finalize()

    assert calls == []
    for reason, record in records:
        assert not record.cleanup_deferred
        assert not record.cleanup_failed and not record.boundary_failed
        assert (record.root / 'evidence.txt').read_text(encoding='utf-8') == reason


def test_replacement_is_refused_without_invoking_cleanup(tmp_path):
    registry, request, base = _retention_registry(tmp_path)
    root = base / 'root'
    root.mkdir()
    (root / 'original.txt').write_text('original', encoding='utf-8')
    calls = []
    record = registry.register(request, root, cleanup=calls.append)
    _mark_retention_case_passed(record)
    original = base / 'original'
    root.rename(original)
    root.mkdir()
    (root / 'replacement.txt').write_text('replacement', encoding='utf-8')

    registry.finalize()

    assert calls == [] and record.boundary_failed
    assert not record.cleanup_deferred and not record.cleanup_failed
    assert (original / 'original.txt').read_text(encoding='utf-8') == 'original'
    assert (root / 'replacement.txt').read_text(encoding='utf-8') == 'replacement'


def test_deferred_child_retains_complete_shared_template(tmp_path):
    registry, request, base = _retention_registry(tmp_path)
    template = base / 'template'
    template.mkdir()
    child = base / 'child'
    child.mkdir()
    calls = []
    template_record = registry.register(request, template, group='shared', cleanup=calls.append)
    template_record.setup_complete = True
    child_record = registry.register(request, child, parent_group='shared', cleanup=calls.append)
    _mark_retention_case_passed(child_record)

    registry.finalize()

    assert child_record.cleanup_deferred
    assert not template_record.cleanup_deferred
    assert template.is_dir() and child.is_dir() and calls == []


def test_session_error_still_retains_complete_template(tmp_path):
    registry, request, base = _retention_registry(tmp_path)
    root = base / 'template'
    root.mkdir()
    calls = []
    record = registry.register(request, root, group='shared', cleanup=calls.append)
    record.setup_complete = True

    registry.finalize(retain_templates=True)

    assert root.is_dir() and calls == []
    assert not record.cleanup_deferred and not record.cleanup_failed
