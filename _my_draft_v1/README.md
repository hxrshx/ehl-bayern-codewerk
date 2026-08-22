# Model Router — baseline (TUM.ai x Viktor challenge)

Baseline implementation for the [Model Router challenge](https://zeta-labs-eb2cf9.viktor.page/tumai-challenge-deck):
train a router that picks the right model for each LLM call in a Viktor
agent trajectory, and prove it with a cache-aware, off-policy cost/quality
frontier.

The real dataset and starter kit are shared live at the hackathon kickoff
(Discord: `discord.gg/J5eNgaAT`) — this repo doesn't have network access to
fetch them, so it implements the pipeline the deck describes
(`scripts/load_trajectories.py`, `scripts/baseline_router.py`) plus a
synthetic data generator so you can run it end-to-end right now and just
drop the real files into `export/` when you have them.

## Quickstart

```bash
# 1. (only until the real dataset drops) generate synthetic sample data
python3 scripts/generate_sample_data.py --out export/

# 2. reconstruct trajectories from the raw per-call export
python3 scripts/load_trajectories.py export/

# 3. run the baseline heuristic router + cache-aware cost/quality frontier
python3 scripts/baseline_router.py export/
```

No GPU, no API key, no pip installs required (matplotlib is optional, for
the chart — `pip install matplotlib` if you want `export/baseline_frontier.png`).

## When the real dataset arrives

```bash
rm export/trajectories_v1_*.jsonl   # drop the synthetic sample data
tar xzf trajectories_v1_01.jsonl.tar.gz -C export/
python3 scripts/load_trajectories.py export/
python3 scripts/baseline_router.py export/
```

Everything else is unchanged — the loader and router don't know or care
whether the model ids are the synthetic ones or the real ~9 ids from the
dataset; `baseline_router.py` auto-detects whatever model ids show up and
adds a placeholder price for any it doesn't already have in
`config/model_tiers.json`.

## What you get

- `export/trajectories.json` — calls regrouped into ordered trajectories
  (no trajectory ids ship, so this is reconstructed by chaining each
  call's `input` as a strict prefix of the next call's `input` within the
  same task; see `common.reconstruct_trajectories`).
- `export/baseline_frontier.json` / `baseline_report.md` — the baseline
  heuristic router (idea/01: route by call position + prompt length),
  swept over an aggressiveness parameter, compared against the logged
  (as-ran) cost, a closed-form random-routing expectation, and an
  always-top-tier ceiling.
- `export/baseline_frontier.png` — the frontier chart, if matplotlib is
  installed.

See [AGENTS.md](AGENTS.md) for the file layout, the placeholders you're
expected to replace (there is no quality label in the data — that's the
actual challenge), and how to swap in a learned router without touching
the evaluation code.

## Layout

```
scripts/
  common.py                trajectory reconstruction, token estimation, pricing config I/O
  load_trajectories.py     export/*.jsonl -> export/trajectories.json
  baseline_router.py       heuristic router + cache-aware cost/quality frontier
  generate_sample_data.py  synthetic data for smoke-testing before kickoff
config/
  model_tiers.json         per-model pricing (edit with real numbers once known)
export/                    dataset goes here (gitignored-style scratch dir)
AGENTS.md                  brief for a coding agent extending the baseline
```
