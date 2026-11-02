"""Production-capable Gaza adapter candidate.

This is deliberately separate from the accepted fixture integration.  It
invokes the checked-in Gaza operator as a subprocess, binds the actual source
tree and Git head, and consumes the operator's native operational-health
receipt.  Tests inject only the subprocess and Git boundaries; they never call
providers, Pages, PowerShell, or a production checkout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

TASK_ROOT = Path(__file__).resolve().parents[2]
if str(TASK_ROOT) not in sys.path:
    sys.path.insert(0, str(TASK_ROOT))

from dispatch_execution_contract import (  # noqa: E402
    Artifact,
    CallableDeskAdapter,
    DispatchEngine,
    EditorialDecision,
    PhaseResult,
    ReceiptBusyError,
    ReceiptStore,
    ReconcileResult,
    RunRequest,
    UnknownOutcomeError,
    deterministic_run_id,
)
from scripts.preflight_repo_state import (  # noqa: E402
    classify_status_line,
    is_shared_dispatch_record_path,
)
from scripts.gaza_sync_core import _assert_no_reparse_ancestors  # noqa: E402


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SOURCE_MANIFEST_VERSION = "gaza-production-source-manifest-v2"
CONTRACT_CONFIG_VERSION = "gaza-production-adapter-v1"
MAX_CAPTURE_BYTES = 64 * 1024
EXPECTED_PUBLIC_EFFECT_KEYS = {"pages", "bluesky", "audio", "public_output"}
SAFE_OPERATOR_STATUSES = {
    "LOCAL_PUBLISH_READY",
    "NO_UPDATE_LOCAL_PUBLISH_READY",
    "NO_PUBLICATION_NEEDED",
    "NO_UPDATE_PUBLISHED",
    "SAFE_PREPARATION_READY",
}
NO_UPDATE_STATUSES = {"NO_UPDATE_LOCAL_PUBLISH_READY", "NO_UPDATE_PUBLISHED", "NO_PUBLICATION_NEEDED"}
EXPLICIT_SCRIPT_FILES = (
    "scripts/run_gaza_daily_operator.py",
    "scripts/run_gaza_production_adapter.py",
    "scripts/run_daily_gaza.py",
    "scripts/run_gaza_dispatch.py",
    "scripts/publish_github_pages.py",
    "scripts/publish_gaza_historical.py",
    "scripts/run_and_notify.py",
    "scripts/preflight_repo_state.py",
    "scripts/gaza_sync_core.py",
    "scripts/run_runner_dispatch.ps1",
    "pyproject.toml",
    "requirements.txt",
    "dispatch_execution_contract.py",
)
SHARED_DISPATCH_RECORD_BINDING_VERSION = "gaza-shared-dispatch-records-v1"
SHARED_DISPATCH_RECORD_PATHS = tuple(
    f"data/records/{name}"
    for name in (
        "curation_decisions.json",
        "dispatches.json",
        "editions.json",
        "records.json",
        "sources.json",
        "story_memory.json",
    )
)


class GazaAdapterError(RuntimeError):
    """The production-shaped adapter rejected incomplete or unsafe evidence."""


class GazaAdapterBlocked(GazaAdapterError):
    """The requested run lacks a required source or target fact."""


def _validate_record_binding_state(binding: Mapping[str, Any], *, label: str) -> None:
    if binding.get("version") != SHARED_DISPATCH_RECORD_BINDING_VERSION:
        raise GazaAdapterError(f"{label} record binding version is invalid")
    files = binding.get("files")
    if not isinstance(files, Mapping) or set(files) != set(SHARED_DISPATCH_RECORD_PATHS):
        raise GazaAdapterError(f"{label} record binding does not cover the exact approved paths")
    dirty_paths = binding.get("dirty_paths")
    if not isinstance(dirty_paths, list) or any(path not in SHARED_DISPATCH_RECORD_PATHS for path in dirty_paths):
        raise GazaAdapterError(f"{label} record binding has an invalid dirty-path list")
    for relative in SHARED_DISPATCH_RECORD_PATHS:
        facts = files.get(relative)
        if not isinstance(facts, Mapping) or type(facts.get("exists")) is not bool:
            raise GazaAdapterError(f"{label} record binding has invalid facts for {relative}")
        if facts["exists"]:
            if type(facts.get("size_bytes")) is not int or not SHA256_RE.fullmatch(str(facts.get("sha256"))):
                raise GazaAdapterError(f"{label} record binding has invalid facts for {relative}")
        elif set(facts) != {"exists"}:
            raise GazaAdapterError(f"{label} record binding has invalid absent-file facts for {relative}")
    identity = binding.get("identity")
    payload = {key: value for key, value in binding.items() if key != "identity"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if identity != f"{SHARED_DISPATCH_RECORD_BINDING_VERSION}:{_sha256(canonical)}":
        raise GazaAdapterError(f"{label} record binding identity is invalid")


def _load_record_binding_artifact(summary: Mapping[str, Any], artifacts: Sequence[Any]) -> Mapping[str, Any]:
    binding = summary.get("shared_dispatch_record_binding")
    if not isinstance(binding, Mapping):
        raise GazaAdapterError("collection shared dispatch record binding is missing")
    if binding.get("version") != SHARED_DISPATCH_RECORD_BINDING_VERSION:
        raise GazaAdapterError("collection record binding version is invalid")
    before = binding.get("before")
    after = binding.get("after")
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        raise GazaAdapterError("collection record binding lacks before/after facts")
    _validate_record_binding_state(before, label="before")
    _validate_record_binding_state(after, label="after")
    identity = binding.get("identity")
    payload = {key: value for key, value in binding.items() if key != "identity"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if identity != f"{SHARED_DISPATCH_RECORD_BINDING_VERSION}:{_sha256(canonical)}":
        raise GazaAdapterError("collection record binding identity is invalid")
    artifact = next(
        (item for item in artifacts if isinstance(item, Mapping) and item.get("artifact_id") == "gaza-shared-dispatch-record-binding"),
        None,
    )
    path = summary.get("shared_dispatch_record_binding_path")
    if not isinstance(artifact, Mapping) or artifact.get("path") != path or artifact.get("version") != binding.get("identity"):
        raise GazaAdapterError("shared dispatch record binding artifact is not bound")
    if not isinstance(path, str):
        raise GazaAdapterError("shared dispatch record binding path is missing")
    binding_file = Path(path)
    if not binding_file.is_file():
        raise GazaAdapterError("shared dispatch record binding artifact is missing")
    raw = binding_file.read_bytes()
    if len(raw) != artifact.get("size_bytes") or _sha256(raw) != artifact.get("sha256"):
        raise GazaAdapterError("shared dispatch record binding artifact digest mismatch")
    try:
        saved = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GazaAdapterError("shared dispatch record binding artifact is unreadable") from exc
    if not isinstance(saved, Mapping) or dict(saved) != dict(binding):
        raise GazaAdapterError("shared dispatch record binding artifact does not match the receipt")
    _validate_record_binding_state(saved["before"], label="before")
    _validate_record_binding_state(saved["after"], label="after")
    return saved


@dataclass(frozen=True)
class ProductionGazaConfig:
    source_root: Path
    pages_repo: Path
    receipt_root: Path
    artifact_root: Path
    scheduled_for: str
    source_branch: str
    pages_branch: str
    python_executable: Path
    expected_source_head: str
    expected_source_manifest_identity: str
    operator_relative_path: str = "scripts/run_gaza_daily_operator.py"

    def validate(self) -> None:
        if not DATE_RE.fullmatch(self.scheduled_for):
            raise GazaAdapterError("scheduled_for must be YYYY-MM-DD")
        for name in ("source_branch", "pages_branch", "operator_relative_path"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise GazaAdapterError(f"{name} must be non-empty")
        for name in ("source_root", "pages_repo", "receipt_root", "artifact_root", "python_executable"):
            path = Path(getattr(self, name))
            if not path.is_absolute():
                raise GazaAdapterError(f"{name} must be absolute")
        source_root = self.source_root.resolve(strict=False)
        artifact_root = self.artifact_root.resolve(strict=False)
        if artifact_root == source_root or source_root in artifact_root.parents:
            raise GazaAdapterError("artifact_root must be outside source_root")
        operator = (source_root / self.operator_relative_path).resolve(strict=False)
        if source_root not in operator.parents and operator != source_root:
            raise GazaAdapterError("operator_relative_path escapes source_root")
        if not source_root.is_dir():
            raise GazaAdapterBlocked("source_root is not an existing directory")
        if not self.pages_repo.is_dir():
            raise GazaAdapterBlocked("pages_repo is not an existing directory")
        if not self.python_executable.is_file():
            raise GazaAdapterBlocked("python executable is not an existing file")
        if not operator.is_file():
            raise GazaAdapterBlocked("operator script is not an existing file")
        if not re.fullmatch(r"[0-9a-fA-F]{40}", self.expected_source_head):
            raise GazaAdapterError("expected_source_head must be a full SHA-1")
        if not re.fullmatch(rf"{re.escape(SOURCE_MANIFEST_VERSION)}:[0-9a-fA-F]{{64}}", self.expected_source_manifest_identity):
            raise GazaAdapterError("expected_source_manifest_identity must be a full source-manifest identity")

    @property
    def operator_script(self) -> Path:
        return self.source_root / self.operator_relative_path

    def identity(self) -> str:
        raw = json.dumps(
            {
                "version": CONTRACT_CONFIG_VERSION,
                "desk": "gaza",
                "source_root": str(self.source_root.resolve(strict=False)),
                "pages_repo": str(self.pages_repo.resolve(strict=False)),
                "source_branch": self.source_branch,
                "pages_branch": self.pages_branch,
                "operator_relative_path": self.operator_relative_path,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return f"{CONTRACT_CONFIG_VERSION}:{hashlib.sha256(raw).hexdigest()}"


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    started_at: str
    completed_at: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _git_head(source_root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(source_root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        raise GazaAdapterBlocked("source Git HEAD could not be read")
    head = completed.stdout.strip()
    if not re.fullmatch(r"[0-9a-fA-F]{40}", head):
        raise GazaAdapterBlocked("source Git HEAD was not a full SHA-1")
    return head.lower()


def _git_status(source_root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(source_root), "status", "--porcelain=v1", "--untracked-files=all"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        raise GazaAdapterBlocked("source Git status could not be read")
    return completed.stdout


def _allowed_shared_dispatch_record_dirty_paths(status_text: str) -> list[str]:
    allowed: list[str] = []
    seen: set[str] = set()
    for raw_line in status_text.splitlines():
        line = raw_line.rstrip("\r\n")
        if line == "":
            continue
        if line.startswith("##"):
            raise GazaAdapterBlocked("source Git status contains an unexpected branch header")
        if len(line) < 4 or line[:2] != " M" or line[2] != " ":
            raise GazaAdapterBlocked("source Git status contains a malformed entry")
        path = line[3:]
        if path in seen:
            raise GazaAdapterBlocked(f"source Git status contains a duplicate path: {path}")
        if path not in SHARED_DISPATCH_RECORD_PATHS:
            raise GazaAdapterBlocked(f"source Git status contains an unapproved dirty path: {path}")
        entry = classify_status_line(line)
        if (
            entry is None
            or entry.get("path") != path
            or entry.get("category") != "shared_dispatch_records"
            or entry.get("is_risky") is True
            or not is_shared_dispatch_record_path(path)
        ):
            raise GazaAdapterBlocked(f"source Git status contains an unapproved dirty path: {path}")
        seen.add(path)
        allowed.append(path)
    return sorted(set(allowed))


def _shared_dispatch_record_path(source_root: Path, relative: str) -> Path:
    root_input = Path(source_root)
    root = root_input.resolve(strict=False)
    candidate_input = root_input / Path(relative)
    _assert_no_reparse_ancestors(candidate_input, allow_missing_leaf=True)
    candidate = candidate_input.resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise GazaAdapterBlocked(f"shared dispatch record escapes source root: {relative}") from exc
    _assert_no_reparse_ancestors(candidate, allow_missing_leaf=True)
    return candidate


def _shared_dispatch_record_binding(source_root: Path, dirty_paths: Sequence[str]) -> dict[str, Any]:
    files: dict[str, dict[str, Any]] = {}
    for relative in SHARED_DISPATCH_RECORD_PATHS:
        path = _shared_dispatch_record_path(source_root, relative)
        if not path.exists():
            if relative in dirty_paths:
                raise GazaAdapterBlocked(f"dirty shared dispatch record is missing: {relative}")
            files[relative] = {"exists": False}
            continue
        try:
            stat = path.stat()
        except OSError as exc:
            raise GazaAdapterBlocked(f"shared dispatch record metadata could not be read: {relative}") from exc
        if int(getattr(stat, "st_file_attributes", 0)) & 0x400:
            raise GazaAdapterBlocked(f"shared dispatch record is a reparse point: {relative}")
        if not path.is_file():
            raise GazaAdapterBlocked(f"shared dispatch record is not a regular file: {relative}")
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise GazaAdapterBlocked(f"shared dispatch record could not be read: {relative}") from exc
        files[relative] = {
            "exists": True,
            "size_bytes": len(raw),
            "sha256": _sha256(raw),
        }
    payload = {
        "version": SHARED_DISPATCH_RECORD_BINDING_VERSION,
        "dirty_paths": list(dirty_paths),
        "files": files,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {**payload, "identity": f"{SHARED_DISPATCH_RECORD_BINDING_VERSION}:{_sha256(canonical)}"}


def _manifest_paths(source_root: Path) -> list[str]:
    paths = set(EXPLICIT_SCRIPT_FILES)
    module_root = source_root / "src" / "bluefern_dispatches"
    if module_root.is_dir():
        paths.update(path.relative_to(source_root).as_posix() for path in module_root.rglob("*.py") if path.is_file())
    source_config = source_root / "data" / "dispatches" / "gaza" / "sources.yml"
    if source_config.is_file():
        paths.add(source_config.relative_to(source_root).as_posix())
    return sorted(paths)


def build_source_manifest(source_root: Path) -> dict[str, Any]:
    """Hash the real operator, direct scripts, imported modules, and config."""
    root = Path(source_root).resolve()
    files: list[dict[str, Any]] = []
    for relative in _manifest_paths(root):
        path = root / relative
        if not path.is_file():
            raise GazaAdapterBlocked(f"source manifest input is missing: {relative}")
        raw = path.read_bytes()
        files.append({"path": relative, "size_bytes": len(raw), "sha256": _sha256(raw)})
    canonical = json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "version": SOURCE_MANIFEST_VERSION,
        "identity": f"{SOURCE_MANIFEST_VERSION}:{_sha256(canonical)}",
        "files": files,
    }


def source_identity(source_root: Path, manifest: Mapping[str, Any], head_reader: Callable[[Path], str] = _git_head) -> str:
    head = head_reader(Path(source_root))
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise GazaAdapterBlocked("source head identity is malformed")
    identity = manifest.get("identity")
    if not isinstance(identity, str) or not identity:
        raise GazaAdapterBlocked("source manifest identity is missing")
    return f"git:{head}:manifest:{identity}"


def _default_runner(args: Sequence[str], cwd: Path) -> CommandResult:
    started = _utc_now()
    completed = subprocess.run(
        list(args),
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        started_at=started,
        completed_at=_utc_now(),
    )


def _json_tail(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    candidates = [index for index, char in enumerate(text) if char == "{"]
    for index in reversed(candidates):
        try:
            value, end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and not text[index + end :].strip():
            return value
    return None


def _bounded_capture(text: str) -> tuple[bytes, bool]:
    raw = text.encode("utf-8", errors="replace")
    return raw[:MAX_CAPTURE_BYTES], len(raw) > MAX_CAPTURE_BYTES


def _write_exclusive(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def _artifact(path: Path, *, artifact_id: str, kind: str, raw: bytes, version: str | None = None) -> Artifact:
    return Artifact(
        artifact_id=artifact_id,
        kind=kind,
        sha256=_sha256(raw),
        size_bytes=len(raw),
        path=str(path),
        version=version,
    )


def _health_expected_status(operator_status: str, ok: bool) -> str:
    if ok and operator_status in SAFE_OPERATOR_STATUSES:
        return "SAFE_NO_OP"
    return "FAILED"


def _validate_public_effects(receipt: Mapping[str, Any]) -> None:
    effects = receipt.get("public_side_effects")
    if not isinstance(effects, Mapping) or set(effects) != EXPECTED_PUBLIC_EFFECT_KEYS:
        raise GazaAdapterError("native health receipt lacks the complete public-effect proof")
    if any(type(value) is not bool for value in effects.values()):
        raise GazaAdapterError("native health receipt public effects are not Boolean")
    if any(effects.values()):
        raise GazaAdapterError("native health receipt reports a public effect")


def _validate_native_health(
    health: Mapping[str, Any],
    *,
    config: ProductionGazaConfig,
    request: RunRequest,
    summary: Mapping[str, Any],
    source_head: str,
) -> None:
    expected = {
        "schema_version": "bluefern_operational_health_receipt_v1",
        "dispatch": "gaza",
        "task_key": "gaza_daily_dispatch",
        "scheduled_for": request.scheduled_for,
        "runner_path": str(config.source_root),
        "source_head": source_head,
        "classification": str(summary.get("operator_status") or "unknown").lower(),
        "exit_code": summary.get("operator_exit_code"),
    }
    for key, value in expected.items():
        if health.get(key) != value:
            raise GazaAdapterError(f"native health field mismatch: {key}")
    if health.get("status") != _health_expected_status(str(summary.get("operator_status") or ""), summary.get("ok") is True):
        raise GazaAdapterError("native health status does not match operator result")
    if not isinstance(health.get("run_id"), str) or not health["run_id"]:
        raise GazaAdapterError("native health run_id is missing")
    _validate_public_effects(health)
    details = health.get("details")
    if not isinstance(details, Mapping):
        raise GazaAdapterError("native health details are missing")
    if details.get("generation_ok") is not True or details.get("validation_ok") is not True:
        raise GazaAdapterError("native health does not prove generation and validation")


class ProductionGazaAdapter:
    desk = "gaza"

    def __init__(
        self,
        config: ProductionGazaConfig,
        *,
        runner: Callable[[Sequence[str], Path], CommandResult] = _default_runner,
        head_reader: Callable[[Path], str] = _git_head,
        status_reader: Callable[[Path], str] = _git_status,
        manifest_builder: Callable[[Path], dict[str, Any]] = build_source_manifest,
    ) -> None:
        config.validate()
        self.config = config
        self.runner = runner
        self.head_reader = head_reader
        self.status_reader = status_reader
        self.manifest_builder = manifest_builder

    def _snapshot(self) -> tuple[str, dict[str, Any], dict[str, Any]]:
        manifest = self.manifest_builder(self.config.source_root)
        head = self.head_reader(self.config.source_root)
        if head.lower() != self.config.expected_source_head.lower():
            raise GazaAdapterBlocked("source Git HEAD does not match the configured expected head")
        if manifest.get("identity") != self.config.expected_source_manifest_identity:
            raise GazaAdapterBlocked("source manifest does not match the configured expected identity")
        dirty_paths = _allowed_shared_dispatch_record_dirty_paths(self.status_reader(self.config.source_root))
        record_binding = _shared_dispatch_record_binding(self.config.source_root, dirty_paths)
        return head.lower(), manifest, record_binding

    def _command(self, request: RunRequest) -> list[str]:
        # These are the only operator flags the candidate supplies.  The
        # existing operator still owns source-provider collection; push,
        # Bluesky, audio, and email stay outside this adapter's authority.
        return [
            str(self.config.python_executable),
            str(self.config.operator_script),
            "--date",
            request.scheduled_for,
            "--pages-repo",
            str(self.config.pages_repo),
            "--pages-branch",
            self.config.pages_branch,
            "--expected-source-branch",
            self.config.source_branch,
            "--contract-prepare",
            "--skip-audio",
            "--skip-bluesky",
            "--tts-provider",
            "none",
        ]

    def _artifact_dir(self, request: RunRequest) -> Path:
        return self.config.artifact_root / request.scheduled_for / request.run_id

    def _summary_artifacts(
        self,
        request: RunRequest,
        result: CommandResult,
        manifest: Mapping[str, Any],
        record_binding: Mapping[str, Any],
    ) -> tuple[list[Artifact], dict[str, Any]]:
        root = self._artifact_dir(request)
        stdout_raw, stdout_truncated = _bounded_capture(result.stdout)
        stderr_raw, stderr_truncated = _bounded_capture(result.stderr)
        stdout_path = root / "operator.stdout.txt"
        stderr_path = root / "operator.stderr.txt"
        manifest_path = root / "source-manifest.json"
        record_binding_path = root / "shared-dispatch-record-binding.json"
        manifest_raw = (json.dumps(dict(manifest), indent=2, sort_keys=True) + "\n").encode("utf-8")
        record_binding_raw = (json.dumps(dict(record_binding), indent=2, sort_keys=True) + "\n").encode("utf-8")
        _write_exclusive(stdout_path, stdout_raw)
        _write_exclusive(stderr_path, stderr_raw)
        _write_exclusive(manifest_path, manifest_raw)
        _write_exclusive(record_binding_path, record_binding_raw)
        artifacts = [
            _artifact(stdout_path, artifact_id="gaza-operator-stdout", kind="operator-stdout", raw=stdout_raw),
            _artifact(stderr_path, artifact_id="gaza-operator-stderr", kind="operator-stderr", raw=stderr_raw),
            _artifact(
                manifest_path,
                artifact_id="gaza-source-manifest",
                kind="executed-source-manifest",
                raw=manifest_raw,
                version=str(manifest["identity"]),
            ),
            _artifact(
                record_binding_path,
                artifact_id="gaza-shared-dispatch-record-binding",
                kind="shared-dispatch-record-binding",
                raw=record_binding_raw,
                version=str(record_binding["identity"]),
            ),
        ]
        return artifacts, {
            "operator_stdout_sha256": _sha256(result.stdout.encode("utf-8", errors="replace")),
            "operator_stderr_sha256": _sha256(result.stderr.encode("utf-8", errors="replace")),
            "operator_stdout_truncated": stdout_truncated,
            "operator_stderr_truncated": stderr_truncated,
            "source_manifest_path": str(manifest_path),
            "source_manifest_identity": manifest.get("identity"),
            "shared_dispatch_record_binding_path": str(record_binding_path),
            "shared_dispatch_record_binding_identity": record_binding.get("identity"),
        }

    def _health_from_summary(self, request: RunRequest, summary: Mapping[str, Any], artifacts: list[Artifact], source_head: str) -> dict[str, Any]:
        health_path = summary.get("operational_receipt")
        if not isinstance(health_path, str) or not health_path:
            raise GazaAdapterError("operator did not return an operational receipt path")
        path = Path(health_path).resolve(strict=False)
        allowed_roots = (self.config.source_root.resolve(), self.config.receipt_root.resolve())
        if not any(path == root or root in path.parents for root in allowed_roots):
            raise GazaAdapterError("operational receipt is outside the configured receipt roots")
        if not path.is_file():
            raise GazaAdapterError("native operational receipt is missing")
        raw = path.read_bytes()
        try:
            health = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GazaAdapterError("native operational receipt is not valid JSON") from exc
        if not isinstance(health, dict):
            raise GazaAdapterError("native operational receipt is not an object")
        _validate_native_health(health, config=self.config, request=request, summary=summary, source_head=source_head)
        summary["operational_run_id"] = health.get("run_id")
        artifacts.append(_artifact(path, artifact_id="gaza-operational-health", kind="operational-health-receipt", raw=raw))
        return health

    def collect(self, request: RunRequest) -> PhaseResult:
        try:
            head_before, manifest_before, records_before = self._snapshot()
        except Exception as exc:
            return PhaseResult(status="blocked", summary={"reason": str(exc)})
        started = _utc_now()
        try:
            result = self.runner(self._command(request), self.config.source_root)
        except UnknownOutcomeError:
            raise
        except Exception as exc:
            return PhaseResult(status="failed", summary={"reason": type(exc).__name__}, error={"message": str(exc)[:512]})
        try:
            head_after, manifest_after, records_after = self._snapshot()
        except GazaAdapterBlocked as exc:
            return PhaseResult(status="failed", summary={"reason": str(exc)}, error={"message": str(exc)[:512]})
        try:
            record_binding = {
                "version": SHARED_DISPATCH_RECORD_BINDING_VERSION,
                "before": records_before,
                "after": records_after,
            }
            binding_canonical = json.dumps(record_binding, sort_keys=True, separators=(",", ":")).encode("utf-8")
            record_binding["identity"] = f"{SHARED_DISPATCH_RECORD_BINDING_VERSION}:{_sha256(binding_canonical)}"
            artifacts, capture = self._summary_artifacts(request, result, manifest_after, record_binding)
        except Exception as exc:
            return PhaseResult(status="unknown", summary={"reason": type(exc).__name__}, error={"message": str(exc)[:512]})
        summary = {
            "entrypoint": "scripts/run_gaza_daily_operator.py:main",
            "operator_exit_code": result.returncode,
            "operator_started_at": result.started_at or started,
            "operator_completed_at": result.completed_at or _utc_now(),
            "operator_status": None,
            "ok": None,
            "date": request.scheduled_for,
            "source_head": head_after,
            "source_identity": request.source_identity,
            "source_manifest_identity": manifest_after.get("identity"),
            "source_manifest_before": manifest_before.get("identity"),
            "shared_dispatch_record_binding": record_binding,
            "external_effect_policy": {
                "provider_collection": "delegated_to_existing_operator",
                "pages_push": False,
                "bluesky": False,
                "audio": False,
                "email": False,
            },
            **capture,
        }
        parsed = _json_tail(result.stdout)
        if parsed is None:
            return PhaseResult(status="unknown", summary=summary, artifacts=artifacts, error={"reason": "operator JSON result missing"})
        summary.update({key: parsed.get(key) for key in ("operator_status", "ok", "date", "generation_ok", "validation_ok", "operational_receipt", "warnings", "next_action", "no_update_status_written")})
        if head_after != head_before or manifest_after != manifest_before:
            return PhaseResult(status="failed", summary=summary, artifacts=artifacts, error={"reason": "executed source identity drifted"})
        try:
            health = self._health_from_summary(request, summary, artifacts, head_after)
        except GazaAdapterError as exc:
            summary["native_health_error"] = str(exc)
            return PhaseResult(status="unknown" if result.returncode == 0 else "failed", summary=summary, artifacts=artifacts, error={"reason": str(exc)})
        summary["operational_run_id"] = health.get("run_id")
        if result.returncode != 0 or parsed.get("ok") is not True:
            return PhaseResult(status="failed", summary=summary, artifacts=artifacts, error={"operator_status": parsed.get("operator_status")})
        if parsed.get("operator_status") not in SAFE_OPERATOR_STATUSES:
            return PhaseResult(status="failed", summary=summary, artifacts=artifacts, error={"reason": "operator status is not an accepted terminal result"})
        return PhaseResult(status="succeeded", summary=summary, artifacts=artifacts)

    def _validate_collection(self, request: RunRequest, collection: Mapping[str, Any]) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
        if collection.get("status") not in {"succeeded", "partial"}:
            raise GazaAdapterError("collection prerequisite is not successful")
        summary = collection.get("summary")
        if not isinstance(summary, Mapping):
            raise GazaAdapterError("collection summary is missing")
        artifacts = collection.get("artifacts")
        if not isinstance(artifacts, list):
            raise GazaAdapterError("collection artifacts are missing")
        if summary.get("source_identity") is None:
            raise GazaAdapterError("collection source identity is missing")
        if summary.get("source_identity") != request.source_identity:
            raise GazaAdapterError("collection source identity does not match request")
        manifest_identity = summary.get("source_manifest_identity")
        manifest_artifact = next((item for item in artifacts if isinstance(item, Mapping) and item.get("artifact_id") == "gaza-source-manifest"), None)
        manifest_path = summary.get("source_manifest_path")
        if not isinstance(manifest_artifact, Mapping) or manifest_artifact.get("version") != manifest_identity or manifest_artifact.get("path") != manifest_path:
            raise GazaAdapterError("executed source manifest artifact is not bound")
        if not isinstance(manifest_path, str):
            raise GazaAdapterError("executed source manifest path is missing")
        manifest_file = Path(manifest_path)
        if not manifest_file.is_file():
            raise GazaAdapterError("executed source manifest file is missing")
        manifest_raw = manifest_file.read_bytes()
        if len(manifest_raw) != manifest_artifact.get("size_bytes") or _sha256(manifest_raw) != manifest_artifact.get("sha256"):
            raise GazaAdapterError("executed source manifest artifact digest mismatch")
        try:
            saved_manifest = json.loads(manifest_raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GazaAdapterError("executed source manifest is unreadable") from exc
        if not isinstance(saved_manifest, dict) or saved_manifest.get("identity") != manifest_identity:
            raise GazaAdapterError("executed source manifest identity mismatch")
        _load_record_binding_artifact(summary, artifacts)
        current_head, current_manifest, current_records = self._snapshot()
        if current_head != summary.get("source_head") or current_manifest != saved_manifest:
            raise GazaAdapterError("executed source dependency manifest drifted")
        record_binding = summary["shared_dispatch_record_binding"]
        if current_records != record_binding["after"]:
            raise GazaAdapterError("shared dispatch record history drifted")
        health_artifact = next((item for item in artifacts if isinstance(item, Mapping) and item.get("artifact_id") == "gaza-operational-health"), None)
        health_path = summary.get("operational_receipt")
        if not isinstance(health_artifact, Mapping) or health_artifact.get("path") != health_path:
            raise GazaAdapterError("native health artifact is not bound")
        health_file = Path(str(health_path))
        if not health_file.is_file():
            raise GazaAdapterError("native health artifact file is missing")
        health_raw = health_file.read_bytes()
        if len(health_raw) != health_artifact.get("size_bytes") or _sha256(health_raw) != health_artifact.get("sha256"):
            raise GazaAdapterError("native health artifact digest mismatch")
        health = self._health_from_summary(request, summary, [*[]], current_head)
        return summary, health

    def validate(self, request: RunRequest, collection: Mapping[str, Any]) -> PhaseResult:
        try:
            summary, health = self._validate_collection(request, collection)
        except GazaAdapterError as exc:
            return PhaseResult(status="failed", summary={"reason": str(exc)})
        if summary.get("operator_status") not in SAFE_OPERATOR_STATUSES:
            return PhaseResult(status="failed", summary={"reason": "native operator status is not safely terminal"})
        if summary.get("generation_ok") is not True or summary.get("validation_ok") is not True:
            return PhaseResult(status="failed", summary={"reason": "operator generation/validation is incomplete"})
        return PhaseResult(
            status="succeeded",
            summary={
                "operator_status": summary.get("operator_status"),
                "source_head": summary.get("source_head"),
                "source_manifest_identity": summary.get("source_manifest_identity"),
                "operational_health_run_id": health.get("run_id"),
                "no_update": summary.get("operator_status") in NO_UPDATE_STATUSES,
                "public_effects": dict(health["public_side_effects"]),
            },
        )

    def editorial_gate(
        self,
        request: RunRequest,
        collection: Mapping[str, Any],
        validation: Mapping[str, Any],
        approval: Mapping[str, Any] | None = None,
    ) -> EditorialDecision:
        if validation.get("status") not in {"succeeded", "partial"}:
            return EditorialDecision(status="rejected", reason="collection validation is not resolved")
        summary = collection.get("summary") if isinstance(collection.get("summary"), Mapping) else {}
        status = str(summary.get("operator_status") or "")
        validation_summary = validation.get("summary") if isinstance(validation.get("summary"), Mapping) else validation
        if status in NO_UPDATE_STATUSES and validation_summary.get("no_update") is True:
            return EditorialDecision(status="no_publication_needed", reason="native Gaza operator proved a no-update result")
        if approval is None:
            return EditorialDecision(status="awaiting_approval", reason="explicit editorial publication approval is required", next_action="provide scoped approval")
        expected_artifacts = sorted(
            [
                {
                    "artifact_id": str(item.get("artifact_id")),
                    "sha256": str(item.get("sha256")),
                    "size_bytes": item.get("size_bytes"),
                    **({"version": item["version"]} if "version" in item else {}),
                }
                for item in collection.get("artifacts", [])
                if isinstance(item, Mapping)
            ],
            key=lambda item: item["artifact_id"],
        )
        validation_summary = validation.get("summary") if isinstance(validation.get("summary"), Mapping) else validation
        expected_binding = {
            "run_id": request.run_id,
            "action": "publish",
            "scope": "gaza-publication",
            "source_identity": request.source_identity,
            "source_manifest_identity": validation_summary.get("source_manifest_identity"),
            "operational_health_run_id": validation_summary.get("operational_health_run_id"),
            "artifact_bindings": expected_artifacts,
        }
        if approval.get("status") != "approved" or any(approval.get(key) != value for key, value in expected_binding.items() if key != "scope") or approval.get("scope") != "gaza-publication":
            return EditorialDecision(status="rejected", reason="editorial approval scope is not valid")
        approval_id = str(approval.get("approval_id") or "")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", approval_id):
            return EditorialDecision(status="rejected", reason="editorial approval identifier is not valid")
        return EditorialDecision(status="approved", reason="scoped editorial approval received", approval_id=approval_id)

    def publish(self, request: RunRequest, collection: Mapping[str, Any], validation: Mapping[str, Any], decision: Mapping[str, Any]) -> PhaseResult:
        return PhaseResult(
            status="blocked",
            summary={
                "reason": "publication_disabled",
                "publication_attempted": False,
                "public_effects": {key: False for key in sorted(EXPECTED_PUBLIC_EFFECT_KEYS)},
            },
            next_action="publication is intentionally disabled; obtain separate publication authorization",
        )

    def reconcile(self, request: RunRequest, receipt: Mapping[str, Any]) -> ReconcileResult:
        phases = receipt.get("phases")
        if not isinstance(phases, Mapping):
            return ReconcileResult(status="still_unknown", phase="collection", next_action="inspect missing phase records")
        publish_phase = phases.get("publish")
        if isinstance(publish_phase, Mapping) and publish_phase.get("status") == "blocked":
            blocked = PhaseResult(
                status="blocked",
                summary=publish_phase.get("summary") if isinstance(publish_phase.get("summary"), Mapping) else {},
                error=publish_phase.get("error") if isinstance(publish_phase.get("error"), Mapping) else None,
                next_action=str(publish_phase.get("next_action")) if publish_phase.get("next_action") else None,
            )
            return ReconcileResult(
                status="resolved",
                phase="publish",
                result=blocked,
                next_action="publication was intentionally disabled and was not attempted",
            )
        if receipt.get("phase") == "publishing" or receipt.get("unknown_phase") == "publish" or "publish" in phases:
            return ReconcileResult(status="still_unknown", phase="publish", next_action="publication outcome remains unknown; do not downgrade to editorial")
        collection = phases.get("collection")
        if not isinstance(collection, Mapping):
            return ReconcileResult(status="still_unknown", phase="collection", next_action="inspect missing collection")
        if collection.get("status") == "unknown":
            return ReconcileResult(status="still_unknown", phase="collection", next_action="native collection outcome remains unknown")
        try:
            summary, health = self._validate_collection(request, collection)
        except GazaAdapterError as exc:
            return ReconcileResult(status="still_unknown", phase="validation", next_action=str(exc))
        validation = phases.get("validation")
        if isinstance(validation, Mapping) and validation.get("status") == "unknown":
            return ReconcileResult(status="still_unknown", phase="validation", next_action="validation outcome remains unknown")
        if isinstance(validation, Mapping) and validation.get("status") in {"failed", "blocked"}:
            preserved = PhaseResult(
                status=str(validation.get("status")),
                summary=validation.get("summary") if isinstance(validation.get("summary"), Mapping) else {},
                error=validation.get("error") if isinstance(validation.get("error"), Mapping) else None,
                next_action=str(validation.get("next_action")) if validation.get("next_action") else None,
            )
            return ReconcileResult(status="resolved", phase="validation", result=preserved, next_action="inspect preserved validation failure")
        try:
            validation_result = self.validate(request, collection)
        except UnknownOutcomeError as exc:
            return ReconcileResult(status="still_unknown", phase="validation", next_action=str(exc))
        if validation_result.status == "unknown":
            return ReconcileResult(status="still_unknown", phase="validation", next_action="validation outcome remains unknown")
        if validation_result.status in {"failed", "blocked"}:
            return ReconcileResult(status="resolved", phase="validation", result=validation_result, next_action="inspect validation failure")
        return ReconcileResult(status="resolved", phase="validation", result=validation_result, next_action="continue editorial gate")

    def as_desk_adapter(self) -> CallableDeskAdapter:
        return CallableDeskAdapter(
            desk=self.desk,
            collect_fn=self.collect,
            validate_fn=self.validate,
            editorial_fn=self.editorial_gate,
            publish_fn=self.publish,
            reconcile_fn=self.reconcile,
        )


def run_scheduled_gaza_contract(config: ProductionGazaConfig, *, approval: Mapping[str, Any] | None = None, runner: Callable[[Sequence[str], Path], CommandResult] = _default_runner, head_reader: Callable[[Path], str] = _git_head, status_reader: Callable[[Path], str] = _git_status, manifest_builder: Callable[[Path], dict[str, Any]] = build_source_manifest) -> dict[str, Any]:
    manifest = manifest_builder(config.source_root)
    identity = source_identity(config.source_root, manifest, head_reader)
    config_identity = config.identity()
    input_fingerprint = _sha256(json.dumps({"date": config.scheduled_for, "source_root": str(config.source_root.resolve(strict=False)), "pages": str(config.pages_repo.resolve(strict=False)), "pages_branch": config.pages_branch}, sort_keys=True).encode("utf-8"))
    run_id = deterministic_run_id(desk="gaza", scheduled_for=config.scheduled_for, config_identity=config_identity, input_fingerprint=input_fingerprint)
    request = RunRequest(desk="gaza", run_id=run_id, scheduled_for=config.scheduled_for, config_identity=config_identity, input_fingerprint=input_fingerprint, source_identity=identity)
    engine = DispatchEngine(ReceiptStore(config.receipt_root), ProductionGazaAdapter(config, runner=runner, head_reader=head_reader, status_reader=status_reader, manifest_builder=manifest_builder).as_desk_adapter())
    try:
        return engine.execute(request, approval=approval)
    except ReceiptBusyError:
        return {"phase": "running", "terminal": False, "success": False, "outcome": "already_running", "next_action": "await_existing_run", "scheduler_outcome": "busy", "run_id": run_id}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the source-bound Gaza production execution contract.")
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--pages-repo", type=Path, required=True)
    parser.add_argument("--receipt-root", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--python", dest="python_executable", type=Path, required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--source-branch", required=True)
    parser.add_argument("--pages-branch", required=True)
    parser.add_argument("--expected-source-head", required=True)
    parser.add_argument("--expected-source-manifest-identity", required=True)
    parser.add_argument("--push", action="store_true", help="Rejected: publication is outside this candidate scope.")
    parser.add_argument("--post-bluesky", action="store_true", help="Rejected: publication is outside this candidate scope.")
    parser.add_argument("--generate-audio", action="store_true", help="Rejected: external effects are outside this candidate scope.")
    parser.add_argument("--tts-provider", default=None, help="Accepted only as part of a rejected audio request.")
    parser.add_argument("--smtp-debug", action="store_true", help="Rejected: email is outside this candidate scope.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.push or args.post_bluesky or args.generate_audio or args.tts_provider is not None or args.smtp_debug:
        print(json.dumps({"phase": "blocked", "outcome": "external_effects_not_authorized", "next_action": "run the reviewed publication path separately"}, sort_keys=True))
        return 2
    config = ProductionGazaConfig(
        source_root=args.source_root.resolve(),
        pages_repo=args.pages_repo.resolve(),
        receipt_root=args.receipt_root.resolve(),
        artifact_root=args.artifact_root.resolve(),
        scheduled_for=args.date,
        source_branch=args.source_branch,
        pages_branch=args.pages_branch,
        python_executable=args.python_executable.resolve(),
        expected_source_head=args.expected_source_head,
        expected_source_manifest_identity=args.expected_source_manifest_identity,
    )
    result = run_scheduled_gaza_contract(config)
    print(json.dumps(result, indent=2, sort_keys=True))
    if result.get("outcome") == "already_running":
        return 0
    if result.get("success") is True:
        return 0
    if result.get("phase") in {"awaiting_approval", "unknown"}:
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())



