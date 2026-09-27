from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, check=False, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        return (result.stderr or result.stdout).strip()
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Show safe publish workflow status for source vs Pages repo.")
    parser.add_argument("--source-repo", default=str(ROOT))
    parser.add_argument("--pages-repo", default=str(ROOT / "bluefern-dispatches-pages"))
    parser.add_argument("--pages-branch", default="gh-pages")
    args = parser.parse_args()

    source_repo = Path(args.source_repo)
    pages_repo = Path(args.pages_repo)
    print("SOURCE REPO")
    print(source_repo)
    print(run_git(source_repo, "status", "--short", "--branch"))
    print("")
    print("PAGES REPO")
    print(pages_repo)
    print("branch:", run_git(pages_repo, "branch", "--show-current"))
    print(run_git(pages_repo, "status", "--short", "--branch"))
    print("")
    print("SAFETY RULES")
    print("- Never run `git add .` in source repo.")
    print("- Never commit `.env`, logs, output/detail, output/paid, temp test dirs, or broad generated artifacts.")
    print(f"- Publish push happens only from the Pages repo on `{args.pages_branch}` after explicit publication authorization.")
    print("- This status helper does not stage, commit, or push any files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
