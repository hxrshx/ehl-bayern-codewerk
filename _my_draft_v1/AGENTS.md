# AGENTS.md — Model Router (TUM.ai x Viktor challenge)

Brief for a coding agent picking up this repo mid-hackathon.

## The task

Train a router that picks the model for each LLM call in a Viktor agent
trajectory. You define the objective (cost, quality, or your own
trade-off) and defend it. Deliverable: a router, an off-policy evaluation
you can defend, and a short presentation.

Full context: `https://zeta-labs-eb2cf9.viktor.page/tumai-challenge-deck`
(13 slides — read them if anything below is ambiguous).

## What ships in `export/`

Real dataset, once dropped in at kickoff: `export/trajectories_v1_*.jsonl`,
one JSON object per line: `{"model": ..., "input": [...], "tools": [...]}`.
No output, no usage, no trajectory ids. One model serves every call in a
trajectory (as logged) — trajectories must be reconstructed by chaining
calls whose `input` grows as a strict prefix of the next call's `input`.

Until then, `scripts/generate_sample_data.py` writes synthetic data in the
same shape into `export/` so the pipeline is testable now.

## Repo layout

- `scripts/common.py` — trajectory reconstruction, token estimation
  (character heuristic, no tiktoken/network dependency), model-tier
  pricing config I/O.
- `scripts/load_trajectories.py export/` — groups raw calls into ordered
  trajectories, estimates tokens per call, writes `export/trajectories.json`.
- `scripts/baseline_router.py export/` — the baseline: a heuristic router
  (route by call position + prompt length, idea/01) swept over an
  aggressiveness parameter to trace a cost/quality frontier, cache-aware
  costing (idea/04: a call is priced at the cached rate for the prefix it
  shares with the previous call *only if the model didn't change*).
  Writes `export/baseline_frontier.json`, `baseline_report.md`,
  `baseline_frontier.png`.
- `config/model_tiers.json` — per-model pricing. Seeded with the 3 model
  ids named in the deck; `baseline_router.py` auto-adds any other ids it
  finds in the data with an inferred (placeholder) price — replace those
  with real numbers when you have them.

## Known placeholders you are expected to improve

1. **Quality proxy** (`quality_proxy()` in `baseline_router.py`): currently
   `routed_tier_rank / max_tier_rank` — assumes quality scales with tier.
   No quality label ships; this is the actual open problem. Real
   directions from the deck: judge-model rescoring of individual calls,
   or matching/weighting trajectories that happened to run on different
   models for the same task.
2. **Output-token estimate**: a flat constant (`--output-tokens`, default
   150) since no usage data ships. Consider inferring it from the delta
   between a call's output (the `function_call` item) and what shows up
   in the *next* call's appended input items.
3. **Router itself**: the baseline is intentionally the "safe bet" —
   idea/01 in the deck. idea/02 ("Learned router") trains a classifier on
   a constructed outcome signal instead of a hand-tuned heuristic.

## Running it

```bash
# one-time: smoke-test with synthetic data (skip once the real dataset is in export/)
python scripts/generate_sample_data.py --out export/

python scripts/load_trajectories.py export/
python scripts/baseline_router.py export/
```

No GPU, no API key, no network access required. Everything above runs on
a laptop in well under a minute for a few thousand calls.

## Extending the baseline

- Swap `heuristic_route()` for a learned classifier (idea/02) — keep the
  same `(calls, ...) -> list[model_id]` signature so the cost/quality
  frontier code in `main()` keeps working unmodified.
- Replace `quality_proxy()` with a real signal, then re-plot the frontier
  — that's the special prize per the deck ("best off-policy-evaluation
  insight").
- If you add real usage/output data from your own judge calls, extend
  `common.Call` rather than bolting on parallel dicts.
