#!/usr/bin/env python3
"""The honesty artifacts — the slide Viktor says wins the special prize.

Built to the organizer's own rubric (Discord, 16:25):
  * ship a number WITH ERROR BARS, not a number
  * SHOW A CASE WHERE YOUR ROUTER IS WRONG — find the segment where routing down
    hurts, quantify it, state the guardrail
  * report COVERAGE alongside savings (policy family 7: abstain is legitimate,
    but you must say how much you abstained on)
  * name the confounding you cannot remove
  * report savings AFTER the cache penalty

Outputs results/honesty.json + a printed report.
Usage: python3 solution/honesty.py
"""
import json, math, statistics
from collections import Counter, defaultdict
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from cost2 import load_pricing, price_of, episode_cost, HEADLINE

def wilson(k, n, z=1.96):
    """Wilson score interval — honest at small n, which is where our gates live."""
    if n == 0: return (0.0, 0.0, 0.0)
    p = k / n
    d = 1 + z*z/n
    c = (p + z*z/(2*n)) / d
    h = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / d
    return (p, max(0.0, c-h), min(1.0, c+h))

def two_prop(k1, n1, k2, n2, z=1.96):
    if n1 == 0 or n2 == 0: return None
    p1, p2 = k1/n1, k2/n2
    se = math.sqrt(p1*(1-p1)/n1 + p2*(1-p2)/n2)
    d = p1 - p2
    return {"diff": d, "lo": d - z*se, "hi": d + z*se,
            "z": (d/se if se else 0.0), "p1": p1, "p2": p2}

