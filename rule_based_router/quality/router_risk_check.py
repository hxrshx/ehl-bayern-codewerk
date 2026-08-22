#!/usr/bin/env python3
"""Step 4: flag router calls that picked cheap in a bucket verdict says needs expensive.

Reads router.py's ACTUAL routing decisions from the already-computed
rule_based_router/results/comparison.jsonl (produced by evaluate.py) rather
than recomputing them, and cross-references against quality_by_bucket.py's
bucket verdicts (Step 3) to find the router's riskiest calls: tasks in a
bucket flagged "expensive needed here" where the router picked a cheap-tier
model anyway.

SCOPE — this is downstream of quality_by_bucket.py's aggregate/statistical
verdicts. It does NOT mean these specific tasks are proven to get worse
answers under our router (we never scored what the router's chosen model
WOULD have produced — see quality_heuristics.py's scope note; no
counterfactual generation is possible here). It means: this task sits in a
difficulty bucket where, empirically, expensive-tier responses scored better
than cheap-tier ones on the ACTUAL served models in this dataset, and the
router chose cheap anyway — a list worth a human looking at, not a
proven-bad list.
"""
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from difficulty_bucket import bucket_for_all_tasks  # noqa: E402
from quality_by_bucket import compute_bucket_table, tier_of  # noqa: E402

COMPARISON_PATH = Path(__file__).resolve().parent.parent / "results" / "comparison.jsonl"

def main():
    export = sys.argv[1] if len(sys.argv) > 1 else "export"
    if not COMPARISON_PATH.exists():
        sys.exit(f"{COMPARISON_PATH} not found — run rule_based_router/scripts/evaluate.py first "
                  f"(router_risk_check.py reads its output rather than recomputing routing decisions).")

    _, verdicts = compute_bucket_table(export)
    buckets = bucket_for_all_tasks(export)

    comparisons = [json.loads(l) for l in open(COMPARISON_PATH)]
    risky = []
    for rec in comparisons:
        key = rec["trajectory"]
        our_model = rec["our_route"][0]
        bucket = buckets.get(key)
        verdict = verdicts.get(bucket, "")
        if tier_of(our_model) == "cheap" and verdict.startswith("expensive needed here"):
            risky.append({
                "trajectory_id": key, "bucket": bucket, "bucket_verdict": verdict,
                "logged_model": rec["model_logged"], "baseline_route": rec["baseline_route"][0],
                "our_route": our_model,
            })

    results_dir = Path(__file__).resolve().parent.parent / "results"
    out_path = results_dir / "router_risk_flags.jsonl"
    with open(out_path, "w") as f:
        for r in risky: f.write(json.dumps(r) + "\n")

    print(f"tasks compared: {len(comparisons)}")
    print("bucket verdicts used:")
    for b, v in verdicts.items():
        print(f"  {b:8s}: {v}")
    print(f"\nrouter calls flagged risky (cheap pick in an 'expensive needed here' bucket): {len(risky)}")
    for r in risky[:20]:
        print(f"  {r['trajectory_id']}  bucket={r['bucket']}  our_route={r['our_route']}  logged={r['logged_model']}")
    if len(risky) > 20:
        print(f"  ... and {len(risky) - 20} more, see {out_path}")

    print()
    print("SCOPE: this is a list worth human review, not proof any specific task got a worse")
    print("answer — we never scored what the router's chosen model would have produced (no")
    print("counterfactual generation is possible here). See quality_heuristics.py's scope note.")
    print(f"wrote {out_path}")

if __name__ == "__main__": main()
