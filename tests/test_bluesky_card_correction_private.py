from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import sys
import uuid
from urllib.error import HTTPError
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
spec = importlib.util.spec_from_file_location("private_bluesky_post_correction", PRIVATE_SOURCE)
assert spec and spec.loader
bluesky_post = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bluesky_post
spec.loader.exec_module(bluesky_post)


DID = "did:plc:q3k2kcpmvqexvxy7y6bdo46l"
HANDLE = "thebluefernco.bsky.social"
URL = "https://dispatches.thebluefernco.com/gaza/editions/2026-10-25/"
TEXT = f"Gaza dispatch | October 25\nLimited-source recovery.\n{URL}"
OLD_RKEY = "oldplain"
OLD_URI = f"at://{DID}/app.bsky.feed.post/{OLD_RKEY}"
OLD_CID = "bafy-old-plain"
NEW_URI = f"at://{DID}/app.bsky.feed.post/new-card"
NEW_CID = "bafy-new-card"
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
    path = TASK_ROOT / f".bluesky-card-correction-fixture-{uuid.uuid4().hex}"
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


def patches(module):
    return [
        patch.object(module, "_gaza_bluesky_context", return_value={"source_artifact_paths": [], "edition_manifest": {}, "story_rows": [], "allowed_corpus": "", "expected_post_snippets": []}),
        patch.object(module, "_extract_gaza_edition_identity", return_value={}),
        patch.object(module, "_gaza_archive_surface_contains_edition", return_value={"ok": True}),
        patch.object(module, "_load_receipt_for_edition_any_status", return_value={"status": "success", "post_uri": OLD_URI}),
        patch.object(module, "_upload_card_thumb", return_value=(THUMB, "uploaded", False, 123, 123, THUMB_HASH)),
        patch.object(module, "_post_json", return_value={"accessJwt": "inert-jwt", "did": DID}),
    ]


def invoke(module, tmp_path, text_path, text_hash, urlopen):
    stack = patches(module)
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
                correction_old_uri=OLD_URI,
                correction_old_cid=OLD_CID,
            )
    finally:
        for item in reversed(stack):
            item.stop()


def old_record(cid=OLD_CID):
    return {"uri": OLD_URI, "cid": cid, "value": {"$type": "app.bsky.feed.post", "text": TEXT}}


def new_record(record):
    return {"uri": NEW_URI, "cid": NEW_CID, "value": record}


def test_correction_ignores_only_exact_old_plain_record_and_writes_separate_receipt(private_root, monkeypatch):
    text_path, text_hash = setup(private_root, monkeypatch)
    created = {}
    calls = []

    def urlopen(req, timeout):
        calls.append(req.full_url)
        if "getRecord" in req.full_url and f"rkey={OLD_RKEY}" in req.full_url:
            return Response(old_record())
        if "getPostThread" in req.full_url:
            return Response({"thread": {"post": {"uri": OLD_URI, "cid": OLD_CID, "likeCount": 0, "repostCount": 0, "replyCount": 0, "quoteCount": 0}}})
        if "listRecords" in req.full_url:
            return Response({"records": [old_record()]})
        if "createRecord" in req.full_url:
            created["record"] = json.loads(req.data.decode("utf-8"))["record"]
            return Response({"uri": NEW_URI, "cid": NEW_CID})
        if "getRecord" in req.full_url and "rkey=new-card" in req.full_url:
            return Response(new_record(created["record"]))
        raise AssertionError(req.full_url)

    result = invoke(bluesky_post, private_root, text_path, text_hash, urlopen)
    assert result["status"] == "success"
    assert result["correction_mode"] is True
    assert sum("createRecord" in url for url in calls) == 1
    receipt_path = private_root / "data" / "dispatches" / "gaza" / "editions" / "2026-10-25" / "bluesky_card_correction_receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "created_verified_pending_old_delete"
    assert receipt["old_uri"] == OLD_URI
    assert receipt["old_cid"] == OLD_CID
    assert receipt["new_uri"] == NEW_URI


