# Bayern CodeWerk — Viktor Challenge submission

**Members:** _FILL BEFORE SUBMITTING_

## Objective
Minimise **input-side cost per task while holding outcome quality**. Cost is the only axis this
export supports honestly: no timings ship, so latency is unmeasurable, and no quality labels ship,
so quality must be constructed — we treat it as a constraint, not a target.

## Routing signal
**Risk features, not prompt wording.** `rule_based_router` reads the tool signature, whether a
stakes tool was called, whether the last tool result was an error, and whether the agent is stuck
in a retry loop. It starts on a price-ordered ladder and moves up or down by named integer steps.
Every decision records which rules fired, so any single routing choice can be explained in one line.

## Headline result (cache-aware, estimated tokens, 95% bootstrap CIs)

| policy | cost | % of today | quality | 95% CI | rerouted |
|---|---|---|---|---|---|
| logged policy (today) | $155.23 | 100.0% | 100.00% | [99.48, 100.49] | — |
| **`rule_based_router`** | **$97.16** | **62.6%** | **99.30%** | **[98.98, 99.60]** | **775/1000** |
| *route everything cheap (no evidence)* | *$55.40* | *35.7%* | *98.67%* | *[98.21, 99.01]* | *699/1000* |
| *always the dearest model* | *$326.65* | *210.4%* | *100.57%* | *[100.26, 100.87]* | — |

**−37% of the bill at 99.3% of current outcome quality.** Two things make that credible rather than
merely large: the router still spreads work across **all nine models** (488 to `sonnet-5`, but also
38 to `opus-4-8`, 5 to `sol`, 4 to `fable-5`) and escalates where its risk rules fire; and paying for
the dearest model everywhere costs **2.1× the bill for +0.6% quality**.

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
