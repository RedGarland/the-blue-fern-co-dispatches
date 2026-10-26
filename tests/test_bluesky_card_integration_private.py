from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import uuid
from unittest.mock import patch

import pytest


TASK_ROOT = Path(__file__).resolve().parent
if (TASK_ROOT / "src" / "bluefern_dispatches" / "bluesky_post.py").exists():
    PACKAGE_ROOT = TASK_ROOT
    BASE_SRC = PACKAGE_ROOT / "src"
    PRIVATE_SOURCE = BASE_SRC / "bluefern_dispatches" / "bluesky_post.py"
elif (TASK_ROOT.parent / "src" / "bluefern_dispatches" / "bluesky_post.py").exists():
    PACKAGE_ROOT = TASK_ROOT.parent
    BASE_SRC = PACKAGE_ROOT / "src"
    PRIVATE_SOURCE = BASE_SRC / "bluefern_dispatches" / "bluesky_post.py"
else:
    PACKAGE_ROOT = TASK_ROOT
    BASE_SRC = TASK_ROOT / ".food-base-a0aad-20261026" / "src"
    PRIVATE_SOURCE = TASK_ROOT / ".bluesky-card-integration-protected-private-20261026" / "bluesky_post.py"
sys.path.insert(0, str(TASK_ROOT))
sys.path.insert(0, str(BASE_SRC))
spec = importlib.util.spec_from_file_location("private_bluesky_post", PRIVATE_SOURCE)
assert spec and spec.loader
bluesky_post = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bluesky_post
spec.loader.exec_module(bluesky_post)


DID = "did:plc:q3k2kcpmvqexvxy7y6bdo46l"
HANDLE = "thebluefernco.bsky.social"
URL = "https://dispatches.thebluefernco.com/gaza/editions/2026-10-25/"
TEXT = f"Gaza dispatch | October 25\nLimited-source recovery.\n{URL}"
THUMB_CID = "bafkreigh2akiscaildc5q6wzq4example"
THUMB_HASH = "a" * 64
THUMB = {"$type": "blob", "ref": {"$link": THUMB_CID}, "mimeType": "image/jpeg", "size": 123}
CARD = {
    "edition_date": "2026-10-25",
    "edition_url": URL,
    "title": "Dispatches from Gaza - October 25, 2026",
    "description": "Limited-source Gaza update from The Blue Fern Co. for October 25, 2026.",
    "thumb_cid": THUMB_CID,
    "thumb_sha256": THUMB_HASH,
}
LIVE = {
    "status": "LIVE_OK",
    "live_http_ok": True,
    "live_archive_ok": True,
    "live_homepage_ok": True,
    "edition_url": URL,
    "archive_url": "https://dispatches.thebluefernco.com/gaza/archive.html",
}


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


@pytest.fixture
def private_root():
    path = TASK_ROOT / f".bluesky-card-fixture-{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def setup(tmp_path: Path, monkeypatch) -> tuple[Path, str]:
    text_path = tmp_path / "approved.txt"
    text_path.write_bytes(TEXT.encode("utf-8"))
    text_hash = hashlib.sha256(TEXT.encode("utf-8")).hexdigest()
    monkeypatch.setenv("BLUESKY_ENABLED", "1")
    monkeypatch.setenv("BLUESKY_POST_AFTER_GAZA", "1")
    monkeypatch.setenv("BLUESKY_HANDLE", HANDLE)
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "inert-test-secret")
    return text_path, text_hash


def common_patches(module):
    return [
        patch.object(module, "_gaza_bluesky_context", return_value={"source_artifact_paths": [], "edition_manifest": {}, "story_rows": [], "allowed_corpus": "", "expected_post_snippets": []}),
        patch.object(module, "_extract_gaza_edition_identity", return_value={}),
        patch.object(module, "_gaza_archive_surface_contains_edition", return_value={"ok": True}),
        patch.object(module, "_load_receipt_for_edition_any_status", return_value=None),
        patch.object(module, "_claim_pending_receipt", return_value=True),
        patch.object(module, "_upload_card_thumb", return_value=(THUMB, "uploaded", False, 123, 123, THUMB_HASH)),
        patch.object(module, "_post_json", return_value={"accessJwt": "inert-jwt", "did": DID}),
    ]


