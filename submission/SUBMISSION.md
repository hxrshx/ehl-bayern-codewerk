# Bayern CodeWerk — Viktor Challenge submission

**Members:** Riya Biju, Harsha Sathish, Pavin Sumathi Palanichamy, Prethebha Muthukumaran, Rohan Sanjay Patil

## Objective
Minimise **input-side cost per task while holding outcome quality**. Cost is the only axis this
export supports honestly: no timings ship, so latency is unmeasurable, and no quality labels ship,
so quality must be constructed — we treat it as a constraint, not a target.

## Routing signal
**Risk features, not prompt wording.** `rule_based_router` reads the tool signature, whether a
stakes tool was called, whether the last tool result was an error, and whether the agent is stuck
in a retry loop. It starts on a price-ordered ladder and moves up or down by named integer steps.
Every decision records which rules fired, so any single routing choice can be explained in one line.

## Headline result — validated on a held-out chunk

Chunk 02 was released on the final morning. The policy was **fitted on chunk 01 and applied
cold**; nothing was re-tuned. Costs are cache-aware and include tool-definition and output
tokens. 95% bootstrap CIs, 400 resamples, seeded.

| policy | build $ | build % | build q | **held-out $** | **held-out %** | **held-out q** | held-out 95% CI |
|---|---|---|---|---|---|---|---|
| logged (today) | $155.23 | 100.0% | 100.0% | $139.32 | 100.0% | 100.0% | [99.48, 100.51] |
| starter `baseline_router` | $136.53 | 88.0% | 98.93% | $121.35 | 87.1% | 99.36% | [98.88, 99.85] |
| **`rule_based_router`** | $97.16 | 62.6% | 99.3% | $90.29 | 64.8% | 98.83% | [98.41, 99.23] |
| *route everything cheap* | *$55.4* | *35.7%* | *98.67%* | *$49.49* | *35.5%* | *99.65%* | *[99.35, 99.96]* |
| *always the dearest* | *$326.65* | *210.4%* | *100.57%* | *$295.57* | *212.2%* | *100.73%* | *[100.47, 100.93]* |

**What generalised: the cost cut.** 62.6% → 64.8% of the logged bill on data the router had
never seen, rerouting 744 of 1,000 tasks and still using all nine models. That is the claim
we stand behind.

**What did not: the quality win.** On the build chunk our router scored +0.36pp above the
starter baseline, but the interval [-0.20, +0.93] spans zero, so that was never a real
difference. On held-out data it is **-0.53pp, CI [-0.90, -0.13]** — excludes zero, so the baseline
is genuinely better on quality by about half a point while costing 22 points more of the bill.

**And the awkward one we are not hiding:** on held-out data our own estimator scores
*route everything cheap* at 99.65% — above both us and the baseline. We do not believe that
is true. It is selection bias: the cheap models' observed quality reflects the jobs the
operator was willing to give them. But we cannot prove that with this data, so we do not
claim a quality win at all. **The claim is a large, reproducible cost reduction with an
estimator whose failure modes we can name.**

## Cost model
The starter kit prices input only and omits tool definitions. Every call ships ~4,200 tokens of tool
manuals and is billed for them (**+19.6%**), and output is billed at up to 5× the input rate
(**+50.1%**). True logged spend is **$155.23**, not $87.42 — the kit understates by 44%, so all
percentages here are of the correct, larger denominator. Rebuilding the kit's own figure also gives
$87.5194 against its printed $87.4229: a cache discount credited on 35,344 tokens that were never
shared, caused by its 2,000-character grouping hash.

## Off-policy method
Matched same-job comparison. Viktor does not route per task — a human sets one model per workspace —
so recurring cron jobs drifted across price tiers on their own: **90 job families, 69 on two or more
tiers.** Same job, different model, difficulty held fixed by construction rather than by a similarity
score. Quality is a constructed signal from objective failure counters over 10,422 tool calls,
hand-checked against 12 episodes and **revised when the check showed it was wrong** (189 of 294 error
tasks recover; v1 punished recovery like failure). The conclusion is unchanged under either version.

**Weakest point:** our CI [98.98, 99.60] overlaps route-everything-cheap [98.21, 99.01]. We **cannot**
claim to beat the loophole on measured quality. What separates them is that this router keeps all nine
models in play and escalates on risk — a design argument, not a measured one. Separately, a fluent,
confident, wrong answer with no failed tool call scores perfectly; that bounds every quality claim here.
Six named failure modes in `evaluation/EVALUATION.md`.

## Assumptions
- **Pricing:** the organiser-pinned sheet.
- **Unit:** one trajectory per row, no prefix grouping, per the organisers' Discord ruling — verified
  independently (no row's input is a prefix of another's).
- **Tokens:** estimates. Real BPE and chars/4 differ ±15% per task with a vendor-correlated bias, so
  cross-vendor savings are the least trustworthy figures here.
- **One dataset, no held-out split** (organiser-confirmed): fit and evaluation share the same 1,000 tasks.

## Reproduce
```bash
tar xzf trajectories_v1_01.jsonl.tar.gz -C export/   # dataset never committed
./evaluation/run.sh export/                          # ~4 min, offline, no API keys
python3 evaluation/chart.py
```
