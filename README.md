# Bayern CodeWerk — Viktor Challenge: Build the Router

**−37% of Viktor's logged bill at 99.3% of current outcome quality**, 95% CI [98.98, 99.60],
775 of 1,000 tasks rerouted, all nine models still in play.

| | cost | % of today | quality | 95% CI |
|---|---|---|---|---|
| logged policy (today) | $155.23 | 100.0% | 100.00% | [99.48, 100.49] |
| **`rule_based_router`** | **$97.16** | **62.6%** | **99.30%** | **[98.98, 99.60]** |
| *route everything cheap* | *$55.40* | *35.7%* | *98.67%* | *[98.21, 99.01]* |
| *always the dearest model* | *$326.65* | *210.4%* | *100.57%* | *[100.26, 100.87]* |

![frontier](evaluation/frontier.png)

| where | what |
|---|---|
| [`rule_based_router/`](rule_based_router/) | the router — risk-feature ladder, one reason code per decision |
| [`evaluation/`](evaluation/) | the off-policy evaluation — frontier, bootstrap CIs, named failure modes |
| [`submission/`](submission/) | manifest and pitch deck |
| [`baseline/`](baseline/) | the unmodified starter kit, kept for comparison |

**Reproduce** (offline, ~4 min, no GPU and no API keys):
```bash
tar xzf trajectories_v1_01.jsonl.tar.gz -C export/
./evaluation/run.sh export/
```

Full method and the six ways our estimate can fail: [`evaluation/EVALUATION.md`](evaluation/EVALUATION.md).
Submission manifest: [`submission/SUBMISSION.md`](submission/SUBMISSION.md).
Dataset is challenge-use only and is never committed.

---

# Viktor Challenge Starter — Build the Router

Starter kit for the **Viktor Challenge** at the TUM.ai hackathon (Munich, 22–23 Aug 2026).
From real LLM-request logs, build a router that picks the right model for every call —
then prove it works, even though the log shows only the model that ran, and no outputs or token counts.

## Quick start (5 minutes)

```bash
# 1. No dataset yet? Generate a synthetic sample with the same shape:
python scripts/make_synthetic_sample.py            # writes ./export/

# 2. Got the real dataset links (shipped at kickoff)? Then instead: the export ships
#    as trajectories_v1_<index>.jsonl.tar.gz archives — download, verify the posted
#    SHA-256, then:  mkdir -p export && tar xzf trajectories_v1_01.jsonl.tar.gz -C export/

# 3. Sanity-check the export, reconstruct trajectories, print stats:
python scripts/load_trajectories.py export/

# 4. Run the baseline heuristic router + cache-aware cost report:
python scripts/baseline_router.py export/

# 5. Turn results into a cost–quality frontier CSV (+ PNG if matplotlib is installed):
python scripts/plot_frontier.py results/routes.jsonl
```

Python 3.10+, standard library only (matplotlib optional for the PNG).

## Using a coding agent

Point Claude Code / Codex / Cursor / opencode at this repo — `AGENTS.md` briefs your agent.
In Claude Code you also get slash commands:

- `/setup` — set up everything needed to participate
- `/make-presentation` — build a Viktor-branded presentation of your solution
- `/prepare-submission` — package your solution into a formal submission

## What's here

| Path | What |
|---|---|
| `AGENTS.md` | Agent briefing: dataset shape, the cache trap, judging, starter ideas |
| `skills/` | The three guided workflows above (plain Markdown, readable by humans too) |
| `scripts/` | Loader + trajectory reconstruction, baseline router, cache-aware cost model (estimated tokens), frontier plot, synthetic sample |
| `templates/presentation.html` | Self-contained branded slide template |

## Rules that matter

- **License:** challenge use only — no redistribution of the dataset. Full terms ship with the download.
- No GPU or API keys needed. Judge-model rescoring is allowed (credits announced at kickoff).
- Questions → the challenge Discord; the Viktor team answers there all weekend.
