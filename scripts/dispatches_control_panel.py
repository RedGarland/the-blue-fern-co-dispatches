from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import date as date_cls
from pathlib import Path
from typing import Any, Callable

import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

DISPATCHES = ("Gaza", "Cascadia", "American Pressure")
ACTIONS = (
    "Run dispatch",
    "Run with notification",
    "Publish Pages locally, no push",
    "Run dashboard",
    "Run doctor",
)

STATUS_BANNER_OK = "OK to review"
STATUS_BANNER_WARN = "Needs attention"
STATUS_BANNER_STOP = "Do not publish"


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def python_executable(root: Path) -> Path:
    return root / ".venv" / "Scripts" / "python.exe"


def validate_date(date_text: str) -> bool:
    try:
        date_cls.fromisoformat(date_text)
    except ValueError:
        return False
    return len(date_text) == 10


def manual_source_path(dispatch: str, date_text: str, root: Path | None = None) -> Path | None:
    base = root or project_root()
    key = dispatch.strip().lower()
    if key == "gaza":
        return base / "data" / "dispatches" / "gaza" / "sources" / date_text / "manual_sources.json"
    if key == "american pressure":
        return base / "data" / "dispatches" / "american-pressure" / "sources" / date_text / "manual_sources.json"
    return None


def expected_output_paths(dispatch: str, date_text: str, root: Path | None = None) -> list[Path]:
    base = root or project_root()
    slug = dispatch.lower().replace(" ", "-")
    return [
        base / "output" / "dispatches" / slug / "editions" / date_text,
        base / "output" / "site" / slug / "editions" / date_text,
    ]


def public_url(dispatch: str, date_text: str) -> str:
    slug = dispatch.lower().replace(" ", "-")
    return f"https://dispatches.thebluefernco.com/{slug}/editions/{date_text}/"


def _pages_publish_command(root: Path) -> list[str]:
    return [
        str(python_executable(root)),
        "scripts\\publish_github_pages.py",
        "--pages-repo",
        str(root / "bluefern-dispatches-pages"),
        "--pages-branch",
        "gh-pages",
        "--remote-url",
        "https://github.com/RedGarland/the-blue-fern-co-dispatches.git",
        "--commit",
        "--no-push",
    ]


def build_command(
    dispatch: str,
    action: str,
    date_text: str,
    options: dict[str, Any] | None = None,
    root: Path | None = None,
) -> list[str]:
    opts = options or {}
    base = root or project_root()
    py = str(python_executable(base))

    if action == "Run dashboard":
        cmd = [py, "scripts\\dispatches_status.py"]
        if opts.get("status_json"):
            cmd.append("--json")
        return cmd
    if action == "Run doctor":
        return [py, "scripts\\doctor.py"]
    if action == "Publish Pages locally, no push":
        return _pages_publish_command(base)

    if not validate_date(date_text):
        raise ValueError("Date must be YYYY-MM-DD")

    if dispatch == "Gaza":
        if action == "Run dispatch":
            cmd = [
                py,
                "scripts\\run_gaza_dispatch.py",
                "--date",
                date_text,
                "--historical",
                "--from-manual-sources",
                "--all",
            ]
        elif action == "Run with notification":
            cmd = [
                py,
                "scripts\\run_and_notify.py",
                "--date",
                date_text,
                "--publish",
            ]
        else:
            raise ValueError(f"Unsupported action for Gaza: {action}")
    elif dispatch == "Cascadia":
        if action == "Run dispatch":
            cmd = [
                py,
                "scripts\\run_cascadia_dispatch.py",
                "--date",
                date_text,
                "--weekly-public",
                "--historical-search",
                "--historical-provider",
                "all",
            ]
        elif action == "Run with notification":
            cmd = [
                py,
                "scripts\\run_cascadia_and_notify.py",
                "--date",
                date_text,
                "--publish",
            ]
        else:
            raise ValueError(f"Unsupported action for Cascadia: {action}")
    elif dispatch == "American Pressure":
        if action == "Run dispatch":
            cmd = [
                py,
                "scripts\\run_american_pressure_dispatch.py",
                "--date",
                date_text,
                "--from-manual-sources",
                "--publish",
            ]
        elif action == "Run with notification":
            cmd = [
                py,
                "scripts\\run_american_pressure_and_notify.py",
                "--date",
                date_text,
                "--publish",
            ]
        else:
            raise ValueError(f"Unsupported action for American Pressure: {action}")
    else:
        raise ValueError(f"Unsupported dispatch: {dispatch}")

    if opts.get("dry_run"):
        if dispatch in ("Gaza", "Cascadia") and "Run dispatch" == action:
            cmd.append("--dry-run")
    return cmd


