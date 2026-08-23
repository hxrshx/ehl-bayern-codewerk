#!/usr/bin/env python3
"""Outcome signal v2 — revised BY the hand-check, not by tuning against results.

WHY v2 EXISTS (this is the audit trail, and it is the point):
v1 weights were pre-registered before any router result existed. We then did what
Viktor asked for in Discord — "hand-check a handful of examples and show where
your proxy agrees and disagrees" — on 12 stratified episodes. Result:

  AGREE    3/3 on the clean episodes (q=1.0). Final messages confirm real, verified
           completions ("page refreshed (38 records, HTTP 200, title verified)").
  DISAGREE 2 clear cases where v1 was WRONG:
    :807 scored 0.35 (our worst) yet finished a full gapfill scan, found 5 leads and
         explicitly verified delivery "not just claimed" — a thorough SUCCESS.
    :123 scored 0.50 yet correctly diagnosed a transient Airtable schema-cache error,
         re-ran, synced 86 clients, and correctly suppressed a false alert — GOOD
         judgement, penalised as failure.
  UNCLEAR 3 episodes have an EMPTY final assistant message (23.8% of the corpus),
          so no human can adjudicate them from the final answer alone.

Diagnosis: v1 counted every error the same. But 189 of 294 error episodes (64.3%)
RECOVERED — the failing call is followed by clean calls and a real final answer.
Penalising recovery is wrong: an agent that hits a transient fault, diagnoses it and
finishes is doing its job. What actually signals waste is UNRECOVERED error, thrash
(duplicate identical calls), and abnormal loop length versus the same job's norm.

v2 therefore splits errors into recovered vs unrecovered and discounts the former.
We report v1 and v2 side by side and never quietly replace one with the other.

Usage: python3 solution/quality_v2.py results/episodes.jsonl
"""
import argparse, json, statistics
from collections import defaultdict
from pathlib import Path

# --- v2 weights. Set from the hand-check diagnosis above, BEFORE re-running the router.
W_UNRECOVERED = 0.30   # error the episode never came back from — the real failure signal
W_RECOVERED   = 0.05   # transient fault the agent handled — small, not free
W_DUP         = 0.15   # identical call repeated: pure waste, no ambiguity
W_THRASH      = 0.20   # loop far longer than the same job's median
CAP_UNREC, CAP_REC = 3, 5

def score(ep, fm_nonempty):
    calls = ep["behavior"]["calls"]
    n = len(calls)
    errs = [i for i, c in enumerate(calls) if c["is_err"]]
    # an error is RECOVERED if a later call succeeds and the episode ends with a real answer
    last_err = max(errs) if errs else -1
    recovered_all = bool(errs) and last_err < n - 1 and fm_nonempty
    n_rec = len(errs) if recovered_all else 0
    n_unrec = 0 if recovered_all else len(errs)
    seen, dup = set(), 0
    for c in calls:
        k = (c["name"], c["args_sha8"])
        if k in seen: dup += 1
        seen.add(k)
    loop = ep.get("_loop_ratio", 1.0)
    q = 1.0
    q -= W_UNRECOVERED * min(n_unrec, CAP_UNREC) / CAP_UNREC
    q -= W_RECOVERED   * min(n_rec,   CAP_REC)   / CAP_REC
    q -= W_DUP         * (1.0 if dup else 0.0)
    q -= W_THRASH      * max(0.0, min(1.0, (loop - 2.0) / 2.0))
    return max(0.0, round(q, 4)), {"n_recovered_err": n_rec, "n_unrecovered_err": n_unrec,
                                   "dup_calls": dup, "loop_ratio": round(loop, 3),
                                   "recovered": recovered_all}

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("episodes", nargs="?", default="results/episodes.jsonl")
    ap.add_argument("--export", default="export")
    ap.add_argument("--out", default="results/quality_v2.jsonl")
    a = ap.parse_args()
    eps = [json.loads(l) for l in open(a.episodes)]
    by_id = {e["row_id"]: e for e in eps}

    # final-message emptiness must come from the raw export
    nonempty = {}
    for p in sorted(Path(a.export).glob("*.jsonl")):
        for i, line in enumerate(open(p)):
            rid = f"{p.name}:{i}"
            if rid not in by_id: continue
            fm = ""
            for it in reversed(json.loads(line)["input"]):
                if it.get("role") == "assistant":
                    c = it.get("content")
                    fm = c if isinstance(c, str) else " ".join(
                        x.get("text", "") for x in c if isinstance(x, dict))
                    break
            nonempty[rid] = bool(fm.strip())

    fam = defaultdict(list)
    for e in eps: fam[e["family_id"]].append(e["behavior"]["n_calls"])
    med = {k: statistics.median(v) for k, v in fam.items()}
    for e in eps:
        m = med.get(e["family_id"], 1) or 1
        e["_loop_ratio"] = e["behavior"]["n_calls"] / m

    v1 = {d["row_id"]: d["q_score"] for d in map(json.loads, open("results/quality.jsonl"))}
    rows, moved = [], []
    with open(a.out, "w") as f:
        for e in eps:
            q, parts = score(e, nonempty.get(e["row_id"], False))
            rows.append(q)
            d = v1.get(e["row_id"], q)
            if abs(q - d) >= 0.2: moved.append((e["row_id"], d, q))
            f.write(json.dumps({"row_id": e["row_id"], "q_score": q,
                                "q_score_v1": d, "final_msg_nonempty": nonempty.get(e["row_id"], False),
                                **parts}) + "\n")
    print(f"episodes={len(rows)}  wrote {a.out}")
    print(f"v1 mean {statistics.fmean(v1.values()):.4f}   v2 mean {statistics.fmean(rows):.4f}")
    print(f"episodes whose score moved >=0.20: {len(moved)}")
    for rid, d, q in sorted(moved, key=lambda t: -(t[2] - t[1]))[:6]:
        print(f"    {rid:34s} v1 {d:.2f} -> v2 {q:.2f}")
    ne = sum(1 for v in nonempty.values() if not v)
    print(f"\nempty final message: {ne}/{len(eps)} ({ne/len(eps):.1%}) — flagged, not silently scored")

if __name__ == "__main__": main()
