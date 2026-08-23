#!/usr/bin/env python3
"""The router: a nameable decision ladder, fit on one split and applied cold to another.

STRUCTURE IT EXPLOITS (criterion/01 "can you name that structure?"):
  Viktor does not route per task. A human picks ONE model per workspace and every
  task runs on it (confirmed by the Viktor bot in the challenge Discord). What the
  log therefore contains is not routing but *preset drift*: the same recurring job,
  run on different days, lands on different price tiers. Those are accidental A/B
  tests on identical work. We recover job identity (the cron path, which survives
  redaction), read the cheapest tier each job was ACTUALLY served on, and route
  future runs of that job there — but only where the evidence is strong enough.

THE LADDER (every episode gets exactly one reason code):
  R0 vendor_pin   — tool fingerprint fixes the vendor (Claude stack vs GPT stack);
                    the default policy never crosses it. --cross-vendor enables the
                    variant, since the operator demonstrably ran jobs on both.
  R1 free_lane    — same price, newer generation (e.g. opus-4-8 -> opus-5). Zero
                    risk, needs no quality argument. Often empty; that is fine.
  R2 family_floor — job seen in the fit split on >=2 tiers: route to the cheapest
                    tier observed on THAT job. Gated: skip if the floor arm looked
                    worse, or if it has fewer than --min-floor-obs observations.
  R3 envelope     — job unseen: route down only if the episode sits inside the
                    cheap tier's OBSERVED operating envelope (trigger kind, token
                    size, call count) measured on the fit split. Weaker evidence,
                    labelled as such.
  R4 abstain      — anything else stays on the logged model. Abstaining is a
                    result, not a failure: it is what keeps the estimate honest.

CONFIDENCE (drives the frontier sweep, and is why the sweep is not gameable):
  C1 family floor, >=2 observations at the floor, no adverse signal
  C2 family floor, 1 observation at the floor
  C3 envelope transfer to an unseen job
  Adopting in C1->C2->C3 order means the frontier's first dollars are the
  best-evidenced ones. Sweeping cheapest-first instead (what the starter kit does)
  produces an impressive curve from no evidence at all.

Usage:
  python3 solution/router.py fit   results/episodes.jsonl --families results/families.jsonl
  python3 solution/router.py apply results/episodes.jsonl --policy results/policy.json
"""
import argparse, json, statistics
from collections import Counter, defaultdict
from pathlib import Path

MIN_FLOOR_OBS = 2        # observations at the floor tier before C1
# Envelope percentile. p95 of ~20 observations is one point from the top: on the GPT
# stack it lands exactly on the corpus maximum, so the gate can never reject anything.
# A gate that admits 95.7% of the corpus is a formality, not evidence. p75 actually
# binds (rejects 26.4%), and frontier.py sweeps this to draw the envelope arm.
ENVELOPE_Q = 0.75

def load_pricing():
    p = Path(__file__).resolve().parent.parent / "scripts" / "pricing.json"
    if not p.exists():
        raise FileNotFoundError(f"{p} missing — the only source of model prices.")
    return json.loads(p.read_text())

def price_of(m, pr):
    if m in pr: return pr[m]
    for k in sorted(pr, key=len, reverse=True):
        if k != "_default" and m.startswith(k): return pr[k]
    return pr["_default"]

def rate(m, pr): return price_of(m, pr)[0]

def job_id(ep):
    """Stable identity for a recurring job. Cron paths survive redaction and recur
    across chunks; interactive traffic has no durable id, so it falls to R3/R4."""
    jk = ep.get("job_key") or ""
    return jk if jk and not jk.startswith("interactive:") else None

