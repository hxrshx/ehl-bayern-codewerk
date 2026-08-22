#!/usr/bin/env python3
"""Rule-based router (Step 4).

Picks ONE model per task from a single price-ordered ladder spanning ALL models
in pricing.json (the router is allowed to cross claude/gpt families — confirmed
choice, not a family-scoped tier list). There is no mid-trajectory switching
logic here: the current export chunk has zero real multi-call trajectories
(every reconstructed trajectory is exactly one call — see load_trajectories.py),
so "route a task" means "pick the model for its one call."

Design: start from a BASE ladder index set by the opening task's stated intent
(quick/check vs analyze/critical keywords), then add/subtract named integer
STEP weights for each rule that fires on this call's features. All weights are
named constants below so they're easy to defend/tune. Every decision reports
which rules fired, for explainability.

This router is NOT constrained to match or beat the baseline on every single
task — Steps 5/evaluate.py compares the two empirically instead of enforcing it
structurally.
"""
import json
from pathlib import Path

PRICING = json.loads((Path(__file__).parent / "pricing.json").read_text())

def _price_of(model):
    return PRICING[model]

# Cheapest -> most expensive, ordered by (uncached_input_price, output_price, id)
# for a deterministic tie-break. Built from pricing.json directly so it always
# reflects whatever's actually in that file.
LADDER = sorted(
    (m for m in PRICING if m != "_default"),
    key=lambda m: (_price_of(m)[0], _price_of(m)[2], m),
)

def _index_of(model_id):
    return LADDER.index(model_id)

# ---- base index from the task's stated intent -------------------------------
BASE_INDEX_LOW_STAKES_KW = 0                          # "quick/draft/check..." -> start cheapest
BASE_INDEX_DEFAULT = _index_of("claude-sonnet-5")      # no strong signal either way
BASE_INDEX_HIGH_STAKES_KW = _index_of("claude-sonnet-4-6")  # "analyze/decide/approve/critical..."

# ---- escalate / de-escalate step weights (ladder positions, not $) ----------
STEP_TOOL_SURFACE_STAKES = 3   # a send/pay/delete/publish-style tool was actually called
STEP_FAILURE_FLAG = 2          # most recent tool result looks like an error
STEP_RETRY_LOOP_FLAG = 4       # same tool retried with similar args -> stuck, wants a strong model
STEP_REASONING_PRESENT = 1     # earlier turns in this task already needed deliberation
STEP_LARGE_INPUT = 1           # big accumulated context -> more for the model to track
STEP_RESUMED_FROM_WAIT = -1    # continuing after a background-wait tick is usually routine bookkeeping

LARGE_INPUT_TOKENS = 35_000    # est. tokens; ~85th percentile of this export's per-call input size
                                # (median is ~17k, so a lower cutoff flags "most tasks", not outliers —
                                # see feature_extraction.cumulative_input_tokens_est)

def route_call(features):
    """features: one row-dict as produced by feature_extraction.extract_features().
    Returns (model_id, triggered_rules: list[str])."""
    if features["high_stakes_kw_hit"] and not features["low_stakes_kw_hit"]:
        index, rules = BASE_INDEX_HIGH_STAKES_KW, ["base:high_stakes_keyword"]
    elif features["low_stakes_kw_hit"] and not features["high_stakes_kw_hit"]:
        index, rules = BASE_INDEX_LOW_STAKES_KW, ["base:low_stakes_keyword"]
    else:
        index, rules = BASE_INDEX_DEFAULT, ["base:default"]

    if features["tool_surface_stakes"]:
        index += STEP_TOOL_SURFACE_STAKES; rules.append("tool_surface_stakes")
    if features["failure_flag"]:
        index += STEP_FAILURE_FLAG; rules.append("failure_flag")
    if features["retry_loop_flag"]:
        index += STEP_RETRY_LOOP_FLAG; rules.append("retry_loop_flag")
    if features["reasoning_present"]:
        index += STEP_REASONING_PRESENT; rules.append("reasoning_present")
    if features["cumulative_input_tokens_est"] > LARGE_INPUT_TOKENS:
        index += STEP_LARGE_INPUT; rules.append("large_input")
    if features["phase_resumed_from_wait"]:
        index += STEP_RESUMED_FROM_WAIT; rules.append("resumed_from_wait")

    index = max(0, min(index, len(LADDER) - 1))
    return LADDER[index], rules

def route_trajectory(feature_rows):
    """feature_rows: ordered list of per-call feature dicts for one trajectory.
    Returns (route: list[model_id], rules_per_call: list[list[str]])."""
    route, rules_per_call = [], []
    for row in feature_rows:
        model, rules = route_call(row)
        route.append(model); rules_per_call.append(rules)
    return route, rules_per_call

if __name__ == "__main__":
    print(f"ladder ({len(LADDER)} models, cheapest -> priciest):")
    for i, m in enumerate(LADDER):
        pu, pc, po = _price_of(m)
        print(f"  [{i}] {m:20s} uncached_in=${pu} cached_in=${pc} out=${po}")
