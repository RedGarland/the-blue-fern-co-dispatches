"""Provider-free real Git/subprocess regression; also runnable with unittest.

The copied, committed scheduler is the code executed in each validated root.
A subprocess-inherited audit hook refuses network access, not runtime guards.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from scripts import food_line_daily_scheduler as scheduler
from scripts import food_line_continuation_ledger as ledger

DATE = "2026-11-02"
RUN = "historical-selected-A"

# Exact minimal source closure, bounded even when CI checks out the full repo.
FIXTURE_FILES = (
    '.gitignore',
    'data/dispatches/food-line/discovery_expansion_config.json',
    'scripts/food_line_daily_scheduler.py',
    'scripts/food_line_continuation_ledger.py',
    'scripts/run_food_line_continuation.py',
    'scripts/run_food_line_discovery_expansion.py',
    'scripts/preflight_repo_state.py',
    'scripts/food_line_runtime_paths.py',
    'scripts/care_line_runtime_paths.py',
    'scripts/operator_runtime_paths.py',
    'scripts/windows/resume_food_line_daily_current.ps1',
    'scripts/windows/run_food_care_proof_continuation.ps1',
    'src/bluefern_dispatches/__init__.py',
    'src/bluefern_dispatches/food_line_discovery_expansion.py',
    'src/bluefern_dispatches/food_line_sources.py',
    'src/bluefern_dispatches/food_line_runtime_state.py',
    'src/bluefern_dispatches/event_ledger.py',
    'src/bluefern_dispatches/operational_health.py',
)


class SelectedProductionPathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="food-selected-real-git-", dir=os.environ.get("DISPATCHES_TEST_ROOT"))
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / ("runner" if "windows_powershell" in self._testMethodName else "runner with spaces")
        self.root.mkdir()
        for relative in FIXTURE_FILES:
            destination = self.root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, destination)
        config = Path("data/dispatches/food-line/discovery_expansion_config.json")
        self.git("init", "--initial-branch=" + scheduler.PRODUCTION_BRANCH)
        self.git("add", "--", *FIXTURE_FILES)
        self.commit("synthetic historical source")
        self.source_head = self.git("rev-parse", "HEAD").strip()
        (self.root / "fixture-runtime-version.txt").write_text("new runtime revision\n", encoding="utf-8")
        self.git("add", "--", "fixture-runtime-version.txt")
        self.commit("synthetic newer recovery runtime")
        self.runtime_head = self.git("rev-parse", "HEAD").strip()
        self.assertNotEqual(self.source_head, self.runtime_head)
        self.source_path = self.root / "src/bluefern_dispatches/food_line_discovery_expansion.py"
        self.config_path = self.root / config
        rows = scheduler.build_food_line_discovery_query_plan(self.root, DATE)
        self.assertTrue(rows)
        self.directory = self.root / scheduler.CONTINUATION_ROOT_RELATIVE / DATE / RUN
        state = ledger.prepare_ledger(
            self.directory, edition_date=DATE, run_id=RUN, source_identity=self.source_head,
            source_module_sha256=hashlib.sha256(self.source_path.read_bytes()).hexdigest(),
            config_sha256=hashlib.sha256(self.config_path.read_bytes()).hexdigest(), query_rows=rows,
        )
        # Synthetic already-completed evidence, never collected from providers.
        state["completed_query_ids"] = list(state["planned_query_ids"])
        state["pending_query_ids"] = []
        state["query_results"] = {qid: {
            "status": "completed", "ok": True, "terminal": True, "uncertain": False,
            "required_coverage_success": True, "direct_source_attempted": False,
            "direct_source_success": False, "candidate_count": 0, "exclusion_count": 0,
            "collection_only": True, "candidates": [],
        } for qid in state["planned_query_ids"]}
        self.ledger_path = self.directory / ledger.LEDGER_NAME
        self.ledger_path.write_text(json.dumps(state), encoding="utf-8")
        self.state_path = self.root / scheduler.DIRECT_CONTINUATION_RELATIVE / DATE / RUN / "run-state.json"
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps({
            "schema_version": scheduler.RUN_STATE_SCHEMA, "edition_date": DATE, "run_id": RUN,
            "status": "incomplete_capped", "resumable": False, "execution_mode": "export_enabled",
            "final_error": "", "coverage": {}, "options": {},
            "agent_export": {"status": "blocked_incomplete_collection", "path": str(self.state_path.parent / "agent-export.json")}, "release_ready": None, "intake_eligible": None,
        }), encoding="utf-8")
        self.legacy_before = self.state_path.read_bytes()
        self.plan_before = (self.directory / ledger.PLAN_NAME).read_bytes()
        guard = self.base / "network-denial-guard"
        guard.mkdir()
        self.network_marker = self.base / "network-attempt.txt"
        (guard / "sitecustomize.py").write_text(
            "import sys, os\nfrom pathlib import Path\n"
            "def deny(event, args):\n"
            "    if event in {'urllib.Request', 'socket.connect', 'socket.getaddrinfo'}:\n"
            "        Path(os.environ['FOOD_TEST_NETWORK_MARKER']).write_text(event)\n"
            "        raise RuntimeError('Network forbidden in provider-free regression')\n"
            "sys.addaudithook(deny)\n", encoding="utf-8")
        self.env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(guard),
                        FOOD_TEST_NETWORK_MARKER=str(self.network_marker))
        self.env.pop("BLUEFERN_TEST_MODE", None)

    def git(self, *args):
        p = subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p.stdout

    def commit(self, message):
        self.git("-c", "user.name=Offline Fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "--no-gpg-sign", "-m", message)

    def args(self, runtime=True):
        args = ["resume", "--repo-root", str(self.root), "--python", sys.executable,
                "--edition-date", DATE, "--branch", scheduler.PRODUCTION_BRANCH,
                "--continuation-run-directory", str(self.directory),
                "--expected-protected-head", self.source_head]
        if runtime:
            args += ["--expected-runtime-head", self.runtime_head]
        return args

    def run_scheduler(self, args=None, driver=None):
        invocation = [sys.executable, str(self.root / "scripts/food_line_daily_scheduler.py")]
        if driver:
            invocation = [sys.executable, "-c", driver]
        result = subprocess.run(invocation + (args if args is not None else self.args()),
                                cwd=self.root, env=self.env, capture_output=True, text=True, timeout=90)
        self.assertFalse(self.network_marker.exists(), "provider/network attempt occurred")
        receipts = list((self.root / "logs/food-line/source-watch" / DATE).glob("*-status-resume.json"))
        self.assertTrue(receipts, result.stdout + result.stderr)
        receipt = json.loads(max(receipts, key=lambda p: p.stat().st_mtime_ns).read_text())
        return result, receipt

    def assert_rejected_without_continuation(self, result, receipt):
        self.assertEqual(result.returncode, 10, result.stdout + result.stderr)
        self.assertIsNone(receipt["child_command_exit_code"])
        self.assertNotIn("continuation_progress", receipt)
        self.assertEqual(self.state_path.read_bytes(), self.legacy_before)
        self.assertEqual((self.directory / ledger.PLAN_NAME).read_bytes(), self.plan_before)

    def test_new_runtime_requires_independent_pin(self):
        result, receipt = self.run_scheduler(self.args(runtime=False))
        self.assert_rejected_without_continuation(result, receipt)
        self.assertIn("ExpectedRuntimeHead", receipt["reason"])

    def test_actual_preflight_status_and_continuation_with_separate_pins(self):
        result, receipt = self.run_scheduler()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # Real scheduler transport is a whole pretty-printed JSON document,
        # not JSONL. This platform-independent assertion covers its framing.
        whole_stdout = json.loads(result.stdout)
        self.assertEqual(whole_stdout["schema_version"], scheduler.SOURCE_RECEIPT_SCHEMA)
        self.assertEqual(whole_stdout["child_continuation_receipt"], receipt["child_continuation_receipt"])
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        self.assertGreater(len(lines), 1)
        self.assertEqual(lines[-1], "}")
        with self.assertRaises(json.JSONDecodeError):
            json.loads(lines[-1])
        self.assertEqual(receipt["source_commit"], self.source_head)
        self.assertEqual(receipt["runtime_source_commit"], self.runtime_head)
        self.assertEqual(receipt["child_command_exit_code"], 0)
        self.assertEqual(receipt["child_continuation_receipt"]["source_identity"], self.source_head)
        self.assertEqual(receipt["child_continuation_receipt"]["run_id"], RUN)
        self.assertEqual(receipt["child_continuation_receipt"]["plan_sha256"], receipt["ledger_binding"]["plan_sha256"])
        self.assertFalse(receipt["continuation_progress"]["collection_triggered"])
        self.assertFalse(receipt["continuation_progress"]["durable_progress"])
        self.assertFalse(receipt["run_record_update_applied"])
        self.assertFalse(receipt["intake_eligible"])
        self.assertFalse(receipt["release_ready"])
        self.assertEqual(receipt["export_status"], "not_requested")
        self.assertIsNone(receipt["inbox_export_path"])
        historical = receipt["legacy_source_state"]
        self.assertEqual(historical["execution_mode"], "export_enabled")
        self.assertEqual(historical["agent_export"]["status"], "blocked_incomplete_collection")
        self.assertIsNone(historical["intake_eligible"])
        self.assertIsNone(historical["release_ready"])
        self.assertNotIn("publication_attempted", historical)
        self.assertNotIn("publication_side_effects", historical)
        self.assertNotIn("source_commit", historical)
        self.assertNotIn("source_identity", historical)
        self.assertEqual(historical["agent_export"]["path"], str(self.state_path.parent / "agent-export.json"))
        self.assertEqual(self.state_path.read_bytes(), self.legacy_before)
        self.assertEqual((self.directory / ledger.PLAN_NAME).read_bytes(), self.plan_before)
        self.assertFalse((self.state_path.parent / "continuation-agent-export.json").exists())
        self.assertFalse((self.root / "logs/food-line/current-intake").exists())
        self.assertFalse((self.root / "logs/care-line").exists())
        self.assertFalse((self.root / "output").exists())
        self.assertEqual(self.git("diff", "--exit-code"), "")

    @unittest.skipUnless(os.name == "nt" and shutil.which("powershell.exe"), "requires Windows PowerShell 5.1")
    def test_windows_powershell_operator_to_actual_python_chain(self):
        import venv
        # Uses the existing interpreter, without pip or package/network installs.
        venv.EnvBuilder(with_pip=False).create(self.root / ".venv")
        operator_root = self.base / "operator"
        if " " in str(self.base):
            self.skipTest("configured-shape transport fixture requires a no-space temporary parent")
        wrapper = self.root / "scripts/windows/run_food_care_proof_continuation.ps1"
        result = subprocess.run([
            "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(wrapper), "-FoodRoot", str(self.root), "-OperatorRoot", str(operator_root),
            "-EditionDate", DATE, "-RunId", "offline-selected-transport",
            "-ContinuationRunDirectory", str(self.directory) + os.sep,
            "-ExpectedProtectedHead", self.source_head, "-ExpectedRuntimeHead", self.runtime_head,
        ], cwd=self.root, env=self.env, capture_output=True, text=True, timeout=120)
        diagnostic = ""
        if result.returncode != 0:
            # Only synthetic fixture logs are inspected. Report bounded framing
            # metadata and stderr, not full receipt bodies or environment values.
            details = []
            for log in sorted(operator_root.rglob("food_resume.*.log")):
                raw = log.read_bytes()
                text = raw.decode("utf-8-sig", errors="replace")
                row = {"file": log.name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                if log.name.endswith("stdout.log"):
                    nonempty = [line.strip() for line in text.splitlines() if line.strip()]
                    row["nonempty_lines"] = len(nonempty)
                    row["last_line"] = nonempty[-1][:160] if nonempty else ""
                    try:
                        value = json.loads(text)
                        row["whole_document_json"] = isinstance(value, dict)
                    except json.JSONDecodeError as error:
                        row["parse_error"] = str(error)[:240]
                else:
                    row["stderr_tail"] = text[-1200:]
                details.append(row)
            diagnostic = "\nSynthetic inner transport diagnostics: " + json.dumps(details, sort_keys=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr + diagnostic)
        self.assertFalse(self.network_marker.exists())
        paths = list(operator_root.rglob("food-care-proof-continuation-receipt.json"))
        self.assertEqual(len(paths), 1)
        operator = json.loads(paths[0].read_text(encoding="utf-8-sig"))
        self.assertEqual(operator["outcome"], "COLLECTION_ONLY_COMPLETE")
        self.assertEqual(operator["expected_protected_head"], self.source_head)
        self.assertEqual(operator["expected_runtime_head"], self.runtime_head)
        self.assertEqual([step["name"] for step in operator["steps"]], ["food_resume"])
        self.assertEqual(operator["steps"][0]["exit_code"], 0)
        self.assertFalse(operator["public_side_effects"]["collection_triggered"])
        scheduler_stdout = Path(operator["steps"][0]["stdout_path"]).read_text(encoding="utf-8-sig")
        receipt = json.loads(scheduler_stdout)
        self.assertEqual(receipt["runtime_source_commit"], self.runtime_head)
        self.assertEqual(receipt["source_commit"], self.source_head)
        self.assertEqual(receipt["child_command_exit_code"], 0)
        self.assertEqual(self.state_path.read_bytes(), self.legacy_before)
        self.assertEqual((self.directory / ledger.PLAN_NAME).read_bytes(), self.plan_before)
        self.assertFalse((self.state_path.parent / "continuation-agent-export.json").exists())
        self.assertFalse((self.root / "output").exists())
        self.assertEqual(self.git("diff", "--exit-code"), "")

    def test_operator_receipt_parser_consumes_complete_stdout_document(self):
        text = (ROOT / "scripts/windows/run_food_care_proof_continuation.ps1").read_text()
        parser = text.split("function Read-StructuredContinuationReceipt {", 1)[1].split("\ntry {", 1)[0]
        self.assertIn("$document = $stdoutText.Trim()", parser)
        self.assertIn("$document | ConvertFrom-Json -ErrorAction Stop", parser)
        self.assertIn("$document.StartsWith('{')", parser)
        self.assertIn("$document.EndsWith('}')", parser)
        self.assertNotIn("$lines", parser)
        self.assertNotIn("-split", parser)

    def test_operator_rejects_runtime_pin_without_selection_before_default_actions(self):
        text = (ROOT / "scripts/windows/run_food_care_proof_continuation.ps1").read_text()
        guard = text.index('if ($ExpectedRuntimeHead -and -not $ContinuationRunDirectory)')
        selection = text.index('if ($ContinuationRunDirectory) {', guard)
        default_sync = text.index('Invoke-ContinuationStep -Name "guarded_runner_sync"', selection)
        self.assertLess(guard, selection)
        self.assertLess(selection, default_sync)
        self.assertIn('throw "ExpectedRuntimeHead requires an explicit ContinuationRunDirectory"', text[guard:selection])

    def test_legacy_direct_layout_remains_supported(self):
        direct = self.state_path.parent / "continuation"
        self.directory.rename(direct)
        self.directory = direct
        result, receipt = self.run_scheduler()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(receipt["continuation_run_directory"], str(direct))
        self.assertEqual(self.state_path.read_bytes(), self.legacy_before)

    def test_duplicate_populated_layouts_are_refused(self):
        direct = self.state_path.parent / "continuation"
        shutil.copytree(self.directory, direct)
        before = self.ledger_path.read_bytes()
        result, receipt = self.run_scheduler()
        self.assert_rejected_without_continuation(result, receipt)
        self.assertIn("multiple populated continuation layouts", receipt["reason"])
        self.assertEqual(self.ledger_path.read_bytes(), before)
        self.assertEqual((direct / ledger.LEDGER_NAME).read_bytes(), before)

    def test_other_run_directory_is_refused(self):
        other = self.directory.with_name("not-the-bound-run")
        shutil.copytree(self.directory, other)
        args = self.args()
        args[args.index("--continuation-run-directory") + 1] = str(other)
        result, receipt = self.run_scheduler(args)
        self.assert_rejected_without_continuation(result, receipt)
        self.assertIn("exact run-bound path", receipt["reason"])

    def test_default_resume_still_needs_no_runtime_pin(self):
        args = ["resume", "--repo-root", str(self.root), "--python", sys.executable,
                "--edition-date", DATE, "--branch", scheduler.PRODUCTION_BRANCH]
        result, receipt = self.run_scheduler(args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(receipt["resume_status"], scheduler.UPSTREAM_NOT_INITIALIZED_STATUS)
        self.assertNotIn("child_continuation_receipt", receipt)
        self.assertEqual(self.state_path.read_bytes(), self.legacy_before)

    def test_runtime_pin_without_selected_directory_fails_before_default_path(self):
        args = ["resume", "--repo-root", str(self.root), "--python", sys.executable,
                "--edition-date", DATE, "--branch", scheduler.PRODUCTION_BRANCH,
                "--expected-runtime-head", self.runtime_head]
        result, receipt = self.run_scheduler(args)
        self.assert_rejected_without_continuation(result, receipt)
        self.assertIn("requires an explicit continuation directory", receipt["reason"])

    def test_wrong_runtime_pin(self):
        args = self.args(); args[-1] = self.source_head
        result, receipt = self.run_scheduler(args)
        self.assert_rejected_without_continuation(result, receipt)
        self.assertIn("runner source commit changed", receipt["reason"])

    def test_dirty_runtime_tree(self):
        path = self.root / "scripts/food_line_daily_scheduler.py"
        path.write_bytes(path.read_bytes() + b"\n# dirty runtime fixture\n")
        result, receipt = self.run_scheduler()
        self.assert_rejected_without_continuation(result, receipt)
        self.assertIn("dirty", receipt["reason"])

    def test_wrong_branch(self):
        self.git("switch", "-c", "fixture-not-production")
        result, receipt = self.run_scheduler()
        self.assert_rejected_without_continuation(result, receipt)
        self.assertIn("branch mismatch", receipt["reason"])

    def test_committed_source_and_config_drift(self):
        for path, expected in [(self.source_path, "source-module"), (self.config_path, "configuration")]:
            with self.subTest(path=path.name):
                previous = path.read_bytes()
                path.write_bytes(previous + b"\n")
                self.git("add", "--", str(path.relative_to(self.root)))
                self.commit("synthetic committed binding drift")
                self.runtime_head = self.git("rev-parse", "HEAD").strip()
                result, receipt = self.run_scheduler()
                self.assert_rejected_without_continuation(result, receipt)
                self.assertIn(expected + " binding drifted", receipt["reason"])
                path.write_bytes(previous)
                self.git("add", "--", str(path.relative_to(self.root)))
                self.commit("restore synthetic binding")
                self.runtime_head = self.git("rev-parse", "HEAD").strip()

    def test_runtime_changes_before_lock_revalidation(self):
        driver = '''import sys, subprocess
from contextlib import contextmanager
from scripts import food_line_daily_scheduler as s
original = s.source_lock
@contextmanager
def changed(*args, **kwargs):
    subprocess.run(['git', '-c', 'user.name=Offline Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '--allow-empty', '--no-gpg-sign', '-m', 'concurrent runtime movement'], check=True, capture_output=True)
    with original(*args, **kwargs):
        yield
s.source_lock = changed
raise SystemExit(s.main(sys.argv[1:]))
'''
        result, receipt = self.run_scheduler(driver=driver)
        self.assert_rejected_without_continuation(result, receipt)
        self.assertIn("runner source commit changed", receipt["reason"])

    def test_runtime_changes_during_child_prevent_success_without_rollback(self):
        driver = """import sys, subprocess
