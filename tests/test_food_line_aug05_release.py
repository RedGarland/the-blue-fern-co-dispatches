from __future__ import annotations

import json
import subprocess
from pathlib import Path

from bluefern_dispatches.food_line_approved_proposal import load_approved_proposal, sha256_file


ROOT = Path(__file__).resolve().parents[1]
DATE = "2026-08-05"
APPROVED_HEADLINE = "North Carolina food pantries report rising demand amid SNAP cuts"
SOURCE_URL = "https://www.northcarolinahealthnews.org/2026/08/04/snap-food-insecurity-pantries-guilford"
GOOGLE_NEWS_WRAPPER = "https://news.google.com/rss/articles/"
GENERATION_COMMIT = "e6fe82e436f10429ebcb14158af7255f267e6c7a"
ARTIFACT_COMMIT = "8bd6c726273982fdf1ba9e348b8db2b4fd0c7407"


def _json(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_aug05_snapshot_proposal_and_public_artifacts_are_consistent() -> None:
    proposal_path = ROOT / "data/dispatches/food-line/review/proposed-editions/2026-08-05.json"
    snapshot_path = ROOT / "data/dispatches/food-line/review/signal-reviews/2026-08-05.json"
    release_path = ROOT / "data/dispatches/food-line/review/releases/2026-08-05.json"
    edition_dir = ROOT / "output/site/food-line/editions/2026-08-05"

    proposal = _json("data/dispatches/food-line/review/proposed-editions/2026-08-05.json")
    snapshot = _json("data/dispatches/food-line/review/signal-reviews/2026-08-05.json")
    release = _json("data/dispatches/food-line/review/releases/2026-08-05.json")
    manifest = _json("output/site/food-line/editions/2026-08-05/edition_manifest.json")
    sources = json.loads((edition_dir / "sources_manifest.json").read_text(encoding="utf-8"))

    assert proposal["edition_date"] == DATE
    assert proposal["draft_status"] == "draft_approved_pending_publication"
    assert proposal["publication_approval"] is False
    assert proposal["publication_datetime"] is None
    assert proposal["selected_item_count"] == 1
    assert proposal["approved_item_count"] == 1
    assert proposal["public_source_count"] == 1
    assert proposal["edition_headline"] == APPROVED_HEADLINE
    assert proposal["review_snapshot_path"] == "data/dispatches/food-line/review/signal-reviews/2026-08-05.json"
    assert proposal["review_snapshot_sha256"] == sha256_file(snapshot_path)

    item = snapshot["items"][0]
    assert item["review_item_id"] == "food-line-current-375d9edb4f29b8379bdabbfc"
    assert item["candidate_id"] == "food-line-discovery-009586c8408f66fc"
    assert item["editorial_status"] == "approve_with_edit"
    assert item["proposed_public_headline"] == APPROVED_HEADLINE
    assert item["source_url"] == SOURCE_URL
    assert item["canonical_source_url"] == SOURCE_URL
    assert GOOGLE_NEWS_WRAPPER not in item["exact_supporting_passage"]

    bundle = load_approved_proposal(ROOT, proposal_path, DATE)
    assert bundle.legacy_current_review_fallback_used is False
    assert bundle.queue_path == snapshot_path
    assert bundle.proposal_sha256 == sha256_file(proposal_path)
    assert bundle.queue_sha256 == sha256_file(snapshot_path)

    assert manifest["generation_mode"] == "approved_current_review_proposal"
    assert manifest["publication_status"] == "unpublished"
    assert manifest["pages_status"] == "not_synced"
    assert manifest["public_release_status"] == "not_published"
    assert manifest["pages_release_status"] == "not_synced"
    assert manifest["source_count"] == 1
    assert manifest["claim_count"] == 1
    assert manifest["approved_proposal_sha256"] == sha256_file(proposal_path)
    assert manifest["review_snapshot_sha256"] == sha256_file(snapshot_path)

    assert release["source_commit"] == ARTIFACT_COMMIT
    assert manifest["generator_source_commit"] == GENERATION_COMMIT
    assert release["source_commit"] != manifest["generator_source_commit"]
    subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={ROOT.as_posix()}",
            "-C",
            str(ROOT),
            "merge-base",
            "--is-ancestor",
            GENERATION_COMMIT,
            ARTIFACT_COMMIT,
        ],
        check=True,
    )
    assert release["approved_proposal_sha256"] == sha256_file(proposal_path)
    assert release["review_snapshot_sha256"] == sha256_file(snapshot_path)
    assert any(entry["pages_path"] == "food-line/rss.xml" for entry in release["entries"])

    assert len(sources) == 1
    assert sources[0]["url"] == SOURCE_URL
    assert GOOGLE_NEWS_WRAPPER not in sources[0]["exact_supporting_passage"]

    html_files = [
        edition_dir / "index.html",
        edition_dir / "source_table.html",
        edition_dir / "claim_ledger.html",
        ROOT / "output/site/food-line/index.html",
        ROOT / "output/site/food-line/archive.html",
        ROOT / "output/site/food-line/rss.xml",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in html_files)
    assert APPROVED_HEADLINE in combined
    assert SOURCE_URL in combined
    assert GOOGLE_NEWS_WRAPPER not in combined
