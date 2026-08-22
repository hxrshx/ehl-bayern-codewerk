#!/usr/bin/env python3
"""Baseline heuristic router (starter idea 01) + cache-aware cost report.

Policy: short-prompt calls early in a trajectory go to a cheaper sibling model;
everything else stays on the logged model. Deliberately simple — an honest floor.

Usage: python scripts/baseline_router.py export/   -> writes results/routes.jsonl
"""
import json, sys
from pathlib import Path
from load_trajectories import iter_requests, group_trajectories, est_tokens
from cost_model import trajectory_cost, logged_route, load_pricing

# Cheaper sibling per family, per scripts/pricing.json: fable is the premium
# (most expensive) claude tier, so the cheap claude sibling is sonnet; among the
# gpt-5.6 variants, luna is cheapest (sol > terra > luna).
CHEAP = {"claude": "claude-sonnet-5", "gpt": "gpt-5.6-luna"}

def cheap_for(model):
    return CHEAP["claude"] if model.startswith("claude") else CHEAP["gpt"]

SMALL_TRAJECTORY = 15_000  # est. input tokens; ~40% of real trajectories fall under this

def route_trajectory(calls):
    """Baseline route: send WHOLE small trajectories to the cheap sibling, keep the rest
    on the logged model. Whole-trajectory routing respects the one-model-per-trajectory
    premise and never pays the cache-reset penalty for a mid-task switch."""
    total = sum(est_tokens(c["input"]) for c in calls)
    if total < SMALL_TRAJECTORY:
        return [cheap_for(c["model"]) for c in calls]
    return [c["model"] for c in calls]

def main():
    export = sys.argv[1] if len(sys.argv) > 1 else "export"
    pricing = load_pricing()
    groups = group_trajectories(r for _, _, r in iter_requests(export))
    results_dir = Path(__file__).resolve().parent.parent / "results"
    results_dir.mkdir(exist_ok=True)
    out = open(results_dir / "routes.jsonl", "w")
    tot_logged = tot_routed = 0.0
    for key, calls in groups.items():
        logged = logged_route(calls); routed = route_trajectory(calls)
        c_logged, _ = trajectory_cost(calls, logged, pricing)
        c_routed, _ = trajectory_cost(calls, routed, pricing)
        tot_logged += c_logged; tot_routed += c_routed
        out.write(json.dumps({"trajectory": key, "n_calls": len(calls), "logged_model": logged[0],
                              "route": routed, "cost_logged_usd": round(c_logged, 6),
                              "cost_routed_usd": round(c_routed, 6),
                              "switches": sum(1 for i in range(1, len(routed)) if routed[i] != routed[i-1])}) + "\n")
    out.close()
    print(f"logged cost (est. input tokens, assumed prices):  ${tot_logged:,.4f}")
    print(f"routed cost:  ${tot_routed:,.4f}  ({(tot_routed/tot_logged-1):+.1%}, cache-aware)")
    print("NOTE: no outputs/usage in the export — token counts are estimates, output cost excluded,")
    print("and this baseline has NO outcome estimate. Constructing one is the challenge.")
    print(f"wrote {results_dir / 'routes.jsonl'}")

if __name__ == "__main__": main()
