from benchmarks.run_protocol import check_artifact, trial_order
from benchmarks.summarize import summarize

from .conftest import unwrap


def manifest():
    return {
        "task": "dependency",
        "fixture_revision": "1",
        "starting_commit": "a" * 40,
        "condition": "baseline",
        "trial": 1,
        "order_seed": 42,
        "agent_count": 2,
        "context_policy": "manual curl",
        "started_at": "2026-01-01T00:00:00+00:00",
    }


def test_usage_dedup_cumulative_resets_and_unknown(client, project):
    pid = project["id"]
    base = f"/api/projects/{pid}"
    run = unwrap(client.post(base + "/experiment-runs", json=manifest()), 201)
    assert unwrap(client.get(base + "/experiment-runs"))["items"][0]["usage"] is None
    url = base + f"/experiment-runs/{run['id']}/usage"
    sample = {
        "source": "runner",
        "sample_id": "one",
        "unit": "output_tokens",
        "mode": "cumulative",
        "epoch": "run1",
        "sequence": 1,
        "quantity": 100,
    }
    first = unwrap(client.post(url, json=sample), 201)
    assert unwrap(client.post(url, json=sample), 201)["id"] == first["id"]
    assert client.post(url, json=sample | {"quantity": 101}).status_code == 409
    unwrap(client.post(url, json=sample | {"sample_id": "two", "sequence": 2, "quantity": 150}), 201)
    assert (
        client.post(url, json=sample | {"sample_id": "reset", "sequence": 3, "quantity": 5}).status_code
        == 409
    )
    unwrap(
        client.post(url, json=sample | {"sample_id": "new", "sequence": 1, "quantity": 5, "epoch": "run2"}),
        201,
    )
    usage = unwrap(client.get(base + "/experiment-runs"))["items"][0]["usage"]
    assert sum(s["value"] for s in usage) == 155
    assert client.post(url, json=sample | {"sample_id": "cost", "unit": "cost"}).status_code == 422
    assert (
        client.post(url, json=sample | {"sample_id": "mixed", "mode": "delta", "sequence": 9}).status_code
        == 409
    )
    assert client.delete(base).status_code == 200


def test_manifest_and_fixture_validation(client, project):
    base = f"/api/projects/{project['id']}/experiment-runs"
    assert client.post(base, json=manifest() | {"started_at": "2026-01-01T00:00:00"}).status_code == 422
    assert client.post(base, json=manifest() | {"ended_at": "2025-01-01T00:00:00Z"}).status_code == 422
    assert trial_order(10, 42) == trial_order(10, 42)
    assert len(trial_order(10, 42)) == 20
    assert not check_artifact({"checks": {"accepted": True}}, {})["accepted"]
    a = manifest() | {"ended_at": "2026-01-01T00:01:00+00:00", "accepted": True}
    b = a | {"condition": "helper_context", "ended_at": "2026-01-01T00:00:45+00:00"}
    result = summarize([a, b])
    assert result["matched_trials"] == 1 and result["conditions"]["helper_context"]["median_seconds"] == 45
    assert summarize([a, b | {"starting_commit": "b" * 40}])["matched_trials"] == 0
