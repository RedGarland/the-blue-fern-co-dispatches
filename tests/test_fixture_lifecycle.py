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
    cleanup = "cleanup=lambda path: (path.joinpath('evidence.txt').unlink(), (_ for _ in ()).throw(OSError('cleanup failure')))[1]" if case == "cleanup_failure" else ""
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


def test_success_is_removed_only_after_all_phases_pass(pytester):
    result, _, roots = _run_case(pytester, 'success')
    assert result.ret == 0
    assert len(roots) == 1 and not roots[0].exists()


@pytest.mark.parametrize('case', ['setup_failure', 'test_failure', 'teardown_failure', 'skip', 'xfail', 'keep'])
def test_incomplete_or_non_successful_runs_are_retained(pytester, case):
    result, _, roots = _run_case(pytester, case)
    assert len(roots) == 1 and roots[0].exists()
    assert (roots[0] / 'evidence.txt').exists()
    assert (result.ret == 0) if case in {'skip', 'xfail', 'keep'} else (result.ret != 0)


def test_cleanup_failure_is_visible_and_retained(pytester):
    result, output, roots = _run_case(pytester, 'cleanup_failure')
    assert result.ret == 0 and len(roots) == 1 and roots[0].exists()
    assert not (roots[0] / 'evidence.txt').exists()
    assert 'cleanup failure' in output and 'retained' in output


def test_shared_template_is_retained_when_child_cleanup_fails(pytester):
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
    register_fixture_root(request, root, parent_group='shared', cleanup=lambda path: (_ for _ in ()).throw(OSError('child cleanup failure')))
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
