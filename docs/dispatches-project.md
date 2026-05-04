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
