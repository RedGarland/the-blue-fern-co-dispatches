from __future__ import annotations

import html
import json
import subprocess
from pathlib import Path
from xml.etree import ElementTree

import pytest

from bluefern_dispatches import gaza_edition_correction as edition
from bluefern_dispatches import gaza_historical_correction as legacy
from bluefern_dispatches.gaza_sources import story_claim_fingerprint
from bluefern_dispatches.historical_agent_archive import build_gaza_published_story_lineage

DATE = "2026-10-14"


def git(root, *args):
    return subprocess.check_output(["git", "-c", "core.longpaths=true", "-C", str(root), *args], text=True).strip()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, ensure_ascii=False, indent=2) + "\n" if not isinstance(value, str) else value
    path.write_bytes(raw.encode("utf-8"))


def init(path, branch):
    path.mkdir()
    git(path, "init", "-b", branch)
    git(path, "config", "user.name", "Synthetic fixture")
    git(path, "config", "user.email", "fixture@example.invalid")
    git(path, "config", "core.autocrlf", "false")
    git(path, "config", "core.longpaths", "true")


def commit(path):
    git(path, "add", "--all")
    git(path, "commit", "-m", "Synthetic test evidence")
    return git(path, "rev-parse", "HEAD")


@pytest.fixture
def case(tmp_path):
    source, pages, evidence = tmp_path / "source", tmp_path / "pages", tmp_path / "review"
    init(source, "add/pages-repo-default")
    write(source / "README.md", "Synthetic source fixture\n")
    base = commit(source)
    init(pages, "gh-pages")
    git(pages, "remote", "add", "origin", "https://github.com/RedGarland/the-blue-fern-co-dispatches.git")
    stories, sources = [], []
    for i in range(1, 4):
        sid = f"gaza-story-{DATE}-{i:03d}"
        title = f"Synthetic {'outside Gaza event' if i < 3 else 'Gaza discovery headline'} {i}"
        summary = f"Synthetic saved {'foreign event' if i < 3 else 'headline without article detail'} {i}."
        published = f"2026-10-{'12' if i == 3 else '14'}T12:00:00+00:00"
        source_row = {"source_record_id": f"synthetic-{i}", "title": title,
            "publisher": f"Publisher {i}", "url": f"https://publisher-{i}.example/article",
            "canonical_url": f"https://publisher-{i}.example/article", "published_at": published,
            "dispatch_slug": "gaza", "category_hint": "conflict", "used_in_story_ids": [sid]}
        source_row["claim_fingerprint"] = story_claim_fingerprint(source_row)
        sources.append(source_row)
        stories.append({"story_id": sid, "title": title, "summary": summary,
            "category": "conflict", "event_date": published, "location": "Gaza",
            "attribution": source_row["publisher"], "publisher_names": [source_row["publisher"]],
            "source_record_ids": [source_row["source_record_id"]], "source_urls": [source_row["url"]],
            "public_rendered": True, "included_in_public_summary": True, "casualty_counts": {}})
    directory = pages / f"gaza/editions/{DATE}"
    write(directory / "curation_manifest.json", stories)
    write(directory / "sources_manifest.json", sources)
    rows = [{"story_id": s["story_id"], "title": s["title"], "classification": "new",
        "include_decision": "include", "public_rendered": True} for s in stories]
    write(directory / "dedupe_report.json", {"edition_date": DATE, "candidates_seen": 3,
        "included_stories": rows, "major_updates": rows[:2], "reasons": rows,
        "nested": {"another_collection": rows}, "unrelated": "KEEP EXACT"})
    write(directory / "edition_manifest.json", {"edition_date": DATE, "story_count": 3,
        "source_count": 3, "publisher_count": 3, "publishers": ["Publisher 1", "Publisher 2", "Publisher 3"],
        "public_exposed": True, "source_adequacy_status": "limited_source_update",
        "source_adequacy_label": "Limited-source update", "warnings": ["Old limited-source warning"],
        "source_adequacy_warnings": ["Old limited-source warning"], "unrelated": "KEEP EXACT"})
    write(directory / "source_quality_report.md", "# Immutable original source diagnostic\n")
    summaries = "".join(f'<p>{html.escape(s["summary"])}</p>' for s in stories)
    glance = "".join(f'<li>{html.escape(s["title"])}</li>' for s in stories)
    articles = "".join(f'<article><h3>{html.escape(s["title"])}</h3><p>{html.escape(s["summary"])}</p>'
        f'<ul><li><a href="{s["source_urls"][0]}">Source</a></li></ul></article>' for s in stories)
    write(directory / "index.html", '<!doctype html><html><body><main><p class="eyebrow">Limited-source update</p>'
        '<h1>Gaza</h1><p><strong>This is a limited-source update generated from three records.</strong></p>'
        f'<h2>Today’s Read</h2><p>Three saved developments.</p>{summaries}'
        f'<h2>At A Glance</h2><ul>{glance}</ul><h2>Core Gaza Developments</h2>{articles}'
        '<h2>Source Mix</h2><p>Source mix: three stories.</p><p>Publishers: three.</p>'
        '<h2>Source Note</h2><p>This edition is generated only from saved source records.</p>'
        '</main><footer>UNRELATED FOOTER &amp; lexical bytes</footer></body></html>\n')
    url = f"https://dispatches.thebluefernco.com/gaza/editions/{DATE}/"
    listing = ('<!doctype html><html><body><main><article><h3><a href="' + url + '">' + stories[0]["title"]
        + '</a></h3><p>' + stories[0]["summary"] + '</p><p class="edition-meta">3 public sources</p></article>'
        '<article><h3>UNRELATED DESK</h3><p>UNRELATED SUMMARY</p></article></main><footer>KEEP</footer></body></html>\n')
    for rel in ("gaza/index.html", "gaza/archive.html", "index.html", "dispatches/index.html"):
        write(pages / rel, listing)
    mp3 = pages / f"gaza/audio/{DATE}.mp3"
    mp3.parent.mkdir(parents=True, exist_ok=True)
    mp3.write_bytes(b"ID3-synthetic-original-recording")
    transcript_url = f"https://dispatches.thebluefernco.com/gaza/audio/{DATE}-transcript.html"
    script = "\n\n".join(s["summary"] for s in stories)
    write(pages / f"gaza/audio/{DATE}.json", {"dispatch_slug": "gaza", "edition_date": DATE,
        "audio_file": DATE + ".mp3", "audio_url": f"/gaza/audio/{DATE}.mp3", "audio_mime_type": "audio/mpeg",
        "audio_file_size_bytes": mp3.stat().st_size, "transcript_url": transcript_url,
        "edition_url": url, "script_text": script, "audio_status": "audio_file_ready"})
    write(pages / f"gaza/audio/{DATE}-transcript.html", '<html><body><main>'
        f'<audio src="/gaza/audio/{DATE}.mp3"></audio>{summaries}</main></body></html>\n')
    write(pages / "gaza/audio/index.html", '<html><body><main><ul><li>'
        f'<a href="/gaza/audio/{DATE}.mp3">MP3</a></li></ul></main></body></html>\n')
    write(pages / "gaza/rss.xml", f'<rss><channel><title>Gaza</title><item><title>{stories[0]["title"]}</title>'
        f'<link>{url}</link><guid>{url}</guid><description>{stories[0]["summary"]}</description></item></channel></rss>\n')
    podcast = f'<rss><channel><item><title>Original Gaza recording</title><link>{transcript_url}</link>'
    podcast += f'<guid>{transcript_url}</guid><description>{stories[0]["summary"]}</description>'
    podcast += f'<enclosure url="https://dispatches.thebluefernco.com/gaza/audio/{DATE}.mp3" length="{mp3.stat().st_size}" type="audio/mpeg" /></item></channel></rss>\n'
    for rel in ("gaza/podcast.xml", "gaza/audio/podcast.xml"):
        write(pages / rel, podcast)
    write(pages / "gaza/flash-briefing.json", [{"uid": f"gaza-{DATE}", "mainText": script,
        "redirectionUrl": f"/gaza/audio/{DATE}.mp3", "updateDate": f"{DATE}T15:00:00Z", "titleText": "Original"}])
    head = commit(pages)
    lineages = []
    for s in stories:
        lineage = build_gaza_published_story_lineage(pages, pages_commit=head, story_id=s["story_id"],
            edition_date=DATE, expected_title=s["title"], expected_prior_claim=s["summary"],
            backfill_reason="Synthetic test identity, no authority", created_at=DATE + "T20:00:00Z")
        path = source / f'data/agent-history/gaza/lineage/published-stories/{s["story_id"]}.json'
        write(path, lineage)
        lineages.append(path)
    source_head = commit(source)
    evidence.mkdir()
    packet_path = evidence / "review-packet.json"
    bindings = {}
    for path in pages.rglob("*"):
        if path.is_file() and ".git" not in path.relative_to(pages).parts:
            rel = path.relative_to(pages).as_posix()
            snapshot = evidence / rel
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            snapshot.write_bytes(path.read_bytes())
            bindings[rel] = {"sha256": legacy.sha256_file(path), "snapshot_path": str(snapshot)}
    write(evidence / "saved-inputs.json", sources)
    bindings["saved_inputs"] = {"sha256": legacy.sha256_file(evidence / "saved-inputs.json"), "snapshot_path": str(evidence / "saved-inputs.json")}
    write(packet_path, {"source_head": base, "pages_head": head, "evidence": bindings})
    request = {"schema_version": edition.REQUEST_SCHEMA, "edition_date": DATE, "correction_date": DATE,
        "correction_at": DATE + "T22:00:00Z", "expected_source_head": source_head,
        "expected_pages_head": head, "review_packet_sha256": legacy.sha256_file(packet_path),
        "tts_provider": "synthetic", "tts_model": "synthetic", "tts_voice": "synthetic",
        "targets": [{"story_id": s["story_id"], "reason": "scope_error" if i < 2 else "insufficient_saved_evidence"} for i, s in enumerate(stories)]}
    request_path = tmp_path / "request.json"
    write(request_path, request)
    return {"source_root": source, "pages_root": pages, "review_packet_path": packet_path,
        "request_path": request_path, "output_root": tmp_path / "prepared", "request": request,
        "stories": stories, "lineages": lineages}


