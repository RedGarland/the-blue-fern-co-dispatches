"""Read-only release-layout regression check; never a publisher or approval gate bypass.

Stages a pinned public Pages snapshot with ONLY the source Gaza archive override.
This intentionally does not replace public CSS with the entire source stylesheet.
The baseline must be a separately reviewed immutable commit containing a dated
Gaza edition. A pass proves this staged layout, not scheduled production delivery.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

MARKER = b"\n/* Keep the Gaza archive's records in view; do not reuse landing-page hero spacing. */\n"
CSS_PATH = "gaza/assets/site.css"
RESERVE = 2 * 1024**3


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def archive_override(source: bytes) -> bytes:
    if source.count(MARKER) != 1:
        raise ValueError("source must contain exactly one reviewed archive override marker")
    override = source[source.index(MARKER):]
    # Fail closed if this formerly narrow suffix expands to another page or a
    # resource import. Full CSS parsing is unnecessary for this flat rule block.
    body = override[len(MARKER):].decode("utf-8")
    rules = re.findall(r"([^{}]+)\{([^{}]*)\}", body)
    remainder = re.sub(r"[^{}]+\{[^{}]*\}", "", body)
    if not rules or remainder.strip():
        raise ValueError("archive override must contain only flat CSS rules")
    for selector, declarations in rules:
        selectors = [item.strip() for item in selector.split(",")]
        if any(not re.fullmatch(r"\.archive--gaza(?:\s+(?:\.[A-Za-z_][\w-]*|h[1-6]|p))*", s) for s in selectors):
            raise ValueError("override selector escaped the Gaza archive scope")
        if re.search(r"@|url\s*\(|expression\s*\(|!important", declarations, re.I):
            raise ValueError("override contains an unsupported import or declaration")
    return override


def candidate_css(baseline: bytes, source: bytes) -> bytes:
    override = archive_override(source)
    count = baseline.count(MARKER)
    if count > 1:
        raise ValueError("baseline has duplicate archive override markers")
    prefix = baseline[:baseline.index(MARKER)] if count else baseline
    return prefix + override


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args], text=True, encoding="utf-8"
    ).strip()


def latest_gaza_path(pages: Path) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup((pages / "gaza/index.html").read_text(encoding="utf-8"), "html.parser")
    dates = []
    for anchor in soup.find_all("a", href=True):
        match = re.fullmatch(r"(?:/gaza/)?editions/(\d{4}-\d{2}-\d{2})/(?:index\.html)?", anchor["href"])
        if match:
            dates.append(match.group(1))
    if not dates:
        raise ValueError("public Gaza homepage has no dated edition link")
    route = f"/gaza/editions/{max(dates)}/"
    if not (pages / route.lstrip("/") / "index.html").is_file():
        raise ValueError("linked Gaza dated edition is missing")
    return route


def require_route_evidence(result: dict, *, count: int) -> None:
    paths = result.get("paths_checked", [])
    pages = result.get("pages", [])
    if len(paths) != count or len(set(paths)) != count or len(pages) != count:
        raise ValueError("visual validator returned incomplete route coverage")
    if set(paths) != {page.get("path") for page in pages}:
        raise ValueError("visual route evidence does not match checked routes")
    if any(page.get("status") != 200 or not page.get("screenshot") for page in pages):
        raise ValueError("route lacks HTTP 200 or screenshot evidence")
    if len(result.get("screenshot_hashes", {})) != count:
        raise ValueError("screenshot hashes do not cover every route")


def stage(baseline: Path, source: Path, output: Path, expected_sha: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
        raise ValueError("baseline must be an immutable 40-character commit SHA")
    if git(baseline, "rev-parse", "HEAD") != expected_sha:
        raise ValueError("Pages checkout does not match the pinned baseline")
    if git(baseline, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Pages baseline is not clean")
    if output.exists():
        raise ValueError("candidate destination already exists; refusing reuse")
    entries = subprocess.check_output(["git", "-C", str(baseline), "ls-files", "-z"]).split(b"\0")
    files = []
    for raw in filter(None, entries):
        name = raw.decode("utf-8")
        path = Path(name)
        if path.is_absolute() or ".." in path.parts or ".git" in path.parts:
            raise ValueError("unsafe tracked Pages path")
        current = baseline
        for part in path.parts:
            current /= part
            if current.is_symlink():
                raise ValueError("Pages snapshot contains a symlink")
        if not current.is_file():
            raise ValueError("Pages snapshot contains a non-file leaf")
        files.append((name, current))
    projected = sum(p.stat().st_size for _, p in files)
    if shutil.disk_usage(output.parent).free < projected + RESERVE:
        raise ValueError("candidate stage lacks projected bytes plus 2 GiB reserve")
    output.mkdir()
    before = {}
    for name, path in files:
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        contents = path.read_bytes()
        before[name] = sha256(contents)
        with target.open("xb") as handle:
            handle.write(contents)
    css = output / CSS_PATH
    if CSS_PATH not in before:
        raise ValueError("Gaza stylesheet missing from pinned baseline")
    source_bytes = (source / "assets/site.css").read_bytes()
    css.write_bytes(candidate_css(css.read_bytes(), source_bytes))
    after = {name: sha256((output / name).read_bytes()) for name, _ in files}
    changed = sorted(name for name in before if before[name] != after[name])
    if set(changed) - {CSS_PATH}:
        raise ValueError("unexpected candidate changed path")
    if any(sha256(path.read_bytes()) != before[name] for name, path in files):
        raise ValueError("baseline changed during staging")
    return {
        "baseline_sha": expected_sha,
        "source_sha": git(source, "rev-parse", "HEAD"),
        "candidate_css_sha256": after[CSS_PATH],
        "source_css_sha256": sha256(source_bytes),
        "override_sha256": sha256(archive_override(source_bytes)),
        "changed_paths": changed,
        "leaf_sha256": after,
        "baseline_unchanged": True,
        "scope": "Pinned public markup plus candidate archive-only CSS; not new-edition generation or publication",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--baseline-sha", required=True)
    args = parser.parse_args()
    if args.evidence.exists():
        raise ValueError("evidence destination already exists")
    args.evidence.mkdir(parents=True)
    result = {"ok": False, "publication_performed": False}
    try:
        result["stage"] = stage(args.baseline, args.source, args.candidate, args.baseline_sha)
        (args.evidence / "stage-binding.json").write_text(
            json.dumps(result["stage"], indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (args.evidence / "visual-regression.json").write_text(
            json.dumps({**result, "status": "browser_pending_or_running"}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        from bluefern_dispatches.public_site_visuals import validate_public_site_visuals
        # No explicit paths: retain all seven canonical routes and every check.
        result["seven_routes"] = validate_public_site_visuals(
            args.candidate, screenshot_dir=args.evidence / "screenshots"
        )
        result["dated_gaza"] = validate_public_site_visuals(
            args.candidate,
            screenshot_dir=args.evidence / "dated-screenshot",
            paths=[latest_gaza_path(args.candidate)],
        )
        require_route_evidence(result["seven_routes"], count=7)
        require_route_evidence(result["dated_gaza"], count=1)
        if not (result["seven_routes"]["ok"] and result["dated_gaza"]["ok"]):
            raise ValueError("positive staged candidate did not pass")
        # Mutation control is private and deliberately restores excessive intro
        # padding. It must be rejected by layout checks, not a browser failure.
        css_path = args.candidate / CSS_PATH
        good_css = css_path.read_bytes()
        try:
            css_path.write_bytes(good_css + b"\n.archive--gaza .archive-intro { padding: 16rem 0; }\n")
            result["negative_css_sha256"] = sha256(css_path.read_bytes())
            result["negative_control"] = validate_public_site_visuals(
                args.candidate, screenshot_dir=args.evidence / "negative-screenshot",
                paths=["/gaza/archive.html"],
            )
            require_route_evidence(result["negative_control"], count=1)
            checks = {issue.get("check") for issue in result["negative_control"].get("issues", [])
                      if issue.get("page") == "/gaza/archive.html"}
            expected = {"gaza_archive_latest", "gaza_archive_first_viewport"}
            if result["negative_control"]["ok"] or not expected.issubset(checks):
                raise ValueError("excessive-padding negative control did not fail the layout gates")
        finally:
            css_path.write_bytes(good_css)
            result["candidate_restored"] = sha256(css_path.read_bytes()) == result["stage"]["candidate_css_sha256"]
        if not result["candidate_restored"]:
            raise ValueError("candidate CSS restoration failed")
        result["ok"] = True
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        (args.evidence / "visual-regression.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(json.dumps({"ok": result["ok"], "error": result.get("error"), "evidence": str(args.evidence)}))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
