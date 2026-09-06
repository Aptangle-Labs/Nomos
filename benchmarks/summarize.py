"""Descriptive matched-trial summary; never infer savings from absent usage."""

import argparse
import json
from collections import defaultdict
from datetime import datetime
from statistics import median


def summarize(manifests):
    pairs = defaultdict(dict)
    for m in manifests:
        key = (
            m["task"],
            m["fixture_revision"],
            m["starting_commit"],
            m.get("runner"),
            m.get("runner_version"),
            m.get("model"),
            m["agent_count"],
            json.dumps(m.get("evaluation", {}).get("fixture_checks"), sort_keys=True),
            m["trial"],
            m["order_seed"],
        )
        if m["condition"] in pairs[key]:
            raise ValueError("Duplicate condition for a matched trial.")
        pairs[key][m["condition"]] = m
    matched = [
        p
        for p in pairs.values()
        if len(p) == 2 and all(m.get("ended_at") and m.get("accepted") is not None for m in p.values())
    ]
    result = {
        "matched_trials": len(matched),
        "unmatched_or_incomplete": len(pairs) - len(matched),
        "conditions": {},
        "interpretation": "Descriptive operator-reported outcomes only; no causal benefit claim. At least 10 matched trials per task/configuration are needed before evaluating direction.",
    }
    for condition in ("baseline", "helper_context"):
        rows = [p[condition] for p in matched]
        durations = [
            (datetime.fromisoformat(m["ended_at"]) - datetime.fromisoformat(m["started_at"])).total_seconds()
            for m in rows
        ]
        result["conditions"][condition] = {
            "trials": len(rows),
            "accepted": sum(m["accepted"] for m in rows),
            "median_seconds": median(durations) if durations else None,
            "min_seconds": min(durations) if durations else None,
            "max_seconds": max(durations) if durations else None,
            "human_interventions": [m.get("human_interventions") for m in rows],
        }
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("manifests", nargs="+")
    args = p.parse_args()
    from pathlib import Path

    from server.routers.outcomes import RunManifest

    manifests = [
        RunManifest.model_validate_json(Path(f).read_text()).model_dump(mode="json") for f in args.manifests
    ]
    print(json.dumps(summarize(manifests), indent=2))


if __name__ == "__main__":
    main()
