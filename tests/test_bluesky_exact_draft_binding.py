import hashlib
import json
from pathlib import Path

import pytest

from bluefern_dispatches import bluesky_post


EDITION_DATE = "2026-10-24"
PUBLIC_URL = "https://dispatches.thebluefernco.com/gaza/editions/2026-10-24/"
HANDLE = "thebluefernco.bsky.social"
DID = "did:plc:q3k2kcpmvqexvxy7y6bdo46l"
TEXT = (
    "Gaza dispatch | October 24\n"
    "WAFA reported two Palestinians killed and others injured in an October 23 strike in Al-Nasr, western Gaza City. Limited-source recovery, not a full daily briefing.\n"
    "https://dispatches.thebluefernco.com/gaza/editions/2026-10-24/"
)
TEXT_SHA256 = hashlib.sha256(TEXT.encode("utf-8")).hexdigest()


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def _live_verification():
    return {
        "status": "LIVE_OK",
        "live_http_ok": True,
        "live_archive_ok": True,
        "live_homepage_ok": True,
        "edition_url": f"{PUBLIC_URL}?v=2026-10-24",
        "archive_url": "https://dispatches.thebluefernco.com/gaza/archive.html?v=2026-10-24",
    }


def _write_exact_text(tmp_path: Path, text: str = TEXT) -> tuple[Path, str]:
    path = tmp_path / "approved-bluesky-post.txt"
    raw = text.encode("utf-8")
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


def _kwargs(tmp_path: Path, text: str = TEXT) -> dict:
    path, digest = _write_exact_text(tmp_path, text)
    return {
        "edition_date": EDITION_DATE,
        "public_url": PUBLIC_URL,
        "run_succeeded": True,
        "post_requested": True,
        "project_root": tmp_path,
        "approved_text_path": path,
        "approved_text_sha256": digest,
        "expected_handle": HANDLE,
        "expected_did": DID,
        "live_verification": _live_verification(),
    }


def _configure_env(monkeypatch):
    monkeypatch.setenv("BLUESKY_ENABLED", "1")
    monkeypatch.setenv("BLUESKY_POST_AFTER_GAZA", "1")
    monkeypatch.setenv("BLUESKY_HANDLE", HANDLE)
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "test-only-secret")


def _session(monkeypatch, did=DID):
    monkeypatch.setattr(
        bluesky_post,
        "_post_json",
        lambda url, payload: {"accessJwt": "token", "did": did},
    )


def test_exact_draft_rejects_wrong_hash_before_authentication(monkeypatch, tmp_path):
    _configure_env(monkeypatch)
    kwargs = _kwargs(tmp_path)
    kwargs["approved_text_sha256"] = "0" * 64
    called = {"session": 0}
    monkeypatch.setattr(bluesky_post, "_post_json", lambda *_args, **_kwargs: called.__setitem__("session", 1))

    result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(**kwargs)

    assert result["status"] == "blocked"
    assert result["reason"] == "exact_text_hash_mismatch"
    assert called["session"] == 0


def test_exact_draft_rejects_wrong_authenticated_did(monkeypatch, tmp_path):
    _configure_env(monkeypatch)
    _session(monkeypatch, did="did:plc:wrong")
    called = []
    monkeypatch.setattr(bluesky_post.request, "urlopen", lambda *args, **kwargs: called.append(args))

    result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(**_kwargs(tmp_path))

    assert result["status"] == "blocked"
    assert result["reason"] == "exact_target_did_mismatch"
    assert called == []


def test_missing_local_edition_uses_verified_public_recovery_and_posts_text_only(monkeypatch, tmp_path):
    _configure_env(monkeypatch)
    _session(monkeypatch)
    calls = []

    def fake_urlopen(req, timeout=20.0):
        calls.append(req.full_url)
        if "listRecords" in req.full_url:
            return FakeResponse({"records": []})
        assert req.full_url.endswith("/com.atproto.repo.createRecord")
        payload = json.loads(req.data.decode("utf-8"))
        assert payload["record"]["text"] == TEXT
        assert "embed" not in payload["record"]
        return FakeResponse({"uri": f"at://{DID}/app.bsky.feed.post/new", "cid": "bafy-new"})

    monkeypatch.setattr(bluesky_post.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(bluesky_post, "_upload_card_thumb", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("thumbnail must not be attempted")))

    result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(**_kwargs(tmp_path))

    assert result["status"] == "success"
    assert result["post_text"] == TEXT
    assert result["approved_text_sha256"] == TEXT_SHA256
    assert calls[0].split("?", 1)[0].endswith("/com.atproto.repo.listRecords")
    receipt = tmp_path / "data" / "dispatches" / "gaza" / "editions" / EDITION_DATE / "bluesky_post_receipt.json"
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["post_text_sha256"] == TEXT_SHA256
    assert payload["target_did"] == DID
    assert payload["approved_text_sha256"] == TEXT_SHA256


