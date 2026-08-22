#!/usr/bin/env python3
"""Per-call feature extraction for the rule-based router (Step 2).

Written generically over a trajectory (ordered list of calls), matching the
task's premise. IMPORTANT CAVEAT for the current export chunk: load_trajectories's
prefix-continuation check finds every reconstructed trajectory here is exactly
ONE call (see that file's docstring) — so in practice call_index is always 0 and
position-in-trajectory features are trivially constant. The logic below still
does the right thing if a future chunk has real multi-call trajectories.

Every feature here is a heuristic over what's actually visible in `input`/`tools`
— there is no ground-truth label for any of it. Each function's docstring says
what it's a heuristic FOR and where it can be wrong; keep that when tuning
keywords_config.json so the router stays defensible to a judging panel.

Usage: python rule_based_router/scripts/feature_extraction.py export/ [out.csv]
Importable: extract_features(trajectory_key, calls) -> list[dict] (one dict per call).
"""
import json, re, sys, difflib
from pathlib import Path
from collections import defaultdict

from load_trajectories import iter_requests, group_trajectories, est_tokens, first_user_text

KEYWORDS = json.loads((Path(__file__).parent / "keywords_config.json").read_text())
STAKES_TOOL_KEYWORDS = KEYWORDS["stakes_tool_keywords"]
LOW_STAKES_TASK_KEYWORDS = KEYWORDS["task_keywords_low_stakes"]
HIGH_STAKES_TASK_KEYWORDS = KEYWORDS["task_keywords_high_stakes"]

RECENT_WINDOW = 3          # "last N calls/items" window for density + retry checks
RETRY_ARG_SIMILARITY = 0.7  # SequenceMatcher ratio above which two calls' arguments count as "similar"

# ---- tools ----------------------------------------------------------------

def tool_names(tools):
    names = []
    for t in tools or []:
        n = t.get("name") or (t.get("function") or {}).get("name")
        if n: names.append(n)
    return names

def called_tool_names(call):
    return [it.get("name") for it in call["input"] if it.get("type") in ("function_call", "custom_tool_call")]

def tool_surface_stakes(call):
    """Heuristic: was a tool that looks irreversible/externally-visible
    (send/post/pay/delete/...) actually CALLED in this call's visible history —
    not just available. Checked against calls made, not the static tools list:
    every record in this dataset carries the same general-purpose ~10-17 tool
    belt (verified: 1000/1000 records' AVAILABLE tools include a stakes
    keyword, since every agent here can always message someone — so 'available'
    has zero discriminative power). Whether a stakes tool was actually CALLED
    varies a lot (427/1000) and is a real signal. Keyword list is editable in
    keywords_config.json; still a heuristic, not a certified risk classifier."""
    names_l = " ".join(n or "" for n in called_tool_names(call)).lower()
    return any(kw in names_l for kw in STAKES_TOOL_KEYWORDS)

# ---- output / failure detection --------------------------------------------

