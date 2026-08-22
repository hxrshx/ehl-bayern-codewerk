#!/usr/bin/env python3
"""Cache-aware cost model on ESTIMATED tokens, for the rule-based router.

Same mechanics as baseline/scripts/cost_model.py (kept as its own copy per this
folder's self-contained setup — see pricing.json), pointed at this folder's own
load_trajectories.est_tokens.

With the current export chunk, every reconstructed trajectory turns out to be a
single call (see load_trajectories.py's prefix-continuation check), so the cache
branch never actually fires here — cost reduces to uncached_price * input_tokens.
The logic is kept cache-aware anyway so it stays correct if a future chunk has
real multi-call trajectories. Output tokens are unknowable (no usage/output in
the export) and are NOT included in any cost figure — say so when quoting numbers.
"""
import json
from pathlib import Path
from load_trajectories import est_tokens

def load_pricing():
    p = Path(__file__).parent / "pricing.json"
    return json.loads(p.read_text())

def price_of(model, pricing):
    if model in pricing: return pricing[model]
    for prefix in sorted(pricing, key=len, reverse=True):
        if prefix != "_default" and model.startswith(prefix): return pricing[prefix]
    return pricing["_default"]

def shared_prefix_tokens(prev_req, req):
    shared = 0
    for a, b in zip(prev_req["input"], req["input"]):
        if a == b: shared += est_tokens(a)
        else: break
    return shared

def trajectory_cost(calls, route, pricing=None):
    """Cost of a reconstructed trajectory if call i had been served by route[i].
    Returns (usd, uncached_input_tokens_est)."""
    pricing = pricing or load_pricing()
    usd, uncached_total = 0.0, 0
    for i, c in enumerate(calls):
        inp = est_tokens(c["input"])
        cached = shared_prefix_tokens(calls[i - 1], c) if (i > 0 and route[i] == route[i - 1]) else 0
        cached = min(cached, inp)
        uncached = inp - cached
        pu, pc, _ = price_of(route[i], pricing)
        usd += (uncached * pu + cached * pc) / 1e6
        uncached_total += uncached
    return usd, uncached_total

def logged_route(calls): return [c["model"] for c in calls]