def test_missing_post_uri_is_uncertain_and_blocks_retry(monkeypatch, tmp_path):
    _configure_env(monkeypatch)
    _session(monkeypatch)
    calls = {"records": 0, "create": 0}

    def fake_urlopen(req, timeout=20.0):
        if "listRecords" in req.full_url:
            calls["records"] += 1
            return FakeResponse({"records": []})
        calls["create"] += 1
        return FakeResponse({"cid": "bafy-no-uri"})

    monkeypatch.setattr(bluesky_post.request, "urlopen", fake_urlopen)
    kwargs = _kwargs(tmp_path)
    first = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(**kwargs)

    assert first["status"] == "uncertain"
    assert first["reason"] == "post_outcome_uncertain_reconcile_before_retry"
    assert first["error_type"] == "InvalidCreateRecordResponse"
    receipt = tmp_path / "data" / "dispatches" / "gaza" / "editions" / EDITION_DATE / "bluesky_post_receipt.json"
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["status"] == "uncertain"
    assert payload["reason"] == "post_outcome_uncertain_reconcile_before_retry"
    assert payload["post_cid"] == "bafy-no-uri"

    second = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(**kwargs)
    assert second["status"] == "blocked"
    assert second["reason"] == "post_outcome_uncertain_reconcile_before_retry"
    assert calls == {"records": 1, "create": 1}


def test_wrong_create_record_identity_is_uncertain_and_blocks_retry(monkeypatch, tmp_path):
    _configure_env(monkeypatch)
    _session(monkeypatch)
    calls = {"records": 0, "create": 0}

    def fake_urlopen(req, timeout=20.0):
        if "listRecords" in req.full_url:
            calls["records"] += 1
            return FakeResponse({"records": []})
        calls["create"] += 1
        return FakeResponse({"uri": "at://did:plc:wrong/app.bsky.feed.like/nope"})

    monkeypatch.setattr(bluesky_post.request, "urlopen", fake_urlopen)
    kwargs = _kwargs(tmp_path)
    first = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(**kwargs)

    assert first["status"] == "uncertain"
    assert first["reason"] == "post_outcome_uncertain_reconcile_before_retry"
    receipt = tmp_path / "data" / "dispatches" / "gaza" / "editions" / EDITION_DATE / "bluesky_post_receipt.json"
    assert json.loads(receipt.read_text(encoding="utf-8"))["status"] == "uncertain"
    second = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(**kwargs)
    assert second["status"] == "blocked"
    assert calls == {"records": 1, "create": 1}


def test_expected_create_record_uri_without_cid_is_uncertain(monkeypatch, tmp_path):
    _configure_env(monkeypatch)
    _session(monkeypatch)

    def fake_urlopen(req, timeout=20.0):
        if "listRecords" in req.full_url:
            return FakeResponse({"records": []})
        return FakeResponse({"uri": f"at://{DID}/app.bsky.feed.post/no-cid"})

    monkeypatch.setattr(bluesky_post.request, "urlopen", fake_urlopen)
    result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(**_kwargs(tmp_path))

    assert result["status"] == "uncertain"
    assert result["reason"] == "post_outcome_uncertain_reconcile_before_retry"


def test_empty_local_receipt_blocks_before_new_post(monkeypatch, tmp_path):
    _configure_env(monkeypatch)
    receipt = tmp_path / "data" / "dispatches" / "gaza" / "editions" / EDITION_DATE / "bluesky_post_receipt.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text("{}", encoding="utf-8")
    called = {"session": 0}
    monkeypatch.setattr(bluesky_post, "_post_json", lambda *_args, **_kwargs: called.__setitem__("session", 1))

    result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(**_kwargs(tmp_path))

    assert result["status"] == "blocked"
    assert result["reason"] == "local_post_receipt_invalid"
    assert called["session"] == 0


def test_malformed_remote_record_blocks_reconciliation(monkeypatch, tmp_path):
    _configure_env(monkeypatch)
    _session(monkeypatch)

    monkeypatch.setattr(
        bluesky_post.request,
        "urlopen",
        lambda req, timeout=20.0: FakeResponse({"records": [{"uri": "at://bad" , "value": "not-a-record"}]}),
    )

    result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(**_kwargs(tmp_path))

    assert result["status"] == "blocked"
    assert result["reason"] == "remote_duplicate_record_invalid"


def test_remote_exact_text_with_embed_or_reply_is_conflict(monkeypatch, tmp_path):
    _configure_env(monkeypatch)
    _session(monkeypatch)

    monkeypatch.setattr(
        bluesky_post.request,
        "urlopen",
        lambda req, timeout=20.0: FakeResponse(
            {
                "records": [
                    {
                        "uri": f"at://{DID}/app.bsky.feed.post/embedded",
                        "cid": "bafy-embedded",
                        "value": {"text": TEXT, "embed": {"external": {"uri": PUBLIC_URL}}, "reply": {"root": {}}},
                    }
                ]
            }
        ),
    )

    result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(**_kwargs(tmp_path))

    assert result["status"] == "blocked"
    assert result["reason"] == "ambiguous_remote_duplicate"


