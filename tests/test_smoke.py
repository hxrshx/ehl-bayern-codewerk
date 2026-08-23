"""Smoke tests: the three invariants the headline numbers rest on.
Run: python3 tests/test_smoke.py   (no pytest needed)"""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evaluation" / "lib"))
from cost2 import load_pricing, price_of, episode_cost, HEADLINE

def test_pricing_ladder_is_the_pinned_sheet():
    """fable-5 must be dearest and luna cheapest. If this flips, every cost
    claim inverts — this is the check that catches a swapped price sheet."""
    p = load_pricing()
    assert price_of("claude-fable-5", p)[0] == 10.0
    assert price_of("gpt-5.6-luna", p)[0] == 0.20
    assert price_of("claude-fable-5", p)[0] > price_of("claude-opus-5", p)[0] > price_of("gpt-5.6-luna", p)[0]

def test_cheap_uncached_undercuts_premium_cached():
    """luna at full price is cheaper than fable at its discount, so 'protect the
    cache' can never justify keeping the dear model."""
    p = load_pricing()
    assert price_of("gpt-5.6-luna", p)[0] < price_of("claude-fable-5", p)[1]

def test_cache_aware_pricing_charges_more_after_a_switch():
    """A model switch resets the prefix cache: the switched route must cost more
    than the same-model route on an identical two-request chain."""
    p = load_pricing()
    ep = {"row_id": "t:0", "n_requests": 2,
          "tok_chars4": {"tools": 0}, "tok_bpe": {"tools": 0},
          "req_ledger": [
              {"model": "claude-opus-5", "chars4": 10_000, "bpe": 10_000, "shared_chars4": 0, "shared_bpe": 0},
              {"model": "claude-opus-5", "chars4": 30_000, "bpe": 30_000, "shared_chars4": 10_000, "shared_bpe": 10_000}]}
    v = {"tok": "chars4", "tools": False, "output": False}
    same = episode_cost(ep, ["claude-opus-5", "claude-opus-5"], v, p)
    swap = episode_cost(ep, ["claude-opus-5", "claude-fable-5"], v, p)
    assert swap > same, "switching must lose the cache discount"

if __name__ == "__main__":
    fails = 0
    for name, fn in sorted((k, v) for k, v in globals().items() if k.startswith("test_")):
        try:
            fn(); print(f"  PASS  {name}")
        except AssertionError as e:
            fails += 1; print(f"  FAIL  {name}: {e}")
    print(f"\n{'all passed' if not fails else str(fails) + ' failed'}")
    sys.exit(1 if fails else 0)
