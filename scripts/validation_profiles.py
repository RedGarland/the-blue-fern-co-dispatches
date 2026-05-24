from __future__ import annotations

import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class ValidationProfile:
    name: str
    description: str
    tests: tuple[str, ...]
    extra_pytest_args: tuple[str, ...] = ()

    @property
    def skipped_unrelated_tests(self) -> bool:
        return self.name != "full_project"


PROFILE_GAZA_DAILY = "gaza_daily"
PROFILE_CASCADIA_WEEKLY = "cascadia_weekly"
PROFILE_AMERICAN_PRESSURE_WEEKLY = "american_pressure_weekly"
PROFILE_FULL_PROJECT = "full_project"

VALIDATION_PROFILES: dict[str, ValidationProfile] = {
    PROFILE_GAZA_DAILY: ValidationProfile(
        name=PROFILE_GAZA_DAILY,
        description="Gaza daily dispatch plus shared publish-safety checks.",
        tests=(
            "tests/test_gaza_sources.py",
            "tests/test_gaza_dispatch_generation.py",
            "tests/test_gaza_backfill.py",
            "tests/test_run_daily_gaza.py",
            "tests/test_dispatches_site.py",
        ),
        extra_pytest_args=("-k", "not american_pressure"),
    ),
    PROFILE_CASCADIA_WEEKLY: ValidationProfile(
        name=PROFILE_CASCADIA_WEEKLY,
        description="Cascadia weekly pipeline plus shared publish-safety checks.",
        tests=(
            "tests/test_cascadia_pipeline.py",
            "tests/test_cascadia_notify.py",
            "tests/test_dispatches_site.py",
        ),
        extra_pytest_args=("-k", "not american_pressure and not gaza"),
    ),
    PROFILE_AMERICAN_PRESSURE_WEEKLY: ValidationProfile(
        name=PROFILE_AMERICAN_PRESSURE_WEEKLY,
        description="American Pressure weekly workflow tests.",
        tests=(
            "tests/test_american_pressure_sources.py",
            "tests/test_american_pressure_candidates.py",
            "tests/test_american_pressure_dispatch.py",
            "tests/test_dispatches_control_panel.py",
        ),
    ),
    PROFILE_FULL_PROJECT: ValidationProfile(
        name=PROFILE_FULL_PROJECT,
        description="Entire project validation suite.",
        tests=(),
    ),
}


def profile_names() -> tuple[str, ...]:
    return tuple(VALIDATION_PROFILES.keys())


def get_profile(name: str) -> ValidationProfile:
    if name not in VALIDATION_PROFILES:
        supported = ", ".join(profile_names())
        raise ValueError(f"Unsupported validation profile '{name}'. Supported: {supported}")
    return VALIDATION_PROFILES[name]


def make_pytest_basetemp(prefix: str) -> Path:
    unique = f"{prefix}-{os.getpid()}-{int(time.time() * 1000)}"
    path = Path(tempfile.gettempdir()) / unique
    path.mkdir(parents=True, exist_ok=True)
    return path


def pytest_command(profile_name: str, basetemp: Path) -> list[str]:
    profile = get_profile(profile_name)
    cmd = [
        ".\\.venv\\Scripts\\python.exe",
        "-B",
        "-m",
        "pytest",
    ]
    if profile.tests:
        cmd.extend(profile.tests)
    cmd.extend(
        [
            "-q",
            "-p",
            "no:cacheprovider",
            "--basetemp",
            str(basetemp),
        ]
    )
    if profile.extra_pytest_args:
        cmd.extend(profile.extra_pytest_args)
    return cmd


def tests_for_profile(profile_name: str) -> tuple[str, ...]:
    return get_profile(profile_name).tests


def apply_env_profile(default_profile: str, env_var: str = "DISPATCHES_VALIDATION_PROFILE") -> str:
    raw = str(os.getenv(env_var, "")).strip()
    return raw if raw else default_profile

