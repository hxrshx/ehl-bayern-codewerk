#!/usr/bin/env python3
"""Annotate a NEW, previously-unseen jsonl file with this router's decisions —
e.g. a held-out validation/test set handed over separately from the main
export, in the same three-field schema (model, input, tools).

Treats each row as its own independent trajectory. No grouping/reconstruction
step is attempted: per the organizers' own ruling (confirmed independently in
evaluation/EVALUATION.md — no row's input is a prefix of another's), this
export's rows are not prefix-chained, so grouping by opening-message hash would
only risk the phantom-trajectory bug documented in
rule_based_router/scripts/load_trajectories.py, for no benefit on data shaped
like this.

router.py and feature_extraction.py are imported UNMODIFIED — this script only
calls them and records what they decided, so there's no risk of the annotation
logic drifting from what the actual router does.

Usage:
  python3 validation/annotate.py <file-or-dir.jsonl> [out.jsonl]

Writes one annotated row per input row:
  row_id, proposed_model, rules_fired, and — only if the row carries a `model`
  field to compare against — logged_model, cost_logged_usd, cost_proposed_usd.
A file with no `model` field (a truly blind test set) still gets routed; it
just prints routing decisions only, since there's nothing to cost-compare.
"""
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "rule_based_router" / "scripts"))
from cost_model import load_pricing, trajectory_cost  # noqa: E402
from feature_extraction import extract_features  # noqa: E402
from router import route_call  # noqa: E402

def iter_rows(path):
    p = Path(path)
    files = sorted(p.glob("*.jsonl")) if p.is_dir() else [p]
    if not files:
        sys.exit(f"no .jsonl files found at {path}")
    for f in files:
        with open(f) as fh:
            for i, line in enumerate(fh):
                if line.strip():
                    yield f"{f.name}:{i}", json.loads(line)

def main():
    if len(sys.argv) < 2:
        sys.exit("usage: python3 validation/annotate.py <file-or-dir.jsonl> [out.jsonl]")
    src = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else str(Path(__file__).resolve().parent / "results" / "annotated.jsonl")
    pricing = load_pricing()

    rows_out, n_rerouted, n_with_logged = [], 0, 0
    total_logged = total_proposed = 0.0

    for row_id, req in iter_rows(src):
        call = req  # each row is its own single-call trajectory — see module docstring
        feats = extract_features(row_id, [call])[0]
        proposed_model, rules = route_call(feats)
        rec = {"row_id": row_id, "proposed_model": proposed_model, "rules_fired": rules}

        logged_model = req.get("model")
        if logged_model:
            n_with_logged += 1
            c_logged, _ = trajectory_cost([call], [logged_model], pricing)
            c_proposed, _ = trajectory_cost([call], [proposed_model], pricing)
            rec.update(logged_model=logged_model,
                       cost_logged_usd=round(c_logged, 6),
                       cost_proposed_usd=round(c_proposed, 6))
            total_logged += c_logged
            total_proposed += c_proposed
            n_rerouted += proposed_model != logged_model

        rows_out.append(rec)

    Path(out_path).parent.mkdir(exist_ok=True, parents=True)
    with open(out_path, "w") as f:
        for r in rows_out:
            f.write(json.dumps(r) + "\n")

    n = len(rows_out)
    print(f"annotated {n} rows from {src}")
    if n_with_logged:
        print(f"rows with a logged model to compare against: {n_with_logged}")
        print(f"rerouted (proposed != logged): {n_rerouted} ({n_rerouted / n_with_logged:.1%})")
        print(f"total logged cost:   ${total_logged:,.4f}")
        print(f"total proposed cost: ${total_proposed:,.4f}  ({(total_proposed / total_logged - 1):+.1%} vs logged)")
        print("NOTE: cost only, input-token estimate, no cache credit across rows (each row is its")
        print("      own trajectory) — no quality/outcome claim is made here. See evaluation/ for")
        print("      the off-policy quality methodology used on the main corpus.")
    else:
        print("no 'model' field found on any row — routing decisions only, no cost comparison possible.")
    print(f"wrote {out_path}")

if __name__ == "__main__":
    main()
