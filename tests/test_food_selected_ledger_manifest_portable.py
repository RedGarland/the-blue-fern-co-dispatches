from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "food-selected-ledger-countfix-manifest-portable.json"


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _canonical_utf8_lf(data: bytes) -> bytes:
    text = data.decode("utf-8", errors="strict")
    canonical = data.replace(b"\r\n", b"\n")
    if b"\r" in canonical:
        raise ValueError("lone carriage return is not a permitted checkout conversion")
    if text.encode("utf-8") != data:
        raise ValueError("UTF-8 bytes do not round-trip exactly")
    return canonical


def test_manifest_binds_five_candidate_files_without_machine_paths() -> None:
    manifest = _manifest()
    files = manifest["runtime_files"]
    assert len(files) == 5
    assert len({row["repository_path"] for row in files}) == 5
    for row in files:
        for key in ("repository_path", "candidate_path"):
            value = row[key]
            assert not Path(value).is_absolute()
            assert "\\" not in value
            assert ":" not in value
        candidate = ROOT / row["candidate_path"]
        assert candidate.is_file(), row["candidate_path"]
        raw = candidate.read_bytes()
        canonical = _canonical_utf8_lf(raw)
        assert len(raw) > 0, row["candidate_path"]
        assert isinstance(row["candidate_bytes"], int) and row["candidate_bytes"] > 0
        assert re.fullmatch(r"[0-9A-F]{64}", row["candidate_sha256"])
        assert len(canonical) == row["canonical_lf_bytes"], row["candidate_path"]
        assert hashlib.sha256(canonical).hexdigest().upper() == row["canonical_lf_sha256"], row["candidate_path"]
        assert re.fullmatch(r"[0-9A-F]{64}", row["base_sha256"])
        assert isinstance(row["base_bytes"], int) and row["base_bytes"] > 0


def test_manifest_preserves_explicit_operator_repository_mapping() -> None:
    operator = next(
        row for row in _manifest()["runtime_files"]
        if row["repository_path"] == "scripts/windows/run_food_care_proof_continuation.ps1"
    )
    assert operator["candidate_path"] == "scripts/windows/run_food_care_proof_continuation.ps1"
    assert operator["candidate_sha256"] == (
        "CBBA242BABF5A186538C591A1BA8AF940630C880B9D8719A37BAC58788E0509D"
    )


def test_manifest_contains_no_absolute_operational_receipt_paths() -> None:
    raw = MANIFEST.read_text(encoding="utf-8")
    assert "C:\\" not in raw
    assert "\\BlueFernRunner\\" not in raw
    assert "operational-health" not in raw


def test_canonicalization_accepts_only_crlf_to_lf_and_preserves_bom() -> None:
    data = b"\xef\xbb\xbfline one\r\nline two\r\n"
    assert _canonical_utf8_lf(data) == b"\xef\xbb\xbfline one\nline two\n"


def test_canonicalization_rejects_invalid_utf8_lone_cr_and_content_mutation() -> None:
    import pytest

    with pytest.raises(ValueError):
        _canonical_utf8_lf(b"line\rnext")
    with pytest.raises(UnicodeDecodeError):
        _canonical_utf8_lf(b"\xff\xfe")
    expected = _canonical_utf8_lf(b"approved\r\n")
    assert _canonical_utf8_lf(b"changed\r\n") != expected


def test_manifest_acceptance_uses_canonical_identity_for_lf_and_crlf_and_rejects_mutation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import copy
    import pytest

    source_root = ROOT
    source_manifest = _manifest()
    module = sys.modules[__name__]

    def make_fixture(line_ending: bytes) -> tuple[Path, Path]:
        fixture_root = tmp_path / ("fixture-lf" if line_ending == b"\n" else "fixture-crlf")
        fixture_manifest = fixture_root / "food-selected-ledger-countfix-manifest-portable.json"
        for row in source_manifest["runtime_files"]:
            source = source_root / row["candidate_path"]
            destination = fixture_root / row["candidate_path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            canonical = _canonical_utf8_lf(source.read_bytes())
            destination.write_bytes(canonical.replace(b"\n", line_ending))
        fixture_manifest.parent.mkdir(parents=True, exist_ok=True)
        fixture_manifest.write_text(json.dumps(copy.deepcopy(source_manifest), indent=2) + "\n", encoding="utf-8")
        return fixture_root, fixture_manifest

    for line_ending in (b"\n", b"\r\n"):
        fixture_root, fixture_manifest = make_fixture(line_ending)
        monkeypatch.setattr(module, "ROOT", fixture_root)
        monkeypatch.setattr(module, "MANIFEST", fixture_manifest)
        test_manifest_binds_five_candidate_files_without_machine_paths()

    mutated_root, mutated_manifest = make_fixture(b"\r\n")
    mutated = mutated_root / source_manifest["runtime_files"][0]["candidate_path"]
    mutated.write_bytes(mutated.read_bytes() + b"\r\n# substantive mutation\r\n")
    monkeypatch.setattr(module, "ROOT", mutated_root)
    monkeypatch.setattr(module, "MANIFEST", mutated_manifest)
    with pytest.raises(AssertionError):
        test_manifest_binds_five_candidate_files_without_machine_paths()