def prepare(case, **kwargs):
    return edition.prepare_edition_correction(**{k: case[k] for k in
        ("source_root", "pages_root", "review_packet_path", "request_path", "output_root")}, **kwargs)


def verify(case, path):
    return edition.verify_edition_correction_packet(packet_path=path,
        source_root=case["source_root"], pages_root=case["pages_root"])


def rebind_synthetic_evidence(case):
    """Commit changed synthetic originals and rebuild, never repair real evidence."""
    head = commit(case["pages_root"])
    for story, path in zip(case["stories"], case["lineages"]):
        lineage = build_gaza_published_story_lineage(case["pages_root"], pages_commit=head,
            story_id=story["story_id"], edition_date=DATE, expected_title=story["title"],
            expected_prior_claim=story["summary"], backfill_reason="Synthetic test rebind only",
            created_at=DATE + "T20:00:00Z")
        write(path, lineage)
    case["request"]["expected_source_head"] = commit(case["source_root"])
    case["request"]["expected_pages_head"] = head
    packet = json.loads(case["review_packet_path"].read_bytes())
    packet["pages_head"] = head
    for rel, row in packet["evidence"].items():
        if rel != "saved_inputs":
            raw = (case["pages_root"] / rel).read_bytes()
            Path(row["snapshot_path"]).write_bytes(raw)
            row["sha256"] = edition._hash(raw)
    write(case["review_packet_path"], packet)
    case["request"]["review_packet_sha256"] = legacy.sha256_file(case["review_packet_path"])
    write(case["request_path"], case["request"])


