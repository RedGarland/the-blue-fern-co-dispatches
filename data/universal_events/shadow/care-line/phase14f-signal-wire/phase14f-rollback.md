# Care Line Signal Wire rollback

If this Phase 14F rehearsal needs to be undone, remove only the generated Signal Wire outputs:

- `output/site/events/event_3b4ad4e528e48744/`
- `output/site/events/event_a12dae614b86cfa9/`
- `output/site/signals/`
- `output/site/care-line/signals/`
- `data/universal_events/shadow/care-line/phase14f-signal-wire/`

Then rerun the build to regenerate from the preserved Phase 14E source evidence.
Do not touch the Phase 14E reviewed records or the deferred evidence-review ledger.
