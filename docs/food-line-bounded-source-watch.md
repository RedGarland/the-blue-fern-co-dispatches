# Food Line bounded source watch

The production Food Line source watch is a private, durable workflow. It creates
an immutable query plan before collection, executes that plan in bounded
partitions, checkpoints after every partition, and fails closed when required
coverage is incomplete. It does not publish or make editorial decisions.

## Why the runner changed

The 2026-08-01 real run exposed an all-or-nothing execution problem. The plan
contained 1,033 rows and 12 configured direct sources. Collection was serial.
Most fetches used a 15-second request timeout, while the project fetcher could
retry a timeout with a timeout of up to 45 seconds. Article and Google News
wrapper resolution added more network waits. The process ran for about 1,204
seconds, outlived its external command wrapper, and had to be stopped. Candidate,
audit, and export writes were all after the collection loop, so the interrupted
run left no durable progress record. Its correct status remains
upstream_collection_failed.

The source-watch code itself did not create a subprocess. The surviving Python
process was the command wrapper's child. Bounded execution now creates workers
deliberately, tracks each PID, and terminates the full worker process group on
query timeout, partition timeout, whole-run timeout, Ctrl+C, or termination.

## Original plan breakdown

The complete plan is retained and recorded. For the 2026-08-01 configuration:

| Query family | Count |
| --- | ---: |
| state_territory | 616 |
| metro | 319 |
| core_hunger | 16 |
| policy_program | 11 |
| pressure | 9 |
| food_bank_provider | 8 |
| nonprofit_report | 7 |
| cost_pressure | 6 |
| institutional_update | 6 |
| public_radio | 6 |
| school_meals_child_nutrition | 6 |
| county_city_agenda | 5 |
| feeding_america_affiliate | 5 |
| snap_state_notice | 5 |
| united_way_211 | 4 |
| social_watchlist | 3 |
| news_article | 1 |

Geographic scope is 616 state/territory, 320 metro, 95 national, and two
state-local rows. Collection channels are 1,026 Google News RSS searches, four
enabled direct RSS sources, and three enabled direct pages. The configuration
contains 12 direct-source definitions; disabled definitions remain visible in
configuration but do not produce plan requests.

The plan builder found no exact duplicate identities. It reports 24 potential
semantic-overlap groups but does not remove them because no yield study yet
supports semantic consolidation. The plan report always records the original
count, exact duplicates removed, semantic consolidations, retained count,
family/geography/source counts, tier counts, requested count, and deferred
count.

## Plan, partition, and stable identities

Every query ID hashes the plan version, edition date, normalized query text,
query family, geography, domain restriction, and discovery channel. Partition
IDs hash their tier and ordered stable query IDs. The immutable plan checksum
excludes mutable execution fields.

Private artifacts live under:

    data/dispatches/food-line/discovery-runs/<edition-date>/<run-id>/

The directory contains query-plan.json, query-plan-report.json, run-state.json,
progress.json, partition attempt files, final-candidates.json, and
final-audit.json. These are current operational records, not historical
records, and are ignored by Git.

## Priority tiers

Tier 1 is required for the daily-current profile. It contains enabled configured
direct sources and high-value national/federal benefit, interruption, closure,
disaster, and pressure searches. The current plan has 57 Tier 1 queries.

Tier 2 contains state/territory targeting, provider demand, food-bank capacity,
school meals, public radio, nonprofit, county/city, and related local pressure
searches. It currently has 649 queries.

Tier 3 contains metros and broad supplemental or exploratory variants. It
currently has 327 queries.

Tier 2 and Tier 3 are never hidden. Daily runs record their 976 queries as
deferred. They can be run later with the supplemental profile without changing
the meaning of the completed required daily scope.

## Production bounds

The daily-current defaults are:

- whole run: 30 minutes;
- partition: 5 minutes;
- one query worker: 90 seconds;
- each request attempt: 15 seconds;
- one retry after the initial request;
- partition size: 25;
- maximum concurrent workers: 2;
- progress interval: 30 seconds;
- results per query: 3;
- required-query success threshold: 90 percent;
- required direct-source success threshold: 75 percent.

The parent submits at most one worker wave at a time, so there is no unbounded
task queue. Request retries use bounded backoff. A query runs in a dedicated
worker process, allowing the parent to terminate it when its query budget
expires. The parent stops scheduling on a partition or whole-run deadline,
cancels queued work, terminates active workers, checkpoints, and exits with a
non-success status.

## Run states and completion

run-state.json begins as planned before the first request. Normal transitions
include running, resumed, partial, completed, completed_with_exclusions,
timed_out, cancelled, and failed. Every non-success state is durable and
resumable.

completed requires every required query to reach a terminal result, required
success coverage at or above the configured threshold, required direct-source
success coverage at or above its threshold, and no structural or checksum
failure. completed_with_exclusions meets those rules but has bounded failures.

partial means required work is incomplete or below its coverage threshold.
timed_out, cancelled, and failed are never reclassified as an empty successful
run. no_exportable_findings is possible only after completed or
completed_with_exclusions collection and only when evidence gates produce zero
exportable findings.

## Commands

Start a new daily run:

    python scripts/run_food_line_discovery_expansion.py \
      --date YYYY-MM-DD \
      --profile daily-current \
      --max-run-minutes 30 \
      --export-agent-inbox \
      --agent-inbox-dir data/dispatches/food-line/agent-inbox

The profile flag is optional because bounded daily-current is the CLI default.
Use --run-id only for a preselected new run ID. A reused run ID is rejected.

Inspect without collection:

    python scripts/run_food_line_discovery_expansion.py --status-run <RUN_ID>

Resume required work:

    python scripts/run_food_line_discovery_expansion.py \
      --date YYYY-MM-DD \
      --resume-run <RUN_ID> \
      --export-agent-inbox

Run deferred supplemental tiers:

    python scripts/run_food_line_discovery_expansion.py \
      --date YYYY-MM-DD \
      --resume-run <RUN_ID> \
      --profile supplemental

The status output gives the exact next command. Resume verifies the immutable
plan checksum, plan version, edition date, and current configuration checksum.
It skips completed queries, retries failed or incomplete work, preserves the run
ID and earlier partition artifacts, increments attempts, and deduplicates
candidates.

The deliberately small real smoke profile is:

    python scripts/run_food_line_discovery_expansion.py \
      --date YYYY-MM-DD \
      --profile smoke \
      --max-partitions 2

It is intentionally incomplete when not all required partitions fit and cannot
export. --legacy-unbounded exists only for explicit compatibility and tests. It
must not be used as the production command.

## Export gate

Agent export reads final deduplicated candidates and requires completed or
completed_with_exclusions status, required coverage, durable final artifacts,
and no unresolved required partition. The agent run ID is the discovery run ID.
Coverage notes disclose required success, direct-source success, bounded
failures, and deferred optional queries.

When export is blocked, run-state.json records the reason, resumability, and
exact resume command. Partial, timed-out, cancelled, and failed runs never write
an inbox envelope and never report no_exportable_findings.

## Scheduling boundary

No schedule is installed by this implementation. Intake should start only after
a qualifying source-watch state and export result. A failed run should be
resumed or retried and alerted; it must not trigger publication or be treated as
a successful empty day.
