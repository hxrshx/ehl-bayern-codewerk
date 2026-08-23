#!/usr/bin/env python3
"""Constructed outcome/quality signal (q_score) per episode.

Reads results/episodes.jsonl (from solution/episodes.py) and emits one row per
episode to results/quality.jsonl with cheap, deterministic proxies for "did
the agent loop run cleanly", plus a composite q_score in [0, 1]. Works on any
episodes.jsonl chunk — nothing chunk-specific is hard-coded.

Per-episode metrics (all from the episode's ordered calls list unless noted):

  err_outputs   count of calls whose tool output matched episodes.ERR_RE
                (nonzero exit codes — JSON `"exit_code": N` or plain
                "Exit code: N" — Tracebacks, No such file, command not found,
                ENOENT, scanned over the first 4KB of the output). This is the
                ONE canonical error definition, imported from episodes.py so
                quality.py can never drift from the flags already stored in
                episodes.jsonl.
                Reconciliation vs. earlier exploratory numbers: a first-pass
                regex (narrower error-string classes / shorter scan window)
                flagged 352 outputs on chunk 01; the canonical ERR_RE flags
                569 because it also catches JSON-encoded exit codes and
                Tracebacks/ENOENT embedded in otherwise exit-0 wrappers. We do
                not force agreement — the canonical, broader-recall definition
                is what ships; the run summary reports both side by side.
  err_rate      err_outputs / max(1, n_calls).
  retries       retry events: call i is_err AND some call j in (i, i+2]
                (i.e. j = i+1 or i+2) has the same tool name but a DIFFERENT
                args_sha8 — the model reacted to the failure by re-invoking
                the tool with changed arguments. Counted once per erroring
                call i. Same-args re-runs are deliberately NOT retries; they
                fall under dup_calls instead.
  dup_calls     wasted exact repeats: sum over (name, args_sha8) pairs of
                (occurrences - 1). args_sha8 hashes redaction-NORMALIZED
                arguments (episodes.norm_text), so per-row PII numbering
                cannot fake or hide a duplicate.
  broken_calls  from one streaming pass over the raw export row (row_id maps
                file:line), sum of three malformation classes:
                  failed_tool    harness-injected pseudo function_call items
                                 named "failed_tool" recording a tool call
                                 that failed validation;
                  unknown_tool   a call whose name is not among that row's
                                 offered tools (excluding "failed_tool",
                                 already counted above) — e.g. a Claude row
                                 invoking the GPT harness's shell_command;
                  bad_json_args  a function_call whose arguments string fails
                                 json.loads. custom_tool_call input (type
                                 "custom" tools, e.g. apply_patch's patch
                                 grammar) is freeform BY CONTRACT and is
                                 exempt — on chunk 01 all 299 apply_patch
                                 inputs are non-JSON by design and zero
                                 function_call arguments are malformed.
  loop_ratio    n_calls / median(n_calls over the episode's family_id within
                this input file); 1.0 for singleton families (and whenever
                the median is 0). >2 means the episode burned twice its
                siblings' typical tool budget on the same recurring job.
  completed     the episode's final item is an assistant message (final_type
                == "message:assistant"). Expected 100% on chunk 01 — the
                field is kept anyway; its emptiness there is itself a
                finding, and held-out chunks may differ.

q_score rationale (weights below): start from 1.0 and subtract
  - err term (up to 0.15, saturating at 3 errors): errors are common and often
    benign probes (ls a missing file, grep with no match), so the penalty is
    small and capped — three-plus failures signal real friction, one does not.
  - retry term (0.20, binary): an error followed by a changed-args re-invoke
    is direct evidence the model had to correct course; flat because one
    observed correction already marks the episode as non-clean.
  - dup term (0.15, binary): exact repeats waste tokens but are sometimes
    legitimate polling; penalized less than retries.
  - broken term (0.20, binary): schema-level failures (wrong tool namespace,
    unparseable args) are the strongest capability signal in the data —
    the harness itself rejected the call.
  - loop term (0.10 per doubling beyond 2x family median): mild, graded
    penalty for runaway loops relative to the job's own baseline; unbounded
    above but the floor at 0 caps the total.
  - incomplete term (0.25, binary): an episode that never returns a final
    assistant message did not finish its turn — worst single signal; inert on
    chunk 01 (100% complete) but pre-registered for held-out chunks.
Floor at 0.

Determinism: no randomness, no timestamps; calibration sampling is ordered by
sha1(row_id); output preserves input row order.

Usage:
  python3 solution/quality.py results/episodes.jsonl [--out results/quality.jsonl]
      [--export-dir export] [--calibration results/calibration_sample.md]
"""
import argparse, json, re, sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parent))
from episodes import ERR_RE, item_text  # single canonical error definition + text extractor

