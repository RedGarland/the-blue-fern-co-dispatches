from pathlib import Path

from scripts.validation_profiles import (
    PROFILE_AMERICAN_PRESSURE_WEEKLY,
    PROFILE_CASCADIA_WEEKLY,
    PROFILE_FULL_PROJECT,
    PROFILE_GAZA_DAILY,
    get_profile,
    make_pytest_basetemp,
    pytest_command,
)


def test_profile_test_membership_is_scoped():
    gaza = get_profile(PROFILE_GAZA_DAILY)
    cascadia = get_profile(PROFILE_CASCADIA_WEEKLY)
    ap = get_profile(PROFILE_AMERICAN_PRESSURE_WEEKLY)
    full = get_profile(PROFILE_FULL_PROJECT)

    assert "tests/test_american_pressure_dispatch.py" not in gaza.tests
    assert "tests/test_american_pressure_dispatch.py" not in cascadia.tests
    assert "tests/test_american_pressure_dispatch.py" in ap.tests
    assert full.tests == ()


def test_pytest_command_includes_expected_k_filters():
    temp = make_pytest_basetemp("bluefern-pytest-profile-test")
    gaza_cmd = pytest_command(PROFILE_GAZA_DAILY, temp)
    cascadia_cmd = pytest_command(PROFILE_CASCADIA_WEEKLY, temp)
    full_cmd = pytest_command(PROFILE_FULL_PROJECT, temp)

    assert "tests/test_dispatches_site.py" in gaza_cmd
    assert "not american_pressure" in " ".join(gaza_cmd)
    assert "tests/test_cascadia_pipeline.py" in cascadia_cmd
    assert "not american_pressure and not gaza" in " ".join(cascadia_cmd)
    assert "tests/test_american_pressure_dispatch.py" not in full_cmd
    assert full_cmd.count("-m") == 1
    assert Path(temp).exists()
