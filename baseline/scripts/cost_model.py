#!/usr/bin/env python3
"""Cache-aware cost model on ESTIMATED tokens.

The export has no `usage` field: every token count here is estimated as
serialized chars / 4 (see load_trajectories.est_tokens). The cache trap still
applies: providers cache the shared input prefix across consecutive calls of a
task, and a model switch resets that cache — the first call after a switch
pays the uncached rate for the whole prefix.

Cached share of call i is estimated as the token size of the item-level prefix
that call i shares with call i-1 of the same trajectory.

Model ids in the export are anonymized (families claude-opus/-sonnet/-fable and
gpt-5.6-* across generations, e.g. claude-opus-5, claude-opus-4-8, gpt-5.6-terra),
so no public price sheet exists. Known tier facts: fable is the MOST expensive
model (organizer-confirmed); opus > sonnet within claude. Exact rates in
DEFAULT_PRICING are still an ASSUMPTION for relative comparisons; unknown ids
fall back to their family rate by prefix match — if a price sheet is posted in
the challenge Discord, put it in scripts/pricing.json; either way, state your
pricing assumption in the writeup.
"""
import json
from pathlib import Path
from load_trajectories import est_tokens

DEFAULT_PRICING = {  # per 1M est. tokens: [uncached_input, cached_input, output] — ASSUMED, not official
    "claude-opus": [15.00, 1.50, 75.00],   # family prefixes: match any generation (claude-opus-5, claude-opus-4-8, ...)
    "claude-sonnet": [3.00, 0.30, 15.00],
    "claude-fable": [20.00, 2.00, 100.00],  # premium tier — organizer-confirmed most expensive; exact rates still assumed
    "gpt-5.6": [2.00, 0.50, 8.00],
    "_default": [2.00, 0.40, 8.00],
}

def load_pricing():
    p = Path(__file__).parent / "pricing.json"
    return json.loads(p.read_text()) if p.exists() else DEFAULT_PRICING

def price_of(model, pricing):
    if model in pricing: return pricing[model]
    for prefix in sorted(pricing, key=len, reverse=True):  # longest family prefix wins
        if prefix != "_default" and model.startswith(prefix): return pricing[prefix]
    return pricing["_default"]

def shared_prefix_tokens(prev_req, req):
    """Estimated tokens of the item-level input prefix shared with the previous call."""
    shared = 0
    for a, b in zip(prev_req["input"], req["input"]):
        if a == b: shared += est_tokens(a)
        else: break
    return shared

def trajectory_cost(calls, route, pricing=None):
    """Cost of a reconstructed trajectory (calls ordered by input length) if call i
    had been served by route[i]. Cache-aware: the shared prefix is billed at the
    cached rate only when route[i] == route[i-1]. Output tokens are unknowable
    (no outputs in the export) and are NOT included — say so when you quote numbers.
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