def test_correction_blocks_other_same_text_conflict(private_root, monkeypatch):
    text_path, text_hash = setup(private_root, monkeypatch)
    other = {"uri": f"at://{DID}/app.bsky.feed.post/other", "cid": "bafy-other", "value": {"$type": "app.bsky.feed.post", "text": TEXT}}
    calls = []

    def urlopen(req, timeout):
        calls.append(req.full_url)
        if "getRecord" in req.full_url and f"rkey={OLD_RKEY}" in req.full_url:
            return Response(old_record())
        if "getPostThread" in req.full_url:
            return Response({"thread": {"post": {"uri": OLD_URI, "cid": OLD_CID, "likeCount": 0, "repostCount": 0, "replyCount": 0, "quoteCount": 0}}})
        if "listRecords" in req.full_url:
            return Response({"records": [old_record(), other]})
        raise AssertionError("create must not be reached")

    result = invoke(bluesky_post, private_root, text_path, text_hash, urlopen)
    assert result["status"] == "blocked"
    assert result["reason"] == "ambiguous_remote_duplicate"
    assert not any("createRecord" in url for url in calls)


def test_correction_blocks_changed_old_engagement_before_create(private_root, monkeypatch):
    text_path, text_hash = setup(private_root, monkeypatch)
    calls = []

    def urlopen(req, timeout):
        calls.append(req.full_url)
        if "getRecord" in req.full_url and f"rkey={OLD_RKEY}" in req.full_url:
            return Response(old_record())
        if "getPostThread" in req.full_url:
            return Response({"thread": {"post": {"uri": OLD_URI, "cid": OLD_CID, "likeCount": 1, "repostCount": 0, "replyCount": 0, "quoteCount": 0}}})
        raise AssertionError("duplicate scan/create must not be reached")

    result = invoke(bluesky_post, private_root, text_path, text_hash, urlopen)
    assert result["status"] == "blocked"
    assert result["reason"] == "correction_old_record_engagement_changed"
    assert not any("createRecord" in url for url in calls)


def test_correction_blocks_wrong_engagement_identity_before_create(private_root, monkeypatch):
    text_path, text_hash = setup(private_root, monkeypatch)
    calls = []

    def urlopen(req, timeout):
        calls.append(req.full_url)
        if "getRecord" in req.full_url and f"rkey={OLD_RKEY}" in req.full_url:
            return Response(old_record())
        if "getPostThread" in req.full_url:
            return Response({"thread": {"post": {"uri": f"at://{DID}/app.bsky.feed.post/other", "cid": OLD_CID, "likeCount": 0, "repostCount": 0, "replyCount": 0, "quoteCount": 0}}})
        raise AssertionError("duplicate scan/create must not be reached")

    result = invoke(bluesky_post, private_root, text_path, text_hash, urlopen)
    assert result["status"] == "blocked"
    assert result["reason"] == "correction_old_engagement_identity_mismatch"
    assert not any("listRecords" in url or "createRecord" in url for url in calls)


def test_correction_timeout_persists_uncertainty_and_blocks_retry(private_root, monkeypatch):
    text_path, text_hash = setup(private_root, monkeypatch)
    create_calls = {"count": 0}

    def first_urlopen(req, timeout):
        if "getRecord" in req.full_url and f"rkey={OLD_RKEY}" in req.full_url:
            return Response(old_record())
        if "getPostThread" in req.full_url:
            return Response({"thread": {"post": {"uri": OLD_URI, "cid": OLD_CID, "likeCount": 0, "repostCount": 0, "replyCount": 0, "quoteCount": 0}}})
        if "listRecords" in req.full_url:
            return Response({"records": [old_record()]})
        if "createRecord" in req.full_url:
            create_calls["count"] += 1
            raise TimeoutError("inert timeout after request start")
        raise AssertionError(req.full_url)

    first = invoke(bluesky_post, private_root, text_path, text_hash, first_urlopen)
    assert first["status"] == "uncertain"
    assert create_calls["count"] == 1

    second = invoke(bluesky_post, private_root, text_path, text_hash, lambda req, timeout: (_ for _ in ()).throw(AssertionError("retry must be blocked before network")))
    assert second["status"] == "blocked"
    assert second["reason"] == "correction_receipt_requires_reconciliation"


