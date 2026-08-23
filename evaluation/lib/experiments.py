#!/usr/bin/env python3
"""Natural-experiment table over recurring job families (WP-D).

Recurring job families (family_id from solution/episodes.py: normalized-sys-
prompt sha  U  job_key  U  >=0.9 payload similarity) that ran on multiple
model tiers are accidental A/B tests: same job, different price point. This
script builds the per-family evidence table and five aggregates:

  1. CERTIFIED savings   - reroute every member of a multi-model family to the
                           cheapest model that DEMONSTRABLY served that family
                           (same-vendor and any-stack variants).
  2. Randomization       - shuffle test: is the observed model mixing within
                           families compatible with tier assignment that is
                           independent of the job?
  3. Quality parity      - pooled 2x2 (err tool-calls) floor tier vs premium
                           tiers across multi-model families, z-test + CI.
  4. Envelope argument   - workload envelope of the globally cheapest model
                           (gpt-5.6-luna on chunk-01); episodes inside it and
                           the saving if moved to their vendor's cheapest.
                           NOT certified - labeled ENVELOPE ARGUMENT.
  5. Free lanes          - old-generation -> same-tier newest-generation moves
                           (opus-4-x -> opus-5 at $0, sonnet-4-6 -> sonnet-5).

Costs are PRELIMINARY: input tokens x uncached rate, under both the chars/4
and the BPE token estimate (WP-G re-joins canonical WP-C costs later).
Quality proxies are PRELIMINARY: per-episode tool-call error rate plus
retry/duplicate flags derived from the call sequence (WP-B supersedes).

Deterministic: seeded shuffles, sorted iteration, no timestamps. Held-out
ready: no chunk-specific constants - floors, ladders, envelope model and
vendor-cheapest targets are all derived from the data + pricing table.

Usage:
  python3 solution/experiments.py results/episodes.jsonl [--out results/families.jsonl]
                                  [--pricing scripts/pricing.json]
                                  [--summary results/experiments_summary.json]
"""
import argparse
import json
import math
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

N_SHUFFLES = 200
SHUFFLE_SEED = 20250822          # deterministic constant, not data-derived
ALPHA = 0.05

# ------------------------------------------------------------------ pricing
def load_pricing(path):
    with open(path) as f:
        return json.load(f)

def uncached_rate(model, pricing):
    """$ per 1M uncached input tokens; exact row, else longest prefix row."""
    if model in pricing:
        return pricing[model][0]
    best = ""
    for key in pricing:
        if key != "_default" and model.startswith(key) and len(key) > len(best):
            best = key
    return pricing[best][0] if best else pricing["_default"][0]

def input_cost(ep, model, pricing, tok_key):
    return ep[tok_key]["total"] * uncached_rate(model, pricing) / 1e6

TOK_KEYS = {"chars4": "tok_chars4", "bpe": "tok_bpe"}

# ------------------------------------------------------------------- stats
def p95(values):
    """95th percentile, linear interpolation (numpy 'linear'), pure python."""
    xs = sorted(values)
    if not xs:
        return 0.0
    idx = 0.95 * (len(xs) - 1)
    lo = int(math.floor(idx))
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (idx - lo) * (xs[hi] - xs[lo])

def two_prop_test(err1, n1, err2, n2):
    """Normal-approx 2-proportion test on rates err1/n1 vs err2/n2.

    Returns (rate1, rate2, z, p_two_sided, p_one_sided_1_greater) with
    None z/p when either arm has no observations or the pooled variance
    degenerates."""
    if n1 == 0 or n2 == 0:
        return (err1 / n1 if n1 else None, err2 / n2 if n2 else None,
                None, None, None)
    r1, r2 = err1 / n1, err2 / n2
    pool = (err1 + err2) / (n1 + n2)
    var = pool * (1 - pool) * (1 / n1 + 1 / n2)
    if var <= 0:
        return r1, r2, None, None, None
    z = (r1 - r2) / math.sqrt(var)
    p_two = math.erfc(abs(z) / math.sqrt(2))
    p_one = 0.5 * math.erfc(z / math.sqrt(2))          # P(Z > z): arm1 worse
    return r1, r2, z, p_two, p_one

