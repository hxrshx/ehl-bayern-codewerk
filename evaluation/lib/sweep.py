#!/usr/bin/env python3
"""The graded cost-quality frontier, with bootstrap confidence intervals.

Two things this fixes over a single-shot frontier:

1. THE ENVELOPE ARM IS SWEPT, NOT ALL-OR-NOTHING. Adopting "all of C3" in one step
   made the envelope a formality: fitted at p95 of ~20 observations, the GPT cap
   landed exactly on the corpus maximum and could never reject anything, so 91% of
   the headline came from a gate that filtered 4.3% of episodes. Sweeping the
   percentile makes the gate real and turns four kinked points into a curve where
   each point is an operating choice.

2. EVERY POINT CARRIES ERROR BARS. Viktor, Discord 16:25: "ship a number with error
   bars, not a number. A confidence interval plus the assumption that would break it
   outscores a bigger point estimate." Episode-level bootstrap, 400 resamples, on
   both axes.

Quality is the matched off-policy estimate: an adopted episode inherits the mean
score observed for the target model on the SAME job where that exists, else that
model's pool mean (weaker, and flagged as such in the honesty report).

Usage: python3 solution/sweep.py
"""
import json, random, statistics, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from cost2 import load_pricing, price_of, episode_cost, HEADLINE

QS = [0.50, 0.60, 0.75, 0.90]      # envelope tightness, strict -> loose
NBOOT = 400
R = Path("results")

def load(p): return [json.loads(l) for l in open(p)]

def q_hat_table(eps, qual):
    q = {d["row_id"]: d["q_score"] for d in qual}
    cell, pool = {}, {}
    for e in eps:
        jk = e.get("job_key") or ""
        if jk and not jk.startswith("interactive:"):
            cell.setdefault((jk, e["model"]), []).append(q[e["row_id"]])
        pool.setdefault(e["model"], []).append(q[e["row_id"]])
    return q, {k: statistics.fmean(v) for k, v in cell.items()}, \
              {k: statistics.fmean(v) for k, v in pool.items()}

def evaluate(eps, routes, q, cell, pool, pr, gen, keep):
    """keep(route_rec) -> adopt this reroute? Returns per-episode (cost, quality)."""
    by = {e["row_id"]: e for e in eps}
    out = []
    for r in routes:
        e = by[r["row_id"]]
        adopt = r["proposed"] != r["logged"] and keep(r)
        m = r["proposed"] if adopt else e["model"]
        c = episode_cost(e, [m] * e["n_requests"], HEADLINE, pr, gen)
        if not adopt: qq = q[r["row_id"]]
        else:
            jk = e.get("job_key") or ""
            qq = cell.get((jk, m), pool.get(m, q[r["row_id"]]))
        out.append((c, qq, adopt))
    return out

def boot(per, n=NBOOT, seed=20260822):
    rnd = random.Random(seed); N = len(per)
    cs, qs = [], []
    for _ in range(n):
        idx = [rnd.randrange(N) for _ in range(N)]
        cs.append(sum(per[i][0] for i in idx))
        qs.append(statistics.fmean(per[i][1] for i in idx))
    cs.sort(); qs.sort()
    lo, hi = int(.025 * n), int(.975 * n) - 1
    return (cs[lo], cs[hi]), (qs[lo], qs[hi])

