# Food Line agent inbox

This directory is a private operator handoff location. Save an exported scheduled-agent JSON response here, then run:

```powershell
python scripts/import_food_line_agent_findings.py validate --input data/dispatches/food-line/agent-inbox/<file>.json
python scripts/import_food_line_agent_findings.py dry-run --input data/dispatches/food-line/agent-inbox/<file>.json --edition-date YYYY-MM-DD --agent-run-id <run-id>
python scripts/import_food_line_agent_findings.py import --input data/dispatches/food-line/agent-inbox/<file>.json --edition-date YYYY-MM-DD --agent-run-id <run-id>
```

Real imports record the SHA-256 input hash in the private intake artifact and copy the input to `processed/YYYY-MM-DD/`. Dry runs do not move files. A repeated identical import is an idempotent no-op; a different file cannot overwrite an existing run artifact. Preserve processed files for audit history. There is no filesystem watcher.