def diff_ci95(err1, n1, err2, n2):
    """Wald 95% CI on rate difference (arm1 - arm2)."""
    if n1 == 0 or n2 == 0:
        return None
    r1, r2 = err1 / n1, err2 / n2
    se = math.sqrt(r1 * (1 - r1) / n1 + r2 * (1 - r2) / n2)
    d = r1 - r2
    return [d - 1.96 * se, d + 1.96 * se]

# ------------------------------------------------------------- call quality
def call_flags(ep):
    """(n_err, n_calls, has_retry, has_dup) from the tool-call sequence.

    retry: a call immediately repeats the previous call (same name+args) -
           the classic model-level retry loop.
    dup:   any (name, args) pair issued more than once anywhere in the
           episode (superset of retry; catches re-doing work after detours).
    """
    calls = ep["behavior"]["calls"]
    n_err = sum(1 for c in calls if c["is_err"])
    seen, has_retry, has_dup, prev = set(), False, False, None
    for c in calls:
        key = (c["name"], c["args_sha8"])
        if key == prev:
            has_retry = True
        if key in seen:
            has_dup = True
        seen.add(key)
        prev = key
    return n_err, len(calls), has_retry, has_dup

# ------------------------------------------------------------ model ladders
def version_tuple(generation):
    """'4-8' -> (4, 8); '5.6' -> (5, 6); '5' -> (5,). Sortable."""
    nums = re.findall(r"\d+", str(generation))
    return tuple(int(n) for n in nums) if nums else (0,)

def ladder_key(model, pricing):
    """Model family ladder = longest pricing prefix row (e.g. 'claude-opus')."""
    best = ""
    for key in pricing:
        if key != "_default" and model.startswith(key) and len(key) > len(best):
            best = key
    return best or model

# ------------------------------------------------------------------- main
def build_family_record(fid, members, pricing):
    """Per-family evidence row. members: list of episode dicts."""
    models = sorted(set(e["model"] for e in members))
    multi_model = len(models) > 1

    # any-stack floor: cheapest observed model (tie: lower tier, then name)
    def floor_of(cands):
        return min(cands, key=lambda m: (uncached_rate(m, pricing),
                                         min(e["tier"] for e in members if e["model"] == m), m))
    any_floor = floor_of(models)
    vendor_floor = {}
    for v in sorted(set(e["vendor"] for e in members)):
        vendor_floor[v] = floor_of(sorted(set(e["model"] for e in members if e["vendor"] == v)))

    per_model = {}
    for m in models:
        eps = [e for e in members if e["model"] == m]
        errs = calls = retries = dups = 0
        for e in eps:
            n_err, n_calls, has_retry, has_dup = call_flags(e)
            errs += n_err
            calls += n_calls
            retries += has_retry
            dups += has_dup
        per_model[m] = {
            "n": len(eps),
            "tier": eps[0]["tier"],
            "prelim_mean_cost_usd": {
                lab: round(sum(input_cost(e, m, pricing, tk) for e in eps) / len(eps), 6)
                for lab, tk in TOK_KEYS.items()},
            "err_rate": round(errs / calls, 4) if calls else 0.0,
            "err_calls": errs,
            "total_calls": calls,
            "retry_rate": round(retries / len(eps), 4),
            "dup_rate": round(dups / len(eps), 4),
            "ends_silent_rate": round(sum(e["behavior"]["ends_silent"] for e in eps) / len(eps), 4),
            "mean_n_calls": round(sum(e["behavior"]["n_calls"] for e in eps) / len(eps), 2),
        }

    # reroute savings (multi-model families only carry non-zero numbers,
    # single-model/singleton families pass through with 0 and floor=logged)
    savings = {"same_vendor": {}, "any_stack": {}}
    for lab, tk in TOK_KEYS.items():
        sv = av = 0.0
        for e in members:
            r = uncached_rate(e["model"], pricing)
            sv += e[tk]["total"] * (r - uncached_rate(vendor_floor[e["vendor"]], pricing)) / 1e6
            av += e[tk]["total"] * (r - uncached_rate(any_floor, pricing)) / 1e6
        savings["same_vendor"][lab] = round(sv, 6)
        savings["any_stack"][lab] = round(av, 6)

    # quality parity: members at the floor PRICE vs members above it
    floor_rate_usd = uncached_rate(any_floor, pricing)
    parity = None
    if multi_model:
        f_err = f_calls = a_err = a_calls = 0
        for e in members:
            n_err, n_calls, _, _ = call_flags(e)
            if uncached_rate(e["model"], pricing) <= floor_rate_usd:
                f_err += n_err
                f_calls += n_calls
            else:
                a_err += n_err
                a_calls += n_calls
        r1, r2, z, p_two, p_one = two_prop_test(f_err, f_calls, a_err, a_calls)
        floor_worse = (r1 is not None and r2 is not None and r1 > r2)
        parity = {
            "floor_err": f_err, "floor_calls": f_calls,
            "above_err": a_err, "above_calls": a_calls,
            "floor_err_rate": round(r1, 4) if r1 is not None else None,
            "above_err_rate": round(r2, 4) if r2 is not None else None,
            "z": round(z, 3) if z is not None else None,
            "p_two_sided": round(p_two, 4) if p_two is not None else None,
            "floor_worse": floor_worse,
            "floor_worse_significant": bool(floor_worse and p_one is not None and p_one < ALPHA),
        }

    return {
        "family_id": fid,
        "n_members": len(members),
        "members": sorted(e["row_id"] for e in members),
        "job_keys": sorted(set(e["job_key"] for e in members)),
        "vendors": sorted(set(e["vendor"] for e in members)),
        "triggers": sorted(set(e["trigger"] for e in members)),
        "task_types": sorted(set(e["task_type"] for e in members)),
        "multi_model": multi_model,
        "model_mix": {m: per_model[m]["n"] for m in models},
        "per_model": per_model,
        "observed_floor_model": any_floor,
        "vendor_floor_models": vendor_floor,
        "floor_basis": "observed" if multi_model else "logged",
        "floor_savings_usd": savings,
        "quality_parity": parity,
    }

