#!/usr/bin/env python3
"""Honest cost model: cache-aware by default, tools counted, output priced.

Fixes three holes in the starter cost model (see analysis.md L3):
  1. `tools` blocks are real billed input (~19% of the bill) — the starter drops them.
  2. Output tokens are never priced — so an opus<->gpt-sol swap costs exactly $0
     on the input side while their output rates differ by 20%.
  3. Cache credit is only meaningful when a cheap model's UNCACHED rate does not
     already undercut a premium model's CACHED rate. It usually does here
     (luna $0.20/M < fable cached $1.00/M), so cache-preservation is never a
     reason to keep an expensive model. We still price it correctly.

Cache-aware is the DEFAULT and only mode (the challenge deck: "price it
cache-aware"). Request k of a chain re-sends every item of request k-1: that
shared prefix bills at the cached rate iff route[k] == route[k-1], else full
price. Chunk-01 is 1000 single-request episodes, so cache credit is ~0 there —
that is a finding, not a bug; the machinery is exercised on chains in --selftest.

Every token count is an ESTIMATE (chars/4 or tiktoken BPE over payload text).
Output tokens are a LOWER BOUND: only model-generated items recoverable from the
echoed history (assistant messages + function_call args), never the final answer
of the last request. Say so whenever quoting a number from this.

Usage:
  python3 solution/cost2.py results/episodes.jsonl --gen results/gen_tokens.jsonl
  python3 solution/cost2.py results/episodes.jsonl --selftest
"""
import argparse, json, sys
from pathlib import Path

VARIANTS = [{"tok": t, "tools": to, "output": o}
            for t in ("chars4", "bpe") for to in (False, True) for o in (False, True)]
ANCHOR = {"tok": "chars4", "tools": False, "output": False}   # starter assumptions
HEADLINE = {"tok": "bpe", "tools": True, "output": True}      # what we quote

def load_pricing(path=None):
    """Prices come from pricing.json — the organiser-pinned sheet, and the single
    source of truth. Searched next to this file first, then the repo's own copies,
    so this works wherever the folder is dropped. Raises rather than guessing."""
    if path:
        cands = [Path(path)]
    else:
        here = Path(__file__).resolve().parent
        cands = [here / "pricing.json",
                 here.parent / "scripts" / "pricing.json",
                 here.parent.parent / "rule_based_router" / "scripts" / "pricing.json",
                 here.parent.parent / "baseline" / "scripts" / "pricing.json"]
    for c in cands:
        if c.exists():
            return json.loads(c.read_text())
    raise FileNotFoundError(
        "pricing.json not found in " + ", ".join(str(c) for c in cands) +
        " — it is the only source of model prices; restore it rather than "
        "substituting assumed rates.")

def price_of(model, pricing):
    if model in pricing: return pricing[model]
    for pre in sorted(pricing, key=len, reverse=True):
        if pre != "_default" and model.startswith(pre): return pricing[pre]
    return pricing["_default"]

def episode_cost(ep, route, variant, pricing, gen_tokens=None):
    """USD for one episode if request k had been served by route[k].

    route: list of model ids, len == ep["n_requests"] (a bare string is broadcast).
    Cache-aware: shared prefix bills cached iff route[k] == route[k-1]."""
    tok = variant["tok"]
    ledger = ep.get("req_ledger") or [{
        "model": ep["model"], "chars4": ep["tok_chars4"]["total"],
        "bpe": ep["tok_bpe"]["total"], "shared_chars4": 0, "shared_bpe": 0}]
    if isinstance(route, str): route = [route] * len(ledger)
    if len(route) != len(ledger):
        raise ValueError(f"route len {len(route)} != {len(ledger)} requests")

    usd = 0.0
    for k, (rec, m) in enumerate(zip(ledger, route)):
        pu, pc, _ = price_of(m, pricing)
        total = rec[tok]
        shared = rec["shared_" + tok] if (k and m == route[k - 1]) else 0
        shared = min(shared, total)
        usd += ((total - shared) * pu + shared * pc) / 1e6
        if variant["tools"]:                      # tools re-sent every request
            usd += ep["tok_" + tok]["tools"] * (pc if shared else pu) / 1e6
    if variant["output"] and gen_tokens is not None:
        g = gen_tokens.get(ep["row_id"], {})
        n = g.get("gen_bpe" if tok == "bpe" else "gen_chars4", 0)
        _, _, po = price_of(route[-1], pricing)   # attribute output to serving model
        usd += n * po / 1e6
    return usd

def logged_cost(eps, variant, pricing, gen_tokens=None):
    return sum(episode_cost(e, [r["model"] for r in e.get("req_ledger")] or [e["model"]],
                            variant, pricing, gen_tokens) for e in eps)

def vname(v): return f"{v['tok']:6s} tools={str(v['tools']):5s} output={str(v['output']):5s}"