def test_complete_zero_story_packet_is_non_authorizing_and_replay_safe(case):
    result = prepare(case)
    path = Path(result["packet_path"])
    packet = json.loads(path.read_bytes())
    assert result["proposal_created"] is False
    assert len(result["missing_inputs"]) == 3
    assert result["preview_surface_count"] == 20
    assert packet["prospective_staged_surface_count"] == 21
    assert all(packet[k] is False for k in edition.AUTHORITY_FIELDS)
    assert verify(case, path)["status"] == "verified"
    assert prepare(case)["status"] == "idempotent_noop"
    root = path.parent / "preview/gaza/editions" / DATE
    assert json.loads((root / "curation_manifest.json").read_bytes()) == []
    sources = json.loads((root / "sources_manifest.json").read_bytes())
    assert len(sources) == 3 and all(s["used_in_story_ids"] == [] for s in sources)
    dedupe = json.loads((root / "dedupe_report.json").read_bytes())
    for collection in ("included_stories", "major_updates", "reasons"):
        assert dedupe[collection] == []
    assert dedupe["nested"]["another_collection"] == []
    manifest = json.loads((root / "edition_manifest.json").read_bytes())
    assert manifest["story_count"] == manifest["source_count"] == manifest["publisher_count"] == 0
    assert manifest["saved_source_count"] == 3
    assert manifest["source_adequacy_status"] == "no_qualified_stories"
    assert manifest["public_exposed"] is False
    audio = json.loads((path.parent / "audio_request.json").read_bytes())
    assert "accuracy and relevance remain undetermined" in audio["script_text"]
    assert all(c["substantive_decision"] is None for c in packet["correction"]["corrections"])
    assert not (path.parent / "proposal.json").exists()
    assert not list(path.parent.glob("preview/**/*.mp3"))