def test_verified_new_then_exact_old_delete_only(private_root, monkeypatch):
    text_path, text_hash = setup(private_root, monkeypatch)
    created = {}
    deleted = {"value": False}

    def urlopen(req, timeout):
        if "getRecord" in req.full_url and f"rkey={OLD_RKEY}" in req.full_url:
            if deleted["value"]:
                raise HTTPError(req.full_url, 400, "not found", {}, io.BytesIO(b'{"error":"RecordNotFound","message":"record not found"}'))
            return Response(old_record())
        if "getPostThread" in req.full_url:
            return Response({"thread": {"post": {"uri": OLD_URI, "cid": OLD_CID, "likeCount": 0, "repostCount": 0, "replyCount": 0, "quoteCount": 0}}})
        if "listRecords" in req.full_url:
            return Response({"records": [old_record()]})
        if "createRecord" in req.full_url:
            created["record"] = json.loads(req.data.decode("utf-8"))["record"]
            return Response({"uri": NEW_URI, "cid": NEW_CID})
        if "getRecord" in req.full_url and "rkey=new-card" in req.full_url:
            return Response(new_record(created["record"]))
        if "deleteRecord" in req.full_url:
            payload = json.loads(req.data.decode("utf-8"))
            assert payload == {"repo": DID, "collection": "app.bsky.feed.post", "rkey": OLD_RKEY, "swapRecord": OLD_CID}
            deleted["value"] = True
            return Response({})
        raise AssertionError(req.full_url)

    result = invoke(bluesky_post, private_root, text_path, text_hash, urlopen)
    assert result["status"] == "success"
    contract = bluesky_post._private_external_card_contract(CARD, edition_date="2026-10-25", public_url=URL, post_text=TEXT)
    with patch.object(bluesky_post.request, "urlopen", side_effect=urlopen):
        deleted_result = bluesky_post.delete_verified_gaza_card_correction(
            access_jwt="inert-jwt",
            project_root=private_root,
            edition_date="2026-10-25",
            public_url=URL,
            post_text=TEXT,
            expected_did=DID,
            old_uri=OLD_URI,
            old_cid=OLD_CID,
            new_uri=NEW_URI,
            new_cid=NEW_CID,
            expected_card_contract=contract,
        )
    assert deleted_result["status"] == "completed"
    receipt_path = private_root / "data" / "dispatches" / "gaza" / "editions" / "2026-10-25" / "bluesky_card_correction_receipt.json"
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["old_delete_status"] == "verified_absent"