# ------------------------------------------------------------ q_score weights
# PRE-REGISTERED 2026-08-22, set and frozen BEFORE any router results existed.
# Chosen on face validity + a sanity read of chunk-01 episodes only (never on
# routing outcomes). Only the human calibration round may revisit them
# (results/calibration_sample.md: >6/30 disagreements -> revisit).
W_ERR = 0.15         # x min(err_outputs, 3)/3
W_RETRY = 0.20       # x (retries > 0)
W_DUP = 0.15         # x (dup_calls > 0)
W_BROKEN = 0.20      # x (broken_calls > 0)
W_LOOP = 0.10        # x max(0, loop_ratio - 2)/2
W_INCOMPLETE = 0.25  # x (not completed)


# ----------------------------------------------------------- episode metrics
def behavior_of_row(ep):
    """episodes.jsonl nests behavior under 'behavior'; tolerate a flat layout."""
    return ep.get("behavior") or ep


def calls_metrics(beh):
    calls = beh.get("calls", [])
    n_calls = beh.get("n_calls", len(calls))
    err_outputs = sum(1 for c in calls if c["is_err"])
    retries = 0
    for i, c in enumerate(calls):
        if not c["is_err"]:
            continue
        if any(j < len(calls)
               and calls[j]["name"] == c["name"]
               and calls[j]["args_sha8"] != c["args_sha8"]
               for j in (i + 1, i + 2)):
            retries += 1
    pair_counts = Counter((c["name"], c["args_sha8"]) for c in calls)
    dup_calls = sum(v - 1 for v in pair_counts.values() if v > 1)
    return {"n_calls": n_calls,
            "err_outputs": err_outputs,
            "err_rate": round(err_outputs / max(1, n_calls), 4),
            "retries": retries,
            "dup_calls": dup_calls,
            "completed": beh.get("final_type", "") == "message:assistant"}


# ------------------------------------------------- raw-export streaming pass
def snippet(text, limit, at=0):
    """Whitespace-collapsed excerpt of ≤limit chars around position `at`."""
    start = max(0, at - 40)
    return re.sub(r"\s+", " ", text[start:start + limit + 80]).strip()[:limit]


def scan_export(export_dir, wanted, collect_excerpts):
    """One streaming pass over the raw chunks; only wanted lines are parsed.

    Returns {row_id: {"broken": {failed_tool, unknown_tool, bad_json_args},
                      "final_msg": str|None, "flagged": [(name, snip), ...]}}.
    """
    out = {}
    by_chunk = defaultdict(dict)  # chunk -> {line_no: row_id}
    for row_id in wanted:
        chunk, line = row_id.rsplit(":", 1)
        by_chunk[chunk][int(line)] = row_id
    for chunk in sorted(by_chunk):
        path = Path(export_dir) / chunk
        if not path.is_file():
            sys.exit(f"broken_calls pass needs the raw export: {path} not found "
                     f"(point --export-dir at the directory holding {chunk})")
        lines = by_chunk[chunk]
        with open(path) as f:
            for i, line in enumerate(f):
                if i not in lines:
                    continue
                out[lines[i]] = scan_row(json.loads(line), collect_excerpts)
    missing = set(wanted) - set(out)
    if missing:
        sys.exit(f"{len(missing)} row_ids not found in export "
                 f"(e.g. {sorted(missing)[:3]})")
    return out


def scan_row(req, collect_excerpts):
    items = req["input"]
    offered = {t.get("name", "") for t in req.get("tools", [])}
    outputs = {it.get("call_id"): it.get("output", "") for it in items
               if it.get("type") in ("function_call_output", "custom_tool_call_output")}
    broken = {"failed_tool": 0, "unknown_tool": 0, "bad_json_args": 0}
    broken_ex, err_ex = [], []
    for it in items:
        ty = it.get("type")
        if ty not in ("function_call", "custom_tool_call"):
            continue
        name = it.get("name", "")
        out = outputs.get(it.get("call_id"), "")
        if not isinstance(out, str):
            out = json.dumps(out)
        reason = None
        if name == "failed_tool":
            broken["failed_tool"] += 1
            reason = "failed_tool"
        elif name not in offered:
            broken["unknown_tool"] += 1
            reason = "unknown_tool"
        if ty == "function_call":
            args = it.get("arguments", "")
            if isinstance(args, str):
                try:
                    json.loads(args)
                except ValueError:
                    broken["bad_json_args"] += 1
                    reason = reason or "bad_json_args"
        if not collect_excerpts:
            continue
        if reason:
            broken_ex.append((f"{name} [{reason}]", snippet(out, 200)))
        else:
            m = ERR_RE.search(out[:4000])
            if m:
                err_ex.append((name, snippet(out, 200, m.start())))
    final_msg = None
    if collect_excerpts and items:
        last = items[-1]
        if last.get("type", "message") == "message" and last.get("role") == "assistant":
            final_msg = re.sub(r"\s+", " ", item_text(last)).strip()[:400]
    return {"broken": broken, "final_msg": final_msg,
            "flagged": (broken_ex + err_ex)[:3]}


