# Dispatches Project Notes

This project is intentionally separate from the existing Gaza and FDA/Cascadia pipeline folders. It borrows the Gaza GitHub Pages visual theme and the Cascadia source/curation philosophy, but writes its own outputs, records, manifests, and backups.

Current dispatch slugs:

- `gaza`
- `cascadia`

Current generated edition date:

- `2026-05-03`

Safety defaults:

- dry-run does not write files
- publisher reports `would_push: false`
- paid/detail artifacts are excluded from `output/site`
- Pages repo publishing copies only `output/site`
- Pages repo publishing preserves `.git/` and writes/validates `CNAME`
- backups are outside the repository by default
- no destructive git or DNS behavior is implemented

Pages repo dry-run:

```powershell
python scripts\publish_github_pages.py --dry-run --pages-repo "C:\Users\Admin\Desktop\Python\Dispatches From The Blue Fern Co\bluefern-dispatches-pages"
```

Copy + commit locally, no push:

```powershell
python scripts\publish_github_pages.py --pages-repo "C:\Users\Admin\Desktop\Python\Dispatches From The Blue Fern Co\bluefern-dispatches-pages" --remote-url "https://github.com/RedGarland/the-blue-fern-co-dispatches/" --commit --no-push
```

Manual push after inspection:

```powershell
cd "C:\Users\Admin\Desktop\Python\Dispatches From The Blue Fern Co\bluefern-dispatches-pages"
git status
git remote -v
git push origin main
```

GitHub Pages and DNS must be configured separately. Do not force-push.

## Cascadia Dispatch Pipeline

The Cascadia pipeline is standalone inside this repository. It is separate from the older FDA/Cascadia media pipeline and must not depend on that project structure.

Region scope:

- Washington
- Oregon
- Idaho

Source configuration:

```text
data/dispatches/cascadia/sources.yml
```

Full run:

```powershell
python scripts\run_cascadia_dispatch.py --date 2026-05-03 --all
```

Operational cadence:

```powershell
python scripts\run_cascadia_dispatch.py --date YYYY-MM-DD --daily
python scripts\run_cascadia_dispatch.py --date YYYY-MM-DD --weekly-public
python scripts\publish_github_pages.py --pages-repo "C:\Users\Admin\Desktop\Python\Dispatches From The Blue Fern Co\bluefern-dispatches-pages" --remote-url "https://github.com/RedGarland/the-blue-fern-co-dispatches.git" --commit --no-push
```

Manual push after inspection:

```powershell
cd "C:\Users\Admin\Desktop\Python\Dispatches From The Blue Fern Co\bluefern-dispatches-pages"
git status
git push origin main
```

Stage outputs:

```text
data/dispatches/cascadia/raw/YYYY-MM-DD/raw_sources.json
data/dispatches/cascadia/normalized/YYYY-MM-DD/normalized_sources.json
data/dispatches/cascadia/curated/YYYY-MM-DD/curation_manifest.json
output/dispatches/cascadia/editions/YYYY-MM-DD/
output/site/cascadia/editions/YYYY-MM-DD/
output/detail/cascadia/YYYY-MM-DD/
data/records/
```

The public Cascadia edition includes only stories with traceable source records and visible source links. Detail records are written only to `output/detail/cascadia/YYYY-MM-DD/` and are not published publicly.

Public brand: The Cascadia Briefing.

Internal data product: Cascadia Signal.

The public URL slug remains `/cascadia/`. The public page and edition format should stay consistent with the existing Blue Fern/Gaza-style dispatch format unless a small label change is required.

Shared dispatch records live under `data/records/`:

- `dispatches.json`
- `editions.json`
- `sources.json`
- `records.json`
- `curation_decisions.json`
- `detail_packages.json`

This structure is dispatch-agnostic and should support Gaza, Cascadia, food insecurity, political actions, healthcare access, and future briefings.

Core rule: **NO FACT WITHOUT A TRACEABLE SOURCE.** Every public factual story, signal, score, trend, summary, and detail/data record must trace back to source records through manifests or the shared dispatch data layer.

Paid/detail artifacts are private. Do not expose `output/detail/`, `output/paid/`, raw source dumps, or Cascadia Signal package files under `output/site/`.