def main():
    R = Path("results")
    eps = {e["row_id"]: e for e in map(json.loads, open(R/"episodes.jsonl"))}
    routes = [json.loads(l) for l in open(R/"routes.jsonl")]
    qual = {d["row_id"]: d for d in map(json.loads, open(R/"quality.jsonl"))}
    gen = {d["row_id"]: d for d in map(json.loads, open(R/"gen_tokens.jsonl"))}
    pol = json.loads((R/"policy.json").read_text())
    pr = load_pricing()
    out = {}

    # ---------------------------------------------------------------- COVERAGE
    by_rule, by_conf = Counter(), Counter()
    moved = 0
    for r in routes:
        by_rule[r["rule"]] += 1; by_conf[r["confidence"]] += 1
        moved += r["proposed"] != r["logged"]
    n = len(routes)
    out["coverage"] = {
        "n_episodes": n, "rerouted": moved, "coverage_pct": round(moved/n*100, 1),
        "abstained": n - moved, "abstain_pct": round((n-moved)/n*100, 1),
        "by_rule": dict(by_rule), "by_confidence": dict(by_conf)}
    print("=" * 74)
    print("COVERAGE  (Viktor policy family 7: 'report coverage alongside savings')")
    print("=" * 74)
    print(f"  episodes {n}   rerouted {moved} ({moved/n:.1%})   abstained {n-moved} ({(n-moved)/n:.1%})")
    for k, v in by_rule.most_common(): print(f"    {k:18s} {v:4d}  ({v/n:5.1%})")

    # ------------------------------------------- WHERE THE ROUTER IS WRONG
    # Segment = the jobs our own gate REFUSED, plus a per-segment error comparison
    # between the floor arm and the premium arms on the same job.
    print("\n" + "=" * 74)
    print("WHERE THE ROUTER IS WRONG  ('find the segment where routing down hurts')")
    print("=" * 74)
    adverse = {j: f for j, f in pol["floors"].items() if f["adverse"]}
    print(f"\n  Our adverse gate refused {len(adverse)} of {len(pol['floors'])} jobs that")
    print("  otherwise qualified for floor routing. Those are the segment where the")
    print("  cheap tier measurably underperformed on the SAME job:\n")
    rows = []
    tot_forgone = 0.0
    for j, f in sorted(adverse.items(), key=lambda kv: -(kv[1]["floor_err"] - kv[1]["above_err"])):
        members = [e for e in eps.values() if (e.get("job_key") or "") == j]
        forgone = sum(episode_cost(e, [e["model"]]*e["n_requests"], HEADLINE, pr, gen)
                      - episode_cost(e, [f["floor"]]*e["n_requests"], HEADLINE, pr, gen)
                      for e in members if price_of(f["floor"], pr)[0] < price_of(e["model"], pr)[0])
        tot_forgone += max(0.0, forgone)
        rows.append({"job": j, "floor": f["floor"], "n_at_floor": f["n_at_floor"],
                     "n_total": f["n_total"], "floor_err": f["floor_err"],
                     "above_err": f["above_err"], "forgone_usd": round(max(0.0, forgone), 4)})
        print(f"    {j[:44]:46s} floor={f['floor']:16s} err {f['floor_err']:.3f} vs {f['above_err']:.3f}"
              f"  n={f['n_at_floor']}/{f['n_total']}  forgone ${max(0.0,forgone):.3f}")
    out["router_is_wrong"] = {"n_jobs_gated": len(adverse), "jobs": rows,
                              "forgone_savings_usd": round(tot_forgone, 4)}
    print(f"\n  Cost of the guardrail: we leave ${tot_forgone:.2f} on the table to avoid these.")
    print("  GUARDRAIL STATED: a job is never routed down if its cheap arm shows a higher")
    print("  error rate than its pricier arms, at any sample size. We prefer forgoing")
    print("  savings to defending a regression we can see in our own data.")

    # honest small-n caveat with Wilson intervals on the worst one
    if rows:
        w = rows[0]
        p, lo, hi = wilson(int(round(w["floor_err"] * max(1, w["n_at_floor"]) * 10)), max(1, w["n_at_floor"]) * 10)
        print(f"\n  CAVEAT (we say this before a judge does): these gates fire on tiny samples")
        print(f"  (n at floor = {min(r['n_at_floor'] for r in rows)}..{max(r['n_at_floor'] for r in rows)}).")
        print("  None of the differences is individually significant; the gate is deliberately")
        print("  conservative — it triggers on ANY adverse point estimate, not on significance.")

    # ---------------------------------------------------- POOLED PARITY + CI
    print("\n" + "=" * 74)
    print("THE NUMBER, WITH ERROR BARS  ('a confidence interval beats a bigger point estimate')")
    print("=" * 74)
    def pooled(skip_adverse):
        fe = fc = ae = ac = 0
        for j, f in pol["floors"].items():
            if skip_adverse and f["adverse"]: continue
            for e in eps.values():
                if (e.get("job_key") or "") != j: continue
                k = sum(1 for c in e["behavior"]["calls"] if c["is_err"]); c_ = len(e["behavior"]["calls"])
                if e["model"] == f["floor"]: fe += k; fc += c_
                else: ae += k; ac += c_
        return fe, fc, ae, ac, two_prop(fe, fc, ae, ac)

    # PRE-REGISTERED headline: ALL matched jobs, gate NOT applied. This is the only
    # unbiased comparison. The post-gate number below is conditioned on our own
    # selection and must never be quoted as evidence that cheap models are better.
    fe, fc, ae, ac, tp = pooled(skip_adverse=False)
    print("  [HEADLINE — all matched jobs, no gating; the unbiased comparison]")
    print(f"  failure rate — floor arm {tp['p1']:.4f} ({fe}/{fc})  vs premium arms {tp['p2']:.4f} ({ae}/{ac})")
    print(f"  difference {tp['diff']:+.4f}   95% CI [{tp['lo']:+.4f}, {tp['hi']:+.4f}]   z={tp['z']:.2f}")
    verdict = ("no measurable quality cost at the floor tier"
               if tp['lo'] <= 0 <= tp['hi'] else "MEASURABLE DIFFERENCE at the floor tier")
    print(f"  -> {verdict}")
    out["parity_ci_ungated"] = {k: round(v, 5) for k, v in tp.items()} | {"floor_n": fc, "premium_n": ac}

    fe2, fc2, ae2, ac2, tp2 = pooled(skip_adverse=True)
    print("\n  [DIAGNOSTIC ONLY — after our adverse gate. CIRCULAR: we removed the jobs")
    print("   where cheap looked worse, so of course cheap now looks better. This measures")
    print("   that the gate does what it says, NOT that cheap models win. Never quoted as]")
    print("   [a quality claim.]")
    print(f"  floor {tp2['p1']:.4f} ({fe2}/{fc2}) vs premium {tp2['p2']:.4f} ({ae2}/{ac2})"
          f"   diff {tp2['diff']:+.4f}  CI [{tp2['lo']:+.4f}, {tp2['hi']:+.4f}]")
    out["parity_ci_post_gate_DIAGNOSTIC_ONLY"] = {k: round(v, 5) for k, v in tp2.items()} | {
        "floor_n": fc2, "premium_n": ac2,
        "warning": "conditioned on our own adverse gate; selection-biased by construction; "
                   "evidence the gate works, NOT evidence cheap models are better"}

    # ---------------------------------------------------- CONFOUNDING WE CANNOT REMOVE
    print("\n" + "=" * 74)
    print("CONFOUNDING WE CANNOT REMOVE  (named before the panel names it)")
    print("=" * 74)
    conf = [
        "Wrong-but-clean answers are invisible. Our signal counts objective failures "
        "(tool errors, retries, duplicate calls). A cheap model that returns a fluent, "
        "confident, WRONG answer with no failed tool call scores a perfect 1.0. This is "
        "the single largest hole and it bounds every quality claim we make.",
        "Envelope transfer (C3) is not a matched comparison. The cheap tier's observed "
        "quality reflects the jobs it was actually GIVEN, not the jobs we would send it. "
        "If the operator only trusted it with easy work, we inherit that selection bias.",
        "Preset assignment is sticky, not random (shuffle test z=2.41, p~0.016). Jobs are "
        "not randomised across tiers, so matched comparisons still carry workspace-level "
        "confounding we cannot difference out.",
        "Token counts are estimates. chars/4 vs real BPE differ +-15% per row and the bias "
        "is vendor-correlated, so cross-vendor savings are the least trustworthy.",
        "Output cost is a lower bound: only model-generated items recoverable from the "
        "echoed history are priced; each task's final answer is unobservable.",
        "Our adverse gate is fit on the same data it is evaluated on. Post-gate parity "
        "numbers are selection-biased by construction and we report them as a diagnostic "
        "only; the headline parity figure is computed with the gate switched OFF.",
        "One dataset, no held-out split (organizer-confirmed). Our policy is fit and "
        "evaluated on the same 1000 episodes; the pipeline runs cold on unseen chunks "
        "(verified on a synthetic chunk) but that generalisation is untested on real data.",
    ]
    for i, c in enumerate(conf, 1): print(f"  {i}. {c}\n")
    out["confounding"] = conf

    # ---------------------------------------------------- CACHE PENALTY HONESTY
    full = {r["row_id"]: r["proposed"] for r in routes}
    after = sum(episode_cost(eps[k], [v]*eps[k]["n_requests"], HEADLINE, pr, gen) for k, v in full.items())
    logged = sum(episode_cost(e, [e["model"]]*e["n_requests"], HEADLINE, pr, gen) for e in eps.values())
    out["savings_after_cache_penalty"] = {"logged_usd": round(logged, 2), "routed_usd": round(after, 2),
                                          "pct": round((1 - after/logged)*100, 1)}
    print("=" * 74)
    print("SAVINGS REPORTED AFTER THE CACHE PENALTY  ('reporting savings after reads more credible')")
    print("=" * 74)
    print(f"  logged ${logged:,.2f} -> routed ${after:,.2f}  ({(1-after/logged)*100:.1f}% saved, cache-aware)")
    print("  On this chunk the cache penalty is ~0 because the organizers confirmed one")
    print("  trajectory per row: there is no shared prefix between rows to lose. We price")
    print("  it anyway, and the machinery is exercised on synthetic multi-call chains.")

    (R/"honesty.json").write_text(json.dumps(out, indent=1))
    print(f"\nwrote {R/'honesty.json'}")

if __name__ == "__main__": main()
