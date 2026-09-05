# Care Line approved-release publication scheduler handoff

The repository now provides the source-side scheduled publication chain:

```text
Task Scheduler
  -> scripts/windows/run_care_line_approved_release_publication.ps1
  -> scripts/care_line_publication_scheduler.py
  -> scripts/run_care_line_publication_runner.py
  -> guarded Care Line-only Pages publication
```

The scheduled helper scans paired Care Line `proposed-editions` and
`signal-reviews` artifacts. Both files must declare `release_ready: true`, be
tracked at the protected source HEAD, and match that HEAD. The source and Pages
checkouts must be on their required branches, equal their local `origin/*`
tracking refs, and contain no risky dirty state. A release that is already a
listable Pages edition and appears in both the Care Line archive and RSS is a
successful no-op. Partial publication state and multiple unpublished approved
releases fail closed.

An eligible release is handed to the existing publication runner with
`--publish --push --isolated-source`. The isolated source checkout prevents
generated publication output from dirtying the long-lived runner. The command
does not request Bluesky, audio, collection, queue mutation, approval creation,
or promotion. Normal and failed runs write a JSON receipt under
`status/care-line/publication-scheduler-runs/YYYY-MM-DD/` and a log under
`logs/care-line/publication-scheduler/YYYY-MM-DD/`.

## Registration boundary

Repository history, scheduler documentation, saved task-definition evidence,
and installed Task Scheduler definitions do not establish a Care Line
approved-release publication task name or cadence. The wrapper therefore does
not register a task or embed a proposed cadence. An operator must choose the
task name and cadence before production registration. That decision must not
change the proven National Collection or Reviewed Event Queue task definitions
or cadence.

After that decision, the task action must invoke only:

```powershell
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "C:\BlueFernRunner\CareLineNationalCurrent8\scripts\windows\run_care_line_approved_release_publication.ps1" -RepositoryRoot "C:\BlueFernRunner\CareLineNationalCurrent8" -PagesRepo "C:\BlueFernRunner\CareLineNationalCurrent8\bluefern-dispatches-pages" -PythonExecutable "C:\BlueFernRunner\CareLineNationalCurrent8\.venv\Scripts\python.exe" -SourceBranch "add/pages-repo-default" -PagesBranch "gh-pages"
```

The task working directory must be
`C:\BlueFernRunner\CareLineNationalCurrent8`. Do not put release dates,
approval data, credentials, collection options, social options, or audio options
in the task action.
