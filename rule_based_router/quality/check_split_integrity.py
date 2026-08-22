#!/usr/bin/env python3
"""Step 5 (separate small task): verify the train/val/test split we're using
is actually safe to lean on for methodology (tune-on-train, report-on-test),
rather than silently leaking or having been built after the fact.

The split lives in trajectories_v1_01_labeled.jsonl (found in ~/Downloads, not
organizer-provided — we built/found it ourselves, per the conversation). Two
checks, both against that file's _persona_group and _split fields:

  1. No persona group appears in more than one of train/val/test. If a group
     leaks across splits, tuning against train could still indirectly "see"
     test examples via a shared recurring template, invalidating any
     held-out claim built on this split.
  2. The split file's mtime predates this project's tuned threshold files
     (router.py, keywords_config.json). This is WEAK evidence only — an mtime
     is not a commit trail and can be touched or copied — so a pass here is
     reported as "consistent with", never "proof of", pre-tuning creation.

Usage: python rule_based_router/quality/check_split_integrity.py [labeled.jsonl]
"""
import json, os, sys
from pathlib import Path
from collections import defaultdict

DEFAULT_LABELED_PATH = Path.home() / "Downloads" / "trajectories_v1_01_labeled.jsonl"
TUNED_FILES = [
    Path(__file__).resolve().parent.parent / "scripts" / "router.py",
    Path(__file__).resolve().parent.parent / "scripts" / "keywords_config.json",
]

def check_no_cross_split_leakage(labeled_path):
    group_splits = defaultdict(set)
    with open(labeled_path) as f:
        for line in f:
            r = json.loads(line)
            group_splits[r["_persona_group"]].add(r["_split"])
    return {g: s for g, s in group_splits.items() if len(s) > 1}

def check_timestamp_precedence(labeled_path):
    """WEAK check only. Returns (labeled_mtime, {tuned_file: {mtime, labeled_older}})."""
    labeled_mtime = os.path.getmtime(labeled_path)
    results = {}
    for f in TUNED_FILES:
        if f.exists():
            tuned_mtime = os.path.getmtime(f)
            results[str(f)] = {"tuned_file_mtime": tuned_mtime, "labeled_older": labeled_mtime < tuned_mtime}
    return labeled_mtime, results

def main():
    labeled_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_LABELED_PATH
    if not labeled_path.exists():
        sys.exit(f"labeled file not found at {labeled_path} — pass its path explicitly.")

    leaking = check_no_cross_split_leakage(labeled_path)
    print(f"persona groups appearing in >1 split: {len(leaking)}")
    if leaking:
        print("  LEAKAGE DETECTED — this split is NOT safe to use as a held-out guarantee as-is:")
        for g, s in list(leaking.items())[:10]:
            print(f"    group={g}  splits={sorted(s)}")
    else:
        print("  none found — no persona group crosses train/val/test.")

    labeled_mtime, results = check_timestamp_precedence(labeled_path)
    print(f"\nlabeled file mtime: {labeled_mtime}")
    print("timestamp precedence check (WEAK evidence only — not proof, see docstring):")
    for f, info in results.items():
        verdict = ("consistent with pre-tuning" if info["labeled_older"]
                   else "labeled file is NEWER than this tuned file — inconclusive/concerning")
        print(f"  {f}: {verdict}")

    print("\nVERDICT: ", end="")
    if leaking:
        print("FAIL — cross-split leakage found. Do not claim this split protects against overfitting.")
    elif results and not all(info["labeled_older"] for info in results.values()):
        print("INCONCLUSIVE — no leakage, but timestamp evidence doesn't clearly support 'split predates "
              "tuning'. State this explicitly if you use the split for any held-out claim.")
    else:
        print("PASS (with caveats) — no leakage found, and mtime evidence is consistent with the split "
              "predating this project's threshold tuning. Still not cryptographic proof.")

if __name__ == "__main__": main()
