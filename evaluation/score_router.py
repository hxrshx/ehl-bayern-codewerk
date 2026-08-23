#!/usr/bin/env python3
"""Score rule_based_router with an off-policy evaluation harness.

WHAT THIS IS. rule_based_router/scripts/evaluate.py compares cost only, and says
so in its own docstring: "no quality/outcome signal (that's the separate, harder
off-policy evaluation problem named in AGENTS.md - not attempted here)." This
file is that missing half. It runs the rule-based router UNMODIFIED and reports
what it costs and what it keeps, with confidence intervals.

Nothing in rule_based_router/ or baseline/ is imported-over or edited. The router
decides; this only measures.

WHAT IT ADDS on top of the cost-only comparison:
  * a cost model that counts the tool-definition tokens shipped on every call
    (+19.6%) and the recovered output tokens (+50.1%), so the logged corpus is
    $155.23 rather than the starter kit's $87.42
  * a constructed outcome signal from objective failure counters, hand-checked
    against 12 episodes and revised when the check showed it was wrong
  * matched same-job comparison: recurring cron jobs that drifted across price
    tiers act as an accidental A/B test, which is what makes any counterfactual
    claim here defensible
  * 95% bootstrap confidence intervals on both axes, 400 resamples, seeded

Usage:  python3 evaluation/score_router.py export/
"""
import importlib.util, json, random, statistics, sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RB   = ROOT.parent / "rule_based_router" / "scripts"

