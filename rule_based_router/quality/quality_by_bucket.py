#!/usr/bin/env python3
"""Step 3: bucket-level cheap-vs-expensive quality comparison.

SCOPE — read this before citing any output from this file. We can only score
the ACTUAL response of the model that really served each task (Step 2). There
is no way to know what a DIFFERENT model would have produced for that same
task — no live LLM access, so no counterfactual response can ever be
generated in this project. The only evidence this file produces is an
aggregate, STATISTICAL comparison: within a difficulty bucket, do tasks that
HAPPENED to be served by a cheap-tier model score similarly (heuristically) to
tasks that HAPPENED to be served by an expensive-tier model?

This is:
  - a claim about a BUCKET (a group of tasks), never about any single task
  - entirely dependent on difficulty_bucket.py's bucketing actually capturing
    what makes a task hard (it's a token-size proxy, not true difficulty)
  - subject to confounding: whatever ELSE correlates with which model a task
    happened to get in the real log (which team/persona typically uses which
    model, etc.) also leaks into this comparison, since routing in the
    original log was not randomized
Never read a bucket's verdict as "a cheap model would ALSO do fine on any
ONE specific expensive-tier task in that bucket" — it is not that.
"""
import csv, sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from router import LADDER  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from quality_heuristics import compute_quality_scores  # noqa: E402

QUALITY_TOLERANCE = 0.05   # avg-quality gap within this -> "cheap safe here"
MIN_N_PER_TIER = 15        # below this many SCORED tasks on either side -> insufficient data

# Bottom half of the price ladder (router.py) = "cheap tier", top half = "expensive tier".
CHEAP_TIER_MODELS = set(LADDER[: len(LADDER) // 2])
EXPENSIVE_TIER_MODELS = set(LADDER[len(LADDER) // 2:])

def tier_of(model):
    return "cheap" if model in CHEAP_TIER_MODELS else "expensive"

def compute_bucket_table(export_dir):
    """Returns (rows, verdicts).
    rows: list of {bucket, model, tier, avg_quality_score_est, n_tasks, verdict}
          — only over SCORED tasks (has_response_text=True); unscored tasks
          are excluded from n_tasks/avg, not zero-filled.
    verdicts: {bucket: verdict string}, computed once per bucket from the
              cheap-tier vs expensive-tier aggregate within that bucket."""
    scored = compute_quality_scores(export_dir)

    by_bucket_model = defaultdict(list)
    for row in scored:
        if row["quality_score_est"] is None:
            continue
        by_bucket_model[(row["bucket"], row["model"])].append(row["quality_score_est"])

    by_bucket_tier = defaultdict(list)
    for (bucket, model), scores in by_bucket_model.items():
        by_bucket_tier[(bucket, tier_of(model))].extend(scores)

    verdicts = {}
    for bucket in ("low", "medium", "high"):
        cheap = by_bucket_tier.get((bucket, "cheap"), [])
        expensive = by_bucket_tier.get((bucket, "expensive"), [])
        if len(cheap) < MIN_N_PER_TIER or len(expensive) < MIN_N_PER_TIER:
            verdicts[bucket] = (f"insufficient data — below n={MIN_N_PER_TIER} threshold "
                                 f"(cheap n={len(cheap)}, expensive n={len(expensive)})")
            continue
        avg_cheap = sum(cheap) / len(cheap)
        avg_expensive = sum(expensive) / len(expensive)
        if abs(avg_cheap - avg_expensive) <= QUALITY_TOLERANCE:
            verdicts[bucket] = f"cheap safe here (avg cheap={avg_cheap:.3f} vs expensive={avg_expensive:.3f}, within tolerance {QUALITY_TOLERANCE})"
        elif avg_expensive > avg_cheap:
            verdicts[bucket] = f"expensive needed here (avg expensive={avg_expensive:.3f} vs cheap={avg_cheap:.3f}, gap > tolerance {QUALITY_TOLERANCE})"
        else:
            verdicts[bucket] = f"cheap safe here (cheap actually scored higher: {avg_cheap:.3f} vs expensive={avg_expensive:.3f})"

    rows = []
    for (bucket, model), scores in sorted(by_bucket_model.items()):
        rows.append({
            "bucket": bucket, "model": model, "tier": tier_of(model),
            "avg_quality_score_est": round(sum(scores) / len(scores), 3),
            "n_tasks": len(scores),
            "verdict": verdicts.get(bucket, "?"),
        })
    return rows, verdicts

def main():
    export = sys.argv[1] if len(sys.argv) > 1 else "export"
    rows, verdicts = compute_bucket_table(export)

    results_dir = Path(__file__).resolve().parent.parent / "results"
    results_dir.mkdir(exist_ok=True)
    out_path = results_dir / "quality_by_bucket.csv"
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["bucket", "model", "tier", "avg_quality_score_est", "n_tasks", "verdict"])
        w.writeheader(); w.writerows(rows)

    print(f"{'bucket':8s} {'model':20s} {'tier':10s} {'avg_q':>7s} {'n':>5s}  verdict")
    for r in rows:
        print(f"{r['bucket']:8s} {r['model']:20s} {r['tier']:10s} {r['avg_quality_score_est']:7.3f} {r['n_tasks']:5d}  {r['verdict']}")

    print()
    print("BUCKET-LEVEL VERDICTS — aggregate, statistical claims about a difficulty tier,")
    print("NOT per-task guarantees. See module docstring for confounds/limitations.")
    for b in ("low", "medium", "high"):
        print(f"  {b:8s}: {verdicts.get(b, '(no data)')}")
    print(f"\nwrote {out_path}")

if __name__ == "__main__": main()