def invoke(module, tmp_path, text_path, text_hash, urlopen):
    stack = common_patches(module)
    for item in stack:
        item.start()
    try:
        with patch.object(module.request, "urlopen", side_effect=urlopen):
            return module.maybe_post_gaza_dispatch_to_bluesky(
                edition_date="2026-10-25",
                public_url=URL,
                run_succeeded=True,
                post_requested=True,
                project_root=tmp_path,
                approved_text_path=text_path,
                approved_text_sha256=text_hash,
                expected_handle=HANDLE,
                expected_did=DID,
                live_verification=LIVE,
                expected_card_mode="external",
                expected_external_card=CARD,
            )
    finally:
        for item in reversed(stack):
            item.stop()


def test_real_exact_path_emits_card_facets_and_verifies_readback(private_root, monkeypatch):
    tmp_path = private_root
    text_path, text_hash = setup(tmp_path, monkeypatch)
    created = {}
    calls = []
    uri = f"at://{DID}/app.bsky.feed.post/newcard"
    cid = "bafy-new-card"

    def urlopen(req, timeout):
        calls.append(req.full_url)
        if "listRecords" in req.full_url:
            return Response({"records": []})
        if "createRecord" in req.full_url:
            payload = json.loads(req.data.decode("utf-8"))
            created["record"] = payload["record"]
            return Response({"uri": uri, "cid": cid})
        if "getRecord" in req.full_url:
            return Response({"uri": uri, "cid": cid, "value": created["record"]})
        raise AssertionError(req.full_url)

    result = invoke(bluesky_post, tmp_path, text_path, text_hash, urlopen)
    assert result["status"] == "success"
    assert result["embed_type"] == "app.bsky.embed.external"
    assert created["record"]["embed"]["external"]["uri"] == URL
    assert created["record"]["embed"]["external"]["title"] == CARD["title"]
    assert created["record"]["embed"]["external"]["description"] == CARD["description"]
    assert created["record"]["embed"]["external"]["thumb"]["ref"]["$link"] == THUMB_CID
    assert created["record"]["facets"][0]["features"][0]["uri"] == URL
    assert any("getRecord" in url for url in calls)
    receipt = next((tmp_path / "data").rglob("bluesky_post_receipt.json")).read_text(encoding="utf-8")
    payload = json.loads(receipt)
    assert payload["card_mode"] == "external"
    assert payload["card_uri"] == URL
    assert payload["thumb_cid"] == THUMB_CID
    assert payload["link_facets_sha256"]


def test_plain_existing_record_is_conflict_and_never_creates(private_root, monkeypatch):
    tmp_path = private_root
    text_path, text_hash = setup(tmp_path, monkeypatch)
    calls = []
    uri = f"at://{DID}/app.bsky.feed.post/plain"

    def urlopen(req, timeout):
        calls.append(req.full_url)
        if "listRecords" in req.full_url:
            return Response({"records": [{"uri": uri, "cid": "cid-plain", "value": {"$type": "app.bsky.feed.post", "text": TEXT}}]})
        raise AssertionError("create/readback must not be reached")

    result = invoke(bluesky_post, tmp_path, text_path, text_hash, urlopen)
    assert result["status"] == "blocked"
    assert result["reason"] == "ambiguous_remote_duplicate"
    assert not any("createRecord" in url for url in calls)


def test_readback_missing_card_is_uncertain_without_second_create_or_delete(private_root, monkeypatch):
    tmp_path = private_root
    text_path, text_hash = setup(tmp_path, monkeypatch)
    calls = []
    uri = f"at://{DID}/app.bsky.feed.post/unverified"

    def urlopen(req, timeout):
        calls.append(req.full_url)
        if "listRecords" in req.full_url:
            return Response({"records": []})
        if "createRecord" in req.full_url:
            return Response({"uri": uri, "cid": "cid-unverified"})
        if "getRecord" in req.full_url:
            return Response({"uri": uri, "cid": "cid-unverified", "value": {"$type": "app.bsky.feed.post", "text": TEXT}})
        raise AssertionError(req.full_url)

    result = invoke(bluesky_post, tmp_path, text_path, text_hash, urlopen)
    assert result["status"] == "uncertain"
    assert result["reason"] == "post_outcome_uncertain_reconcile_before_retry"
    assert sum("createRecord" in url for url in calls) == 1
    assert not any("deleteRecord" in url for url in calls)