def main():
    pr = load_pricing()
    eps = load(R / "episodes.jsonl")
    qual = load(R / "quality_v2.jsonl")
    gen = {d["row_id"]: d for d in load(R / "gen_tokens.jsonl")}
    q, cell, pool = q_hat_table(eps, qual)
    by = {e["row_id"]: e for e in eps}

    rows = []
    def add(label, routes, keep, note):
        per = evaluate(eps, routes, q, cell, pool, pr, gen, keep)
        cost = sum(p[0] for p in per); qq = statistics.fmean(p[1] for p in per)
        cov = sum(1 for p in per if p[2])
        (cl, ch), (ql, qh) = boot(per)
        rows.append({"point": label, "cost_usd": round(cost, 2),
                     "cost_lo": round(cl, 2), "cost_hi": round(ch, 2),
                     "quality": round(qq, 4), "q_lo": round(ql, 4), "q_hi": round(qh, 4),
                     "adopted": cov, "coverage_pct": round(cov / len(per) * 100, 1), "note": note})
        return rows[-1]

    base_routes = load(R / "routes.jsonl")
    logged = add("logged policy", base_routes, lambda r: False, "what Viktor actually paid")
    L = logged["cost_usd"]; LQ = logged["quality"]

    add("certified (C1+C2)", base_routes, lambda r: r["confidence"] in ("C1", "C2"),
        "matched same-job evidence only")

    for qq in QS:
        subprocess.run([sys.executable, "solution/router.py", "both", str(R / "episodes.jsonl"),
                        "--envelope-q", str(qq), "--policy", str(R / f"policy_q{int(qq*100)}.json"),
                        "--out", str(R / f"routes_q{int(qq*100)}.jsonl")],
                       capture_output=True, check=True)
        rs = load(R / f"routes_q{int(qq*100)}.jsonl")
        add(f"+ envelope p{int(qq*100)}", rs, lambda r: True, f"envelope gate at p{int(qq*100)}")

    # reference marks
    def const_route(pick):
        return [{"row_id": e["row_id"], "logged": e["model"], "proposed": pick(e),
                 "confidence": "ref"} for e in eps]
    cheap = {v: min({e["model"] for e in eps if e["vendor"] == v},
                    key=lambda m: price_of(m, pr)[0]) for v in {e["vendor"] for e in eps}}
    add("all-cheapest (degenerate)", const_route(lambda e: cheap[e["vendor"]]), lambda r: True,
        "no evidence — the loophole, priced")
    add("always frontier model", const_route(
        lambda e: max({x["model"] for x in eps if x["vendor"] == e["vendor"]},
                      key=lambda m: price_of(m, pr)[0])), lambda r: True, "the deck's ceiling anchor")

    for r in rows:
        r["cost_pct"] = round(r["cost_usd"] / L * 100, 1)
        r["quality_pct"] = round(r["quality"] / LQ * 100, 2)
        r["quality_pct_lo"] = round(r["q_lo"] / LQ * 100, 2)
        r["quality_pct_hi"] = round(r["q_hi"] / LQ * 100, 2)

    (R / "sweep.json").write_text(json.dumps(rows, indent=1))
    import csv
    with open(R / "frontier.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)

    print(f"{'point':26s} {'cost':>8s} {'%log':>6s} {'qual%':>7s}  {'95% CI':>16s} {'cov':>6s}")
    for r in rows:
        print(f"{r['point']:26s} {r['cost_usd']:8.2f} {r['cost_pct']:5.1f}% {r['quality_pct']:6.2f}% "
              f" [{r['quality_pct_lo']:6.2f},{r['quality_pct_hi']:6.2f}] {r['coverage_pct']:5.1f}%")
    return rows

if __name__ == "__main__": main()

def chart(rows):
    """The one chart. y = quality as % of logged, so 'parity' is a line you can see."""
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    V, N, PCH, GREY = "#5B3BE8", "#12132B", "#E8845A", "#9E99B8"
    curve=[r for r in rows if r["point"].startswith(("logged","certified","+ envelope"))]
    deg=[r for r in rows if "degenerate" in r["point"]][0]
    top=[r for r in rows if "always frontier" in r["point"]][0]
    fig,ax=plt.subplots(figsize=(12,7),dpi=150)
    ax.axhline(100,ls="--",lw=1.4,color=GREY,zorder=1)
    ax.text(2,100.12,"parity with what Viktor runs today",fontsize=11,color=GREY,va="bottom")
    xs=[r["cost_usd"] for r in curve]; ys=[r["quality_pct"] for r in curve]
    lo=[r["quality_pct"]-r["quality_pct_lo"] for r in curve]; hi=[r["quality_pct_hi"]-r["quality_pct"] for r in curve]
    ax.errorbar(xs,ys,yerr=[lo,hi],fmt="-o",color=V,lw=3,ms=9,capsize=5,elinewidth=1.6,
                zorder=5,label="our router  (envelope gate swept)")
    ax.scatter([deg["cost_usd"]],[deg["quality_pct"]],marker="X",s=190,color=PCH,
               edgecolor=N,lw=1.2,zorder=6,label="route everything cheap  (no evidence)")
    ax.annotate(f"${deg['cost_usd']:.0f} · {deg['quality_pct']:.1f}%",(deg["cost_usd"],deg["quality_pct"]),
                xytext=(0,-26),textcoords="offset points",ha="center",fontsize=11,color=PCH,fontweight="bold")
    cert=[r for r in curve if r["point"].startswith("certified")][0]
    ax.annotate(f"CERTIFIED  −{100-cert['cost_pct']:.0f}%  at parity\nmatched same-job evidence",
                (cert["cost_usd"],cert["quality_pct"]),xytext=(-18,46),textcoords="offset points",
                fontsize=13,fontweight="bold",color=V,ha="right",
                arrowprops=dict(arrowstyle="-",color=V,lw=1.4))
    p90=[r for r in curve if "p90" in r["point"]][0]
    ax.annotate(f"envelope arm  −{100-p90['cost_pct']:.0f}%  at {p90['quality_pct']:.1f}%\nweaker evidence — and our estimator\ncannot separate it from the X",
                (p90["cost_usd"],p90["quality_pct"]),xytext=(34,-92),textcoords="offset points",
                fontsize=12,color=N,arrowprops=dict(arrowstyle="-",color=N,lw=1.2))
    ax.annotate(f"always the frontier model → ${top['cost_usd']:.0f}  ({top['cost_pct']:.0f}% of logged)\nfor +{top['quality_pct']-100:.1f}% quality",
                (0.985,0.055),xycoords="axes fraction",ha="right",fontsize=11.5,color=PCH,fontweight="bold")
    ax.set_xlim(0,175); ax.set_ylim(96.5,101.5)
    ax.set_xlabel("cost for the whole corpus, USD   (cache-aware · tools + output included · estimated tokens)",fontsize=13)
    ax.set_ylabel("outcome quality, % of logged",fontsize=13)
    ax.set_title("Adding per-task routing to Viktor: what each dollar of saving is backed by",
                 fontsize=17,color=N,fontweight="bold",pad=14)
    ax.tick_params(labelsize=12); ax.grid(alpha=.2)
    ax.legend(fontsize=12,loc="lower left",framealpha=.96)
    ax.text(.005,-.135,"Bars are 95% bootstrap CIs over episodes (400 resamples). Quality is a constructed "
            "failure-counter signal, not a label; tokens are estimates.",
            transform=ax.transAxes,fontsize=9.5,color="#666")
    fig.tight_layout(); fig.savefig("results/frontier.png",bbox_inches="tight")
    print("wrote results/frontier.png")

if __name__ == "__main__":
    pass
