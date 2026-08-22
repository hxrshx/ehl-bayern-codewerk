#!/usr/bin/env python3
"""Pairs quality_by_bucket.py's evidence with actual $ cost, for charting.

SCOPE: cost here is the REAL logged cost of the model that actually served
each scored task (import cost_model, route=[logged model]) — not a
hypothetical reroute. This is the only cost figure that can be honestly paired
with a quality_score_est, since quality was only ever measured for the model
that actually ran (see quality_heuristics.py's scope note — no counterfactual
generation is possible in this project). Do not read a bucket's avg cost as
"what our router would have paid for these tasks."

Outputs one row per (bucket, tier): avg_cost_usd_per_task, avg_quality_score_est,
n_tasks_scored — the same aggregate/statistical scope as quality_by_bucket.py.

Usage: python rule_based_router/quality/quality_vs_cost.py export/
"""
import csv, json, sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from load_trajectories import iter_requests, group_trajectories  # noqa: E402
from cost_model import trajectory_cost, load_pricing  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from quality_heuristics import compute_quality_scores  # noqa: E402
from quality_by_bucket import tier_of  # noqa: E402

def main():
    export = sys.argv[1] if len(sys.argv) > 1 else "export"
    pricing = load_pricing()
    groups = group_trajectories(r for _, _, r in iter_requests(export))
    scored = compute_quality_scores(export)

    cost_by_traj = {}
    for key, calls in groups.items():
        usd, _ = trajectory_cost(calls, [c["model"] for c in calls], pricing)
        cost_by_traj[key] = usd

    by_bucket_tier = defaultdict(lambda: {"cost": [], "quality": []})
    for row in scored:
        if row["quality_score_est"] is None:
            continue
        key = (row["bucket"], tier_of(row["model"]))
        by_bucket_tier[key]["cost"].append(cost_by_traj[row["trajectory_id"]])
        by_bucket_tier[key]["quality"].append(row["quality_score_est"])

    out_rows = []
    for (bucket, tier), d in sorted(by_bucket_tier.items()):
        n = len(d["cost"])
        out_rows.append({
            "bucket": bucket, "tier": tier, "n_tasks": n,
            "avg_cost_usd_per_task": round(sum(d["cost"]) / n, 5),
            "avg_quality_score_est": round(sum(d["quality"]) / n, 3),
        })

    results_dir = Path(__file__).resolve().parent.parent / "results"
    out_path = results_dir / "quality_vs_cost.csv"
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["bucket", "tier", "n_tasks", "avg_cost_usd_per_task", "avg_quality_score_est"])
        w.writeheader(); w.writerows(out_rows)

    print(f"{'bucket':8s} {'tier':10s} {'n':>5s} {'avg_cost/task':>14s} {'avg_quality':>12s}")
    for r in out_rows:
        print(f"{r['bucket']:8s} {r['tier']:10s} {r['n_tasks']:5d} {r['avg_cost_usd_per_task']:14.5f} {r['avg_quality_score_est']:12.3f}")
    print(f"\nwrote {out_path}")

if __name__ == "__main__": main()
