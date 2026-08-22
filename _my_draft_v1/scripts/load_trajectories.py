#!/usr/bin/env python3
"""Reconstruct Viktor trajectories from the raw per-call JSONL export.

Usage:
    python scripts/load_trajectories.py export/ [--out export/trajectories.json]

Input : export/*.jsonl (or *.jsonl.gz) -- one line per LLM call, each line
        {"model": ..., "input": [...], "tools": [...]}. No output, no usage,
        no trajectory ids ship with the data (see the challenge deck,
        "The dataset" / "A trajectory").
Output: a single JSON file with calls grouped back into ordered
        trajectories, with estimated token counts per call.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export_dir", type=Path, help="directory containing *.jsonl call exports")
    parser.add_argument("--out", type=Path, default=None, help="output path (default: <export_dir>/trajectories.json)")
    args = parser.parse_args()

    out_path = args.out or (args.export_dir / "trajectories.json")

    records = list(common.iter_jsonl_records(args.export_dir))
    if not records:
        print(f"No records found in {args.export_dir}. Nothing to do.", file=sys.stderr)
        sys.exit(1)

    trajectories, mismatches = common.reconstruct_trajectories(records)
    common.annotate_tokens(trajectories)

    payload = [common.trajectory_to_dict(t) for t in trajectories]
    out_path.write_text(json.dumps(payload, indent=2))

    call_counts = [len(t.calls) for t in trajectories]
    model_counts: dict[str, int] = {}
    for t in trajectories:
        model_counts[t.logged_model] = model_counts.get(t.logged_model, 0) + 1

    print(f"Loaded {len(records)} raw calls from {args.export_dir}")
    print(f"Reconstructed {len(trajectories)} trajectories")
    print(f"  calls/trajectory: min={min(call_counts)} max={max(call_counts)} "
          f"avg={sum(call_counts) / len(call_counts):.1f}")
    if mismatches:
        print(f"  WARNING: {mismatches} calls did not cleanly prefix-chain onto the "
              f"previous call in their group (ambiguous opening messages, or a real "
              f"branch). Ordering for those is a best effort.", file=sys.stderr)
    print("  model distribution (one model serves the whole trajectory, per the log):")
    for model, count in sorted(model_counts.items(), key=lambda kv: -kv[1]):
        print(f"    {model:30s} {count}")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
