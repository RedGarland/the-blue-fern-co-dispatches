# Pages Publish Safety

Source repo: `C:\PythonProjects\Dispatches From The Blue Fern Co`

Pages repo: `C:\PythonProjects\Dispatches From The Blue Fern Co\bluefern-dispatches-pages`

Rules:

- Do not run `git add .` in source repo.
- Do not commit `.env`, logs, `output/detail`, `output/paid`, test temp dirs, or broad generated artifacts.
- Publish push must happen only from `bluefern-dispatches-pages`.
- Pages branch must be `gh-pages`.

- Local publish behavior: the publisher copies the generated `output/site` files into the `bluefern-dispatches-pages` repository and creates a local commit by default. Pushing those commits to the remote is an explicit, separate step (the publisher skips push unless invoked with an explicit push option).
- To publish live from this machine, either run the dispatch runner with its `--push` flag (for example `scripts\run_daily_gaza.py --push`) or run `git push origin gh-pages` from inside the `bluefern-dispatches-pages` repo. Do not push the Pages branch from the source repo.

Quick status command:

```powershell
.\.venv\Scripts\python.exe scripts\status_pages_repo.py
```