# ------------------------------------------------------------------ fit
def fit(eps, fams, pricing):
    by_fam = {f["family_id"]: f for f in fams}
    ep_fam = {e["row_id"]: e.get("family_id") for e in eps}

    # --- R2 evidence: cheapest tier actually observed per job, with guard rails
    job_obs = defaultdict(lambda: defaultdict(list))   # job -> model -> [episodes]
    for e in eps:
        j = job_id(e)
        if j: job_obs[j][e["model"]].append(e)

    floors = {}
    for j, mm in job_obs.items():
        if len(mm) < 2: continue                        # need >=2 tiers on the same job
        cheapest = min(mm, key=lambda m: (rate(m, pricing), m))
        if rate(cheapest, pricing) >= max(rate(m, pricing) for m in mm):
            continue                                    # no actual saving available
        # adverse-signal gate: did the floor arm fail more than the pricier arms?
        def err(eplist):
            c = sum(x["behavior"]["n_calls"] for x in eplist)
            e_ = sum(1 for x in eplist for k in x["behavior"]["calls"] if k["is_err"])
            return (e_ / c) if c else 0.0
        floor_err = err(mm[cheapest])
        above = [x for m, lst in mm.items() if m != cheapest for x in lst]
        adverse = bool(above) and floor_err > err(above) + 1e-12
        vendors = {x["vendor"] for lst in mm.values() for x in lst}
        floors[j] = {"floor": cheapest, "n_at_floor": len(mm[cheapest]),
                     "n_total": sum(len(v) for v in mm.values()),
                     "models_seen": sorted(mm), "adverse": adverse,
                     "floor_err": round(floor_err, 4), "above_err": round(err(above), 4),
                     "vendor": sorted(vendors)[0] if len(vendors) == 1 else "mixed",
                     "family_id": ep_fam.get(mm[cheapest][0]["row_id"])}

    # --- R1 free lanes: SAME LADDER only (a ladder is one capability tier, e.g.
    # opus / sonnet / fable / sol / terra / luna). Crossing ladders is a real
    # downgrade and must earn its way through R2 or R3 — never through here.
    # A naive "strip the last token" family key silently merges sol/terra/luna
    # into one family and would smuggle a 25x downgrade in as a "free lane".
    def ladder(m):
        parts = m.split("-")
        if m.startswith("claude"): return ("claude", parts[1])          # claude-opus-5 -> opus
        if m.startswith("gpt"):    return ("gpt", parts[-1])            # gpt-5.6-sol   -> sol
        return (m, m)
    # Two further guards, both learned the hard way:
    #  * fire only when STRICTLY cheaper. An equal-price swap saves $0, so it is
    #    not a "free lane", it is a free risk.
    #  * break ties toward the BEST-SUPPORTED model, never alphabetically —
    #    otherwise 331 opus-5 episodes get routed onto opus-4-6, seen twice.
    seen = Counter(e["model"] for e in eps)
    lanes = {}
    groups = defaultdict(list)
    for m in {e["model"] for e in eps}:
        groups[ladder(m)].append(m)
    for _, ms in groups.items():
        if len(ms) < 2: continue
        target = min(ms, key=lambda m: (rate(m, pricing), -seen[m], m))
        for m in ms:
            if m != target and rate(m, pricing) > rate(target, pricing):
                lanes[m] = target       # same capability tier, strictly cheaper

    # --- R3 envelope: the operating envelope of each vendor's cheapest tier
    env = {}
    for vend in {e["vendor"] for e in eps}:
        pool = [e for e in eps if e["vendor"] == vend]
        if not pool: continue
        cheap = min({e["model"] for e in pool}, key=lambda m: rate(m, pricing))
        obs = [e for e in pool if e["model"] == cheap]
        if not obs: continue
        toks = sorted(e["tok_bpe"]["total"] for e in obs)
        calls = sorted(e["behavior"]["n_calls"] for e in obs)
        q = lambda xs: xs[min(len(xs) - 1, int(ENVELOPE_Q * len(xs)))]  # ENVELOPE_Q set from CLI
        env[vend] = {"cheap_model": cheap, "n_obs": len(obs),
                     "max_tokens": q(toks), "max_calls": q(calls),
                     "triggers": sorted({e["trigger"] for e in obs})}

    return {"floors": floors, "free_lanes": lanes, "envelopes": env,
            "params": {"min_floor_obs": MIN_FLOOR_OBS, "envelope_q": ENVELOPE_Q},
            "fit_stats": {"n_episodes": len(eps), "n_jobs_with_floor": len(floors)}}

