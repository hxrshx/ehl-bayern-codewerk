#!/usr/bin/env python3
"""Shared difficulty-bucket definition for the quality module (Steps 2-4).

Not something that already existed as an explicit function in router.py /
feature_extraction.py — those files only have continuous token-count features
and single threshold constants, no discrete bucket function. This bucketer
REUSES those existing thresholds directly rather than inventing new cutoffs:
  - LOW / MEDIUM boundary = router.py's own reasoning about "small" (mirrors
    baseline/scripts/baseline_router.py's SMALL_TRAJECTORY = 15,000 est.
    tokens). Copied as a literal here rather than cross-imported from
    baseline/scripts/baseline_router.py on purpose: baseline/scripts and
    rule_based_router/scripts both define modules literally named
    load_trajectories.py / cost_model.py, and importing across both packages
    in one process risks sys.modules resolving the WRONG one of the two
    same-named modules (this exact collision was hit and debugged earlier
    when reconstructing trajectories — see load_trajectories.py's history).
    Keep this constant in sync by hand if baseline's threshold changes.
  - MEDIUM / HIGH boundary = router.py's own LARGE_INPUT_TOKENS (35,000 est.
    tokens, ~85th percentile of this export) — imported directly since this
    IS the same package (no collision risk).

This is a bucket over TASK SIZE only (cumulative_input_tokens_est), not a
composite "how hard is this" score — size is a legible, already-validated
proxy in this codebase, but it is NOT the same thing as true task difficulty
(a short prompt asking for something judgment-heavy would be misbucketed LOW).
Name this as a limitation wherever a bucket verdict is reported.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from router import LARGE_INPUT_TOKENS  # noqa: E402  (same package — safe, direct import)

SMALL_TRAJECTORY = 15_000  # literal copy of baseline/scripts/baseline_router.py's SMALL_TRAJECTORY — see docstring

BUCKETS = ["low", "medium", "high"]

def difficulty_bucket(cumulative_input_tokens_est):
    if cumulative_input_tokens_est < SMALL_TRAJECTORY: return "low"
    if cumulative_input_tokens_est < LARGE_INPUT_TOKENS: return "medium"
    return "high"

def bucket_for_all_tasks(export_dir):
    """Convenience: {trajectory_id: bucket} for every task, reusing
    feature_extraction.extract_features (import) rather than recomputing
    token estimates a third time in some other file."""
    from load_trajectories import iter_requests, group_trajectories
    from feature_extraction import extract_features
    groups = group_trajectories(r for _, _, r in iter_requests(export_dir))
    return {key: difficulty_bucket(extract_features(key, calls)[-1]["cumulative_input_tokens_est"])
            for key, calls in groups.items()}