# ----------------------------------------------------------------- q_score
def score(m):
    pens = {
        "err": round(W_ERR * min(m["err_outputs"], 3) / 3, 4),
        "retry": W_RETRY if m["retries"] > 0 else 0.0,
        "dup": W_DUP if m["dup_calls"] > 0 else 0.0,
        "broken": W_BROKEN if m["broken_calls"] > 0 else 0.0,
        "loop": round(W_LOOP * max(0.0, m["loop_ratio"] - 2) / 2, 4),
        "incomplete": W_INCOMPLETE if not m["completed"] else 0.0,
    }
    return max(0.0, round(1.0 - sum(pens.values()), 4)), pens


# ------------------------------------------------------------- calibration
import hashlib


def sha1_order(row_id):
    return hashlib.sha1(row_id.encode()).hexdigest()


CAL_HEADER = """# Human calibration set — q_score sanity check

Instructions for the team: mark AGREE/DISAGREE per episode; >6 disagreements
→ weights get revisited.

AGREE means: the q_score's verdict (clean vs. degraded, and roughly how
degraded) matches what you see in the transcript excerpts. Judge the run
quality, not the redaction noise. 30 episodes: the 10 lowest q_scores, 10
scored 1.0, and 10 pseudo-random picks (ordered by sha1(row_id) — no
cherry-picking). Weights were pre-registered before any router results
existed (see solution/quality.py).
"""


def render_episode(idx, row, m, scan):
    pen_str = ", ".join(f"{k} −{v}" for k, v in m["penalties"].items() if v) or "none"
    lines = [
        f"### {idx}. `{row['row_id']}` — q_score {m['q_score']}",
        f"- model `{row.get('model', '?')}` · trigger `{row.get('trigger', '?')}` "
        f"· job_key `{row.get('job_key', '?')}`",
        f"- components: n_calls={m['n_calls']}, err_outputs={m['err_outputs']}, "
        f"retries={m['retries']}, dup_calls={m['dup_calls']}, "
        f"broken_calls={m['broken_calls']}, loop_ratio={m['loop_ratio']}, "
        f"completed={m['completed']}",
        f"- penalties: {pen_str}",
    ]
    fm = scan.get("final_msg")
    lines.append(f"- final assistant message (first 400 chars): {fm}" if fm
                 else "- final assistant message: NONE — episode does not end "
                      "with an assistant message")
    if scan.get("flagged"):
        lines.append("- flagged calls:")
        for name, snip in scan["flagged"]:
            lines.append(f"  - `{name}` — `{snip}`")
    else:
        lines.append("- flagged calls: none")
    lines.append("- [ ] AGREE  [ ] DISAGREE — notes:")
    return "\n".join(lines)


def write_calibration(path, rows, metrics, scans):
    order = {e["row_id"]: sha1_order(e["row_id"]) for e in rows}
    by_id = {e["row_id"]: e for e in rows}
    worst = sorted(rows, key=lambda e: (metrics[e["row_id"]]["q_score"],
                                        order[e["row_id"]]))[:10]
    perfect = sorted((e for e in rows if metrics[e["row_id"]]["q_score"] == 1.0),
                     key=lambda e: order[e["row_id"]])[:10]
    taken = {e["row_id"] for e in worst} | {e["row_id"] for e in perfect}
    rand = [e for e in sorted(rows, key=lambda e: order[e["row_id"]])
            if e["row_id"] not in taken][:10]
    sections = [("A. 10 lowest q_score", worst),
                ("B. 10 at q_score = 1.0", perfect),
                ("C. 10 pseudo-random (sha1(row_id) order)", rand)]
    parts, idx = [CAL_HEADER], 0
    for title, members in sections:
        parts.append(f"\n## {title}\n")
        for e in members:
            idx += 1
            rid = e["row_id"]
            parts.append(render_episode(idx, by_id[rid], metrics[rid], scans[rid]) + "\n")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(parts))
    return [e["row_id"] for e in worst + perfect + rand]