def _sanitize_line(text: str) -> str:
    if "SMTP_PASSWORD" not in text:
        return text
    return text.replace("SMTP_PASSWORD", "[REDACTED_KEY]")


def run_command_streaming(
    command: list[str],
    cwd: Path,
    on_line: Callable[[str], None],
    on_done: Callable[[int], None],
) -> subprocess.Popen[str]:
    merged_env = os.environ.copy()
    proc = subprocess.Popen(
        command,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
        env=merged_env,
    )

    def _reader() -> None:
        assert proc.stdout is not None
        for raw_line in proc.stdout:
            on_line(_sanitize_line(raw_line.rstrip("\n")))
        proc.wait()
        on_done(proc.returncode)

    threading.Thread(target=_reader, daemon=True).start()
    return proc


def load_status_json(root: Path) -> dict[str, Any]:
    cmd = [str(python_executable(root)), "scripts\\dispatches_status.py", "--json"]
    completed = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, check=False)
    payload: dict[str, Any]
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        payload = {
            "ok": False,
            "critical_errors": ["Could not parse status JSON"],
            "warnings": [completed.stderr.strip()] if completed.stderr.strip() else [],
        }
    payload.setdefault("_status_exit_code", completed.returncode)
    return payload


def summarize_status_for_gui(status: dict[str, Any]) -> dict[str, Any]:
    critical = list(status.get("critical_errors") or [])
    warnings = list(status.get("warnings") or [])
    pages = status.get("pages_repo") or {}
    project = status.get("project") or {}
    safety = status.get("public_safety") or {}
    dispatches = status.get("dispatches") or {}

    def _clean(value: Any) -> Any:
        if isinstance(value, str) and "SMTP_PASSWORD" in value:
            return "[REDACTED]"
        if isinstance(value, list):
            return [_clean(v) for v in value]
        if isinstance(value, dict):
            return {k: _clean(v) for k, v in value.items()}
        return value

    gaza = dispatches.get("gaza") or {}
    cascadia = dispatches.get("cascadia") or {}
    american = dispatches.get("american_pressure") or {}
    dedupe = gaza.get("latest_dedupe_report") or {}
    gap = cascadia.get("latest_weekly_gap_report") or {}
    registry = american.get("registry_summary") or {}

    flags = {
        "do_not_publish": not bool(status.get("ok", False)),
        "pages_dirty": pages.get("clean") is False,
        "source_changes": bool(project.get("has_source_test_doc_changes")),
        "output_site_detail_exists": bool(safety.get("output_site_detail_exists")),
        "output_site_paid_exists": bool(safety.get("output_site_paid_exists")),
        "gaza_zero_source_linked": bool(gaza.get("public_linked_zero_source_dates")),
        "gaza_zero_story_linked": bool(gaza.get("public_linked_zero_story_dates")),
        "gaza_dedupe_refusal_linked": bool(gaza.get("public_linked_dedupe_refusal_dates")),
        "gaza_repeated_urls": bool(gaza.get("repeated_source_urls_recent")),
    }

    return _clean(
        {
            "overview": {
                "ok": bool(status.get("ok", False)),
                "critical_errors": critical,
                "warnings": warnings,
                "project_root": project.get("root"),
                "source_repo_branch": project.get("branch"),
                "source_repo_head_short_sha": project.get("head_short_sha"),
                "source_repo_tracking": project.get("tracking"),
                "source_changes": project.get("has_source_test_doc_changes"),
                "generated_runtime_dirt": project.get("has_generated_runtime_dirt"),
                "python_executable": project.get("python"),
                "status_timestamp": project.get("timestamp"),
            },
            "pages_repo_summary": {
                "path": pages.get("path"),
                "exists": pages.get("exists"),
                "branch": pages.get("branch"),
                "head_short_sha": pages.get("head_short_sha"),
                "clean": pages.get("clean"),
                "tracking": pages.get("tracking"),
                "cname_value": pages.get("cname_value"),
                "cname_ok": pages.get("cname_ok"),
            },
            "public_safety_checks": {
                "output_site_detail_exists": safety.get("output_site_detail_exists"),
                "output_site_paid_exists": safety.get("output_site_paid_exists"),
                "smtp_password_in_logs": safety.get("smtp_password_in_logs") or [],
                "bad_fns_link_hits": safety.get("bad_fns_link_hits") or [],
                "old_project_runtime_hits": safety.get("old_project_runtime_hits") or [],
            },
            "dispatch_stats": {
                "gaza": gaza,
                "cascadia": cascadia,
                "american_pressure": american,
            },
            "gaza_dedupe_stats": {
                "public_archive_dates": gaza.get("public_archive_dates") or [],
                "stale_or_unlinked_edition_dates": gaza.get("stale_or_unlinked_edition_dates") or [],
                "public_linked_zero_source_dates": gaza.get("public_linked_zero_source_dates") or [],
                "repeated_source_url_count": len(gaza.get("repeated_source_urls_recent") or {}),
                "latest_dedupe_report_edition_date": dedupe.get("edition_date"),
                "input_candidates": dedupe.get("input_candidate_count"),
                "kept_candidates": dedupe.get("kept_candidate_count"),
                "suppressed_candidates": dedupe.get("suppressed_candidate_count"),
                "dedupe_warnings": dedupe.get("warnings") or [],
            },
            "cascadia_discovery_stats": {
                "latest_weekly_edition_date": cascadia.get("latest_weekly_edition_date"),
                "weekly_labels_only": cascadia.get("weekly_labels_only"),
                "transitional_public_links": cascadia.get("transitional_public_links") or [],
                "source_checks_attempted": gap.get("source_checks_attempted"),
                "source_checks_successful": gap.get("source_checks_successful"),
                "successful_fetch_rate": gap.get("successful_fetch_rate"),
                "final_public_story_count": gap.get("final_public_story_count"),
                "final_zero_story_result_credible": gap.get("final_zero_story_result_is_credible"),
                "latest_weekly_gap_report_path": gap.get("path"),
            },
            "american_pressure_stats": {
                "source_registry_exists": american.get("source_registry_exists"),
                "total_registry_sources": registry.get("total_sources"),
                "enabled_registry_sources": registry.get("enabled_sources"),
                "enabled_sources_by_pillar": registry.get("enabled_by_pillar") or {},
                "latest_source_health_report_path": american.get("latest_source_health_report"),
                "latest_manual_source_date": american.get("latest_manual_source_date"),
                "latest_manual_source_exists_for_latest_public_edition": american.get(
                    "latest_manual_source_exists_for_latest_public_edition"
                ),
                "live_fetch_disabled_by_default": american.get("live_fetch_disabled_by_default"),
                "latest_public_source_count_gt_zero": american.get("latest_public_source_count_gt_zero"),
                "bad_fns_hits_in_active_output": american.get("bad_fns_hits_in_active_output") or [],
            },
            "flags": flags,
        }
    )