def test_remote_match_does_not_mask_later_conflict(monkeypatch, tmp_path):
    _configure_env(monkeypatch)
    _session(monkeypatch)

    monkeypatch.setattr(
        bluesky_post.request,
        "urlopen",
        lambda req, timeout=20.0: FakeResponse(
            {
                "records": [
                    {"uri": f"at://{DID}/app.bsky.feed.post/match", "cid": "bafy-match", "value": {"text": TEXT}},
                    {"uri": f"at://{DID}/app.bsky.feed.post/conflict", "cid": "bafy-conflict", "value": {"text": "Different copy " + PUBLIC_URL}},
                ]
            }
        ),
    )

    result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(**_kwargs(tmp_path))

    assert result["status"] == "blocked"
    assert result["reason"] == "ambiguous_remote_duplicate"


def test_remote_match_with_unexamined_cursor_is_uncertain(monkeypatch, tmp_path):
    _configure_env(monkeypatch)
    _session(monkeypatch)

    monkeypatch.setattr(
        bluesky_post.request,
        "urlopen",
        lambda req, timeout=20.0: FakeResponse(
            {"records": [{"uri": f"at://{DID}/app.bsky.feed.post/match", "value": {"text": TEXT}}], "cursor": "next"}
        ),
    )

    result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(**_kwargs(tmp_path))

    assert result["status"] == "blocked"
    assert result["reason"] == "remote_duplicate_scan_incomplete"


def test_exact_draft_skips_matching_remote_post_without_create_record(monkeypatch, tmp_path):
    _configure_env(monkeypatch)
    _session(monkeypatch)
    called = []

    def fake_urlopen(req, timeout=20.0):
        called.append(req.full_url)
        assert "listRecords" in req.full_url
        return FakeResponse({"records": [{"uri": f"at://{DID}/app.bsky.feed.post/existing", "cid": "bafy-existing", "value": {"text": TEXT}}]})

    monkeypatch.setattr(bluesky_post.request, "urlopen", fake_urlopen)

    result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(**_kwargs(tmp_path))

    assert result["status"] == "skipped"
    assert result["reason"] == "skipped_matching_remote_record"
    assert result["post_uri"] == f"at://{DID}/app.bsky.feed.post/existing"
    assert len(called) == 1


def test_exact_draft_blocks_different_content_for_same_public_url(monkeypatch, tmp_path):
    _configure_env(monkeypatch)
    _session(monkeypatch)
    called = []

    def fake_urlopen(req, timeout=20.0):
        called.append(req.full_url)
        assert "listRecords" in req.full_url
        return FakeResponse(
            {
                "records": [
                    {
                        "uri": f"at://{DID}/app.bsky.feed.post/different",
                        "cid": "bafy-different",
                        "value": {"text": "Different approved copy\n" + PUBLIC_URL},
                    }
                ]
            }
        )

    monkeypatch.setattr(bluesky_post.request, "urlopen", fake_urlopen)

    result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(**_kwargs(tmp_path))

    assert result["status"] == "blocked"
    assert result["reason"] == "ambiguous_remote_duplicate"
    assert len(called) == 1


def test_exact_draft_blocks_mismatched_local_receipt_before_session(monkeypatch, tmp_path):
    _configure_env(monkeypatch)
    receipt = tmp_path / "data" / "dispatches" / "gaza" / "editions" / EDITION_DATE / "bluesky_post_receipt.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(
        json.dumps(
            {
                "status": "success",
                "public_url": PUBLIC_URL,
                "post_uri": f"at://{DID}/app.bsky.feed.post/existing",
                "post_text_sha256": "0" * 64,
                "target_did": DID,
            }
        ),
        encoding="utf-8",
    )
    called = {"session": 0}
    monkeypatch.setattr(
        bluesky_post,
        "_post_json",
        lambda *_args, **_kwargs: called.__setitem__("session", called["session"] + 1),
    )

    result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(**_kwargs(tmp_path))

    assert result["status"] == "blocked"
    assert result["reason"] == "local_receipt_content_mismatch"
    assert called["session"] == 0