# ------------------------------------------------------------------- main
def pct(values, p):
    s = sorted(values)
    return s[min(len(s) - 1, int(p * (len(s) - 1) + 0.5))]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("episodes_jsonl")
    ap.add_argument("--out", default="results/quality.jsonl")
    ap.add_argument("--export-dir",
                    default=str(Path(__file__).resolve().parent.parent / "export"),
                    help="directory holding the raw trajectory chunks (broken_calls pass)")
    ap.add_argument("--calibration", default=None, metavar="MD",
                    help="also write the 30-episode human calibration set to this path")
    args = ap.parse_args()

    with open(args.episodes_jsonl) as f:
        rows = [json.loads(l) for l in f if l.strip()]

    # per-episode call metrics + family medians for loop_ratio
    metrics = {}
    fam_calls = defaultdict(list)
    for e in rows:
        m = calls_metrics(behavior_of_row(e))
        metrics[e["row_id"]] = m
        fam_calls[e.get("family_id", e["row_id"])].append(m["n_calls"])
    fam_median = {fid: median(v) for fid, v in fam_calls.items()}
    for e in rows:
        m = metrics[e["row_id"]]
        fid = e.get("family_id", e["row_id"])
        med = fam_median[fid]
        m["loop_ratio"] = (1.0 if len(fam_calls[fid]) == 1 or med == 0
                           else round(m["n_calls"] / med, 4))

    # streaming pass over the raw export for broken_calls (+ excerpts if needed)
    scans = scan_export(args.export_dir, [e["row_id"] for e in rows],
                        collect_excerpts=bool(args.calibration))
    for e in rows:
        m = metrics[e["row_id"]]
        b = scans[e["row_id"]]["broken"]
        m["broken_calls"] = sum(b.values())
        m["broken_detail"] = b
        m["q_score"], m["penalties"] = score(m)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    keep_ctx = ("vendor", "tier", "model", "family_id", "job_key", "trigger", "task_type")
    with open(out, "w") as f:
        for e in rows:
            m = metrics[e["row_id"]]
            rec = {"row_id": e["row_id"], **{k: e.get(k) for k in keep_ctx},
                   "ends_silent": behavior_of_row(e).get("ends_silent"),
                   **{k: m[k] for k in ("n_calls", "err_outputs", "err_rate",
                                        "retries", "dup_calls", "broken_calls",
                                        "broken_detail", "loop_ratio", "completed",
                                        "q_score", "penalties")}}
            f.write(json.dumps(rec) + "\n")

    if args.calibration:
        write_calibration(args.calibration, rows, metrics, scans)

    # ------------------------------ summary (recomputed from emitted rows)
    ms = [metrics[e["row_id"]] for e in rows]
    qs = [m["q_score"] for m in ms]
    n = len(rows)
    print(f"episodes={n}  wrote {out}" +
          (f" and {args.calibration}" if args.calibration else ""))
    print(f"q_score: min={min(qs)} p10={pct(qs, .10)} median={pct(qs, .50)} "
          f"p90={pct(qs, .90)} mean={sum(qs)/n:.4f} at1.0={sum(q == 1.0 for q in qs)}"
          f" ({100*sum(q == 1.0 for q in qs)/n:.1f}%)")
    tot_calls = sum(m["n_calls"] for m in ms)
    print(f"canonical rates: err_outputs={sum(m['err_outputs'] for m in ms)}"
          f"/{tot_calls} calls; episodes w/ err={sum(m['err_outputs'] > 0 for m in ms)}"
          f", retry={sum(m['retries'] > 0 for m in ms)}"
          f", dup={sum(m['dup_calls'] > 0 for m in ms)}"
          f", broken={sum(m['broken_calls'] > 0 for m in ms)}"
          f", incomplete={sum(not m['completed'] for m in ms)}")
    for key in ("tier", "vendor"):
        groups = defaultdict(list)
        for e in rows:
            groups[e.get(key)].append(metrics[e["row_id"]])
        print(f"by {key}: " + "  ".join(
            f"{g}: n={len(v)} q={sum(m['q_score'] for m in v)/len(v):.4f} "
            f"err_rate={sum(m['err_rate'] for m in v)/len(v):.4f}"
            for g, v in sorted(groups.items(), key=lambda kv: str(kv[0]))))
    worst = sorted(rows, key=lambda e: (metrics[e["row_id"]]["q_score"],
                                        sha1_order(e["row_id"])))[:5]
    for e in worst:
        m = metrics[e["row_id"]]
        print(f"worst: {e['row_id']} q={m['q_score']} model={e.get('model')} "
              f"trigger={e.get('trigger')} err={m['err_outputs']} retry={m['retries']} "
              f"dup={m['dup_calls']} broken={m['broken_calls']} loop={m['loop_ratio']}")


if __name__ == "__main__":
    main()
