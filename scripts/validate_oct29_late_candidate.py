"""One-shot private Oct29 candidate validation; no push, commit, publication or gate override."""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import traceback
import xml.etree.ElementTree as ET

SOURCE = "5a6c591831654c9e9e307711b74a268ce00d8d0d"
PAGES = "a952b7669ea16504b953fbd933cb226b4c643101"
DATE = "2026-10-29"
REL = "gaza/editions/" + DATE
DATED = tuple(REL + "/" + name for name in (
    "index.html", "edition_manifest.json", "sources_manifest.json", "curation_manifest.json"))
EXPECTED = set(DATED) | {"gaza/index.html", "gaza/archive.html", "gaza/rss.xml",
                         "index.html", "dispatches/index.html"}
RESERVE = 2 * 1024**3
RELATION_NOTE = ("Broad-topic heuristic only; not a finding that this is the same incident, "
                 "an increased casualty toll, or independent corroboration. "
                 "Current story ID, claim and source URL remain distinct.")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def blob(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), *args])


def dump(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def require(condition, message: str) -> None:
    if not condition:
        raise ValueError(message)


def tracked(root: Path):
    result = {}
    for row in git(root, "ls-tree", "-r", "-z", "HEAD").split(b"\0"):
        if not row:
            continue
        header, raw_path = row.split(b"\t", 1)
        mode, kind, oid = header.decode().split()
        path = raw_path.decode()
        require(mode == "100644" and kind == "blob", "unsupported Pages mode/type: " + path)
        require(not Path(path).is_absolute() and ".." not in Path(path).parts, "unsafe Pages path")
        local = root / path
        require(local.is_file() and not local.is_symlink(), "non-file Pages leaf: " + path)
        raw = local.read_bytes()
        require(blob(raw) == oid, "Pages working bytes differ: " + path)
        result[path] = {"sha256": sha(raw), "blob": oid, "size": len(raw)}
    return result


def leaves(root: Path):
    return {p.relative_to(root).as_posix(): sha(p.read_bytes())
            for p in root.rglob("*") if p.is_file()}


def copy_baseline(pages: Path, candidate: Path, inventory: dict) -> None:
    require(not candidate.exists(), "candidate destination already exists")
    require(shutil.disk_usage(candidate.parent).free >=
            sum(x["size"] for x in inventory.values()) + RESERVE,
            "candidate lacks projected bytes plus unchanged 2 GiB reserve")
    candidate.mkdir()
    for name in inventory:
        target = candidate / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((pages / name).read_bytes())


def tree_oid(inventory: dict, candidate: Path) -> str:
    tree = {}
    for path in inventory:
        node = tree
        parts = path.split("/")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = blob((candidate / path).read_bytes())
    def digest(node):
        body = b""
        for name, value in sorted(node.items(), key=lambda x:
                                 (x[0] + ("/" if isinstance(x[1], dict) else "")).encode()):
            is_dir = isinstance(value, dict)
            oid = digest(value) if is_dir else value
            body += (("40000" if is_dir else "100644") + " " + name).encode() + b"\0"
            body += bytes.fromhex(oid)
        return hashlib.sha1(b"tree " + str(len(body)).encode() + b"\0" + body).hexdigest()
    return digest(tree)


def exact_copy(edition: Path, approved: dict) -> dict:
    from bs4 import BeautifulSoup
    sources = read(edition / "sources_manifest.json")
    rows = read(edition / "curation_manifest.json")
    manifest = read(edition / "edition_manifest.json")
    require(isinstance(sources, list) and isinstance(rows, list), "unexpected manifest shape")
    rendered = [r for r in rows if r.get("public_rendered") is True
                and r.get("include_decision") == "include"]
    require(len(sources) == len(rendered) == 3, "all three approved reports must survive normal dedupe")
    require(manifest["source_count"] == manifest["story_count"] == 3, "manifest counts differ")
    require(manifest["publisher_count"] == 1 and manifest["publishers"] == ["WAFA"], "publisher count differs")
    expected = {r["url"]: r["summary"] for r in approved["approved_copy"]}
    require(set(r["url"] for r in sources) == set(expected), "source URL inventory differs")
    source_by_id = {r["source_record_id"]: r for r in sources}
    require(len(source_by_id) == 3, "duplicate source identities")
    for source in sources:
        require(source["summary_or_snippet"] == expected[source["url"]], "source copy drift")
        require(source["published_at"] == DATE, "source publication date drift")
        require(source["attribution_mode"] == "reported_public_source", "unsupported corroboration attribution")
        require(not source.get("story_selection_excluded_reason"), "source was excluded")
    require(len({r["story_id"] for r in rendered}) == 3, "stories merged or duplicated")
    used = []
    document = BeautifulSoup((edition / "index.html").read_text(encoding="utf-8"), "html.parser")
    articles = document.select("article")
    require(len(articles) == 3, "rendered article count differs")
    for row in rendered:
        ids = row.get("source_record_ids") or []
        require(len(ids) == 1 and ids[0] in source_by_id, "merged or foreign story provenance")
        source = source_by_id[ids[0]]
        used.append(ids[0])
        require(row["summary"] == expected[source["url"]], "story summary differs from approval")
        nested = row.get("source_records") or []
        require(len(nested) == 1 and nested[0]["url"] == source["url"] and
                nested[0]["summary_or_snippet"] == expected[source["url"]], "nested source copy drift")
        linked = [a for a in articles if a.find("a", href=source["url"])]
        require(len(linked) == 1 and expected[source["url"]] in
                [p.get_text(" ", strip=True) for p in linked[0].find_all("p")],
                "exact approved summary absent from linked article")
    require(len(set(used)) == 3, "source used by multiple stories")
    return {"sources": sources, "rows": rows, "manifest": manifest}


def main(args) -> None:
    root, evidence, candidate = args.runtime.resolve(), args.evidence.resolve(), args.candidate.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    pages = root / "bluefern-dispatches-pages"
    require(git(root, "rev-parse", "HEAD").decode().strip() == SOURCE, "production source pin drift")
    require(git(pages, "rev-parse", "HEAD").decode().strip() == PAGES, "Pages pin drift")
    require(not git(pages, "status", "--porcelain", "--untracked-files=all").strip(), "Pages baseline dirty")
    inventory = tracked(pages)
    before = {p: row["sha256"] for p, row in inventory.items()}
    dump(evidence / "baseline.json", {"source": SOURCE, "pages": PAGES, "leaves": inventory})
    require(shutil.disk_usage(root).free >= RESERVE, "normal 2 GiB admission reserve unavailable")
    packet_bytes = args.inputs.read_bytes()
    approval_bytes = args.approval.read_bytes()
    packet, approved = json.loads(packet_bytes), json.loads(approval_bytes)
    require(approved["source_sha"] == SOURCE and approved["pages_sha"] == PAGES, "approval pins differ")
    require(len(packet) == 3, "input inventory differs")
    require({r["url"]: r["summary_or_snippet"] for r in packet} ==
            {r["url"]: r["summary"] for r in approved["approved_copy"]}, "input/approval copy mismatch")
    require(all(r["retrieved_at"] == approved["source_observation_batch"] for r in packet), "retrieval provenance drift")
    dump(evidence / "input-binding.json", {"input_sha256": sha(packet_bytes),
         "approval_sha256": sha(approval_bytes), "provenance": approved["packet_provenance"]})
    manual = root / "data/dispatches/gaza/sources" / DATE / "manual_sources.json"
    require(not manual.exists(), "manual input path collision")
    manual.parent.mkdir(parents=True, exist_ok=True)
    manual.write_bytes(packet_bytes)
    os.environ["BLUEFERN_RUNTIME_ROOT"] = str(root)
    os.environ["BLUEFERN_BACKUP_ROOT"] = str(evidence / "generation-backups")
    sys.path[:0] = [str(root), str(root / "src")]
    from scripts.run_gaza_dispatch import run_gaza_dispatch
    from bluefern_dispatches import generator as gen, root_homepage as rh
    from bluefern_dispatches.gaza_correction_activation import load_activation
    activation = load_activation(root)
    dump(evidence / "activation.json", activation)
    require(activation and activation["current_pages_head"] == PAGES, "correction activation not proven")
    result = run_gaza_dispatch(root, DATE, from_manual_sources=True, dry_run=False,
                               render=False, all_steps=True, allow_thin_edition=False,
                               allow_post_edition_date_sources=False)
    dump(evidence / "generation-result.json", result)
    diagnostics = root / "data/dispatches/gaza/editions" / DATE
    if diagnostics.exists():
        shutil.copytree(diagnostics, evidence / "generation-diagnostics")
    dispatch_output = root / "output/dispatches/gaza/editions" / DATE
    if dispatch_output.exists():
        shutil.copytree(dispatch_output, evidence / "raw-dispatch-output")
    raw = root / "output/site" / REL
    if raw.exists():
        shutil.copytree(raw, evidence / "raw-edition")
    print(json.dumps({"stage": "generation", "result": result}, ensure_ascii=False), flush=True)
    require(result.get("ok") is True, "normal generation failed; no release or dedupe override")
    exact_copy(raw, approved)  # Fail before late-layer/browser if any approved report was suppressed.
    copy_baseline(pages, candidate, inventory)
    for relative in DATED:
        target = candidate / relative
        require(not target.exists(), "dated candidate path collision")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((root / "output/site" / relative).read_bytes())
    edition = candidate / REL
    manifest = read(edition / "edition_manifest.json")
    before_manifest = json.loads(json.dumps(manifest))
    for key in list(manifest):
        if key.startswith("local_"):
            del manifest[key]
    manifest["source_manifest_path"] = REL + "/sources_manifest.json"
    manifest["curation_manifest_path"] = REL + "/curation_manifest.json"
    manifest["free_public_artifacts"] = list(DATED)
    manifest["warnings"] = [str(w).replace(str(root) + "/", "") for w in manifest.get("warnings", [])]
    manifest.update(source_adequacy_label=approved["label"], edition_type="limited_source_late_edition",
        public_archive_title=approved["title"], public_archive_subtitle=approved["summary"],
        public_summary=approved["summary"], generation_route="approved_manual_late_edition",
        publication_scope="site_only", audio_render_authorized=False, social_publication_authorized=False,
        scheduled_pipeline_execution=False, reviewed_input_sha256=sha(packet_bytes),
        collection_scope="Three WAFA reports only; automatic provider coverage not completed by this manual review.",
        source_evidence_note="Approved paraphrases of previously inspected WAFA article bodies; not full-text snapshots. Reconstructed input metadata has separately recorded provenance. Publisher dates remain date-only; timezone unverified.")
    if "scheduled_run_local_time" in manifest:
        manifest["normal_morning_schedule_reference_local_time"] = manifest.pop("scheduled_run_local_time")
    for key in list(manifest):
        if key in ("contains_later_same_day_update", "contains_post_edition_date_update",
                   "post_edition_date_retrieval_batches") or key.startswith("later_same_day_update_") or key.startswith("post_edition_date_update_"):
            del manifest[key]
    for batch in manifest.get("retrieval_batches", []):
        batch["batch_classification"] = "manual_late_edition_review"
    dump(edition / "edition_manifest.json", manifest)
    rows = read(edition / "curation_manifest.json")
    for row in rows:
        if row.get("prior_story_matched"):
            row["dedupe_relation_scope"] = RELATION_NOTE
    dump(edition / "curation_manifest.json", rows)
    page = (edition / "index.html").read_text(encoding="utf-8")
    old = '<p class="eyebrow">Limited-source update /'
    require(page.count(old) == 1, "unexpected edition eyebrow")
    page = page.replace(old, '<p class="eyebrow">' + approved["label"] + " /", 1)
    heading = "<h1>Dispatches From Gaza</h1>"
    require(page.count(heading) == 1, "unexpected edition heading")
    page = page.replace(heading, heading + "\n<p><strong>" + html.escape(approved["disclosure"]) + "</strong></p>", 1)
    (edition / "index.html").write_text(page, encoding="utf-8")
    dispatch = gen.DispatchConfig(slug="gaza", name="Dispatches From Gaza", edition_date=DATE,
        tagline=approved["label"], logo="gaza-logo.png", sources=[], stories=[], detail_artifacts=[])
    require(gen._gaza_aggregate_render_from_pages_history(candidate, dispatch, DATE, pages,
            dry_run=False, wrote=[]), "guarded aggregate rendering did not run")
    release = rh.select_current_desk_by_slug(rh.discover_public_releases(candidate, verify_root=candidate)).get("gaza")
    require(release and release.edition_date == DATE, "new release not discoverable")
    release = dataclasses.replace(release, type_label=approved["label"], publication_time_text=None)
    (candidate / "index.html").write_text(rh.render_sitewide_homepage_from_template(
        (pages / "index.html").read_text(encoding="utf-8"), release), encoding="utf-8")
    (candidate / "dispatches/index.html").write_text(rh.render_dispatch_directory_from_template(
        (pages / "dispatches/index.html").read_text(encoding="utf-8"), release), encoding="utf-8")
    exact_copy(edition, approved)
    for shared_path in ("index.html", "dispatches/index.html"):
        original = (pages / shared_path).read_text(encoding="utf-8")
        updated = (candidate / shared_path).read_text(encoding="utf-8")
        other_cards = [article for article in re.findall(r"<article\b[^>]*>.*?</article>", original, re.S | re.I)
                       if "gaza" not in article.lower()]
        require(other_cards, "unrelated-card inventory is empty: " + shared_path)
        for article in other_cards:
            require(article in updated, "unrelated shared article card changed: " + shared_path)
    from urllib.parse import urlsplit, unquote
    import posixpath
    from bs4 import BeautifulSoup
    new_soup = BeautifulSoup((edition / "index.html").read_text(encoding="utf-8"), "html.parser")
    for element in new_soup.find_all(href=True) + new_soup.find_all(src=True):
        link = element.get("href") or element.get("src")
        parsed = urlsplit(link)
        if parsed.scheme or parsed.netloc or not parsed.path:
            continue
        target = posixpath.normpath(unquote(parsed.path).lstrip("/") if parsed.path.startswith("/")
                                   else posixpath.join(REL, unquote(parsed.path)))
        target_path = candidate / target
        if parsed.path.endswith("/"):
            target_path /= "index.html"
        require(target_path.is_file(), "new edition local navigation/asset target missing: " + link)
    require((edition / "sources_manifest.json").read_bytes() == (raw / "sources_manifest.json").read_bytes(),
            "late layer changed source manifest")
    after = leaves(candidate)
    changed = {p for p in set(before) | set(after) if before.get(p) != after.get(p)}
    require(changed == EXPECTED, "candidate delta is not exact nine paths: " + repr(sorted(changed)))
    require(not set(before) - set(after), "candidate deleted historical paths")
    require(before == {p: row["sha256"] for p, row in tracked(pages).items()}, "baseline mutated")
    require(not git(root, "diff", "--name-only", "--", "src", "scripts", "assets", ".github").strip(),
            "protected production code changed")
    require(not gen.public_site_contains_detail_artifacts(candidate), "private/detail content leaked")
    require(not gen.public_site_contains_blocked_public_text(candidate), "blocked public text")
    from scripts.validate_publish_scope import validate_publish_scope
    gaza = sorted(p for p in EXPECTED if p.startswith("gaza/"))
    shared = sorted(EXPECTED - set(gaza))
    require(not validate_publish_scope(dispatch="gaza", date_text=DATE, source_changed_paths=[],
            pages_changed_paths=gaza, strict=True), "dated Gaza scope failed")
    require(not validate_publish_scope(dispatch="sitewide", source_changed_paths=[],
            pages_changed_paths=shared, strict=True), "shared surfaces scope failed")
    timing = {"rss_pubDate": [], "rss_lastBuildDate": [], "html_publication_metadata": []}
    baseline_rss = ET.fromstring((pages / "gaza/rss.xml").read_bytes())
    rss = ET.fromstring((candidate / "gaza/rss.xml").read_bytes())
    def items(xml):
        return {x.findtext("guid"): ET.tostring(x, encoding="unicode").strip()
                for x in xml.findall("./channel/item")}
    old_items, new_items = items(baseline_rss), items(rss)
    require(set(new_items) - set(old_items) == {"https://dispatches.thebluefernco.com/" + REL + "/"},
            "RSS new identity differs")
    require(all(new_items.get(k) == v for k, v in old_items.items()), "RSS previous items changed")
    for surface in ("gaza/archive.html", "gaza/rss.xml"):
        old_dates = set(re.findall(r"editions/(\d{4}-\d{2}-\d{2})/", (pages / surface).read_text(encoding="utf-8")))
        new_dates = set(re.findall(r"editions/(\d{4}-\d{2}-\d{2})/", (candidate / surface).read_text(encoding="utf-8")))
        require(len(old_dates) == 147 and new_dates == old_dates | {DATE}, "prior 147 dates not preserved")
    current_item = next(x for x in rss.findall("./channel/item") if x.findtext("guid", "").endswith("/" + REL + "/"))
    timing["rss_pubDate"] = [x.text for x in current_item.findall("pubDate")]
    timing["rss_lastBuildDate"] = [x.text for x in rss.findall("./channel/lastBuildDate")]
    require(not timing["rss_pubDate"], "new RSS item must not claim an unestablished publication time")
    require(timing["rss_lastBuildDate"] == [x.text for x in baseline_rss.findall("./channel/lastBuildDate")],
            "new channel publication time was invented")
    from bs4 import BeautifulSoup
    for relative in ("index.html", "dispatches/index.html", "gaza/index.html", "gaza/archive.html", REL + "/index.html"):
        soup = BeautifulSoup((candidate / relative).read_text(encoding="utf-8"), "html.parser")
        require("late edition" in soup.get_text(" ", strip=True).lower(), "late label missing: " + relative)
        if relative == REL + "/index.html":
            for meta in soup.find_all("meta"):
                if re.search(r"published|modified|datePublished|dateModified", str(meta), re.I):
                    timing["html_publication_metadata"].append(str(meta))
            for script in soup.find_all("script", type="application/ld+json"):
                require(not re.search(r"datePublished|dateModified", script.get_text()), "unexpected publication JSON-LD")
            require(not timing["html_publication_metadata"], "dated page claims unestablished publication time")
    require(manifest["generated_at"][:10] != DATE, "fresh generation time was backdated")
    require(all(k not in manifest for k in ("public_published_at", "published_at", "publication_date")), "publication not yet established")
    dump(evidence / "timing-review.json", timing)
    dump(evidence / "late-layer-delta.json", {"manifest_before": before_manifest, "manifest_after": manifest,
         "html_change": "One eyebrow replacement and one disclosure insertion; approved story copy unchanged.",
         "curation_change": "Only prior-related rows receive explicit non-identity/non-corroboration caveat."})
    payload = evidence / "candidate-overrides"
    for relative in sorted(EXPECTED):
        target = payload / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((candidate / relative).read_bytes())
    proof = {"source": SOURCE, "pages": PAGES, "input_sha256": sha(packet_bytes),
        "candidate_tree": tree_oid(after, candidate), "changed_paths": sorted(changed),
        "postimages": {p: {"sha256": after[p], "blob": blob((candidate / p).read_bytes())} for p in sorted(changed)},
        "preimages": {p: inventory.get(p) for p in sorted(changed)}, "baseline_unchanged": True,
        "candidate_leaves": len(after), "unchanged_leaves": len(before) - len(set(before) & changed),
        "all_prior_dates": 147, "publication_performed": False}
    dump(evidence / "composition.json", proof)
    print(json.dumps({"stage": "static-pass", "proof": proof}, ensure_ascii=False), flush=True)
    from bluefern_dispatches.public_site_visuals import validate_public_site_visuals
    for name, paths, count in (("seven-routes", None, 7), ("dated-oct29", ["/" + REL + "/"], 1)):
        result = validate_public_site_visuals(candidate, screenshot_dir=evidence / name, paths=paths)
        dump(evidence / (name + ".json"), result)
        require(len(result["paths_checked"]) == count and len(result["pages"]) == count, "visual route coverage differs")
        require(len(set(result["paths_checked"])) == count and
                set(result["paths_checked"]) == {p["path"] for p in result["pages"]}, "visual route identities differ")
        require(len(result["screenshots"]) == count and len(result["screenshot_hashes"]) == count,
                "visual screenshot/hash coverage incomplete")
        for screenshot, expected_sha in result["screenshot_hashes"].items():
            require(sha(Path(screenshot).read_bytes()) == expected_sha, "screenshot hash mismatch")
        require(all(p["status"] == 200 and p["screenshot"] for p in result["pages"]), "visual route HTTP/evidence failed")
        require(result["ok"] is True, "unchanged browser validation failed: " + name)
    require(after == leaves(candidate), "browser changed candidate bytes")
    require(before == {p: row["sha256"] for p, row in tracked(pages).items()}, "browser changed baseline")
    dump(evidence / "RESULT.json", {"ok": True, "scope": "Private candidate validation only", **proof})
    print("OCT29_CANDIDATE_VALIDATION_PASSED", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ("runtime", "candidate", "evidence", "inputs", "approval"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        main(args)
    except Exception as exc:
        args.evidence.mkdir(parents=True, exist_ok=True)
        dump(args.evidence / "FAILURE.json", {"ok": False, "error": str(exc), "traceback": traceback.format_exc(),
             "publication_performed": False, "retry_performed": False})
        traceback.print_exc()
        raise SystemExit(1)
