from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, check=False, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        return (result.stderr or result.stdout).strip()
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Show safe publish workflow status for source vs Pages repo.")
    parser.add_argument("--source-repo", default=r"C:\PythonProjects\Dispatches From The Blue Fern Co")
    parser.add_argument("--pages-repo", default=r"C:\PythonProjects\Dispatches From The Blue Fern Co\bluefern-dispatches-pages")
    parser.add_argument("--pages-branch", default="gh-pages")
    args = parser.parse_args()

    source_repo = Path(args.source_repo)
    pages_repo = Path(args.pages_repo)
    print("SOURCE REPO")
    print(source_repo)
    print(run_git(source_repo, "status", "--short"))
    print("")
    print("PAGES REPO")
    print(pages_repo)
    print("branch:", run_git(pages_repo, "branch", "--show-current"))
    print(run_git(pages_repo, "status", "--short"))
    print("")
    print("SAFETY RULES")
    print("- Never run `git add .` in source repo.")
    print("- Never commit `.env`, logs, output/detail, output/paid, temp test dirs, or broad generated artifacts.")
    print(f"- Publish push happens only from Pages repo on `{args.pages_branch}`.")
    print("")
    print("PAGES-ONLY PUSH COMMANDS")
    print(f'cd "{pages_repo}"')
    print(f"git checkout {args.pages_branch}")
    print("git add american-pressure/ assets/index_updated_logo.html index.html")
    print('git commit -m "Publish American Pressure map and homepage link updates"')
    print(f"git push origin {args.pages_branch}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

