# Operator-run comparisons

These tools prepare manifests and check **reported** outcome artifacts. They do not start agents, invoke models, spend API funds, or establish a measured benefit. Deterministic pytest protocol tests and stochastic model trials are distinct.

Run from the repository root with the Nomos Python environment:

```sh
python -m benchmarks.run_protocol plan --trials 10 --seed 42
python -m benchmarks.run_protocol init --fixture dependency --condition baseline --trial 1 --seed 42 --starting-commit YOUR_FULL_COMMIT --agent-count 2 --context-policy 'manual curl' --output trial1-baseline.json
```

Execute the fixture's instruction with your chosen runner. Keep tasks, fixture revision, starting commit, acceptance checks, runner/model/version, context policy per condition and agent count fixed across paired trials. Record runner/model/version in `init` flags. Follow the randomized order and reset the checkout between runs. Do not run experiments in your working checkout or production Nomos data directory.

Write an observation JSON file containing every field under the selected fixture's `checks`. Missing fields fail rather than defaulting to success. Preserve independent acceptance-test output, terminal logs, and source commit/diff alongside the observation. Then:

```sh
python -m benchmarks.run_protocol finish trial1-baseline.json --artifact observed.json --human-interventions 2
python -m benchmarks.run_protocol validate trial1-baseline.json
python -m benchmarks.summarize trial1-baseline.json trial1-helper.json
```

Run `helper_context` with the same trial/seed/configuration and an explicitly documented helper context policy. Repeat at least 10 matched trials **per task/configuration** before evaluating direction; report failed trials as well as successes, medians/ranges, quality and human interventions. `summarize` refuses duplicate condition records and excludes unmatched/incomplete pairs. It reports descriptive aggregates only, so avoid pooling unrelated tasks when interpreting results. Unknown usage is not zero and no token-derived cost is inferred.

To import a completed manifest into an isolated/private development project:

```sh
export NOMOS_API_KEY='your-agent-key'
python -m benchmarks.run_protocol import trial1-baseline.json --project 1 --request-key trial1-baseline
```

Fixture coverage: restart/handoff, dependency contention, shared-file conflict, and revision-bound review. `run_manifest.schema.json` is generated from the API model. No actual model-outcome trials or adapter-acceptance latency measurements have been performed by this implementation. Runner-specific wake adapters and the proposed p95 acceptance target require a supported runner integration.