def randomization_test(episodes, families, pricing):
    """Within-vendor model-column shuffle test on multi-member families.

    Statistic: fraction of multi-member families whose members all share one
    model. Null: reassign the vendor's logged model labels uniformly at
    random across that vendor's episodes (tier follows the model), which is
    exactly 'tier assignment independent of the job'."""
    multi = [fid for fid, mem in families.items() if len(mem) >= 2]
    def frac_single(model_of):
        single = sum(1 for fid in multi
                     if len(set(model_of[e["row_id"]] for e in families[fid])) == 1)
        return single / len(multi) if multi else 0.0

    observed = frac_single({e["row_id"]: e["model"] for e in episodes})
    rng = random.Random(SHUFFLE_SEED)
    by_vendor = defaultdict(list)
    for e in episodes:                       # episodes arrive in sorted order
        by_vendor[e["vendor"]].append(e)
    null = []
    for _ in range(N_SHUFFLES):
        model_of = {}
        for v in sorted(by_vendor):
            eps = by_vendor[v]
            labels = [e["model"] for e in eps]
            rng.shuffle(labels)
            for e, m in zip(eps, labels):
                model_of[e["row_id"]] = m
        null.append(frac_single(model_of))
    mean = sum(null) / len(null)
    sd = math.sqrt(sum((x - mean) ** 2 for x in null) / (len(null) - 1))
    return {
        "n_multi_member_families": len(multi),
        "observed_frac_single_model": round(observed, 4),
        "null_mean": round(mean, 4),
        "null_sd": round(sd, 4),
        "z": round((observed - mean) / sd, 3) if sd > 0 else None,
        "n_shuffles": N_SHUFFLES,
        "seed": SHUFFLE_SEED,
    }

