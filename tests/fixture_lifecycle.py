from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pytest

from scripts.test_storage_admission import require_test_root_capacity


def _identity(path: Path) -> tuple[int, int, int]:
    stat = os.lstat(path)
    return stat.st_dev, stat.st_ino, stat.st_mode


def _is_redirect(path: Path) -> bool:
    stat = os.lstat(path)
    return path.is_symlink() or bool(getattr(stat, "st_file_attributes", 0) & 0x400)


def _ancestors(root: Path, basetemp: Path) -> tuple[tuple[Path, tuple[int, int, int]], ...]:
    chain = []
    current = basetemp
    for part in root.relative_to(basetemp).parts:
        chain.append((current, _identity(current)))
        current = current / part
    chain.append((root, _identity(root)))
    return tuple(chain)


@dataclass
class FixtureRun:
    root: Path
    nodeid: str
    basetemp: Path
    keep_requested: bool = False
    group: str | None = None
    parent_group: str | None = None
    setup_complete: bool = False
    reports: dict[str, object] = field(default_factory=dict)
    cleanup: Callable[[Path], None] = shutil.rmtree
    root_identity: tuple[int, int, int] | None = None
    ancestor_identities: tuple[tuple[Path, tuple[int, int, int]], ...] = ()
    cleanup_failed: bool = False
    boundary_failed: bool = False
    cleanup_deferred: bool = False

    @property
    def is_template(self) -> bool:
        return self.group is not None


class FixtureRunRegistry:
    def __init__(self) -> None:
        self.basetemp: Path | None = None
        self.basetemp_identity: tuple[int, int, int] | None = None
        self.records: list[FixtureRun] = []

    def register(self, request, root: Path, *, group=None, parent_group=None, keep=False, cleanup=shutil.rmtree):
        if self.basetemp is None:
            self.basetemp = request.config._tmp_path_factory.getbasetemp().absolute()
            if _is_redirect(self.basetemp):
                raise ValueError(f"pytest basetemp must not be a redirect: {self.basetemp}")
            self.basetemp_identity = _identity(self.basetemp)
        assert self.basetemp is not None
        require_test_root_capacity(self.basetemp)
        lexical_root = root.absolute()
        if not lexical_root.is_dir() or _is_redirect(lexical_root):
            raise ValueError(f"fixture root must be a real directory created before registration: {root}")
        if lexical_root == self.basetemp or self.basetemp not in lexical_root.parents:
            raise ValueError(f"fixture root is outside pytest basetemp: {root}")
        lexical_chain = _ancestors(lexical_root, self.basetemp)
        if any(_is_redirect(path) for path, _ in lexical_chain):
            raise ValueError(f"fixture root or ancestor must not be a redirect: {root}")
        resolved = lexical_root.resolve(strict=True)
        if resolved != lexical_root:
            raise ValueError(f"fixture root must not resolve through a redirect: {root}")
        record = FixtureRun(
            root=lexical_root,
            nodeid=request.node.nodeid,
            basetemp=self.basetemp,
            keep_requested=keep or request.node.get_closest_marker("keep_fixture") is not None,
            group=group,
            parent_group=parent_group,
            cleanup=cleanup,
            root_identity=_identity(lexical_root),
            ancestor_identities=lexical_chain,
        )
        self.records.append(record)
        return record

    def mark_setup_complete(self, root: Path) -> None:
        self._record(root).setup_complete = True

    def record_report(self, item, report) -> None:
        for record in self.records:
            if record.nodeid == item.nodeid:
                record.reports[report.when] = report

    def _record(self, root: Path) -> FixtureRun:
        lexical_root = root.absolute()
        for record in self.records:
            if record.root == lexical_root:
                return record
        raise KeyError(f"unregistered fixture root: {root}")

    @staticmethod
    def _phase_success(record: FixtureRun) -> bool:
        phases = ("setup", "call", "teardown")
        return (
            record.setup_complete
            and all(phase in record.reports for phase in phases)
            and all(getattr(record.reports[phase], "outcome", None) == "passed" for phase in phases)
            and not any(bool(getattr(report, "wasxfail", False)) for report in record.reports.values())
        )

    @classmethod
    def _must_retain(cls, record: FixtureRun) -> bool:
        if record.is_template:
            return record.keep_requested or not record.setup_complete
        return record.keep_requested or not cls._phase_success(record)

    def _boundary_ok(self, record: FixtureRun) -> bool:
        try:
            if not record.root.is_dir() or _is_redirect(record.root):
                return False
            if _identity(record.root) != record.root_identity:
                return False
            resolved = record.root.resolve(strict=True)
            if resolved != record.root or resolved == record.basetemp or record.basetemp not in resolved.parents:
                return False
            if _identity(record.basetemp) != self.basetemp_identity:
                return False
            return all(not _is_redirect(path) and _identity(path) == identity for path, identity in record.ancestor_identities)
        except (FileNotFoundError, OSError, RuntimeError):
            return False

    def _cleanup_record(self, record: FixtureRun) -> None:
        if not self._boundary_ok(record):
            record.boundary_failed = True
            print(f"fixture cleanup refusal: boundary or identity changed; retained {record.root}", file=sys.stderr)
            return
        # A path check cannot bind later recursive deletion to this object.
        # Keep the cleanup argument for caller compatibility, but do not invoke
        # any path callback until a separately reviewed object-bound backend exists.
        record.cleanup_deferred = True
        print(
            f"fixture retention: automatic cleanup deferred (no verified object-bound backend); retained {record.root}",
            file=sys.stderr,
        )

    def finalize(self, *, retain_templates: bool = False) -> None:
        retain = {id(record): self._must_retain(record) for record in self.records}
        if retain_templates:
            for record in self.records:
                if record.is_template:
                    retain[id(record)] = True
        children = sorted((r for r in self.records if not r.is_template), key=lambda r: len(r.root.parts), reverse=True)
        for record in children:
            if retain[id(record)]:
                print(f"fixture retention: retained {record.root}", file=sys.stderr)
            else:
                self._cleanup_record(record)
                retain[id(record)] = record.cleanup_deferred or record.cleanup_failed or record.boundary_failed or record.root.exists()
        for record in self.records:
            if record.is_template and any(
                child.parent_group == record.group and (retain[id(child)] or child.cleanup_deferred or child.cleanup_failed or child.boundary_failed)
                for child in self.records
            ):
                retain[id(record)] = True
        templates = sorted((r for r in self.records if r.is_template), key=lambda r: len(r.root.parts), reverse=True)
        for record in templates:
            if retain[id(record)]:
                print(f"fixture retention: retained shared template {record.root}", file=sys.stderr)
            else:
                self._cleanup_record(record)


def pytest_configure(config):
    config.addinivalue_line("markers", "keep_fixture: retain the registered fixture root for investigation")
    config._fixture_run_registry = FixtureRunRegistry()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    registry = getattr(item.config, "_fixture_run_registry", None)
    if registry is not None:
        registry.record_report(item, report)


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    registry = getattr(session.config, "_fixture_run_registry", None)
    if registry is not None:
        registry.finalize(retain_templates=exitstatus != 0)


def register_fixture_root(request, root: Path, **kwargs):
    return request.config._fixture_run_registry.register(request, root, **kwargs)


def mark_fixture_setup_complete(request, root: Path) -> None:
    request.config._fixture_run_registry.mark_setup_complete(root)