def _parse_json_maybe(s):
    if not isinstance(s, str): return None
    try:
        v = json.loads(s)
        return v if isinstance(v, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None

_ERROR_WORD_RE = re.compile(r"\b(error|exception|failed|traceback)\b", re.IGNORECASE)

def _looks_like_failure(output_str):
    """Hybrid failure detector. The export's function_call_output.output is not
    one consistent shape (verified against real samples): shell-style JSON
    ({"content":..., "exit_code":N}), API-style JSON ({"success":bool,
    "error":...}), wait_for_background_work JSON ({"job_status":...}), or plain
    text with no JSON at all. Structured fields are checked first since they're
    reliable; the keyword regex fallback is NOISY (the words "error"/"failed"
    show up constantly in benign content — skill docs, source code, logs being
    read) and is a known, named limitation, not a fixed detector."""
    parsed = _parse_json_maybe(output_str)
    if parsed is not None:
        if "exit_code" in parsed:
            return parsed["exit_code"] not in (0, None)
        if "success" in parsed:
            return parsed["success"] is False
        if "job_status" in parsed:
            return parsed["job_status"] == "failed"
    return bool(_ERROR_WORD_RE.search(output_str or ""))

def failure_flag(call):
    """True if the MOST RECENT function_call_output / custom_tool_call_output
    anywhere in this call's input looks like an error. NOT restricted to the
    literal last item: verified against the real export that every one of this
    chunk's 1000 records ends in an assistant message (0/1000 end in a tool
    output) — these are all 'the agent about to speak/decide again after its
    own last turn' snapshots, so the last tool result (if any) sits a bit
    earlier in the item list, not at the very end."""
    for item in reversed(call["input"]):
        if item.get("type") in ("function_call_output", "custom_tool_call_output"):
            return _looks_like_failure(item.get("output", ""))
    return False

# ---- retry loop -------------------------------------------------------------

def retry_loop_flag(call):
    """True if, among the last RECENT_WINDOW function_call/custom_tool_call
    items in this call's input, the same tool name appears 2+ times with
    similar arguments (string-overlap ratio) — a heuristic for 'the agent is
    stuck retrying the same thing'. Only looks at CALLS, not their outputs."""
    fcalls = [it for it in call["input"] if it.get("type") in ("function_call", "custom_tool_call")]
    recent = fcalls[-RECENT_WINDOW:]
    for i in range(len(recent)):
        for j in range(i + 1, len(recent)):
            a, b = recent[i], recent[j]
            if a.get("name") != b.get("name"): continue
            args_a = a.get("arguments") or a.get("input") or ""
            args_b = b.get("arguments") or b.get("input") or ""
            if difflib.SequenceMatcher(None, str(args_a), str(args_b)).ratio() >= RETRY_ARG_SIMILARITY:
                return True
    return False

# ---- reasoning / phase signals ----------------------------------------------

def reasoning_present(call):
    """True if any reasoning-type item appears anywhere in this call's input
    history (gpt-family only — claude calls never carry these, per AGENTS.md)."""
    return any(it.get("type") == "reasoning" for it in call["input"])

def _last_nonnull_type(call):
    items = call["input"]
    return items[-1].get("type") if items else None

def phase_change_signal(call):
    """Small dict of phase-of-task signals, all derived from this call's own
    input history (no cross-call trajectory average is available — see the
    module docstring on why every trajectory here is length 1):
      - tool_call_density_recent: fraction of the last RECENT_WINDOW items that
        are function_call/custom_tool_call
      - tool_call_density_overall: same fraction over the whole input
      - last_item_type: type of the final input item (what this call is reacting to)
      - resumed_from_wait: True if the final item is a function_call_output whose
        matching function_call (by call_id) was a wait_for_background_work-style tool
    """
    items = call["input"]
    is_call = lambda it: it.get("type") in ("function_call", "custom_tool_call")
    recent = items[-RECENT_WINDOW:] if items else []
    density_recent = (sum(1 for it in recent if is_call(it)) / len(recent)) if recent else 0.0
    density_overall = (sum(1 for it in items if is_call(it)) / len(items)) if items else 0.0

    last_type = _last_nonnull_type(call)
    # Every record in this chunk ends in an assistant message (verified: 0/1000
    # end in a tool output), so "resumed from wait" has to look at the most
    # recent tool output anywhere in the history, not literally the last item.
    resumed_from_wait = False
    last_output = next((it for it in reversed(items)
                         if it.get("type") in ("function_call_output", "custom_tool_call_output")), None)
    if last_output is not None:
        call_id = last_output.get("call_id")
        origin = next((it for it in reversed(items)
                        if it.get("type") in ("function_call", "custom_tool_call")
                        and it.get("call_id") == call_id), None)
        if origin and "wait_for_background_work" in (origin.get("name") or ""):
            resumed_from_wait = True

    return {
        "tool_call_density_recent": round(density_recent, 3),
        "tool_call_density_overall": round(density_overall, 3),
        "last_item_type": last_type,
        "resumed_from_wait": resumed_from_wait,
    }

# ---- opening task keywords ---------------------------------------------------

def opening_task_keywords(first_user_text_):
    text_l = first_user_text_.lower()
    return {
        "low_stakes_kw_hit": any(kw in text_l for kw in LOW_STAKES_TASK_KEYWORDS),
        "high_stakes_kw_hit": any(kw in text_l for kw in HIGH_STAKES_TASK_KEYWORDS),
    }

# ---- top-level extraction -----------------------------------------------------

def extract_features(trajectory_key, calls):
    rows = []
    opening_kw = opening_task_keywords(first_user_text(calls[0]))
    n = len(calls)
    prev_tokens = 0
    for i, call in enumerate(calls):
        cum_tokens = est_tokens(call["input"])
        row = {
            "trajectory_id": trajectory_key,
            "call_index": i,
            "position_frac": (i / (n - 1)) if n > 1 else 1.0,
            "cumulative_input_tokens_est": cum_tokens,
            "input_delta_tokens_est": cum_tokens - prev_tokens,
            "tool_surface_count": len(call.get("tools") or []),
            "tool_surface_stakes": tool_surface_stakes(call),
            "reasoning_present": reasoning_present(call),
            "failure_flag": failure_flag(call),
            "retry_loop_flag": retry_loop_flag(call),
            **{f"phase_{k}": v for k, v in phase_change_signal(call).items()},
            "low_stakes_kw_hit": opening_kw["low_stakes_kw_hit"],
            "high_stakes_kw_hit": opening_kw["high_stakes_kw_hit"],
            "n_calls_in_trajectory": n,
            "model_logged": call["model"],  # ground truth, kept aside — NOT a feature for the router
        }
        rows.append(row)
        prev_tokens = cum_tokens
    return rows

def main():
    export = sys.argv[1] if len(sys.argv) > 1 else "export"
    out_path = sys.argv[2] if len(sys.argv) > 2 else str(Path(__file__).resolve().parent.parent / "results" / "features.csv")

    groups = group_trajectories(r for _, _, r in iter_requests(export))
    all_rows = []
    for key, calls in groups.items():
        all_rows.extend(extract_features(key, calls))

    Path(out_path).parent.mkdir(exist_ok=True, parents=True)
    try:
        import pandas as pd
        df = pd.DataFrame(all_rows)
        df.to_csv(out_path, index=False)
        print(df.describe(include="all").transpose().to_string())
    except ImportError:
        import csv
        with open(out_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=all_rows[0].keys())
            w.writeheader(); w.writerows(all_rows)
        print("pandas not installed — wrote plain CSV, skipped describe()")

    print(f"\nwrote {len(all_rows)} rows to {out_path}")
    print(f"tool_surface_stakes=True: {sum(r['tool_surface_stakes'] for r in all_rows)}")
    print(f"failure_flag=True: {sum(r['failure_flag'] for r in all_rows)}")
    print(f"retry_loop_flag=True: {sum(r['retry_loop_flag'] for r in all_rows)}")
    print(f"reasoning_present=True: {sum(r['reasoning_present'] for r in all_rows)}")
    print(f"high_stakes_kw_hit=True: {sum(r['high_stakes_kw_hit'] for r in all_rows)}")
    print(f"low_stakes_kw_hit=True: {sum(r['low_stakes_kw_hit'] for r in all_rows)}")

if __name__ == "__main__": main()
