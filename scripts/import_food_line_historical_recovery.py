from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bluefern_dispatches.food_line_historical_recovery import (  # noqa: E402
    FoodLineHistoricalRecoveryError,
    build_recovery,
    cluster_spec_template,
    dry_run_result,
    import_recovery,
    migrate_recovery_to_four_tiers,
    parse_aggregate_handoff,
    record_historical_event_review,
    sha256_bytes,
    validate_migration_implementation_commit,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a private Food Line historical event-review recovery")
    parser.add_argument("operation", choices=("inspect", "template", "validate", "dry-run", "import", "migrate", "review"))
    parser.add_argument("--input", type=Path)
    parser.add_argument("--cluster-spec", type=Path)
    parser.add_argument("--template-output", type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--pages-root", type=Path)
    parser.add_argument("--captured-at", default="")
    parser.add_argument("--run-month", help="retain findings from agent runs completed/started in YYYY-MM")
    parser.add_argument(
        "--predecessor-artifact-set",
        help="exact sha256: artifact-set identity of the immutable predecessor (migrate only)",
    )
    parser.add_argument(
        "--implementation-source-commit",
        help="40-character commit containing the migration implementation (migrate only)",
    )
    parser.add_argument("--successor-identity", help="exact four-tier successor sha256: identity (review only)")
    parser.add_argument("--artifact-set", help="exact four-tier successor artifact-set identity (review only)")
    parser.add_argument("--event-id", help="exact recovered event identity (review only)")
    parser.add_argument("--decision", help="closed historical editorial decision (review only)")
    parser.add_argument("--review-artifact", type=Path, help="independent private review JSON (review only)")
    parser.add_argument("--review-artifact-sha256", help="exact SHA-256 of the independent review JSON")
    parser.add_argument("--operator", help="operator recording the reviewed decision")
    parser.add_argument("--review-dry-run", action="store_true", help="validate a review without recording it")
    args = parser.parse_args(argv)

    try:
        if args.operation == "review":
            if (
                args.pages_root is None
                or not args.successor_identity
                or not args.artifact_set
                or not args.event_id
                or not args.decision
                or args.review_artifact is None
                or not args.review_artifact_sha256
                or not args.operator
            ):
                parser.error(
                    "review requires --pages-root, --successor-identity, --artifact-set, "
                    "--event-id, --decision, --review-artifact, --review-artifact-sha256, "
                    "and --operator"
                )
            result = record_historical_event_review(
                args.repo_root.resolve(),
                args.pages_root.resolve(),
                successor_identity_sha256=args.successor_identity,
                artifact_set_sha256=args.artifact_set,
                event_id=args.event_id,
                decision=args.decision,
                review_artifact_path=args.review_artifact,
                review_artifact_sha256=args.review_artifact_sha256,
                operator=args.operator,
                dry_run=args.review_dry_run,
            )
            print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
            return 0
        if args.input is None:
            parser.error(f"{args.operation} requires --input")
        if args.operation == "migrate":
            if (
                args.cluster_spec is None
                or not args.captured_at
                or not args.run_month
                or not args.predecessor_artifact_set
                or not args.implementation_source_commit
            ):
                parser.error(
                    "migrate requires --cluster-spec, --captured-at, --run-month, "
                    "--predecessor-artifact-set, and --implementation-source-commit"
                )
            repository_root = args.repo_root.resolve()
            validate_migration_implementation_commit(
                repository_root,
                args.implementation_source_commit,
            )
            result = migrate_recovery_to_four_tiers(
                repository_root,
                args.input,
                args.cluster_spec,
                predecessor_artifact_set_sha256=args.predecessor_artifact_set,
                implementation_source_commit=args.implementation_source_commit,
                captured_at=args.captured_at,
                run_month=args.run_month,
            )
            print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
            return 0
        parsed = parse_aggregate_handoff(args.input.read_bytes(), run_month=args.run_month)
        if args.operation == "inspect":
            result = {
                key: value
                for key, value in parsed.items()
                if key not in {"findings", "sources"}
            }
            result["status"] = "inspected"
            result["publication_approval"] = False
        elif args.operation == "template":
            if args.template_output is None:
                parser.error("template requires --template-output")
            allowed = (args.repo_root / "data" / "agent-history-staging" / "food-line").resolve()
            output = args.template_output.resolve()
            try:
                output.relative_to(allowed)
            except ValueError as exc:
                raise FoodLineHistoricalRecoveryError(
                    "template output must remain under data/agent-history-staging/food-line"
                ) from exc
            template = cluster_spec_template(parsed)
            if output.exists():
                existing = json.loads(output.read_text(encoding="utf-8"))
                if existing != template:
                    raise FoodLineHistoricalRecoveryError("refusing to overwrite a different cluster template")
                status = "idempotent_noop"
            else:
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps(template, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
                status = "template_created"
            result = {
                "status": status,
                "template_path": str(output),
                "input_sha256": parsed["input_sha256"],
                "unassigned_finding_count": len(template["unassigned_finding_ids"]),
                "publication_approval": False,
            }
        else:
            if args.cluster_spec is None or not args.captured_at:
                parser.error(f"{args.operation} requires --cluster-spec and --captured-at")
            artifacts = build_recovery(
                args.repo_root.resolve(),
                args.input.resolve(),
                args.cluster_spec.resolve(),
                pages_root=args.pages_root.resolve() if args.pages_root else None,
                captured_at=args.captured_at,
                run_month=args.run_month,
            )
            spec_sha = sha256_bytes(args.cluster_spec.read_bytes())
            if args.operation in {"validate", "dry-run"}:
                result = dry_run_result(args.repo_root.resolve(), artifacts, cluster_spec_sha256=spec_sha)
                result["status"] = "validated" if args.operation == "validate" else "validated_dry_run"
            else:
                result = import_recovery(args.repo_root.resolve(), artifacts, cluster_spec_sha256=spec_sha)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    except (FoodLineHistoricalRecoveryError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "publication_approval": False}, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