def test_partial_removal_keeps_qualified_rows_and_bytes(case):
    case["request"]["targets"] = case["request"]["targets"][:2]
    write(case["request_path"], case["request"])
    result = prepare(case)
    root = Path(result["packet_path"]).parent
    corrected = json.loads((root / f"preview/gaza/editions/{DATE}/curation_manifest.json").read_bytes())
    assert corrected == [case["stories"][2]]
    page = (root / f"preview/gaza/editions/{DATE}/index.html").read_text(encoding="utf-8")
    assert case["stories"][2]["summary"] in page
    assert "1 retained public story remains" in page
    assert '<footer>UNRELATED FOOTER &amp; lexical bytes</footer>' in page
    for rel in ("index.html", "dispatches/index.html", "gaza/index.html", "gaza/archive.html"):
        text = (root / "preview" / rel).read_text(encoding="utf-8")
        assert '<article><h3>UNRELATED DESK</h3><p>UNRELATED SUMMARY</p></article>' in text
        assert "3 public sources" not in text


@pytest.mark.parametrize("role", list(edition.PREVIEW_PATHS))
def test_each_missing_preview_surface_is_rejected(case, role):
    result = prepare(case)
    path = Path(result["packet_path"])
    packet = json.loads(path.read_bytes())
    row = next(r for r in packet["representations"] if r["role"] == role)
    (path.parent / row["input_path"]).unlink()
    with pytest.raises(edition.Error, match="missing or unexpected"):
        verify(case, path)


@pytest.mark.parametrize("relative", ["source_quality_report.md", "dispatches/index.html"])
def test_missing_mandatory_original_dependency_is_rejected(case, relative):
    path = case["pages_root"] / (f"gaza/editions/{DATE}/{relative}" if relative.endswith(".md") else relative)
    path.unlink()
    with pytest.raises(edition.Error, match="missing affected surface"):
        prepare(case)


def test_preparation_never_substitutes_for_substantive_reviews(case):
    with pytest.raises(edition.Error, match="formal proposal blocked"):
        prepare(case, formal=True)
    assert not case["output_root"].exists()


def test_mixed_reasons_and_prior_day_dates_stay_separate(case):
    result = prepare(case)
    packet = json.loads(Path(result["packet_path"]).read_bytes())
    reasons = [r["reason"] for r in packet["correction"]["corrections"]]
    assert reasons == ["scope_error", "scope_error", "insufficient_saved_evidence"]
    original = json.loads(case["lineages"][2].read_bytes())
    assert original["evidence"]["story"]["event_date"].startswith("2026-10-12")
    assert packet["correction"]["corrections"][2]["underlying_article_verdict"] == "undetermined"


