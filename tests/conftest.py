import sys
from pathlib import Path

from scripts.test_storage_admission import (
    explicit_test_root,
    require_test_root_capacity,
    validate_pytest_basetemp,
)

pytest_plugins = ("fixture_lifecycle",)


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def pytest_sessionstart(session):
    # Direct pytest invocation must enforce the same two-volume admission as
    # the safe launcher; callers cannot bypass it by invoking pytest directly.
    require_test_root_capacity(ROOT)
    configured_root = explicit_test_root()
    basetemp = getattr(session.config.option, "basetemp", None)
    validate_pytest_basetemp(basetemp, configured_root)
