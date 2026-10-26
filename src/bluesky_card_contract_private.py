"""Private fail-closed contract for Gaza Bluesky external-card posts.

This module is deliberately separate from the protected production source.  It
models the contract that the exact-draft posting path must enforce before a
createRecord request and after the resulting record is read back.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import copy
import hashlib
import re
from typing import Any, Callable


class CardContractError(ValueError):
    """The approved text/card binding is not safe to publish or accept."""


def validate_card_mode(mode: Any, contract: Any) -> None:
    """Require an explicit shape; never silently downgrade a card to plain text."""

    if mode not in {"external", "plain"}:
        raise CardContractError("exact-draft card mode must be explicit")
    if mode == "external" and not isinstance(contract, ExternalCardContract):
        raise CardContractError("external-card contract is required")
    if mode == "plain" and contract is not None:
        raise CardContractError("plain mode cannot carry an external-card contract")


_DATE_IN_URL = re.compile(r"/editions/(\d{4}-\d{2}-\d{2})/")
_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
_POST_URI = re.compile(r"^at://([^/]+)/app\.bsky\.feed\.post/([^/]+)$")


@dataclass(frozen=True)
class ExternalCardContract:
    edition_date: str
    edition_url: str
    title: str
    description: str
    thumb_cid: str
    thumb_sha256: str

    def validate(self, post_text: str) -> None:
        try:
            parsed_date = date.fromisoformat(self.edition_date)
        except ValueError as exc:
            raise CardContractError("invalid edition date") from exc
        if self.edition_url != self.edition_url.strip() or not self.edition_url.startswith("https://"):
            raise CardContractError("edition URL must be an absolute HTTPS URL")
        match = _DATE_IN_URL.search(self.edition_url.rstrip("/") + "/")
        if not match or match.group(1) != self.edition_date:
            raise CardContractError("edition URL/date binding mismatch")
        if "/gaza/editions/" not in self.edition_url:
            raise CardContractError("card contract is not bound to the Gaza edition")
        if not self.title.strip() or not self.description.strip():
            raise CardContractError("card title and description are required")
        if len(self.title) > 300 or len(self.description) > 1000:
            raise CardContractError("card metadata exceeds the bounded size")
        # Avoid platform-specific %-d/%#d directives in the shared harness.
        formatted_date = f"{parsed_date:%B} {parsed_date.day}, {parsed_date.year}"
        if formatted_date not in self.title or formatted_date not in self.description:
            raise CardContractError("card metadata/date binding mismatch")
        if not self.thumb_cid.strip():
            raise CardContractError("uploaded thumbnail CID is required")
        if not _SHA256.fullmatch(self.thumb_sha256.strip()):
            raise CardContractError("thumbnail source hash is required")
        if post_text.count(self.edition_url) != 1:
            raise CardContractError("approved text must contain the edition URL exactly once")


def _link_facet(text: str, url: str) -> dict[str, Any]:
    encoded = text.encode("utf-8")
    needle = url.encode("utf-8")
    start = encoded.find(needle)
    if start < 0 or encoded.find(needle, start + 1) >= 0:
        raise CardContractError("edition URL must have one unambiguous link range")
    return {
        "index": {"byteStart": start, "byteEnd": start + len(needle)},
        "features": [{"$type": "app.bsky.richtext.facet#link", "uri": url}],
    }


def _thumb_cid(thumb: Any) -> str:
    if not isinstance(thumb, dict):
        return ""
    ref = thumb.get("ref")
    if not isinstance(ref, dict):
        return ""
    return str(ref.get("$link") or ref.get("link") or "").strip()


def build_external_record(
    *,
    post_text: str,
    contract: ExternalCardContract,
    thumb_blob: dict[str, Any],
    created_at: str,
) -> dict[str, Any]:
    if not isinstance(contract, ExternalCardContract):
        raise CardContractError("external-card contract is required")
    contract.validate(post_text)
    if _thumb_cid(thumb_blob) != contract.thumb_cid:
        raise CardContractError("thumbnail blob does not match approved thumbnail CID")
    record = {
        "$type": "app.bsky.feed.post",
        "text": post_text,
        "createdAt": created_at,
        "facets": [_link_facet(post_text, contract.edition_url)],
        "embed": {
            "$type": "app.bsky.embed.external",
            "external": {
                "uri": contract.edition_url,
                "title": contract.title,
                "description": contract.description,
                "thumb": copy.deepcopy(thumb_blob),
            },
        },
    }
    return record


def validate_external_record(
    *,
    record: Any,
    post_text: str,
    contract: ExternalCardContract,
) -> None:
    if not isinstance(contract, ExternalCardContract):
        raise CardContractError("external-card contract is required")
    contract.validate(post_text)
    if not isinstance(record, dict) or record.get("$type") != "app.bsky.feed.post":
        raise CardContractError("readback is not a Bluesky post record")
    if record.get("text") != post_text:
        raise CardContractError("readback text does not match approved text")
    if "reply" in record:
        raise CardContractError("exact card workflow requires a standalone post")
    embed = record.get("embed")
    if not isinstance(embed, dict) or embed.get("$type") != "app.bsky.embed.external":
        raise CardContractError("external card embed is missing or wrong type")
    external = embed.get("external")
    if not isinstance(external, dict):
        raise CardContractError("external card body is missing")
    if external.get("uri") != contract.edition_url:
        raise CardContractError("external card URI/date binding mismatch")
    if external.get("title") != contract.title:
        raise CardContractError("external card title mismatch")
    if external.get("description") != contract.description:
        raise CardContractError("external card description mismatch")
    if _thumb_cid(external.get("thumb")) != contract.thumb_cid:
        raise CardContractError("external card thumbnail identity mismatch")
    facets = record.get("facets")
    expected = _link_facet(post_text, contract.edition_url)
    if not isinstance(facets, list) or facets != [expected]:
        raise CardContractError("link facets are missing or do not bind the edition URL")


def validate_create_response(response: Any, *, expected_did: str) -> tuple[str, str]:
    if not isinstance(response, dict):
        raise CardContractError("createRecord response is invalid")
    uri = str(response.get("uri") or "").strip()
    cid = str(response.get("cid") or "").strip()
    match = _POST_URI.fullmatch(uri)
    if not match or match.group(1) != expected_did or not cid:
        raise CardContractError("createRecord identity is invalid")
    return uri, cid


def validate_readback(
    *,
    response: Any,
    readback: Any,
    expected_did: str,
    post_text: str,
    contract: ExternalCardContract,
) -> tuple[str, str]:
    uri, cid = validate_create_response(response, expected_did=expected_did)
    if not isinstance(readback, dict) or readback.get("uri") != uri or readback.get("cid") != cid:
        raise CardContractError("readback identity does not match createRecord response")
    validate_external_record(record=readback.get("value"), post_text=post_text, contract=contract)
    return uri, cid


def reconcile_existing_records(
    records: list[Any], *, post_text: str, contract: ExternalCardContract
) -> dict[str, Any]:
    """Classify every checked record; never update or delete a mismatch."""

    contract.validate(post_text)
    exact: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for row in records:
        if not isinstance(row, dict):
            return {"status": "uncertain", "reason": "malformed_remote_record"}
        uri = str(row.get("uri") or "").strip()
        cid = str(row.get("cid") or "").strip()
        value = row.get("value")
        same_text = isinstance(value, dict) and value.get("text") == post_text
        same_url = isinstance(value, dict) and contract.edition_url in str(value)
        if not same_text and not same_url:
            continue
        try:
            validate_create_response({"uri": uri, "cid": cid}, expected_did=uri.split("/", 3)[2] if uri.startswith("at://") else "")
            validate_external_record(record=value, post_text=post_text, contract=contract)
        except CardContractError:
            conflicts.append({"uri": uri, "cid": cid})
        else:
            exact.append({"uri": uri, "cid": cid})
    if conflicts or len(exact) > 1:
        return {"status": "conflict", "reason": "ambiguous_remote_duplicate", "matches": exact, "conflicts": conflicts}
    if exact:
        return {"status": "match", "post_uri": exact[0]["uri"], "post_cid": exact[0]["cid"]}
    return {"status": "none"}


def create_and_verify(
    *,
    create_record: Callable[[dict[str, Any]], Any],
    read_record: Callable[[str], Any],
    did: str,
    post_text: str,
    contract: ExternalCardContract,
    thumb_blob: dict[str, Any],
    created_at: str,
) -> dict[str, Any]:
    """Private harness: an exception after create is always uncertain."""

    try:
        payload = {"repo": did, "collection": "app.bsky.feed.post", "record": build_external_record(
            post_text=post_text, contract=contract, thumb_blob=thumb_blob, created_at=created_at
        )}
    except CardContractError as exc:
        return {"status": "blocked", "reason": "card_contract_invalid", "error": str(exc)}
    try:
        response = create_record(payload)
    except Exception as exc:  # noqa: BLE001 - request outcome is not known.
        return {"status": "uncertain", "reason": "post_request_outcome_uncertain", "error": type(exc).__name__}
    try:
        uri, cid = validate_create_response(response, expected_did=did)
        readback = read_record(uri)
        validate_readback(
            response=response,
            readback=readback,
            expected_did=did,
            post_text=post_text,
            contract=contract,
        )
    except Exception as exc:  # noqa: BLE001 - callers must persist uncertain state.
        return {"status": "uncertain", "reason": "post_readback_failed", "error": type(exc).__name__}
    return {"status": "success", "post_uri": uri, "post_cid": cid, "embed_verified": True}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