def test_delete_cid_race_is_uncertain_and_preserves_compare_and_swap(private_root):
    contract = bluesky_post._private_external_card_contract(CARD, edition_date="2026-10-25", public_url=URL, post_text=TEXT)
    bluesky_post._write_correction_receipt(
        project_root=private_root,
        edition_date="2026-10-25",
        public_url=URL,
        post_text=TEXT,
        old_uri=OLD_URI,
        old_cid=OLD_CID,
        old_engagement={"likeCount": 0, "repostCount": 0, "replyCount": 0, "quoteCount": 0},
        status="created_verified_pending_old_delete",
        new_uri=NEW_URI,
        new_cid=NEW_CID,
        card_contract=contract,
    )
    new_value = bluesky_post.build_external_record(post_text=TEXT, contract=contract, thumb_blob=THUMB, created_at="2026-10-26T00:00:00Z")
    delete_payloads = []
    absence_checks = []

    def urlopen(req, timeout):
        if "getRecord" in req.full_url and "rkey=new-card" in req.full_url:
            return Response(new_record(new_value))
        if "getRecord" in req.full_url and f"rkey={OLD_RKEY}" in req.full_url:
            return Response(old_record())
        if "getPostThread" in req.full_url:
            return Response({"thread": {"post": {"uri": OLD_URI, "cid": OLD_CID, "likeCount": 0, "repostCount": 0, "replyCount": 0, "quoteCount": 0}}})
        if "deleteRecord" in req.full_url:
            delete_payloads.append(json.loads(req.data.decode("utf-8")))
            raise HTTPError(req.full_url, 400, "swap mismatch", {}, io.BytesIO(b'{"error":"InvalidSwap","message":"record changed"}'))
        if "getRecord" in req.full_url and f"rkey={OLD_RKEY}" in req.full_url:
            absence_checks.append(req.full_url)
        raise AssertionError(req.full_url)

    with patch.object(bluesky_post.request, "urlopen", side_effect=urlopen):
        result = bluesky_post.delete_verified_gaza_card_correction(
            access_jwt="inert-jwt",
            project_root=private_root,
            edition_date="2026-10-25",
            public_url=URL,
            post_text=TEXT,
            expected_did=DID,
            old_uri=OLD_URI,
            old_cid=OLD_CID,
            new_uri=NEW_URI,
            new_cid=NEW_CID,
            expected_card_contract=contract,
        )

    assert result["status"] == "uncertain"
    assert result["reason"] == "correction_old_delete_outcome_uncertain"
    assert delete_payloads == [{"repo": DID, "collection": "app.bsky.feed.post", "rkey": OLD_RKEY, "swapRecord": OLD_CID}]
    assert absence_checks == []
    receipt_path = private_root / "data" / "dispatches" / "gaza" / "editions" / "2026-10-25" / "bluesky_card_correction_receipt.json"
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["status"] == "uncertain"


def test_delete_readback_still_present_persists_uncertainty_across_restart(private_root):
    contract = bluesky_post._private_external_card_contract(CARD, edition_date="2026-10-25", public_url=URL, post_text=TEXT)
    bluesky_post._write_correction_receipt(
        project_root=private_root,
        edition_date="2026-10-25",
        public_url=URL,
        post_text=TEXT,
        old_uri=OLD_URI,
        old_cid=OLD_CID,
        old_engagement={"likeCount": 0, "repostCount": 0, "replyCount": 0, "quoteCount": 0},
        status="created_verified_pending_old_delete",
        new_uri=NEW_URI,
        new_cid=NEW_CID,
        card_contract=contract,
    )
    new_value = bluesky_post.build_external_record(post_text=TEXT, contract=contract, thumb_blob=THUMB, created_at="2026-10-26T00:00:00Z")
    delete_calls = {"count": 0}

    def urlopen(req, timeout):
        if "getRecord" in req.full_url and "rkey=new-card" in req.full_url:
            return Response(new_record(new_value))
        if "getRecord" in req.full_url and f"rkey={OLD_RKEY}" in req.full_url:
            return Response(old_record())
        if "getPostThread" in req.full_url:
            return Response({"thread": {"post": {"uri": OLD_URI, "cid": OLD_CID, "likeCount": 0, "repostCount": 0, "replyCount": 0, "quoteCount": 0}}})
        if "deleteRecord" in req.full_url:
            delete_calls["count"] += 1
            return Response({})
        raise AssertionError(req.full_url)

    with patch.object(bluesky_post.request, "urlopen", side_effect=urlopen):
        first = bluesky_post.delete_verified_gaza_card_correction(
            access_jwt="inert-jwt",
            project_root=private_root,
            edition_date="2026-10-25",
            public_url=URL,
            post_text=TEXT,
            expected_did=DID,
            old_uri=OLD_URI,
            old_cid=OLD_CID,
            new_uri=NEW_URI,
            new_cid=NEW_CID,
            expected_card_contract=contract,
        )
        second = bluesky_post.delete_verified_gaza_card_correction(
            access_jwt="inert-jwt",
            project_root=private_root,
            edition_date="2026-10-25",
            public_url=URL,
            post_text=TEXT,
            expected_did=DID,
            old_uri=OLD_URI,
            old_cid=OLD_CID,
            new_uri=NEW_URI,
            new_cid=NEW_CID,
            expected_card_contract=contract,
        )

    assert first == {"status": "uncertain", "reason": "correction_old_record_still_present"}
    assert second == {"status": "blocked", "reason": "correction_receipt_not_ready_for_delete"}
    assert delete_calls["count"] == 1
    receipt_path = private_root / "data" / "dispatches" / "gaza" / "editions" / "2026-10-25" / "bluesky_card_correction_receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "uncertain"
    assert receipt["reason"] == "correction_old_record_still_present"
    assert receipt["old_delete_status"] == "old_record_still_present"