def open_path(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]
    except Exception:
        return False
    return True


@dataclass
class RunState:
    process: subprocess.Popen[str] | None = None
    running: bool = False


class DispatchesControlPanel:
    def __init__(self, root_win: tk.Tk):
        self.root_win = root_win
        self.root_win.title("Dispatches Control Panel")
        self.root_win.geometry("1200x760")

        self.root_dir = project_root()
        self.run_state = RunState()
        self.ui_queue: queue.Queue[tuple[str, Any]] = queue.Queue()

        self.dispatch_var = tk.StringVar(value=DISPATCHES[0])
        self.action_var = tk.StringVar(value=ACTIONS[0])
        self.date_var = tk.StringVar(value=date_cls.today().isoformat())
        self.open_after_var = tk.BooleanVar(value=False)
        self.dry_run_var = tk.BooleanVar(value=False)
        self.publish_toggle_var = tk.BooleanVar(value=True)

        self.status_banner_var = tk.StringVar(value=STATUS_BANNER_WARN)
        self.execution_var = tk.StringVar(value="Ready")
        self.command_var = tk.StringVar(value="")

        self._build_ui()
        self._poll_ui_queue()

    def _build_ui(self) -> None:
        tabs = ttk.Notebook(self.root_win)
        tabs.pack(fill=tk.BOTH, expand=True)

        run_tab = ttk.Frame(tabs)
        stats_tab = ttk.Frame(tabs)
        logs_tab = ttk.Frame(tabs)

        tabs.add(run_tab, text="Run Dispatches")
        tabs.add(stats_tab, text="Statistics / Health")
        tabs.add(logs_tab, text="Logs / Output")

        self._build_run_tab(run_tab)
        self._build_stats_tab(stats_tab)
        self._build_logs_tab(logs_tab)

    def _build_run_tab(self, frame: ttk.Frame) -> None:
        top = ttk.Frame(frame)
        top.pack(fill=tk.X, padx=10, pady=10)

        ttk.Label(top, text="Dispatch").grid(row=0, column=0, sticky="w")
        ttk.Combobox(top, values=DISPATCHES, textvariable=self.dispatch_var, state="readonly", width=22).grid(
            row=0, column=1, padx=6, sticky="w"
        )

        ttk.Label(top, text="Date (YYYY-MM-DD)").grid(row=0, column=2, sticky="w")
        ttk.Entry(top, textvariable=self.date_var, width=16).grid(row=0, column=3, padx=6, sticky="w")

        ttk.Label(top, text="Action").grid(row=1, column=0, sticky="w")
        ttk.Combobox(top, values=ACTIONS, textvariable=self.action_var, state="readonly", width=30).grid(
            row=1, column=1, padx=6, sticky="w"
        )

        ttk.Checkbutton(top, text="Open output page after success", variable=self.open_after_var).grid(
            row=1, column=2, columnspan=2, sticky="w"
        )
        ttk.Checkbutton(top, text="Dry-run if supported", variable=self.dry_run_var).grid(
            row=2, column=0, columnspan=2, sticky="w"
        )
        ttk.Checkbutton(top, text="Publish toggle (where applicable)", variable=self.publish_toggle_var).grid(
            row=2, column=2, columnspan=2, sticky="w"
        )

        btn_row = ttk.Frame(frame)
        btn_row.pack(fill=tk.X, padx=10)
        self.execute_btn = ttk.Button(btn_row, text="Execute", command=self.execute_action)
        self.execute_btn.pack(side=tk.LEFT)
        self.stop_btn = ttk.Button(btn_row, text="Stop (best effort)", command=self.stop_action, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=6)
        ttk.Button(btn_row, text="Clear Output", command=self.clear_output).pack(side=tk.LEFT, padx=6)

        open_row = ttk.Frame(frame)
        open_row.pack(fill=tk.X, padx=10, pady=6)
        ttk.Button(open_row, text="Open local dispatch archive", command=self.open_archive).pack(side=tk.LEFT)
        ttk.Button(open_row, text="Open latest local edition", command=self.open_latest_edition).pack(side=tk.LEFT, padx=6)
        ttk.Button(open_row, text="Open source folder", command=self.open_source_folder).pack(side=tk.LEFT, padx=6)
        ttk.Button(open_row, text="Open log folder", command=lambda: self._open(self.root_dir / "logs")).pack(side=tk.LEFT, padx=6)
        ttk.Button(open_row, text="Open output/site", command=lambda: self._open(self.root_dir / "output" / "site")).pack(side=tk.LEFT, padx=6)
        ttk.Button(
            open_row,
            text="Open Pages repo folder",
            command=lambda: self._open(self.root_dir / "bluefern-dispatches-pages"),
        ).pack(side=tk.LEFT, padx=6)

        ttk.Label(frame, textvariable=self.execution_var, foreground="#333366").pack(anchor="w", padx=10)
        ttk.Label(frame, textvariable=self.command_var, foreground="#444444", wraplength=1150).pack(anchor="w", padx=10)

        self.output_text = ScrolledText(frame, height=22)
        self.output_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

    def _build_stats_tab(self, frame: ttk.Frame) -> None:
        top = ttk.Frame(frame)
        top.pack(fill=tk.X, padx=10, pady=10)

        self.banner_label = ttk.Label(top, textvariable=self.status_banner_var)
        self.banner_label.pack(side=tk.LEFT)
        ttk.Button(top, text="Refresh Statistics", command=self.refresh_status).pack(side=tk.LEFT, padx=8)
        ttk.Button(top, text="Copy JSON status", command=self.copy_status_json).pack(side=tk.LEFT, padx=8)

        open_row = ttk.Frame(frame)
        open_row.pack(fill=tk.X, padx=10, pady=6)
        ttk.Button(open_row, text="Open latest Gaza edition", command=lambda: self.open_dispatch_latest("gaza")).pack(side=tk.LEFT)
        ttk.Button(open_row, text="Open latest Cascadia edition", command=lambda: self.open_dispatch_latest("cascadia")).pack(side=tk.LEFT, padx=6)
        ttk.Button(open_row, text="Open latest American Pressure edition", command=lambda: self.open_dispatch_latest("american-pressure")).pack(side=tk.LEFT, padx=6)
        ttk.Button(open_row, text="Open Gaza archive", command=lambda: self._open(self.root_dir / "output" / "site" / "gaza" / "archive.html")).pack(side=tk.LEFT, padx=6)
        ttk.Button(open_row, text="Open Cascadia archive", command=lambda: self._open(self.root_dir / "output" / "site" / "cascadia" / "archive.html")).pack(side=tk.LEFT, padx=6)
        ttk.Button(open_row, text="Open American Pressure archive", command=lambda: self._open(self.root_dir / "output" / "site" / "american-pressure" / "archive.html")).pack(side=tk.LEFT, padx=6)

        self.stats_text = ScrolledText(frame, height=30)
        self.stats_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)

    def _build_logs_tab(self, frame: ttk.Frame) -> None:
        ttk.Label(frame, text="Live command output is mirrored from Run Dispatches tab.").pack(anchor="w", padx=10, pady=10)
        self.logs_text = ScrolledText(frame, height=32)
        self.logs_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

    def _poll_ui_queue(self) -> None:
        while True:
            try:
                kind, payload = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "line":
                self._append_output(str(payload))
            elif kind == "done":
                self._on_command_done(int(payload))
        self.root_win.after(100, self._poll_ui_queue)

    def _append_output(self, line: str) -> None:
        self.output_text.insert(tk.END, line + "\n")
        self.output_text.see(tk.END)
        self.logs_text.insert(tk.END, line + "\n")
        self.logs_text.see(tk.END)

    def execute_action(self) -> None:
        dispatch = self.dispatch_var.get()
        action = self.action_var.get()
        date_text = self.date_var.get().strip()

        if action in ("Run dispatch", "Run with notification") and not validate_date(date_text):
            messagebox.showerror("Invalid date", "Date must be in YYYY-MM-DD format.")
            return

        warnings = self._preflight_warnings(dispatch, action, date_text)
        if warnings:
            messagebox.showwarning("Preflight warnings", "\n".join(warnings))

        opts = {
            "dry_run": self.dry_run_var.get(),
            "publish": self.publish_toggle_var.get(),
            "status_json": action == "Run dashboard",
        }
        try:
            cmd = build_command(dispatch, action, date_text, opts, root=self.root_dir)
        except ValueError as exc:
            messagebox.showerror("Unsupported selection", str(exc))
            return

        cmd_str = subprocess.list2cmdline(cmd)
        self.command_var.set(f"Command: {cmd_str}")
        self.execution_var.set(
            f"Running dispatch={dispatch} action={action} date={date_text}"
        )
        self._append_output(f"$ {cmd_str}")

        self.run_state.running = True
        self.execute_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)

        self.run_state.process = run_command_streaming(
            cmd,
            self.root_dir,
            on_line=lambda line: self.ui_queue.put(("line", line)),
            on_done=lambda rc: self.ui_queue.put(("done", rc)),
        )

    def _preflight_warnings(self, dispatch: str, action: str, date_text: str) -> list[str]:
        notes: list[str] = []
        if dispatch == "Cascadia" and action in ("Run dispatch", "Run with notification"):
            notes.append("Cascadia runs weekly-public historical-search workflow for selected date.")
        if dispatch in ("Gaza", "American Pressure") and action in ("Run dispatch", "Run with notification"):
            path = manual_source_path(dispatch, date_text, root=self.root_dir)
            if path is not None and not path.exists():
                notes.append(f"Missing manual sources: {path}")
        return notes

    def _on_command_done(self, returncode: int) -> None:
        self.run_state.running = False
        self.execute_btn.configure(state=tk.NORMAL)
        self.stop_btn.configure(state=tk.DISABLED)
        status = "success" if returncode == 0 else "failure"
        self.execution_var.set(f"Finished with exit code={returncode} ({status})")
        self._append_output(f"[exit code] {returncode}")

        if returncode == 0 and self.open_after_var.get():
            d = self.dispatch_var.get()
            dt = self.date_var.get().strip()
            self._open(self.root_dir / "output" / "site" / d.lower().replace(" ", "-") / "editions" / dt / "index.html")

    def stop_action(self) -> None:
        proc = self.run_state.process
        if proc is None or not self.run_state.running:
            return
        proc.terminate()
        self._append_output("[info] stop requested")

    def clear_output(self) -> None:
        self.output_text.delete("1.0", tk.END)

    def refresh_status(self) -> None:
        def _worker() -> None:
            status = load_status_json(self.root_dir)
            summary = summarize_status_for_gui(status)
            self.ui_queue.put(("line", "[status] refreshed"))
            self.root_win.after(0, lambda: self._render_status(summary, status))

        threading.Thread(target=_worker, daemon=True).start()

    def _render_status(self, summary: dict[str, Any], raw_status: dict[str, Any]) -> None:
        flags = summary.get("flags", {})
        if flags.get("do_not_publish"):
            self.status_banner_var.set(STATUS_BANNER_STOP)
            self.banner_label.configure(foreground="red")
        elif flags.get("pages_dirty") or flags.get("source_changes"):
            self.status_banner_var.set(STATUS_BANNER_WARN)
            self.banner_label.configure(foreground="#b58900")
        else:
            self.status_banner_var.set(STATUS_BANNER_OK)
            self.banner_label.configure(foreground="green")

        lines = [json.dumps(summary, indent=2)]
        if flags.get("output_site_detail_exists") or flags.get("output_site_paid_exists"):
            lines.append("CRITICAL: output/site/detail or output/site/paid exists.")
        if flags.get("gaza_zero_source_linked"):
            lines.append("CRITICAL: Linked Gaza edition has zero sources.")
        if flags.get("gaza_zero_story_linked"):
            lines.append("CRITICAL: Linked Gaza edition has zero stories.")
        if flags.get("gaza_dedupe_refusal_linked"):
            lines.append("CRITICAL: Linked Gaza dedupe refusal detected.")
        if flags.get("gaza_repeated_urls"):
            lines.append("WARNING: Repeated Gaza source URLs across recent public editions.")

        self.stats_text.delete("1.0", tk.END)
        self.stats_text.insert(tk.END, "\n\n".join(lines))
        self.stats_text.see(tk.END)
        self._latest_raw_status = raw_status

    def copy_status_json(self) -> None:
        payload = getattr(self, "_latest_raw_status", None)
        if not payload:
            messagebox.showinfo("Status", "Refresh statistics first.")
            return
        self.root_win.clipboard_clear()
        self.root_win.clipboard_append(json.dumps(payload, indent=2))
        self._append_output("[status] copied JSON to clipboard")

    def open_archive(self) -> None:
        slug = self.dispatch_var.get().lower().replace(" ", "-")
        self._open(self.root_dir / "output" / "site" / slug / "archive.html")

    def open_latest_edition(self) -> None:
        slug = self.dispatch_var.get().lower().replace(" ", "-")
        self.open_dispatch_latest(slug)

    def open_dispatch_latest(self, slug: str) -> None:
        editions = self.root_dir / "output" / "site" / slug / "editions"
        if not editions.exists():
            messagebox.showwarning("Missing", f"No editions folder: {editions}")
            return
        dated = sorted([p.name for p in editions.iterdir() if p.is_dir()])
        if not dated:
            messagebox.showwarning("Missing", "No edition folders found.")
            return
        self._open(editions / dated[-1] / "index.html")

    def open_source_folder(self) -> None:
        dispatch = self.dispatch_var.get()
        date_text = self.date_var.get().strip()
        slug = dispatch.lower().replace(" ", "-")
        if dispatch == "Cascadia":
            path = self.root_dir / "data" / "dispatches" / "cascadia" / "sources"
        else:
            path = self.root_dir / "data" / "dispatches" / slug / "sources" / date_text
        self._open(path)

    def _open(self, path: Path) -> None:
        if not open_path(path):
            messagebox.showwarning("Open failed", f"Could not open path: {path}")


def main() -> int:
    if "--self-check" in sys.argv:
        root = project_root()
        cmd = build_command("Gaza", "Run dispatch", date_cls.today().isoformat(), {}, root=root)
        if "--no-push" not in " ".join(_pages_publish_command(root)):
            return 1
        print("ok")
        print(" ".join(cmd[:3]))
        return 0

    win = tk.Tk()
    app = DispatchesControlPanel(win)
    app.refresh_status()
    win.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