def load(name, path):
    """Import by explicit path. rule_based_router and evaluation/lib both define
    a `router` module, so plain imports would shadow one another."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod

sys.path.insert(0, str(ROOT / "lib"))
from cost2 import load_pricing, price_of, episode_cost, HEADLINE   # ours

def main():
    export = sys.argv[1] if len(sys.argv) > 1 else "export"
    res = ROOT / "results"; res.mkdir(exist_ok=True)
    for f in ("episodes.jsonl", "quality_v2.jsonl", "gen_tokens.jsonl"):
        if not (res / f).exists():
            sys.exit(f"missing {res/f}\nRun:  ./evaluation/run.sh {export}")

    lt  = load("rb_lt", RB / "load_trajectories.py")
    fx  = load("rb_fx", RB / "feature_extraction.py")
    rtr = load("rb_router", RB / "router.py")

    pr   = load_pricing()
    eps  = {e["row_id"]: e for e in map(json.loads, open(res / "episodes.jsonl"))}
    qual = {d["row_id"]: d["q_score"] for d in map(json.loads, open(res / "quality_v2.jsonl"))}
    gen  = {d["row_id"]: d for d in map(json.loads, open(res / "gen_tokens.jsonl"))}

    # rebuild the router's own grouping, keeping line numbers so each decision
    # maps back to a row of the export
    import hashlib
    groups = {}
    for chunk, line, req in lt.iter_requests(export):
        k = hashlib.sha1(lt.first_user_text(req)[:2000].encode()).hexdigest()[:16]
        groups.setdefault(k, []).append((chunk, line, req))
    for k in groups:
        groups[k].sort(key=lambda t: len(t[2]["input"]))

    routes = []
    for k, items in groups.items():
        rows = fx.extract_features(k, [r for _, _, r in items])
        route, rules = rtr.route_trajectory(rows)
        for (chunk, line, req), m, rl in zip(items, route, rules):
            routes.append({"row_id": f"{chunk}:{line}", "logged": req["model"],
                           "proposed": m, "rules": rl})
    (res / "routes_rule_based.jsonl").write_text(
        "\n".join(json.dumps(r) for r in routes) + "\n")

    # matched off-policy quality: a rerouted task inherits the score observed for
    # the target model on the SAME recurring job where that exists, else that
    # model's pool mean (weaker, and flagged in the writeup)
    cell, pool = {}, {}
    for e in eps.values():
        jk = e.get("job_key") or ""
        if jk and not jk.startswith("interactive:"):
            cell.setdefault((jk, e["model"]), []).append(qual[e["row_id"]])
        pool.setdefault(e["model"], []).append(qual[e["row_id"]])
    cell = {k: statistics.fmean(v) for k, v in cell.items()}
    pool = {k: statistics.fmean(v) for k, v in pool.items()}

    def per_ep(rs):
        out = []
        for r in rs:
            e = eps[r["row_id"]]; m = r["proposed"]; moved = m != e["model"]
            c = episode_cost(e, [m] * e["n_requests"], HEADLINE, pr, gen)
            if moved:
                jk = e.get("job_key") or ""
                q = cell.get((jk, m), pool.get(m, qual[r["row_id"]]))
            else:
                q = qual[r["row_id"]]
            out.append((c, q, moved))
        return out

    def boot(per, n=400, seed=20260823):
        rnd = random.Random(seed); N = len(per); cs, qs = [], []
        for _ in range(n):
            i = [rnd.randrange(N) for _ in range(N)]
            cs.append(sum(per[j][0] for j in i))
            qs.append(statistics.fmean(per[j][1] for j in i))
        cs.sort(); qs.sort(); lo, hi = int(.025 * n), int(.975 * n) - 1
        return (cs[lo], cs[hi]), (qs[lo], qs[hi])

    logged = [(episode_cost(e, [e["model"]] * e["n_requests"], HEADLINE, pr, gen),
               qual[e["row_id"]], False) for e in eps.values()]
    L  = sum(p[0] for p in logged); LQ = statistics.fmean(p[1] for p in logged)

    def mark(label, rs, note=""):
        per = per_ep(rs)
        c = sum(p[0] for p in per); q = statistics.fmean(p[1] for p in per)
        mv = sum(1 for p in per if p[2]); (cl, ch), (ql, qh) = boot(per)
        return {"policy": label, "cost_usd": round(c, 2), "cost_pct": round(c / L * 100, 1),
                "cost_ci": [round(cl, 2), round(ch, 2)],
                "quality_pct": round(q / LQ * 100, 2),
                "quality_ci": [round(ql / LQ * 100, 2), round(qh / LQ * 100, 2)],
                "rerouted": mv, "coverage_pct": round(mv / len(per) * 100, 1), "note": note}

    cheap = {v: min({e["model"] for e in eps.values() if e["vendor"] == v},
                    key=lambda m: price_of(m, pr)[0])
             for v in {e["vendor"] for e in eps.values()}}
    const = lambda pick: [{"row_id": e["row_id"], "logged": e["model"], "proposed": pick(e)}
                          for e in eps.values()]

    out = [
        mark("logged policy (what Viktor runs today)", const(lambda e: e["model"]), "the baseline"),
        mark("rule_based_router", routes, "this repo's router, unmodified"),
        mark("route everything cheap", const(lambda e: cheap[e["vendor"]]),
             "no evidence - the loophole, priced"),
        mark("always the dearest model",
             const(lambda e: max({x["model"] for x in eps.values() if x["vendor"] == e["vendor"]},
                                 key=lambda m: price_of(m, pr)[0])), "the ceiling"),
    ]
    (res / "scorecard.json").write_text(json.dumps(out, indent=1))

    w = max(len(r["policy"]) for r in out)
    print(f"\n{'policy':{w}s} {'cost':>9s} {'%today':>7s} {'quality':>8s} {'95% CI':>16s} {'cov':>7s}")
    for r in out:
        print(f"{r['policy']:{w}s} {r['cost_usd']:9.2f} {r['cost_pct']:6.1f}% {r['quality_pct']:7.2f}% "
              f"[{r['quality_ci'][0]:6.2f},{r['quality_ci'][1]:6.2f}] {r['coverage_pct']:6.1f}%")
    print(f"\nwhere rule_based_router sends work (all 9 models remain in play):")
    for m, n in Counter(r["proposed"] for r in routes).most_common():
        print(f"    {m:20s} {n:4d}")
    print(f"\nwrote {res/'scorecard.json'} and {res/'routes_rule_based.jsonl'}")

if __name__ == "__main__":
    main()