def test_feed_identity_and_historical_audio_are_preserved(case):
    result = prepare(case)
    path = Path(result["packet_path"])
    packet = json.loads(path.read_bytes())
    for role in ("rss", "podcast", "audio_podcast"):
        rel = edition.PREVIEW_PATHS[role]
        original = ElementTree.fromstring((case["pages_root"] / rel).read_bytes()).find('.//item')
        items = ElementTree.fromstring((path.parent / "preview" / rel).read_bytes()).findall('.//item')
        assert items[0].findtext('guid') == packet["correction_id"]
        assert items[1].findtext('guid') == original.findtext('guid')
        if role != "rss":
            assert items[1].find('enclosure').attrib == original.find('enclosure').attrib
            assert items[0].find('enclosure').get('length') == "0"
    metadata = json.loads((path.parent / f"preview/gaza/audio/{DATE}.json").read_bytes())
    original_metadata = json.loads((case["pages_root"] / f"gaza/audio/{DATE}.json").read_bytes())
    assert metadata["script_text"] == original_metadata["script_text"]
    assert metadata["historical_recording"] is True and metadata["current_briefing"] is False
    transcript = (path.parent / f"preview/gaza/audio/{DATE}-transcript.html").read_text(encoding="utf-8")
    assert "Historical recording" in transcript and case["stories"][0]["summary"] in transcript


def test_snapshot_or_bound_packet_hash_drift_fails(case):
    snapshot = case["review_packet_path"].parent / f"gaza/editions/{DATE}/index.html"
    snapshot.write_bytes(snapshot.read_bytes() + b"tampered")
    with pytest.raises(edition.Error, match="evidence hash drifted"):
        prepare(case)


def test_head_drift_and_packet_fingerprint_drift_fail(case):
    result = prepare(case)
    path = Path(result["packet_path"])
    packet = json.loads(path.read_bytes())
    packet["pages_head"] = "0" * 40
    write(path, packet)
    with pytest.raises(edition.Error, match="fingerprint drifted"):
        verify(case, path)
    write(case["source_root"] / "different.txt", "source moved\n")
    commit(case["source_root"])
    with pytest.raises(edition.Error, match="head drifted"):
        prepare(case)


def test_contradictory_original_manifests_fail(case):
    path = case["pages_root"] / f"gaza/editions/{DATE}/edition_manifest.json"
    data = json.loads(path.read_bytes())
    data["story_count"] = 0
    write(path, data)
    rebind_synthetic_evidence(case)
    with pytest.raises(edition.Error, match="original edition/curation manifest contradiction"):
        prepare(case)


def test_uninventoried_claim_surface_fails_closed(case):
    write(case["pages_root"] / "extra.html", case["stories"][0]["title"])
    # A dirty Pages worktree is itself a fail-closed inventory boundary.
    with pytest.raises(edition.Error, match="clean"):
        prepare(case)


def test_clean_but_uninventoried_surface_is_rejected(case, monkeypatch):
    original = legacy._git
    def with_extra(repo, *args):
        result = original(repo, *args)
        return result + "\nHEAD:other-current-listing.html" if args[0] == "grep" else result
    monkeypatch.setattr(legacy, "_git", with_extra)
    with pytest.raises(edition.Error, match="uninventoried claim-bearing"):
        prepare(case)
    assert not case["output_root"].exists()


