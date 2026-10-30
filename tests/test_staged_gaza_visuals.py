"""Pure regression tests; real browser execution belongs to the CI job."""
import importlib.util
from pathlib import Path
import unittest
from copy import deepcopy

PATH = Path(__file__).resolve().parents[1] / "scripts/check_staged_gaza_visuals.py"
spec = importlib.util.spec_from_file_location("staged_gaza_visuals", PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class OverrideTests(unittest.TestCase):
    def source(self, body=b".archive--gaza .archive-intro { padding: 0 0 .5rem; }\n"):
        return b"/* source-only unrelated CSS must not leak */\n" + module.MARKER + body

    def test_only_override_is_appended(self):
        baseline = b"/* public historical CSS */\r\n"
        got = module.candidate_css(baseline, self.source())
        self.assertEqual(got, baseline + module.MARKER + b".archive--gaza .archive-intro { padding: 0 0 .5rem; }\n")
        self.assertNotIn(b"source-only", got)

    def test_previous_override_replaced_not_stacked(self):
        old = b"base\r\n" + module.MARKER + b".archive--gaza { padding: 99rem; }\n"
        got = module.candidate_css(old, self.source())
        self.assertEqual(got.count(module.MARKER), 1)
        self.assertNotIn(b"99rem", got)
        self.assertTrue(got.startswith(b"base\r\n"))

    def test_current_candidate_idempotent(self):
        first = module.candidate_css(b"base", self.source())
        self.assertEqual(first, module.candidate_css(first, self.source()))

    def test_missing_marker_refused(self):
        with self.assertRaises(ValueError):
            module.candidate_css(b"base", b"anything")

    def test_duplicate_marker_refused(self):
        for baseline, source in [(module.MARKER * 2, self.source()), (b"base", self.source() * 2)]:
            with self.assertRaises(ValueError):
                module.candidate_css(baseline, source)

    def test_unrelated_selector_refused(self):
        for selector in (b"body", b".archive--gaza-else", b".archive--gaza, body", b".archive--gaza + body", b".archive--gaza ~ p"):
            with self.assertRaises(ValueError):
                module.archive_override(self.source(selector + b" { padding: 0; }"))

    def test_resource_import_and_important_refused(self):
        for declaration in (b"background: url(https://example.org/a)", b"padding: 0 !important", b"@import 'x'"):
            with self.assertRaises(ValueError):
                module.archive_override(self.source(b".archive--gaza { " + declaration + b"; }"))

    def test_trailing_unparsed_content_refused(self):
        with self.assertRaises(ValueError):
            module.archive_override(self.source() + b"unsafe;")

    def test_flat_scoped_rules_and_clamp_supported(self):
        body = b".archive--gaza { padding: 1rem 0 2rem; }\n.archive--gaza .archive-intro h1 { font-size: clamp(1.8rem, 3vw, 2.5rem); }\n"
        self.assertEqual(module.archive_override(self.source(body)), module.MARKER + body)


class RouteEvidenceTests(unittest.TestCase):
    def result(self):
        return {"paths_checked": ["/gaza/"], "pages": [{"path": "/gaza/", "status": 200, "screenshot": "x.png"}], "screenshot_hashes": {"x.png": "abc"}}

    def test_complete_evidence_accepted(self):
        module.require_route_evidence(self.result(), count=1)

    def test_missing_route_refused(self):
        with self.assertRaises(ValueError):
            module.require_route_evidence(self.result(), count=7)

    def test_duplicate_routes_refused(self):
        r = self.result()
        r["paths_checked"] *= 2
        r["pages"] *= 2
        with self.assertRaises(ValueError):
            module.require_route_evidence(r, count=2)

    def test_bad_status_or_screenshot_refused(self):
        for change in ({"status": 404}, {"screenshot": None}, {"path": "/other/"}):
            r = self.result()
            r["pages"][0].update(change)
            with self.assertRaises(ValueError):
                module.require_route_evidence(r, count=1)

    def test_missing_hash_refused(self):
        r = self.result()
        r["screenshot_hashes"] = {}
        with self.assertRaises(ValueError):
            module.require_route_evidence(r, count=1)


if __name__ == "__main__":
    unittest.main()
