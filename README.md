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

## Two findings behind those numbers

**We checked the rules against the data, not our intuition.** The first version of the
router's risky-tool list included *send* and *message*. It fired on **411 of 1,000 tasks** —
almost all of it the agent routinely posting results to Slack, which is not a risk signal.
Narrowing it to genuinely irreversible actions dropped that to **56**, and that single fix is
what took the router from *worse* than the starter baseline to better than it.

**The starter kit's own cost figure is slightly wrong, and we can show why.** Rebuilding it
gives $87.52 against its printed $87.42. The gap is a cache discount credited on **35,344
tokens that were never actually shared** between calls, caused by its 2,000-character
grouping hash. Leaving that discount in reproduces their figure exactly; removing it gives
ours. Separately, the kit prices input only — adding tool-definition tokens (**+19.6%**) and
output (**+50.1%**) is what takes the real corpus cost from $87.42 to **$155.23**.

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