def install_synthetic_reviews(case):
    for index, target in enumerate(case["request"]["targets"]):
        rel = f'data/agent-history/gaza/reviews/synthetic-{index}.json'
        audit_rel = f'data/agent-history/gaza/reviews/decisions/synthetic-{index}.json'
        review = {"schema_version": edition.REVIEW_SCHEMA, "story_id": target["story_id"],
            "owning_edition_date": DATE, "reason": target["reason"],
            "decision": edition.DECISIONS[target["reason"]], "resulting_review_state": "substantively_reviewed",
            "lineage_sha256": legacy.sha256_file(case["lineages"][index]),
            "review_packet_sha256": case["request"]["review_packet_sha256"],
            "reviewer": "Synthetic fixture only", "reviewed_at": DATE + "T22:00:00Z",
            "decision_reason": "Synthetic independent review test; no real editorial decision.",
            "evidence_references": [{"url": case["stories"][index]["source_urls"][0], "supporting_passage": "Synthetic saved evidence.",
                                     "role": "principal", "evidence_type": "source_scope" if index < 2 else "saved_input"}],
            "assessment": {"materially_concerns_gaza": False} if index < 2 else {
                "materially_concerns_gaza": None, "saved_article_evidence_sufficient": False,
                "underlying_article_verdict": "undetermined",
                "saved_input_sha256": legacy.sha256_file(case["review_packet_path"].parent / "saved-inputs.json")},
            **{k: False for k in edition.AUTHORITY_FIELDS}}
        write(case["source_root"] / rel, review)
        write(case["source_root"] / audit_rel, review)
        target["review_path"], target["decision_audit_path"] = rel, audit_rel
    case["request"]["expected_source_head"] = commit(case["source_root"])
    write(case["request_path"], case["request"])


def test_formal_proposal_requires_committed_exact_reviews_and_audits(case):
    install_synthetic_reviews(case)
    result = prepare(case, formal=True)
    path = Path(result["packet_path"])
    assert path.name == "proposal.json" and result["proposal_created"] is True
    assert result["missing_inputs"] == []
    assert verify(case, path)["status"] == "verified"
    proposal = json.loads(path.read_bytes())
    assert proposal["schema_version"] == edition.PROPOSAL_SCHEMA
    assert all(proposal[k] is False for k in edition.AUTHORITY_FIELDS)
    # Legacy approval/staging fail closed on the distinct schema.
    with pytest.raises(legacy.CorrectionValidationError):
        legacy._validate_proposal_shape(proposal)


@pytest.mark.parametrize("mutation", ["non_gaza", "article_false", "decision", "authority", "audit", "uncommitted"])
def test_insufficient_evidence_cannot_be_mislabeled_or_approved(case, mutation):
    install_synthetic_reviews(case)
    target = case["request"]["targets"][2]
    path = case["source_root"] / target["review_path"]
    review = json.loads(path.read_bytes())
    if mutation == "non_gaza":
        review["assessment"]["materially_concerns_gaza"] = False
    elif mutation == "article_false":
        review["assessment"]["underlying_article_verdict"] = "false"
    elif mutation == "decision":
        review["decision"] = "confirmed_public_scope_contamination"
    elif mutation == "authority":
        review["publication_authorized"] = True
    else:
        review["decision_reason"] += " changed"
    write(path, review)
    if mutation != "audit":
        write(case["source_root"] / target["decision_audit_path"], review)
    if mutation != "uncommitted":
        case["request"]["expected_source_head"] = commit(case["source_root"])
        write(case["request_path"], case["request"])
    with pytest.raises(edition.Error):
        prepare(case, formal=True)
    assert not case["output_root"].exists()


def test_output_inside_either_repository_is_rejected(case):
    for root in (case["source_root"], case["pages_root"]):
        case["output_root"] = root / "out"
        with pytest.raises(edition.Error, match="outside"):
            prepare(case)


@pytest.mark.parametrize("removed", [(1,), (2,), (3,), (1, 3), (2, 3)])
def test_noncontiguous_array_removals_preserve_retained_identity(case, removed):
    case["request"]["targets"] = [t for i, t in enumerate(case["request"]["targets"], 1) if i in removed]
    write(case["request_path"], case["request"])
    result = prepare(case)
    data = json.loads((Path(result["packet_path"]).parent / f"preview/gaza/editions/{DATE}/curation_manifest.json").read_bytes())
    assert data == [s for i, s in enumerate(case["stories"], 1) if i not in removed]