def test_uncertain_post_writes_uncertain_receipt_and_blocks_retry(monkeypatch, tmp_path):
    _configure_env(monkeypatch)
    _session(monkeypatch)
    calls = {"records": 0, "create": 0}

    def first_urlopen(req, timeout=20.0):
        if "listRecords" in req.full_url:
            calls["records"] += 1
            return FakeResponse({"records": []})
        calls["create"] += 1
        raise TimeoutError("simulated post timeout after request start")

    monkeypatch.setattr(bluesky_post.request, "urlopen", first_urlopen)
    kwargs = _kwargs(tmp_path)
    first = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(**kwargs)

    assert first["status"] == "uncertain"
    assert first["reason"] == "post_outcome_uncertain_reconcile_before_retry"
    receipt = tmp_path / "data" / "dispatches" / "gaza" / "editions" / EDITION_DATE / "bluesky_post_receipt.json"
    assert json.loads(receipt.read_text(encoding="utf-8"))["status"] == "uncertain"

    monkeypatch.setattr(bluesky_post, "_post_json", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("retry must be blocked")))
    second = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(**kwargs)

    assert second["status"] == "blocked"
    assert second["reason"] == "post_outcome_uncertain_reconcile_before_retry"
    assert calls == {"records": 1, "create": 1}


def test_remote_matching_text_without_cid_is_invalid_and_blocks(monkeypatch, tmp_path):
    _configure_env(monkeypatch)
    _session(monkeypatch)
    monkeypatch.setattr(
        bluesky_post.request,
        "urlopen",
        lambda req, timeout=20.0: FakeResponse(
            {"records": [{"uri": f"at://{DID}/app.bsky.feed.post/missing-cid", "value": {"text": TEXT}}]}
        ),
    )

    result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(**_kwargs(tmp_path))

    assert result["status"] == "blocked"
    assert result["reason"] == "remote_duplicate_record_invalid"


def test_remote_matching_text_with_empty_rkey_is_invalid_and_blocks(monkeypatch, tmp_path):
    _configure_env(monkeypatch)
    _session(monkeypatch)
    monkeypatch.setattr(
        bluesky_post.request,
        "urlopen",
        lambda req, timeout=20.0: FakeResponse(
            {"records": [{"uri": f"at://{DID}/app.bsky.feed.post/", "cid": "bafy-empty-rkey", "value": {"text": TEXT}}]}
        ),
    )

    result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(**_kwargs(tmp_path))

    assert result["status"] == "blocked"
    assert result["reason"] == "remote_duplicate_record_invalid"


def _write_local_success_receipt(tmp_path: Path, **overrides) -> None:
    receipt = tmp_path / "data" / "dispatches" / "gaza" / "editions" / EDITION_DATE / "bluesky_post_receipt.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": "success",
        "public_url": PUBLIC_URL,
        "post_uri": f"at://{DID}/app.bsky.feed.post/existing",
        "post_cid": "bafy-existing",
        "post_text_sha256": TEXT_SHA256,
        "target_handle": HANDLE,
        "target_did": DID,
        "embed_type": "",
    }
    payload.update(overrides)
    receipt.write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.parametrize(
    ("overrides",),
    [
        ({"post_uri": f"at://{DID}/app.bsky.feed.like/wrong-collection"},),
        ({"post_cid": ""},),
        ({"embed_type": "app.bsky.embed.external"},),
    ],
    ids=["wrong-collection", "missing-cid", "external-embed"],
)
def test_invalid_local_success_evidence_blocks_without_network(monkeypatch, tmp_path, overrides):
    _configure_env(monkeypatch)
    _write_local_success_receipt(tmp_path, **overrides)
    called = {"session": 0}
    monkeypatch.setattr(
        bluesky_post,
        "_post_json",
        lambda *_args, **_kwargs: called.__setitem__("session", called["session"] + 1),
    )

    result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(**_kwargs(tmp_path))

    assert result["status"] == "blocked"
    assert result["reason"] == "local_receipt_invalid"
    assert called["session"] == 0


def test_interruption_after_request_start_leaves_pending_marker_and_blocks_overlap(monkeypatch, tmp_path):
    _configure_env(monkeypatch)
    _session(monkeypatch)
    calls = {"records": 0, "create": 0}

    def interrupted_urlopen(req, timeout=20.0):
        if "listRecords" in req.full_url:
            calls["records"] += 1
            return FakeResponse({"records": []})
        calls["create"] += 1
        raise KeyboardInterrupt()

    monkeypatch.setattr(bluesky_post.request, "urlopen", interrupted_urlopen)
    kwargs = _kwargs(tmp_path)

    with pytest.raises(KeyboardInterrupt):
        bluesky_post.maybe_post_gaza_dispatch_to_bluesky(**kwargs)

    receipt = tmp_path / "data" / "dispatches" / "gaza" / "editions" / EDITION_DATE / "bluesky_post_receipt.json"
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["status"] == "pending"
    assert payload["reason"] == "post_attempt_pending_reconcile_before_retry"

    second = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(**kwargs)

    assert second["status"] == "blocked"
    assert second["reason"] == "post_attempt_pending_reconcile_before_retry"
    assert calls == {"records": 1, "create": 1}