def test_correction_pending_claim_is_exclusive_under_concurrency(private_root):
    contract = bluesky_post._private_external_card_contract(CARD, edition_date="2026-10-25", public_url=URL, post_text=TEXT)
    # Keep directory provisioning outside the race so this test exercises the
    # durable claim lock, not Windows' concurrent recursive mkdir behavior.
    bluesky_post._correction_receipt_path(private_root, "2026-10-25").parent.mkdir(parents=True, exist_ok=True)

    def claim():
        return bluesky_post._claim_correction_pending_receipt(
            project_root=private_root,
            edition_date="2026-10-25",
            public_url=URL,
            post_text=TEXT,
            old_uri=OLD_URI,
            old_cid=OLD_CID,
            old_engagement={"likeCount": 0, "repostCount": 0, "replyCount": 0, "quoteCount": 0},
            card_contract=contract,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: claim(), range(2)))
    assert sorted(results) == [False, True]
    receipt_path = private_root / "data" / "dispatches" / "gaza" / "editions" / "2026-10-25" / "bluesky_card_correction_receipt.json"
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["status"] == "pending_create"


def test_deletion_rejects_arbitrary_400_as_absence(private_root):
    contract = bluesky_post._private_external_card_contract(CARD, edition_date="2026-10-25", public_url=URL, post_text=TEXT)
    bluesky_post._write_correction_receipt(
        project_root=private_root,
        edition_date="2026-10-25",
        public_url=URL,
        post_text=TEXT,
        old_uri=OLD_URI,
        old_cid=OLD_CID,
        old_engagement={"likeCount": 0, "repostCount": 0, "replyCount": 0, "quoteCount": 0},
        status="created_verified_pending_old_delete",
        new_uri=NEW_URI,
        new_cid=NEW_CID,
        card_contract=contract,
    )
    new_value = bluesky_post.build_external_record(post_text=TEXT, contract=contract, thumb_blob=THUMB, created_at="2026-10-26T00:00:00Z")
    deleted = {"value": False}

    def urlopen(req, timeout):
        if "getRecord" in req.full_url and "rkey=new-card" in req.full_url:
            return Response(new_record(new_value))
        if "getRecord" in req.full_url and f"rkey={OLD_RKEY}" in req.full_url:
            if deleted["value"]:
                raise HTTPError(req.full_url, 400, "bad request", {}, io.BytesIO(b'{"error":"OtherError","message":"not a record-not-found response"}'))
            return Response(old_record())
        if "getPostThread" in req.full_url:
            return Response({"thread": {"post": {"uri": OLD_URI, "cid": OLD_CID, "likeCount": 0, "repostCount": 0, "replyCount": 0, "quoteCount": 0}}})
        if "deleteRecord" in req.full_url:
            deleted["value"] = True
            return Response({})
        raise AssertionError(req.full_url)

    with patch.object(bluesky_post.request, "urlopen", side_effect=urlopen):
        result = bluesky_post.delete_verified_gaza_card_correction(
            access_jwt="inert-jwt",
            project_root=private_root,
            edition_date="2026-10-25",
            public_url=URL,
            post_text=TEXT,
            expected_did=DID,
            old_uri=OLD_URI,
            old_cid=OLD_CID,
            new_uri=NEW_URI,
            new_cid=NEW_CID,
            expected_card_contract=contract,
        )
    assert result["status"] == "uncertain"
    assert result["reason"] == "correction_old_delete_outcome_uncertain"
