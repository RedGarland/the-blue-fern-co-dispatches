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
    parse_aggregate_handoff,
    sha256_bytes,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a private Food Line historical event-review recovery")
    parser.add_argument("operation", choices=("inspect", "template", "validate", "dry-run", "import"))
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--cluster-spec", type=Path)
    parser.add_argument("--template-output", type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--pages-root", type=Path)
    parser.add_argument("--captured-at", default="")
    parser.add_argument("--run-month", help="retain findings from agent runs completed/started in YYYY-MM")
    args = parser.parse_args(argv)

    try:
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