from scripts import food_line_daily_scheduler as s
original = s._invoke_python
def changed(python, root, arguments):
    result = original(python, root, arguments)
    if 'scripts/run_food_line_continuation.py' in arguments:
        subprocess.run(['git', '-c', 'user.name=Offline Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '--allow-empty', '--no-gpg-sign', '-m', 'runtime movement during child'], check=True, capture_output=True)
    return result
s._invoke_python = changed
raise SystemExit(s.main(sys.argv[1:]))
"""
        result, receipt = self.run_scheduler(driver=driver)
        self.assertEqual(result.returncode, 10, result.stdout + result.stderr)
        self.assertIn("runner source commit changed", receipt["reason"])
        self.assertEqual(receipt["child_command_exit_code"], 0)
        self.assertNotIn("continuation_progress", receipt)
        durable = json.loads(self.ledger_path.read_text())
        self.assertEqual(durable["completed_query_ids"], durable["planned_query_ids"])
        self.assertEqual(durable["binding"]["source_identity"], self.source_head)
        self.assertEqual((self.directory / ledger.PLAN_NAME).read_bytes(), self.plan_before)
        self.assertEqual(self.state_path.read_bytes(), self.legacy_before)

    def test_pending_batches_use_provider_stub_without_replay_or_latest_record_mutation(self):
        durable = json.loads(self.ledger_path.read_text())
        planned = list(durable["planned_query_ids"])
        self.assertGreater(len(planned), 600)
        durable["completed_query_ids"] = planned[:200]
        durable["pending_query_ids"] = planned[200:]
        durable["query_results"] = {qid: durable["query_results"][qid] for qid in planned[:200]}
        self.ledger_path.write_text(json.dumps(durable), encoding="utf-8")
        latest = scheduler.Layout(self.root).run_record(DATE)
        latest.parent.mkdir(parents=True, exist_ok=True)
        latest.write_text(json.dumps({"run_id": "newer-unrelated-B", "status": "running"}), encoding="utf-8")
        latest_before = latest.read_bytes()
        call_log = self.base / "synthetic-provider-calls.jsonl"
        self.env["FOOD_FIXTURE_PROVIDER_CALLS"] = str(call_log)
        child_driver = r"""import sys, os, json
from pathlib import Path
from scripts import run_food_line_continuation as r
def provider(root, edition_date, **kwargs):
    row = kwargs['query_plan_override'][0]
    with Path(os.environ['FOOD_FIXTURE_PROVIDER_CALLS']).open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(r.stable_query_id(row)) + '\n')
    return {'status': 'completed', 'ok': True, 'query_rows': [{**row, 'direct_fetch_status': 'ok'}], 'candidates': [], 'queries_succeeded': 1, 'queries_failed': 0, 'queries_timed_out': 0, 'direct_source_fetch_attempt_count': 0, 'direct_source_fetch_success_count': 0}
r.run_food_line_discovery_expansion = provider
raise SystemExit(r.run(r.build_parser().parse_args()))
"""
        driver = """import sys
from scripts import food_line_daily_scheduler as s
original = s._continuation_command
def command(**kwargs):
    args = original(**kwargs)
    return ['-c', CHILD_DRIVER, *args[1:]]
s._continuation_command = command
raise SystemExit(s.main(sys.argv[1:]))
""".replace("CHILD_DRIVER", repr(child_driver))
        for expected_count in (400, 600):
            result, receipt = self.run_scheduler(driver=driver)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertEqual(receipt["child_command_exit_code"], 2)
            self.assertEqual(receipt["continuation_progress"]["after_completed_count"], expected_count)
            self.assertTrue(receipt["continuation_progress"]["durable_progress"])
            self.assertFalse(receipt["child_continuation_receipt"]["gates"]["intake_eligible"])
            self.assertEqual(latest.read_bytes(), latest_before)
            self.assertEqual(self.state_path.read_bytes(), self.legacy_before)
            self.assertEqual((self.directory / ledger.PLAN_NAME).read_bytes(), self.plan_before)
        called = [json.loads(line) for line in call_log.read_text().splitlines()]
        self.assertEqual(called, planned[200:600])
        self.assertEqual(len(set(called)), 400)
        self.assertFalse((self.state_path.parent / "continuation-agent-export.json").exists())

    def test_child_exit_and_receipt_corruption_fail_closed(self):
        # Real child executes, then the subprocess seam supplies contradictory
        # transport evidence. Runtime Git/preflight/status/ledger checks stay real.
        for mutation, reason in [
            ("exit", "exit/status"), ("intake", "downstream gate"),
            ("handoff", "suppressed source-stage handoff"),
            ("run_id", "receipt binding"), ("plan_sha256", "receipt binding"),
            ("edition_date", "receipt binding"), ("pending-zero-exit", "exit/status"),
            ("completed-two-exit", "exit/status"), ("pending-complete-ledger", "contradicts durable ledger"),
        ]:
            with self.subTest(mutation=mutation):
                driver = '''import sys, subprocess, json
from scripts import food_line_daily_scheduler as s
original = s._invoke_python
def corrupt(python, root, arguments):
    result = original(python, root, arguments)
    if 'scripts/run_food_line_continuation.py' not in arguments:
        return result
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    mutation = MUTATION
    code = result.returncode
    if mutation == 'exit': code = 1
    elif mutation == 'completed-two-exit': code = 2
    elif mutation == 'pending-zero-exit': payload['status'] = 'incomplete_pending'
    elif mutation == 'pending-complete-ledger':
        payload['status'] = 'incomplete_pending'
        code = 2
    elif mutation == 'intake': payload['gates']['intake_eligible'] = True
    elif mutation == 'handoff': payload['source_stage_handoff']['status'] = 'eligible'
    else: payload[mutation] = 'foreign'
    return subprocess.CompletedProcess(result.args, code, json.dumps(payload), 'synthetic contradictory evidence')
s._invoke_python = corrupt
raise SystemExit(s.main(sys.argv[1:]))
'''.replace("MUTATION", repr(mutation))
                result, receipt = self.run_scheduler(driver=driver)
                self.assertEqual(result.returncode, 10, result.stdout + result.stderr)
                self.assertIn(reason, receipt["reason"])
                self.assertEqual(receipt["child_command_exit_code"], 1 if mutation == "exit" else (2 if mutation in {"completed-two-exit", "pending-complete-ledger"} else 0))
                self.assertEqual(receipt["command_exit_code"], receipt["child_command_exit_code"])
                self.assertNotIn("continuation_progress", receipt)
                self.assertEqual(self.state_path.read_bytes(), self.legacy_before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