def pooled_parity(family_rows):
    """Pooled 2x2 over multi-model families: floor-price arm vs premium arm."""
    f_err = f_calls = a_err = a_calls = 0
    n_fam = 0
    for rec in family_rows:
        par = rec["quality_parity"]
        if par is None:
            continue
        n_fam += 1
        f_err += par["floor_err"]
        f_calls += par["floor_calls"]
        a_err += par["above_err"]
        a_calls += par["above_calls"]
    r1, r2, z, p_two, p_one = two_prop_test(f_err, f_calls, a_err, a_calls)
    ci = diff_ci95(f_err, f_calls, a_err, a_calls)
    floor_excess_significant = bool(p_one is not None and p_one < ALPHA
                                    and r1 is not None and r2 is not None and r1 > r2)
    return {
        "n_families_pooled": n_fam,
        "floor_err": f_err, "floor_calls": f_calls,
        "above_err": a_err, "above_calls": a_calls,
        "floor_err_rate": round(r1, 4) if r1 is not None else None,
        "above_err_rate": round(r2, 4) if r2 is not None else None,
        "rate_diff_floor_minus_above": round(r1 - r2, 4) if r1 is not None and r2 is not None else None,
        "diff_ci95": [round(x, 4) for x in ci] if ci else None,
        "z": round(z, 3) if z is not None else None,
        "p_two_sided": round(p_two, 4) if p_two is not None else None,
        "floor_excess_significant": floor_excess_significant,
        "headline_supported": not floor_excess_significant,
    }

def envelope_argument(episodes, pricing):
    """ENVELOPE ARGUMENT (not certified): the globally cheapest model's
    observed workload envelope, and what moving every in-envelope episode to
    its vendor's cheapest observed model would save on input cost."""
    cheapest_global = min(sorted(set(e["model"] for e in episodes)),
                          key=lambda m: (uncached_rate(m, pricing), m))
    src = [e for e in episodes if e["model"] == cheapest_global]
    vendor_cheapest = {}
    for v in sorted(set(e["vendor"] for e in episodes)):
        cands = sorted(set(e["model"] for e in episodes if e["vendor"] == v))
        vendor_cheapest[v] = min(cands, key=lambda m: (uncached_rate(m, pricing),
                                                       version_tuple(next(e["generation"] for e in episodes if e["model"] == m)) * -1 if False else m))
    triggers = sorted(set(e["trigger"] for e in src))
    max_calls = max(e["behavior"]["n_calls"] for e in src)
    out = {
        "label": "ENVELOPE ARGUMENT (not certified)",
        "envelope_model": cheapest_global,
        "n_envelope_source_episodes": len(src),
        "trigger_types": triggers,
        "max_n_calls": max_calls,
        "vendor_cheapest_targets": vendor_cheapest,
    }
    for lab, tk in TOK_KEYS.items():
        cap = p95(e[tk]["total"] for e in src)
        inside = [e for e in episodes
                  if e["trigger"] in triggers
                  and e["behavior"]["n_calls"] <= max_calls
                  and e[tk]["total"] <= cap]
        movable = [e for e in inside
                   if uncached_rate(e["model"], pricing)
                   > uncached_rate(vendor_cheapest[e["vendor"]], pricing)]
        save = sum(e[tk]["total"] * (uncached_rate(e["model"], pricing)
                                     - uncached_rate(vendor_cheapest[e["vendor"]], pricing)) / 1e6
                   for e in movable)
        out[lab] = {
            "token_p95_cap": round(cap, 1),
            "token_range_in_source": [min(e[tk]["total"] for e in src),
                                      max(e[tk]["total"] for e in src)],
            "n_inside": len(inside),
            "n_movable": len(movable),
            "by_vendor_inside": {v: sum(1 for e in inside if e["vendor"] == v)
                                 for v in sorted(vendor_cheapest)},
            "savings_usd": round(save, 2),
        }
    return out

