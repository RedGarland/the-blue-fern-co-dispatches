from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
if (ROOT / "src" / "bluefern_dispatches" / "bluesky_post.py").exists():
    PACKAGE_ROOT = ROOT
elif (ROOT.parent / "src" / "bluefern_dispatches" / "bluesky_post.py").exists():
    PACKAGE_ROOT = ROOT.parent
else:
    PACKAGE_ROOT = ROOT / ".bluesky-card-correction-review-package-20261026"
PACKAGE_SRC = PACKAGE_ROOT / "src"
BASE_SRC = PACKAGE_SRC
sys.path.insert(0, str(PACKAGE_SRC))
sys.path.insert(1, str(BASE_SRC))
candidate_path = PACKAGE_SRC / "bluefern_dispatches" / "bluesky_post.py"
spec = importlib.util.spec_from_file_location("packaged_bluesky_post", candidate_path)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def test_packaged_source_imports_its_contract_dependency():
    assert module.ExternalCardContract.__module__ == "bluesky_card_contract_private"
    assert callable(module.delete_verified_gaza_card_correction)


def test_non_gaza_food_path_retains_skip_behavior():
    result = module.maybe_post_food_line_dispatch_to_bluesky(
        edition_date="2026-10-25",
        public_url="https://dispatches.thebluefernco.com/food-line/editions/2026-10-25/",
        post_text=None,
        run_succeeded=False,
        public_rendered=False,
        public_signal_count=0,
        post_requested=True,
        project_root=ROOT,
    )
    assert result["status"] == "skipped"
    assert result["reason"] == "run_failed"
