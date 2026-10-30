"""Two private approved Gaza candidates; never pushes or publishes.

The only commit created here is an explicitly unpublished local staging snapshot,
with authentic public Pages ancestry, to exercise unchanged October30 history guards.
"""
from __future__ import annotations
import argparse
import base64
import json
from pathlib import Path
import re
import subprocess
import sys
import traceback
import xml.etree.ElementTree as ET

import validate_oct29_late_candidate as v


def run_stage(harness: Path, runtime: Path, candidate: Path, evidence: Path,
              fixtures: Path, date: str, private_proof: Path | None = None) -> dict:
    folder = fixtures / ("oct29_late_validation" if date == "2026-10-29" else "oct30_validation")
    command = [sys.executable, str(harness), "--runtime", str(runtime), "--candidate", str(candidate),
               "--evidence", str(evidence), "--inputs", str(folder / "manual_sources.json"),
               "--approval", str(folder / "approval.json"), "--date", date]
    if private_proof is not None:
        command += ["--private-baseline-proof", str(private_proof)]
    subprocess.run(command, check=True)
    result = v.read(evidence / "RESULT.json")
    v.require(result.get("ok") is True, "stage did not produce successful validation receipt")
    return result


def main(args) -> None:
    runtime29, runtime30 = args.runtime29.resolve(), args.runtime30.resolve()
    evidence, candidates, fixtures = args.evidence.resolve(), args.candidates.resolve(), args.fixtures.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    candidates.mkdir(parents=True, exist_ok=False)
    v.require(runtime29 != runtime30 and not runtime29.is_relative_to(runtime30) and
              not runtime30.is_relative_to(runtime29), "source runtimes must be isolated")
    pages29, pages30 = runtime29 / "bluefern-dispatches-pages", runtime30 / "bluefern-dispatches-pages"
    for runtime, pages in ((runtime29, pages29), (runtime30, pages30)):
        v.require(v.git(runtime, "rev-parse", "HEAD").decode().strip() == v.SOURCE, "source pin drift")
        v.require(v.git(pages, "rev-parse", "HEAD").decode().strip() == v.PUBLIC_PAGES, "public Pages pin drift")
        v.require(not v.git(pages, "status", "--porcelain", "--untracked-files=all").strip(),
                  "fresh Pages checkout is dirty")
    original = v.tracked(pages29)
    v.require(v.tracked(pages30) == original, "second public baseline bytes differ")
    v.dump(evidence / "actual-public-baseline.json", {"source": v.SOURCE, "published_pages_base": v.PUBLIC_PAGES,
                                                    "leaves": original})
    harness = Path(__file__).with_name("validate_oct29_late_candidate.py")
    candidate29, candidate30 = candidates / "oct29", candidates / "oct30"
    result29 = run_stage(harness, runtime29, candidate29, evidence / "oct29", fixtures, "2026-10-29")
    paths29 = set(result29["changed_paths"])
    v.require(len(paths29) == 9 and result29["pages"] == v.PUBLIC_PAGES, "October29 binding differs")

    # Prepare only the second isolated Pages checkout. The real baseline remains untouched.
    for path in sorted(paths29):
        target = pages30 / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((candidate29 / path).read_bytes())
    v.git(pages30, "add", "--", *sorted(paths29))
    tree = v.git(pages30, "write-tree").decode().strip()
    v.require(tree == result29["candidate_tree"], "private staging index is not exact validated October29 tree")
    commit = v.git(pages30, "-c", "user.name=Private CI validation",
                   "-c", "user.email=private-validation@invalid",
                   "commit-tree", tree, "-p", v.PUBLIC_PAGES,
                   "-m", "PRIVATE UNPUBLISHED October29 staging baseline; not a release").decode().strip()
    v.git(pages30, "checkout", "--detach", commit)
    v.require(v.git(pages30, "show", "-s", "--format=%P", commit).decode().strip() == v.PUBLIC_PAGES,
              "private staging ancestry differs")
    v.require(not v.git(pages30, "status", "--porcelain", "--untracked-files=all").strip(),
              "private staging checkout is dirty")
    private_proof = {"scope": "Private unpublished October29 staging baseline", "source": v.SOURCE,
                     "published_pages_base": v.PUBLIC_PAGES, "private_staging_commit": commit,
                     "private_staging_tree": tree, "oct29_result_path": str(evidence / "oct29/RESULT.json"),
                     "publication_performed": False, "remote_mutation_performed": False}
    private_path = evidence / "private-staging-baseline.json"
    v.dump(private_path, private_proof)
    print(json.dumps({"stage": "private_unpublished_baseline", "proof": private_proof}), flush=True)
    result30 = run_stage(harness, runtime30, candidate30, evidence / "oct30", fixtures, "2026-10-30", private_path)

    before = {p: r["sha256"] for p, r in original.items()}
    after = v.leaves(candidate30)
    dated29 = {"gaza/editions/2026-10-29/" + name for name in
               ("index.html", "edition_manifest.json", "sources_manifest.json", "curation_manifest.json")}
    dated30 = {p.replace("2026-10-29", "2026-10-30") for p in dated29}
    shared = {"index.html", "dispatches/index.html", "gaza/index.html", "gaza/archive.html", "gaza/rss.xml"}
    expected = dated29 | dated30 | shared
    changed = {p for p in set(before) | set(after) if before.get(p) != after.get(p)}
    v.require(changed == expected and len(changed) == 13, "combined candidate is not exact thirteen-path delta")
    v.require(not set(before) - set(after), "combined candidate deleted historical leaves")
    for path in dated29:
        v.require((candidate29 / path).read_bytes() == (candidate30 / path).read_bytes(),
                  "October30 changed approved October29 dated bytes: " + path)
    for path in ("gaza/index.html", "gaza/archive.html"):
        text29 = (candidate29 / path).read_text(encoding="utf-8")
        text30 = (candidate30 / path).read_text(encoding="utf-8")
        rows = [row for row in re.findall(r"<li\b[^>]*>.*?</li>", text29, re.S | re.I)
                if 'href="editions/2026-10-29/"' in row]
        v.require(len(rows) == 1 and rows[0] in text30, "October29 aggregate row changed or missing: " + path)
    def rss_item(root):
        xml = ET.fromstring((root / "gaza/rss.xml").read_bytes())
        found = [item for item in xml.findall("./channel/item")
                 if item.findtext("guid", "").endswith("/gaza/editions/2026-10-29/")]
        v.require(len(found) == 1, "October29 RSS identity missing or duplicated")
        return ET.tostring(found[0], encoding="unicode").strip()
    v.require(rss_item(candidate29) == rss_item(candidate30), "October29 RSS item changed")
    v.require(v.tracked(pages29) == original, "actual public baseline mutated")
    v.require(v.git(pages29, "rev-parse", "HEAD").decode().strip() == v.PUBLIC_PAGES, "public baseline HEAD changed")
    v.require(v.git(pages30, "rev-parse", "HEAD").decode().strip() == commit, "private baseline HEAD changed")
    for runtime in (runtime29, runtime30):
        v.require(not v.git(runtime, "diff", "--name-only", "--", "src", "scripts", "assets", ".github").strip(),
                  "protected source code changed")

    # Both dated editions must be checked on the FINAL combined candidate.
    # October30 plus all seven shared routes were just checked in the second fresh process.
    sys.path[:0] = [str(runtime30), str(runtime30 / "src")]
    from bluefern_dispatches.public_site_visuals import validate_public_site_visuals
    visual = validate_public_site_visuals(candidate30, screenshot_dir=evidence / "final-oct29-screenshot",
                                         paths=["/gaza/editions/2026-10-29/"])
    v.dump(evidence / "final-oct29-browser.json", visual)
    route = "/gaza/editions/2026-10-29/"
    v.require(visual["ok"] is True and visual["paths_checked"] == [route] and len(visual["pages"]) == 1,
              "final combined October29 browser validation failed")
    page = visual["pages"][0]
    v.require(page["path"] == route and page["status"] == 200 and page["screenshot"],
              "final October29 route evidence failed")
    v.require(len(visual["screenshots"]) == len(visual["screenshot_hashes"]) == 1,
              "final October29 screenshot/hash coverage differs")
    for screenshot, digest in visual["screenshot_hashes"].items():
        v.require(v.sha(Path(screenshot).read_bytes()) == digest, "final October29 screenshot hash mismatch")
    v.require(after == v.leaves(candidate30), "final browser changed candidate bytes")
    proof = {"ok": True, "scope": "Private combined October29/October30 validation; not publication",
             "source": v.SOURCE, "published_pages_base": v.PUBLIC_PAGES,
             "private_staging_base": commit, "private_staging_tree": tree,
             "candidate_tree": v.tree_oid(after, candidate30),
             "changed_paths": sorted(changed), "candidate_leaves": len(after),
             "unchanged_leaves": len(before) - len(set(before) & changed),
             "prior_public_dates_preserved": 147, "new_dates": ["2026-10-29", "2026-10-30"],
             "oct29_dated_bytes_preserved": True, "oct29_aggregate_row_rss_preserved": True,
             "postimages": {p: {"sha256": after[p], "blob": v.blob((candidate30 / p).read_bytes())}
                           for p in sorted(changed)},
             "preimages": {p: original.get(p) for p in sorted(changed)},
             "input_sha256": {"2026-10-29": result29["input_sha256"], "2026-10-30": result30["input_sha256"]},
             "publication_performed": False, "remote_mutation_performed": False}
    payload = evidence / "combined-candidate-overrides"
    for path in sorted(expected):
        target = payload / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((candidate30 / path).read_bytes())
    v.dump(evidence / "COMBINED_RESULT.json", proof)
    print(json.dumps({"stage": "combined_pass", "proof": proof}), flush=True)
    print(json.dumps({"stage": "validated_combined_public_payload",
                     "files_base64": {p: base64.b64encode((candidate30 / p).read_bytes()).decode("ascii")
                                      for p in sorted(expected)}}), flush=True)
    print("BOTH_GAZA_CANDIDATES_VALIDATED_NOT_PUBLISHED", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ("runtime29", "runtime30", "evidence", "candidates", "fixtures"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        main(args)
    except Exception as exc:
        args.evidence.mkdir(parents=True, exist_ok=True)
        v.dump(args.evidence / "COMBINED_FAILURE.json",
               {"ok": False, "error": str(exc), "traceback": traceback.format_exc(),
                "publication_performed": False, "remote_mutation_performed": False})
        traceback.print_exc()
        raise SystemExit(1)