def selftest(pricing):
    """Prove the cache machinery on a synthetic 2-request chain."""
    ep = {"row_id": "synthetic:0", "n_requests": 2,
          "tok_chars4": {"tools": 1000}, "tok_bpe": {"tools": 1000},
          "req_ledger": [
              {"model": "claude-opus-5", "chars4": 10_000, "bpe": 10_000, "shared_chars4": 0, "shared_bpe": 0},
              {"model": "claude-opus-5", "chars4": 30_000, "bpe": 30_000, "shared_chars4": 10_000, "shared_bpe": 10_000}]}
    v = {"tok": "chars4", "tools": False, "output": False}
    same = episode_cost(ep, ["claude-opus-5", "claude-opus-5"], v, pricing)
    swap = episode_cost(ep, ["claude-opus-5", "claude-sonnet-5"], v, pricing)
    # same-model: 10k unc + (30k-10k unc + 10k cached); switch: 10k unc @opus + 30k unc @sonnet
    exp_same = (10_000 * 5.0 + 20_000 * 5.0 + 10_000 * 0.5) / 1e6
    exp_swap = (10_000 * 5.0 + 30_000 * 2.0) / 1e6
    print(f"  same-model chain : ${same:.6f}  (expected ${exp_same:.6f})  {'OK' if abs(same-exp_same)<1e-9 else 'FAIL'}")
    print(f"  switched chain   : ${swap:.6f}  (expected ${exp_swap:.6f})  {'OK' if abs(swap-exp_swap)<1e-9 else 'FAIL'}")
    print(f"  cache credit kept by staying on one model: ${exp_swap and (10_000*5.0-10_000*0.5)/1e6:.6f} on the shared prefix")
    # the arbitrage check that kills 'caching protects premium models'
    print("\n  cheap-uncached vs premium-cached (per 1M):")
    for prem in ("claude-fable-5", "claude-opus-5"):
        for cheap in ("gpt-5.6-luna", "claude-sonnet-5"):
            pc = price_of(prem, pricing)[1]; cu = price_of(cheap, pricing)[0]
            print(f"    {cheap} uncached ${cu:.2f} {'<' if cu < pc else '>='} {prem} cached ${pc:.2f}"
                  f"  -> caching {'does NOT' if cu < pc else 'DOES'} protect {prem}")
    return abs(same - exp_same) < 1e-9 and abs(swap - exp_swap) < 1e-9

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("episodes", nargs="?", default="results/episodes.jsonl")
    ap.add_argument("--gen", default="results/gen_tokens.jsonl")
    ap.add_argument("--out", default="results/costs_summary.json")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    pricing = load_pricing()

    if a.selftest:
        print("cache machinery self-test:"); sys.exit(0 if selftest(pricing) else 1)

    eps = [json.loads(l) for l in open(a.episodes)]
    gen = {}
    if Path(a.gen).exists():
        gen = {d["row_id"]: d for d in map(json.loads, open(a.gen))}

    anchor = logged_cost(eps, ANCHOR, pricing, gen)
    print(f"episodes={len(eps)}  requests={sum(e['n_requests'] for e in eps)}")
    print(f"\nANCHOR (starter assumptions: chars4, no tools, no output): ${anchor:,.4f}")
    print("  starter kit prints $87.4229 for the same corpus. The $0.0965 gap is NOT an error:")
    print("  the starter credits a cached rate on 35,344 tokens of 'shared prefix' between")
    print("  requests its grouping merged into fake trajectories (analysis.md L1). Those are")
    print("  separate cron runs that never shared a prefix, so no cache discount is owed.")
    print("  Disabling that credit inside the starter's own code reproduces $87.5194 exactly.")
    print(f"  -> {'MATCH (uncached-corrected anchor)' if abs(anchor-87.5194)<0.005 else 'MISMATCH'}")

    print("\n8-variant grid of logged cost:")
    grid = {}
    for v in VARIANTS:
        c = logged_cost(eps, v, pricing, gen)
        grid[vname(v).strip()] = round(c, 4)
        print(f"  {vname(v)}  ${c:9,.4f}   {(c/anchor-1)*+100:+6.1f}% vs anchor")

    head = logged_cost(eps, HEADLINE, pricing, gen)
    print(f"\nHEADLINE (bpe + tools + output): ${head:,.4f}  ({(head/anchor-1)*100:+.1f}% vs anchor)")
    tools_only = logged_cost(eps, {"tok": "bpe", "tools": True, "output": False}, pricing, gen)
    bpe_only = logged_cost(eps, {"tok": "bpe", "tools": False, "output": False}, pricing, gen)
    print(f"  decomposition: bpe vs chars4 {(bpe_only/anchor-1)*100:+.1f}%"
          f" | +tools {(tools_only/bpe_only-1)*100:+.1f}% | +output {(head/tools_only-1)*100:+.1f}%")

    per_model = {}
    for e in eps:
        c = episode_cost(e, [r["model"] for r in e["req_ledger"]], HEADLINE, pricing, gen)
        d = per_model.setdefault(e["model"], [0, 0.0]); d[0] += 1; d[1] += c
    print("\nper-model logged cost (headline variant):")
    for m, (n, c) in sorted(per_model.items(), key=lambda kv: -kv[1][1]):
        print(f"  {m:20s} n={n:4d}  ${c:8.4f}  ({c/head*100:5.1f}%)")

    print("\ncache machinery:")
    ok = selftest(pricing)
    Path(a.out).write_text(json.dumps({
        "anchor_usd": round(anchor, 4), "headline_usd": round(head, 4),
        "grid": grid, "per_model_headline": {m: {"n": n, "usd": round(c, 4)} for m, (n, c) in per_model.items()},
        "cache_selftest_ok": ok, "variants": {"anchor": ANCHOR, "headline": HEADLINE}}, indent=2))
    print(f"\nwrote {a.out}")

if __name__ == "__main__": main()