def free_lanes(episodes, pricing):
    """Old-generation -> newest-generation moves within the same pricing
    ladder, same vendor, same tier. Derived, not hardcoded: on chunk-01 this
    yields opus-4-8/4-6 -> opus-5 ($0, cache/ops uniformity) and
    sonnet-4-6 -> sonnet-5 (real price cut)."""
    meta = {}
    for e in episodes:
        meta[e["model"]] = (e["vendor"], e["tier"], ladder_key(e["model"], pricing),
                            version_tuple(e["generation"]))
    lanes = []
    for m in sorted(meta):
        v, tier, ladder, gen = meta[m]
        newer = [m2 for m2, (v2, t2, l2, g2) in meta.items()
                 if v2 == v and t2 == tier and l2 == ladder and g2 > gen]
        if not newer:
            continue
        target = max(newer, key=lambda m2: meta[m2][3])
        eps = [e for e in episodes if e["model"] == m]
        delta_rate = uncached_rate(m, pricing) - uncached_rate(target, pricing)
        lanes.append({
            "from_model": m,
            "to_model": target,
            "n_episodes": len(eps),
            "rate_delta_per_1m_uncached_in": round(delta_rate, 4),
            "savings_usd": {lab: round(sum(e[tk]["total"] for e in eps) * delta_rate / 1e6, 4)
                            for lab, tk in TOK_KEYS.items()},
            "note": ("identical price: $0 saving, value is cache/ops uniformity"
                     if delta_rate == 0 else "real price cut"),
        })
    return lanes

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("episodes", help="results/episodes.jsonl from solution/episodes.py")
    ap.add_argument("--out", default=None,
                    help="per-family table (default: <episodes_dir>/families.jsonl)")
    ap.add_argument("--pricing", default=None,
                    help="pricing table (default: <repo>/scripts/pricing.json)")
    ap.add_argument("--summary", default=None,
                    help="aggregates json (default: <out_dir>/experiments_summary.json)")
    args = ap.parse_args()

    ep_path = Path(args.episodes)
    out_path = Path(args.out) if args.out else ep_path.parent / "families.jsonl"
    pricing_path = (Path(args.pricing) if args.pricing
                    else Path(__file__).resolve().parent.parent / "scripts" / "pricing.json")
    summary_path = Path(args.summary) if args.summary else out_path.parent / "experiments_summary.json"

    pricing = load_pricing(pricing_path)
    with open(ep_path) as f:
        episodes = sorted((json.loads(l) for l in f if l.strip()),
                          key=lambda e: e["row_id"])
    families = defaultdict(list)
    for e in episodes:
        families[e["family_id"]].append(e)

    family_rows = [build_family_record(fid, mem, pricing)
                   for fid, mem in sorted(families.items())]
    family_rows.sort(key=lambda r: (-r["n_members"], r["family_id"]))

    total_cost = {lab: sum(input_cost(e, e["model"], pricing, tk) for e in episodes)
                  for lab, tk in TOK_KEYS.items()}
    mm_rows = [r for r in family_rows if r["multi_model"]]

    certified = {}
    for variant in ("same_vendor", "any_stack"):
        certified[variant] = {}
        for lab in TOK_KEYS:
            usd = sum(r["floor_savings_usd"][variant][lab] for r in mm_rows)
            certified[variant][lab] = {"usd": round(usd, 2),
                                       "pct_of_total_input_spend": round(100 * usd / total_cost[lab], 1)}

    rand = randomization_test(episodes, families, pricing)
    parity = pooled_parity(family_rows)
    envelope = envelope_argument(episodes, pricing)
    lanes = free_lanes(episodes, pricing)
    floor_worse = [r["family_id"] for r in mm_rows if r["quality_parity"]["floor_worse"]]
    floor_worse_sig = [r["family_id"] for r in mm_rows
                       if r["quality_parity"]["floor_worse_significant"]]

    summary = {
        "method_note": ("PRELIMINARY costs: input tokens x uncached rate (no cache modeling); "
                        "PRELIMINARY quality: tool-call err/retry/dup proxies. "
                        "WP-B/WP-C/WP-G supersede."),
        "n_episodes": len(episodes),
        "n_families": len(family_rows),
        "n_multi_member_families": sum(1 for r in family_rows if r["n_members"] >= 2),
        "n_multi_model_families": len(mm_rows),
        "n_episodes_in_multi_model_families": sum(r["n_members"] for r in mm_rows),
        "total_input_spend_usd": {lab: round(v, 2) for lab, v in total_cost.items()},
        "certified_floor_savings": certified,
        "randomization_evidence": rand,
        "pooled_quality_parity": parity,
        "envelope_argument": envelope,
        "free_lanes": lanes,
        "floor_worse_families": {"n": len(floor_worse), "family_ids": floor_worse,
                                 "n_significant": len(floor_worse_sig),
                                 "significant_family_ids": floor_worse_sig},
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        for r in family_rows:
            f.write(json.dumps(r) + "\n")
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=1)

    # ------------------------------------------------------------- report
    print(f"families={len(family_rows)}  multi-member={summary['n_multi_member_families']}"
          f"  multi-model={len(mm_rows)} (covering {summary['n_episodes_in_multi_model_families']} episodes)")
    print(f"total input spend: chars4 ${total_cost['chars4']:.2f}  bpe ${total_cost['bpe']:.2f}\n")

    print("[1] CERTIFIED floor savings (multi-model families only):")
    for variant in ("same_vendor", "any_stack"):
        c = certified[variant]
        print(f"    {variant:11s}  chars4 ${c['chars4']['usd']:.2f} ({c['chars4']['pct_of_total_input_spend']}%)"
              f"   bpe ${c['bpe']['usd']:.2f} ({c['bpe']['pct_of_total_input_spend']}%)")

    print(f"\n[2] Randomization: observed {rand['observed_frac_single_model']:.1%} of multi-member"
          f" families single-model vs null {rand['null_mean']:.1%} +/- {rand['null_sd']:.1%}"
          f" (z={rand['z']}, {rand['n_shuffles']} within-vendor shuffles, seed={rand['seed']})")

    print(f"\n[3] Pooled quality parity (floor-price arm vs premium arms, {parity['n_families_pooled']} families):")
    print(f"    floor err rate {parity['floor_err_rate']:.4f} ({parity['floor_err']}/{parity['floor_calls']})"
          f"  vs premium {parity['above_err_rate']:.4f} ({parity['above_err']}/{parity['above_calls']})")
    print(f"    diff {parity['rate_diff_floor_minus_above']:+.4f}  CI95 {parity['diff_ci95']}"
          f"  z={parity['z']} p={parity['p_two_sided']}")
    print("    HEADLINE " + ("SUPPORTED" if parity["headline_supported"] else "KILLED")
          + ": cheap tiers show no measurable excess failure rate on the same jobs"
          + ("" if parity["headline_supported"] else " -- floor arm significantly worse"))

    env = envelope
    print(f"\n[4] {env['label']}: {env['envelope_model']} envelope"
          f" (triggers={env['trigger_types']}, n_calls<={env['max_n_calls']})")
    for lab in TOK_KEYS:
        e = env[lab]
        pct = 100 * e["savings_usd"] / total_cost[lab]
        print(f"    {lab:6s} token cap p95={e['token_p95_cap']:.0f}: {e['n_inside']} episodes inside,"
              f" {e['n_movable']} movable -> ${e['savings_usd']:.2f} ({pct:.1f}% of spend)")

    print("\n[5] Free lanes (same ladder+tier, newer generation):")
    for ln in lanes:
        print(f"    {ln['from_model']} -> {ln['to_model']}: {ln['n_episodes']} eps,"
              f" chars4 ${ln['savings_usd']['chars4']:.2f} / bpe ${ln['savings_usd']['bpe']:.2f}"
              f"  ({ln['note']})")

    print(f"\nfloor-worse families: {len(floor_worse)}/{len(mm_rows)}"
          f" (significant at p<{ALPHA}: {len(floor_worse_sig)})")

    print("\ntop-10 multi-model families by any-stack chars4 saving:")
    top = sorted(mm_rows, key=lambda r: -r["floor_savings_usd"]["any_stack"]["chars4"])[:10]
    print(f"    {'family_id':34s} {'n':>3s} {'models':40s} {'floor':18s} {'save$':>7s} {'parity'}")
    for r in top:
        mix = ",".join(f"{m.split('-', 1)[1]}:{n}" for m, n in sorted(r["model_mix"].items()))
        par = r["quality_parity"]
        flag = ("WORSE*" if par["floor_worse_significant"]
                else "worse" if par["floor_worse"] else "ok")
        print(f"    {r['family_id']:34s} {r['n_members']:3d} {mix[:40]:40s}"
              f" {r['observed_floor_model']:18s}"
              f" {r['floor_savings_usd']['any_stack']['chars4']:7.2f} {flag}")

    print(f"\nwrote {out_path} and {summary_path}")

if __name__ == "__main__":
    main()
