#!/usr/bin/env python3
"""Cost–quality frontier from results/routes.jsonl.

Quality here is a PLACEHOLDER (fraction of calls kept on the logged model) so the
pipeline runs end to end. Replace `quality()` with your constructed outcome signal —
that replacement is the actual challenge.

Usage: python scripts/plot_frontier.py results/routes.jsonl
Writes results/frontier.csv (+ frontier.png if matplotlib is installed).
"""
import json, sys, csv
from pathlib import Path

def quality(rec):  # PLACEHOLDER — replace with your outcome signal
    kept = sum(1 for i, m in enumerate(rec["route"]) if m == rec["logged_model"])
    return kept / len(rec["route"])

def main():
    default_src = Path(__file__).resolve().parent.parent / "results" / "routes.jsonl"
    src = sys.argv[1] if len(sys.argv) > 1 else str(default_src)
    recs = [json.loads(l) for l in open(src)]
    total_calls = sum(r["n_calls"] for r in recs)
    # Sweep how much of the router's proposal to adopt: adopt for the cheapest X% of
    # trajectories first (they are the safest bets), keep the rest on the logged model.
    by_size = sorted(recs, key=lambda r: r["cost_logged_usd"])
    rows = []
    for frac in [i / 10 for i in range(0, 11)]:
        adopt = set(id(r) for r in by_size[: int(round(frac * len(recs)))])
        cost = sum(r["cost_routed_usd"] if id(r) in adopt else r["cost_logged_usd"] for r in recs)
        kept = sum((quality(r) if id(r) in adopt else 1.0) * r["n_calls"] for r in recs)
        rows.append({"adopt_frac": frac, "cost_usd": round(cost, 4),
                     "quality_placeholder": round(kept / total_calls, 3)})
    out = Path(src).parent / "frontier.csv"
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
    print(f"wrote {out}")
    for r in rows: print(r)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        plt.figure(figsize=(6, 4))
        plt.plot([r["cost_usd"] for r in rows], [r["quality_placeholder"] for r in rows], "o-", color="#6748FD")
        plt.xlabel("cost (USD, estimated tokens, cache-aware)"); plt.ylabel("quality (placeholder)")
        plt.title("Cost–quality frontier"); plt.tight_layout()
        png = Path(src).parent / "frontier.png"; plt.savefig(png, dpi=150)
        print(f"wrote {png}")
    except ImportError:
        print("matplotlib not installed — skipped PNG (CSV is enough)")

if __name__ == "__main__": main()