def test_external_card_mode_requires_contract_before_any_session_or_create(private_root, monkeypatch):
    tmp_path = private_root
    text_path, text_hash = setup(tmp_path, monkeypatch)
    calls = []

    def urlopen(req, timeout):
        calls.append(req.full_url)
        raise AssertionError("network must not be reached")

    stack = common_patches(bluesky_post)
    for item in stack:
        item.start()
    try:
        with patch.object(bluesky_post.request, "urlopen", side_effect=urlopen):
            result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(
                edition_date="2026-10-25",
                public_url=URL,
                run_succeeded=True,
                post_requested=True,
                project_root=tmp_path,
                approved_text_path=text_path,
                approved_text_sha256=text_hash,
                expected_handle=HANDLE,
                expected_did=DID,
                live_verification=LIVE,
                expected_card_mode="external",
            )
    finally:
        for item in reversed(stack):
            item.stop()
    assert result["status"] == "blocked"
    assert result["reason"] == "external_card_contract_invalid"
    assert calls == []


def test_matching_external_duplicate_receipt_replays_without_rejection(private_root, monkeypatch):
    tmp_path = private_root
    text_path, text_hash = setup(tmp_path, monkeypatch)
    contract = bluesky_post._private_external_card_contract(CARD, edition_date="2026-10-25", public_url=URL, post_text=TEXT)
    record = bluesky_post.build_external_record(
        post_text=TEXT,
        contract=contract,
        thumb_blob=THUMB,
        created_at="2026-10-26T00:00:00Z",
    )
    uri = f"at://{DID}/app.bsky.feed.post/existing-card"
    cid = "bafy-existing-card"
    calls = []
    stack = [
        patch.object(bluesky_post, "_gaza_bluesky_context", return_value={"source_artifact_paths": [], "edition_manifest": {}, "story_rows": [], "allowed_corpus": "", "expected_post_snippets": []}),
        patch.object(bluesky_post, "_extract_gaza_edition_identity", return_value={}),
        patch.object(bluesky_post, "_gaza_archive_surface_contains_edition", return_value={"ok": True}),
        patch.object(bluesky_post, "_upload_card_thumb", return_value=(THUMB, "uploaded", False, 123, 123, THUMB_HASH)),
        patch.object(bluesky_post, "_post_json", return_value={"accessJwt": "inert-jwt", "did": DID}),
    ]
    for item in stack:
        item.start()
    try:
        def urlopen(req, timeout):
            calls.append(req.full_url)
            if "listRecords" in req.full_url:
                return Response({"records": [{"uri": uri, "cid": cid, "value": record}]})
            raise AssertionError(req.full_url)

        with patch.object(bluesky_post.request, "urlopen", side_effect=urlopen):
            first = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(
                edition_date="2026-10-25",
                public_url=URL,
                run_succeeded=True,
                post_requested=True,
                project_root=tmp_path,
                approved_text_path=text_path,
                approved_text_sha256=text_hash,
                expected_handle=HANDLE,
                expected_did=DID,
                live_verification=LIVE,
                expected_card_mode="external",
                expected_external_card=CARD,
            )
        assert first["status"] == "skipped"
        receipt = json.loads(next((tmp_path / "data").rglob("bluesky_post_receipt.json")).read_text(encoding="utf-8"))
        assert receipt["embed_type"] == "app.bsky.embed.external"
        assert receipt["link_facets_sha256"]
        call_count = len(calls)

        with patch.object(bluesky_post.request, "urlopen", side_effect=AssertionError("replay must use the validated receipt")):
            second = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(
                edition_date="2026-10-25",
                public_url=URL,
                run_succeeded=True,
                post_requested=True,
                project_root=tmp_path,
                approved_text_path=text_path,
                approved_text_sha256=text_hash,
                expected_handle=HANDLE,
                expected_did=DID,
                live_verification=LIVE,
                expected_card_mode="external",
                expected_external_card=CARD,
            )
        assert second["reason"] == "skipped_existing_exact_receipt"
        assert len(calls) == call_count
    finally:
        for item in reversed(stack):
            item.stop()
