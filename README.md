# Bayern CodeWerk — Viktor Challenge: Build the Router

**−35% of Viktor's logged bill on a held-out chunk we had never seen**, rerouting 744 of
1,000 tasks across all nine models — with an honest account of what our numbers will not support.

| policy | build $ | build % | **held-out $** | **held-out %** | **held-out quality** | 95% CI |
|---|---|---|---|---|---|---|
| logged (today) | $155.23 | 100.0% | $139.32 | 100.0% | 100.00% | — |
| starter `baseline_router` | $136.53 | 88.0% | $121.35 | 87.1% | 99.36% | [98.88, 99.85] |
| **`rule_based_router`** | **$97.16** | **62.6%** | **$90.29** | **64.8%** | **98.83%** | [98.41, 99.23] |
| *route everything cheap* | *$55.40* | *35.7%* | *$49.49* | *35.5%* | *99.65%* | *[99.35, 99.96]* |
| *always the dearest* | *$326.65* | *210.4%* | *$295.57* | *212.2%* | *100.73%* | *[100.47, 100.93]* |

Chunk 02 was released on the final morning. The policy was **fitted on chunk 01 and applied
cold** — nothing re-tuned. Costs are cache-aware and include tool-definition and output tokens.

**The cost cut generalised** (62.6% → 64.8%). **The quality win did not**: paired on the same
tasks the baseline is 0.53pp better held-out, CI [−0.90, −0.13], which excludes zero. And our
own estimator ranks *route everything cheap* above both — we believe that is selection bias in
the cheap tier's observed quality, but we cannot prove it here, so **we claim a cost reduction,
not a quality win.**

**The cost–quality frontier**, priced cache-aware, with 95% bootstrap confidence intervals:

![cost–quality frontier](evaluation/frontier.png)

| where | what |
|---|---|
| [`rule_based_router/`](rule_based_router/) | the router — risk-feature ladder, one reason code per decision |
| [`evaluation/`](evaluation/) | the **off-policy evaluation** — frontier, bootstrap CIs, named failure modes |
| [`submission/`](submission/) | manifest and pitch deck |
| [`baseline/`](baseline/) | the unmodified starter kit, kept for comparison |

**Reproduce** (offline, ~4 min, no GPU and no API keys):
```bash
tar xzf trajectories_v1_01.jsonl.tar.gz -C export/
./evaluation/run.sh export/
```

Full method and the ways our estimate can fail: [`evaluation/EVALUATION.md`](evaluation/EVALUATION.md).
Submission manifest: [`submission/SUBMISSION.md`](submission/SUBMISSION.md).
Dataset is challenge-use only and is never committed.

## About `baseline/`

`baseline/` holds the unmodified Viktor starter kit exactly as shipped, so its numbers can be
reproduced and compared against. Its original README is preserved at `baseline/README.md`.
