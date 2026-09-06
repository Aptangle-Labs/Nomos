"""Prepare matched trials, validate reported outcome artifacts, and import manifests."""

import argparse
import json
import random
from datetime import UTC, datetime
from pathlib import Path

from server.routers.outcomes import RunManifest

FIXTURES = Path(__file__).parent / "fixtures"


def check_artifact(fixture, artifact):
    """Checks explicit observations against fixture requirements; does not execute a model."""
    checks = fixture["checks"]
    return {
        "accepted": all(artifact.get(k) == value for k, value in checks.items()),
        "checks": {
            k: {"expected": v, "observed": artifact.get(k), "pass": artifact.get(k) == v}
            for k, v in checks.items()
        },
    }


def trial_order(trials, seed):
    rng = random.Random(seed)
    result = []
    for trial in range(1, trials + 1):
        conditions = ["baseline", "helper_context"]
        rng.shuffle(conditions)
        result.extend({"trial": trial, "condition": condition} for condition in conditions)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--trials", type=int, default=10)
    plan.add_argument("--seed", type=int, required=True)
    init = sub.add_parser("init")
    init.add_argument("--fixture", choices=[p.stem for p in FIXTURES.glob("*.json")], required=True)
    init.add_argument("--condition", choices=["baseline", "helper_context"], required=True)
    init.add_argument("--trial", type=int, required=True)
    init.add_argument("--seed", type=int, required=True)
    init.add_argument("--starting-commit", required=True)
    init.add_argument("--runner")
    init.add_argument("--runner-version")
    init.add_argument("--model")
    init.add_argument("--agent-count", type=int, default=2)
    init.add_argument("--context-policy", required=True)
    init.add_argument("--output", type=Path, required=True)
    finish = sub.add_parser("finish")
    finish.add_argument("manifest", type=Path)
    finish.add_argument("--artifact", type=Path, required=True)
    finish.add_argument("--human-interventions", type=int, required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("manifest", type=Path)
    imp = sub.add_parser("import")
    imp.add_argument("manifest", type=Path)
    imp.add_argument("--base-url", default="http://127.0.0.1:8484")
    imp.add_argument("--project", type=int, required=True)
    imp.add_argument("--request-key", required=True)
    args = parser.parse_args()
    if args.command == "plan":
        if not 1 <= args.trials <= 10000:
            parser.error("trials must be between 1 and 10000")
        print(json.dumps(trial_order(args.trials, args.seed), indent=2))
        return
    if args.command == "init":
        fixture = json.loads((FIXTURES / (args.fixture + ".json")).read_text())
        manifest = RunManifest(
            task=args.fixture,
            fixture_revision=fixture["revision"],
            starting_commit=args.starting_commit,
            condition=args.condition,
            trial=args.trial,
            order_seed=args.seed,
            agent_count=args.agent_count,
            runner=args.runner,
            runner_version=args.runner_version,
            model=args.model,
            context_policy=args.context_policy,
            started_at=datetime.now(UTC),
            evaluation={"fixture_checks": fixture["checks"], "provenance": "operator_reported"},
        )
        # Exclusive creation protects another trial from accidental overwrite.
        with args.output.open("x") as f:
            f.write(manifest.model_dump_json(indent=2) + "\n")
        print(args.output)
        return
    manifest = RunManifest.model_validate_json(args.manifest.read_text())
    if args.command == "finish":
        if manifest.task not in {p.stem for p in FIXTURES.glob("*.json")}:
            raise ValueError("Unknown fixture task.")
        fixture = json.loads((FIXTURES / (manifest.task + ".json")).read_text())
        if manifest.fixture_revision != fixture["revision"]:
            raise ValueError("Fixture revision changed; use the original fixture.")
        artifact = json.loads(args.artifact.read_text())
        scored = check_artifact(fixture, artifact)
        data = manifest.model_dump(mode="json") | {
            "ended_at": datetime.now(UTC).isoformat(),
            "accepted": scored["accepted"],
            "human_interventions": args.human_interventions,
            "evaluation": manifest.evaluation | scored,
            "evidence_references": manifest.evidence_references + [str(args.artifact.resolve())],
        }
        checked = RunManifest.model_validate(data)
        args.manifest.write_text(checked.model_dump_json(indent=2) + "\n")
        print(json.dumps(scored, indent=2))
    elif args.command == "validate":
        print("Manifest valid; outcome observations remain operator-reported.")
    else:
        import os

        from nomos_client.http import Client

        client = Client(args.base_url, os.environ.get("NOMOS_API_KEY", ""))
        try:
            print(
                json.dumps(
                    client.request(
                        "POST",
                        f"/api/projects/{args.project}/experiment-runs",
                        manifest.model_dump(mode="json"),
                        args.request_key,
                    )
                )
            )
        finally:
            client.close()


if __name__ == "__main__":
    main()