@pytest.mark.parametrize("newline,final_newline,bom", [("\r\n", True, True), ("\n", False, False)])
def test_lexical_newlines_bom_and_unrelated_numeric_spelling(case, newline, final_newline, bom):
    numeric = '"unrelated_metric" : 1.2300e+02'
    for path in case["pages_root"].rglob("*"):
        if not path.is_file() or ".git" in path.relative_to(case["pages_root"]).parts or path.suffix not in {".html", ".xml", ".json"}:
            continue
        text = path.read_text(encoding="utf-8")
        if path.name in {"edition_manifest.json", "dedupe_report.json"}:
            text = text.replace('"unrelated": "KEEP EXACT"', '"unrelated": "KEEP EXACT",\n  ' + numeric)
        formatted = text.rstrip("\n").replace("\n", newline) + (newline if final_newline else "")
        path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + formatted.encode("utf-8"))
    rebind_synthetic_evidence(case)
    result = prepare(case)
    root = Path(result["packet_path"]).parent
    packet = json.loads(Path(result["packet_path"]).read_bytes())
    for row in packet["representations"]:
        if row["prior_sha256"] is None:
            continue
        original = (case["pages_root"] / row["public_path"]).read_bytes()
        corrected = (root / row["input_path"]).read_bytes()
        assert corrected.startswith(b"\xef\xbb\xbf") is bom
        assert corrected.endswith(newline.encode()) is final_newline
        if newline == "\r\n":
            assert b"\n" not in corrected.replace(b"\r\n", b"")
        if row["role"] in {"edition_manifest", "dedupe_report"}:
            assert numeric.encode() in original and numeric.encode() in corrected
    assert verify(case, Path(result["packet_path"]))["status"] == "verified"


def test_keyed_and_string_dedupe_collections_are_cleaned_and_history_preserved(case):
    path = case["pages_root"] / f"gaza/editions/{DATE}/dedupe_report.json"
    payload = json.loads(path.read_bytes())
    payload["keyed"] = {s["story_id"]: {"title": s["title"]} for s in case["stories"]}
    payload["string_ids"] = [s["story_id"] for s in case["stories"]]
    payload["corrections"] = [{"historical_notice": "KEEP PRIOR HISTORY EXACT"}]
    write(path, payload)
    rebind_synthetic_evidence(case)
    result = prepare(case)
    corrected = json.loads((Path(result["packet_path"]).parent / f"preview/gaza/editions/{DATE}/dedupe_report.json").read_bytes())
    assert corrected["keyed"] == {} and corrected["string_ids"] == []
    assert corrected["corrections"][0] == payload["corrections"][0]


def test_later_flash_is_unchanged_and_has_immutable_ownership_dependencies(case):
    later = "2026-10-15"
    root = case["pages_root"]
    # A future edition relative to the corrected historical one, not new news.
    raw = (root / f"gaza/audio/{DATE}.mp3").read_bytes()
    (root / f"gaza/audio/{later}.mp3").write_bytes(raw)
    metadata = json.loads((root / f"gaza/audio/{DATE}.json").read_bytes())
    for key in ("edition_date", "audio_file", "audio_url", "transcript_url", "edition_url"):
        metadata[key] = metadata[key].replace(DATE, later)
    metadata["script_text"] = "Unrelated later briefing."
    write(root / f"gaza/audio/{later}.json", metadata)
    write(root / f"gaza/audio/{later}-transcript.html", f'<html><body><main><audio src="/gaza/audio/{later}.mp3"></audio></main></body></html>\n')
    for rel in ("gaza/podcast.xml", "gaza/audio/podcast.xml"):
        text = (root / rel).read_text(encoding="utf-8")
        item = ElementTree.fromstring(text).find('.//item')
        addition = ElementTree.tostring(item, encoding="unicode").replace(DATE, later)
        write(root / rel, text.replace('</channel>', addition + '</channel>'))
    write(root / "gaza/flash-briefing.json", [{"uid": f"gaza-{later}", "mainText": "Unrelated later briefing.", "redirectionUrl": f"/gaza/audio/{later}.mp3"}])
    rebind_synthetic_evidence(case)
    result = prepare(case)
    root_out = Path(result["packet_path"]).parent
    assert (root_out / "preview/gaza/flash-briefing.json").read_bytes() == (root / "gaza/flash-briefing.json").read_bytes()
    packet = json.loads(Path(result["packet_path"]).read_bytes())
    assert f"gaza/audio/{later}.mp3" in {r["public_path"] for r in packet["original_artifacts"]}