# ------------------------------------------------------------------ apply
def route_one(ep, pol, pricing, cross_vendor=False):
    """-> (model, rule, confidence, reason). One rule fires per episode."""
    logged = ep["model"]
    j = job_id(ep)

    f = pol["floors"].get(j) if j else None
    if f and not f["adverse"]:
        if cross_vendor or f["vendor"] in (ep["vendor"], "mixed"):
            if rate(f["floor"], pricing) < rate(logged, pricing):
                conf = "C1" if f["n_at_floor"] >= pol["params"]["min_floor_obs"] else "C2"
                return (f["floor"], "R2_family_floor", conf,
                        f"job seen on {len(f['models_seen'])} tiers; cheapest observed "
                        f"{f['floor']} ({f['n_at_floor']}/{f['n_total']} runs), no adverse signal")
    if f and f["adverse"]:
        return (logged, "R4_abstain", "abstain",
                f"floor arm {f['floor']} showed higher error on this job "
                f"({f['floor_err']} vs {f['above_err']}) — gated, kept logged")

    lane = pol["free_lanes"].get(logged)
    if lane and lane != logged and rate(lane, pricing) <= rate(logged, pricing):
        return (lane, "R1_free_lane", "C1",
                f"same family, price {rate(lane,pricing)} <= {rate(logged,pricing)} — no quality claim needed")

    e = pol["envelopes"].get(ep["vendor"])
    if e and rate(e["cheap_model"], pricing) < rate(logged, pricing):
        inside = (ep["tok_bpe"]["total"] <= e["max_tokens"]
                  and ep["behavior"]["n_calls"] <= e["max_calls"]
                  and ep["trigger"] in e["triggers"])
        if inside:
            return (e["cheap_model"], "R3_envelope", "C3",
                    f"unseen job inside {e['cheap_model']} observed envelope "
                    f"(<= {e['max_tokens']} tok, <= {e['max_calls']} calls, trigger {ep['trigger']})")
    return (logged, "R4_abstain", "abstain", "no sufficient evidence — kept on logged model")

def main():
    global ENVELOPE_Q
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mode", choices=["fit", "apply", "both"])
    ap.add_argument("episodes")
    ap.add_argument("--families", default="results/families.jsonl")
    ap.add_argument("--policy", default="results/policy.json")
    ap.add_argument("--out", default="results/routes.jsonl")
    ap.add_argument("--cross-vendor", action="store_true")
    ap.add_argument("--envelope-q", type=float, default=ENVELOPE_Q,
                    help="cheap-tier envelope percentile; lower = stricter gate")
    a = ap.parse_args()
    ENVELOPE_Q = a.envelope_q
    pricing = load_pricing()
    eps = [json.loads(l) for l in open(a.episodes)]

    if a.mode in ("fit", "both"):
        fams = [json.loads(l) for l in open(a.families)] if Path(a.families).exists() else []
        pol = fit(eps, fams, pricing)
        Path(a.policy).write_text(json.dumps(pol, indent=1))
        print(f"fit on {len(eps)} episodes -> {a.policy}")
        print(f"  jobs with a usable floor : {len(pol['floors'])}"
              f"  (adverse-gated: {sum(1 for v in pol['floors'].values() if v['adverse'])})")
        print(f"  free lanes               : {pol['free_lanes'] or 'none'}")
        for v, e in sorted(pol["envelopes"].items()):
            print(f"  envelope {v:6s} -> {e['cheap_model']:15s} n={e['n_obs']:3d} "
                  f"<= {e['max_tokens']:,} tok, <= {e['max_calls']} calls")
    if a.mode in ("apply", "both"):
        pol = json.loads(Path(a.policy).read_text())
        rules, confs, moved = Counter(), Counter(), 0
        with open(a.out, "w") as fh:
            for ep in eps:
                m, rule, conf, why = route_one(ep, pol, pricing, a.cross_vendor)
                rules[rule] += 1; confs[conf] += 1; moved += (m != ep["model"])
                fh.write(json.dumps({"row_id": ep["row_id"], "logged": ep["model"],
                                     "proposed": m, "rule": rule, "confidence": conf,
                                     "reason": why, "n_requests": ep["n_requests"]}) + "\n")
        print(f"\napplied to {len(eps)} episodes -> {a.out}   rerouted={moved} ({moved/len(eps):.1%})")
        for r, n in rules.most_common(): print(f"  {r:18s} {n:4d}")
        print("  " + "  ".join(f"{c}={n}" for c, n in sorted(confs.items())))

if __name__ == "__main__": main()
