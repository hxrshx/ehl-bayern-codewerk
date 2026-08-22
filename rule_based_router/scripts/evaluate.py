#!/usr/bin/env python3
"""Step 5: compare cost of logged / baseline / rule-based-router on the same tasks.

Empirical comparison only — the rule-based router is NOT architecturally forced
to beat or match the baseline on every task (that was a deliberate choice: no
per-task guarantee, see conversation). This script just runs all three policies
over every reconstructed task and reports the totals plus how many individual
tasks came out cheaper/equal/worse, so the "ours <= baseline" claim is measured,
not assumed.

BASELINE_CHEAP/BASELINE_SMALL_TRAJECTORY below are a frozen SNAPSHOT of
baseline/scripts/baseline_router.py's policy (copied, not imported, so this
folder stays self-contained) — keep them in sync if the baseline changes.

Cost-only comparison: no output tokens (unknowable, no usage field in the
export) and no quality/outcome signal (that's the separate, harder off-policy
evaluation problem named in AGENTS.md — not attempted here).

Usage: python rule_based_router/scripts/evaluate.py export/
"""
import json, sys
from pathlib import Path
from load_trajectories import iter_requests, group_trajectories, est_tokens
from cost_model import trajectory_cost, logged_route, load_pricing
from feature_extraction import extract_features
from router import route_trajectory as rule_router_route

BASELINE_CHEAP = {"claude": "claude-sonnet-5", "gpt": "gpt-5.6-luna"}
BASELINE_SMALL_TRAJECTORY = 15_000

def _baseline_cheap_for(model):
    return BASELINE_CHEAP["claude"] if model.startswith("claude") else BASELINE_CHEAP["gpt"]

def baseline_route_trajectory(calls):
    total = sum(est_tokens(c["input"]) for c in calls)
    if total < BASELINE_SMALL_TRAJECTORY:
        return [_baseline_cheap_for(c["model"]) for c in calls]
    return [c["model"] for c in calls]

def main():
    export = sys.argv[1] if len(sys.argv) > 1 else "export"
    pricing = load_pricing()
    groups = group_trajectories(r for _, _, r in iter_requests(export))

    results_dir = Path(__file__).resolve().parent.parent / "results"
    results_dir.mkdir(exist_ok=True)
    out = open(results_dir / "comparison.jsonl", "w")

    tot_logged = tot_baseline = tot_ours = 0.0
    n_ours_le_baseline = n_ours_lt_baseline = 0
    n = 0

    for key, calls in groups.items():
        logged = logged_route(calls)
        baseline = baseline_route_trajectory(calls)
        feats = extract_features(key, calls)
        ours, rules_per_call = rule_router_route(feats)

        c_logged, _ = trajectory_cost(calls, logged, pricing)
        c_baseline, _ = trajectory_cost(calls, baseline, pricing)
        c_ours, _ = trajectory_cost(calls, ours, pricing)

        tot_logged += c_logged; tot_baseline += c_baseline; tot_ours += c_ours
        n += 1
        if c_ours <= c_baseline + 1e-12: n_ours_le_baseline += 1
        if c_ours < c_baseline - 1e-12: n_ours_lt_baseline += 1

        out.write(json.dumps({
            "trajectory": key, "n_calls": len(calls),
            "model_logged": logged[0], "baseline_route": baseline, "our_route": ours,
            "rules_triggered": rules_per_call,
            "cost_logged_usd": round(c_logged, 6),
            "cost_baseline_usd": round(c_baseline, 6),
            "cost_ours_usd": round(c_ours, 6),
        }) + "\n")

    out.close()
    print(f"tasks compared: {n}")
    print(f"total logged cost   (as-run, est. tokens):         ${tot_logged:,.4f}")
    print(f"total baseline cost (whole-trajectory heuristic):  ${tot_baseline:,.4f}  ({(tot_baseline/tot_logged-1):+.1%} vs logged)")
    print(f"total our-router cost (rule-based):                ${tot_ours:,.4f}  ({(tot_ours/tot_logged-1):+.1%} vs logged, {(tot_ours/tot_baseline-1):+.1%} vs baseline)")
    print(f"tasks where ours <= baseline: {n_ours_le_baseline}/{n} ({n_ours_le_baseline/n:.1%})  [empirical, not a structural guarantee]")
    print(f"tasks where ours <  baseline: {n_ours_lt_baseline}/{n} ({n_ours_lt_baseline/n:.1%})")
    print("NOTE: no outputs/usage in the export — token counts are estimates, output cost excluded.")
    print("NOTE: quality/outcome is NOT evaluated here — this is a cost-only comparison; see AGENTS.md's")
    print("      off-policy evaluation challenge for why quality is the harder, unresolved half.")
    print(f"wrote {results_dir / 'comparison.jsonl'}")

if __name__ == "__main__": main()
