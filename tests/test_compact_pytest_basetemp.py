"""Compact allocator regressions; synthetic fixtures only."""
import concurrent.futures
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import validation_profiles as candidate



class ShortBaseTemp(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="compact-", dir=os.environ["DISPATCHES_TEST_ROOT"])
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_short_unique_children_same_explicit_root_and_no_reuse(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            paths = list(pool.map(lambda _: candidate.make_pytest_basetemp(
                "bluefern-pytest-gaza", root=self.root), range(24)))
        self.assertEqual(len(set(paths)), 24)
        for path in paths:
            self.assertEqual(path.parent, self.root.resolve())
            self.assertTrue(path.is_dir())
            self.assertLessEqual(len(path.name), 10)
        evidence = paths[0] / "failed-fixture-evidence.txt"
        evidence.write_text("retain")
        new = candidate.make_pytest_basetemp("bluefern-pytest-gaza", root=self.root)
        self.assertNotIn(new, paths)
        self.assertEqual(evidence.read_text(), "retain")

    def test_missing_root_never_falls_back_to_temp(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(candidate.tempfile, "mkdtemp") as create:
            with self.assertRaisesRegex(RuntimeError, "explicitly name"):
                candidate.make_pytest_basetemp("gaza")
            create.assert_not_called()

    def test_capacity_failures_precede_allocation(self):
        for fail_at in [1, 2]:
            calls = []
            def check(path):
                calls.append(path)
                if len(calls) == fail_at:
                    raise RuntimeError("capacity admission refused")
                return Path(path)
            with patch.object(candidate, "require_test_root_capacity", side_effect=check), \
                 patch.object(candidate, "explicit_test_root", return_value=self.root), \
                 patch.object(candidate.tempfile, "mkdtemp") as create:
                with self.assertRaisesRegex(RuntimeError, "admission refused"):
                    candidate.make_pytest_basetemp("gaza", root=self.root)
                create.assert_not_called()

    def test_redirect_root_is_still_rejected(self):
        with patch.object(Path, "is_symlink", return_value=True), \
             patch.object(candidate.tempfile, "mkdtemp") as create:
            with self.assertRaisesRegex(RuntimeError, "redirect"):
                candidate.make_pytest_basetemp("gaza", root=self.root)
            create.assert_not_called()

    def test_full_tree_copy_keeps_all_relative_files_and_bytes(self):
        source = self.root / "source"
        files = [
            Path("short.json"),
            Path("review/proposed-editions/deep/archive/") / ("care-event-" + "x" * 54 + ".json"),
        ]
        for i, relative in enumerate(files):
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(bytes([i]) * 17)
        base = candidate.make_pytest_basetemp("bluefern-pytest-gaza", root=self.root)
        target = base / "dispatches-site-template0/repo/data/dispatches/care-line"
        shutil.copytree(source, target)
        self.assertEqual(sorted(p.relative_to(target) for p in target.rglob("*") if p.is_file()), sorted(files))
        for relative in files:
            self.assertEqual((source / relative).read_bytes(), (target / relative).read_bytes())

    def test_allocation_error_propagates_without_fallback_or_cleanup(self):
        marker = self.root / "prior-failed-copy.txt"
        marker.write_text("preserve")
        with patch.object(candidate.tempfile, "mkdtemp", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                candidate.make_pytest_basetemp("gaza", root=self.root)
        self.assertEqual(marker.read_text(), "preserve")

    def test_reported_path_budget_model_not_native_windows_proof(self):
        old_component = "bluefern-pytest-gaza-3120-1793192865065"
        new = candidate.make_pytest_basetemp("gaza", root=self.root)
        projected = 261 - len(old_component) + len(new.name)
        self.assertEqual(len(old_component), 39)
        self.assertEqual(projected, 232)
        self.assertLess(projected, 260)

    def test_longer_root_is_not_silently_relocated_or_certified(self):
        longer = self.root / ("long-" + "x" * 70)
        longer.mkdir()
        child = candidate.make_pytest_basetemp("gaza", root=longer)
        self.assertEqual(child.parent, longer)
        # Short naming is not a general guarantee for arbitrary root lengths.
        self.assertGreater(261 - 39 + len(child.name) + 75, 260)

    def test_collision_is_not_reused_and_existing_evidence_survives(self):
        occupied = self.root / "p-occupied"
        occupied.mkdir()
        marker = occupied / "evidence.txt"
        marker.write_text("preserve")
        with patch.object(candidate.tempfile, "_get_candidate_names",
                          return_value=iter(["occupied", "freshone"])):
            allocated = candidate.make_pytest_basetemp("gaza", root=self.root)
        self.assertEqual(allocated, self.root / "p-freshone")
        self.assertEqual(marker.read_text(), "preserve")



if __name__ == "__main__":
    unittest.main(verbosity=2)
