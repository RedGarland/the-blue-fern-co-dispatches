# Dispatches From The Blue Fern Co.

Unified static dispatch site for:

- `gaza` - Dispatches From Gaza, always free/public.
- `cascadia` - Cascadia Systems Dispatch, prepared for future public/detail tier separation.

Public site output is generated under `output/site/` with URLs rooted at:

- `https://dispatches.thebluefernco.com/`
- `https://dispatches.thebluefernco.com/gaza/`
- `https://dispatches.thebluefernco.com/cascadia/`

## Build

```powershell
python scripts\publish_github_pages.py
```

Dry-run mode reports planned writes, public URLs, backup paths, push status, warnings, and paid/detail exclusion status:

```powershell
python scripts\publish_github_pages.py --dry-run
```

The script does not push, force-push, delete pages, or change DNS.

## GitHub Pages Repo Publishing

GitHub Pages and DNS must be configured separately. This project only prepares a deployable static site root in the local Pages repo.

Dry-run:

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

The publisher copies only `output/site/` into the Pages repo, writes `CNAME` with `dispatches.thebluefernco.com`, preserves `.git/`, and never pushes automatically.

## Site Structure

- `assets/` - shared Blue Fern/Gaza/Cascadia visual assets.
- `src/bluefern_dispatches/` - static site generator and safety checks.
- `scripts/publish_github_pages.py` - build/dry-run entrypoint.
- `output/site/` - public static site output.
- `bluefern-dispatches-pages/` - local Pages repo static root after publishing.
- `output/detail/` and `output/paid/` - reserved non-public detail roots.
- `data/dispatches/`, `data/sources/`, `data/curation/`, `data/records/` - project-scoped data roots for future ingestion and records.

## Backups

Edition backups default to:

```text
C:\Users\Admin\Desktop\Python\dispatches-bluefern-backups
```

Each edition receives a per-dispatch folder such as:

```text
C:\Users\Admin\Desktop\Python\dispatches-bluefern-backups\gaza\2026-05-03\
C:\Users\Admin\Desktop\Python\dispatches-bluefern-backups\cascadia\2026-05-03\
```

Backups include rendered HTML, source manifest, curation manifest, edition manifest, and a run manifest.

## Source Traceability

The generator enforces the project rule: public factual stories must carry source IDs that resolve to source records. Editorial/admin copy may render without external reporting only when explicitly marked as `editorial_admin_copy`.

Generated edition manifests include:

- `edition_manifest.json`
- `sources_manifest.json`
- `curation_manifest.json`

## Public vs Detail/Paid Separation

Public/free artifacts are written only under `output/site/`. Detail/paid roots are reserved under `output/detail/` and `output/paid/` and are checked so they cannot be nested inside the public site output.

Gaza remains free/public. Cascadia includes placeholder detail artifact metadata only; no paid/detail files are copied into public output.

## Adding Future Dispatches

Add a new dispatch configuration in `src/bluefern_dispatches/generator.py` with:

- a unique slug
- a dated edition path using `YYYY-MM-DD`
- source records for every factual public story
- curation records with inclusion/detail flags
- logo assets in `assets/`

Then run tests and dry-run publishing before writing public output.
