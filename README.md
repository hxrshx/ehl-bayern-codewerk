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

## About `baseline/`

`baseline/` holds the unmodified Viktor starter kit (loader, cache-aware cost model,
baseline router, frontier plot) exactly as shipped, so its numbers can be reproduced
and compared against. Its original README is preserved at `baseline/README.md`.