def test_mixed_newlines_and_unapproved_render_are_rejected(case):
    path = case["pages_root"] / f"gaza/editions/{DATE}/index.html"
    path.write_bytes(path.read_bytes().replace(b"</main>", b"\r\n</main>"))
    rebind_synthetic_evidence(case)
    with pytest.raises(edition.Error, match="mixed newlines"):
        prepare(case)


def test_updated_hashes_do_not_hide_semantically_contradictory_preview(case):
    result = prepare(case)
    path = Path(result["packet_path"])
    packet = json.loads(path.read_bytes())
    row = next(r for r in packet["representations"] if r["role"] == "edition_manifest")
    artifact = path.parent / row["input_path"]
    data = json.loads(artifact.read_bytes())
    data["story_count"] = 3
    write(artifact, data)
    packet["artifact_hashes"][row["input_path"]] = legacy.sha256_file(artifact)
    row["sha256"] = legacy.sha256_file(artifact)
    packet["packet_sha256"] = legacy.fingerprint_payload(packet, "packet_sha256")
    write(path, packet)
    with pytest.raises(edition.Error, match="validator-owned deterministic output"):
        verify(case, path)


def test_rehashed_tampered_original_snapshot_is_rejected(case):
    result = prepare(case)
    path = Path(result["packet_path"])
    packet = json.loads(path.read_bytes())
    rel = f"evidence/public/gaza/editions/{DATE}/source_quality_report.md"
    (path.parent / rel).write_bytes(b"tampered historical recommendation")
    packet["artifact_hashes"][rel] = legacy.sha256_file(path.parent / rel)
    packet["packet_sha256"] = legacy.fingerprint_payload(packet, "packet_sha256")
    write(path, packet)
    with pytest.raises(edition.Error, match="original public evidence drifted"):
        verify(case, path)


def test_render_assets_are_bound_but_feed_discovery_links_are_not_assets(case):
    path = case["pages_root"] / f"gaza/editions/{DATE}/index.html"
    text = path.read_text(encoding="utf-8")
    text = text.replace('<body>', '<head><link rel="stylesheet" href="../../assets/site.css">'
        '<link rel="alternate" href="/gaza/rss.xml"></head><body>')
    write(path, text)
    write(case["pages_root"] / "gaza/assets/site.css", "body { color: blue; }\n")
    rebind_synthetic_evidence(case)
    result = prepare(case)
    packet_path = Path(result["packet_path"])
    packet = json.loads(packet_path.read_bytes())
    assert packet["render_asset_dependencies"] == ["gaza/assets/site.css"]
    assert (packet_path.parent / "preview/gaza/assets/site.css").read_bytes() == (case["pages_root"] / "gaza/assets/site.css").read_bytes()
    assert verify(case, packet_path)["status"] == "verified"


def test_pending_audio_request_cannot_be_rehashed_into_render_authority(case):
    result = prepare(case)
    path = Path(result["packet_path"])
    packet = json.loads(path.read_bytes())
    audio_path = path.parent / "audio_request.json"
    audio = json.loads(audio_path.read_bytes())
    audio["render_authorized"] = True
    write(audio_path, audio)
    packet["artifact_hashes"]["audio_request.json"] = legacy.sha256_file(audio_path)
    packet["audio_request_sha256"] = legacy.sha256_file(audio_path)
    packet["packet_sha256"] = legacy.fingerprint_payload(packet, "packet_sha256")
    write(path, packet)
    with pytest.raises(edition.Error, match="audio request differs"):
        verify(case, path)


def test_zero_story_casualty_aggregates_are_recomputed_from_retained_claims(case):
    path = case["pages_root"] / f"gaza/editions/{DATE}/edition_manifest.json"
    data = json.loads(path.read_bytes())
    data["casualty_totals"] = {"new_deaths": 3, "new_injuries": 2}
    write(path, data)
    rebind_synthetic_evidence(case)
    result = prepare(case)
    data = json.loads((Path(result["packet_path"]).parent / f"preview/gaza/editions/{DATE}/edition_manifest.json").read_bytes())
    assert data["casualty_totals"] == {"new_deaths": 0, "new_injuries": 0}
