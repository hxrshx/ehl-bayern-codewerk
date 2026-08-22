#!/usr/bin/env python3
"""Step 2: heuristic quality_score_est per task.

SCOPE — read before using any output from this file. This scores ONLY the
response of the model that ACTUALLY served each task (recovered by
extract_response.py). It is a heuristic estimate of surface-level response
quality (hedging, length, tool-skipping, unresolved errors, repetition,
refusal) — NOT a measure of correctness, NOT a judge-model score, and it says
NOTHING about what a DIFFERENT model would have produced for that same task:
no counterfactual response can ever be generated in this project (no live LLM
access). The only valid cross-model comparison is quality_by_bucket.py's
aggregate, bucket-level statistic — never read a single task's
quality_score_est as "model X would score Y on task Z", only "the model that
actually ran on task Z scored ~Y by this heuristic."

Tasks with no recoverable response text get quality_score_est=None — NOT 0.0.
Do not silently drop or zero-fill these; see extract_response.py (this is a
structural gap hitting 97% of GPT-served tasks, not random noise).

Every penalty is a heuristic proxy with a named, real failure mode — see each
function's docstring. All weights are named constants below.
"""
import json, sys
from pathlib import Path
from collections import Counter, defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from load_trajectories import iter_requests, group_trajectories  # noqa: E402
from feature_extraction import (  # noqa: E402
    extract_features, called_tool_names, tool_names, failure_flag, STAKES_TOOL_KEYWORDS,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_response import extract_response  # noqa: E402
from difficulty_bucket import difficulty_bucket  # noqa: E402

_KW = json.loads((Path(__file__).parent / "quality_keywords_config.json").read_text())
HEDGING_PHRASES = _KW["hedging_phrases"]
REFUSAL_PHRASES = _KW["refusal_phrases"]
LEGITIMATE_REASON_KEYWORDS = _KW["legitimate_reason_keywords"]
ACKNOWLEDGMENT_KEYWORDS = _KW["acknowledgment_keywords"]

# ---- penalty weights (subtracted from a starting score of 1.0, floored at 0.0) ----
PENALTY_HEDGING = 0.15
PENALTY_LENGTH_ANOMALY = 0.20
PENALTY_TOOL_SKIP = 0.15
PENALTY_UNRESOLVED_ERROR = 0.25
PENALTY_REPETITION = 0.10
PENALTY_REFUSAL = 0.30

LENGTH_ANOMALY_DECILE = 0.10        # bottom 10% of word count WITHIN this task's bucket -> anomalous
MIN_CHARS_FOR_TRUNCATION_CHECK = 20  # shorter replies are too short to judge "looks cut off" reliably
TERMINAL_CHARS = tuple('.!?"\'`)]}')

REPETITION_NGRAM_SIZE = 6            # words per shingle
REPETITION_MIN_REPEATS = 3           # same shingle appearing this many+ times -> flagged

# ---- individual penalty checks ---------------------------------------------

def _looks_truncated(text):
    """Proxy for 'response was cut off mid-thought': no terminal punctuation
    on a long-enough reply. KNOWN FAILURE MODE: false positives on
    legitimately punctuation-less endings — a trailing code block, a list
    item, an emoji, a bare filename."""
    t = text.strip()
    if len(t) < MIN_CHARS_FOR_TRUNCATION_CHECK:
        return False
    return not t.endswith(TERMINAL_CHARS)

def _has_repetition(text):
    """Proxy for 'the model got stuck looping its own output': the same
    REPETITION_NGRAM_SIZE-word phrase appears >= REPETITION_MIN_REPEATS
    times. KNOWN FAILURE MODE: misses repetition at other granularities
    (single repeated words, the same idea restated in different words) and
    can false-positive on legitimately repeated boilerplate (e.g. a
    checklist template)."""
    words = text.split()
    if len(words) < REPETITION_NGRAM_SIZE * REPETITION_MIN_REPEATS:
        return False
    grams = [" ".join(words[i:i + REPETITION_NGRAM_SIZE]) for i in range(len(words) - REPETITION_NGRAM_SIZE + 1)]
    return Counter(grams).most_common(1)[0][1] >= REPETITION_MIN_REPEATS

def _tool_available_stakes(call):
    """Whether a stakes-flavored tool was AVAILABLE (not necessarily called).

    Deliberately NOT the same thing as feature_extraction.tool_surface_stakes:
    that feature was redefined (see feature_extraction.py's own docstring) to
    mean a stakes tool was actually CALLED, after finding that 'available' is
    ~always true in this dataset (every task's toolbelt includes messaging
    tools) and therefore uninformative there. tool_skip_penalty needs the
    ORIGINAL availability sense, paired with 'never called anything at all' —
    reusing tool_surface_stakes here would make this penalty impossible to
    fire by construction. So this reads call['tools'] directly, reusing the
    SAME STAKES_TOOL_KEYWORDS list and tool_names() helper from
    feature_extraction.py (data/helper reused, just not that one feature)."""
    names_l = " ".join(tool_names(call.get("tools"))).lower()
    return any(kw in names_l for kw in STAKES_TOOL_KEYWORDS)

# ---- scoring -----------------------------------------------------------------

def score_response(call, features, response_row, length_thresholds):
    """Returns {"quality_score_est": float, "penalties_fired": list[str]}, or
    None if there's no recoverable response text to score at all."""
    if not response_row["has_response_text"]:
        return None

    text = response_row["response_text"]
    text_l = text.lower()
    score = 1.0
    fired = []

    if any(p in text_l for p in HEDGING_PHRASES):
        score -= PENALTY_HEDGING; fired.append("hedging")

    bucket = difficulty_bucket(features["cumulative_input_tokens_est"])
    word_count = len(text.split())
    bottom_decile = word_count <= length_thresholds.get(bucket, 0)
    truncated = _looks_truncated(text)
    if bottom_decile or truncated:
        score -= PENALTY_LENGTH_ANOMALY
        reasons = (["bottom_decile"] if bottom_decile else []) + (["truncated"] if truncated else [])
        fired.append("length_anomaly:" + "+".join(reasons))

    if _tool_available_stakes(call) and not called_tool_names(call):
        score -= PENALTY_TOOL_SKIP; fired.append("tool_skip")

    if failure_flag(call) and not any(kw in text_l for kw in ACKNOWLEDGMENT_KEYWORDS):
        score -= PENALTY_UNRESOLVED_ERROR; fired.append("unresolved_error")

    if _has_repetition(text):
        score -= PENALTY_REPETITION; fired.append("repetition")

    if any(p in text_l for p in REFUSAL_PHRASES) and not any(kw in text_l for kw in LEGITIMATE_REASON_KEYWORDS):
        score -= PENALTY_REFUSAL; fired.append("refusal")

    return {"quality_score_est": round(max(0.0, score), 3), "penalties_fired": fired}

def compute_quality_scores(export_dir):
    """Two-pass over the export:
      1. Gather response text + features for every task, so length_anomaly
         can compare against this CORPUS's real per-bucket word-count
         distribution instead of a guessed constant.
      2. Score each task against those corpus-derived thresholds.
    Returns list of dicts: trajectory_id, model, bucket, has_response_text,
    quality_score_est (float or None if unscored), penalties_fired."""
    groups = group_trajectories(r for _, _, r in iter_requests(export_dir))

    prelim = []
    for key, calls in groups.items():
        last_call = calls[-1]
        feats = extract_features(key, calls)[-1]
        resp = extract_response(last_call)
        bucket = difficulty_bucket(feats["cumulative_input_tokens_est"])
        prelim.append((key, last_call, feats, resp, bucket))

    by_bucket_wordcounts = defaultdict(list)
    for _, _, _, resp, bucket in prelim:
        if resp["has_response_text"]:
            by_bucket_wordcounts[bucket].append(len(resp["response_text"].split()))

    length_thresholds = {}
    for bucket, counts in by_bucket_wordcounts.items():
        counts = sorted(counts)
        idx = max(0, int(len(counts) * LENGTH_ANOMALY_DECILE) - 1)
        length_thresholds[bucket] = counts[idx]

    rows = []
    for key, last_call, feats, resp, bucket in prelim:
        result = score_response(last_call, feats, resp, length_thresholds)
        rows.append({
            "trajectory_id": key,
            "model": last_call["model"],
            "bucket": bucket,
            "has_response_text": resp["has_response_text"],
            "quality_score_est": result["quality_score_est"] if result else None,
            "penalties_fired": result["penalties_fired"] if result else [],
        })
    return rows

def main():
    export = sys.argv[1] if len(sys.argv) > 1 else "export"
    rows = compute_quality_scores(export)
    results_dir = Path(__file__).resolve().parent.parent / "results"
    results_dir.mkdir(exist_ok=True)
    out_path = results_dir / "quality_scores.jsonl"
    with open(out_path, "w") as f:
        for r in rows: f.write(json.dumps(r) + "\n")

    n = len(rows)
    scored = [r for r in rows if r["quality_score_est"] is not None]
    print(f"tasks={n}  scored={len(scored)} ({len(scored)/n:.1%})  unscored_no_recoverable_text={n-len(scored)}")
    if scored:
        avg = sum(r["quality_score_est"] for r in scored) / len(scored)
        print(f"avg quality_score_est among SCORED tasks only: {avg:.3f}")
    penalty_counts = Counter(p.split(":")[0] for r in rows for p in r["penalties_fired"])
    print(f"penalty trigger counts: {dict(penalty_counts)}")
    print()
    print("PER-TASK HEURISTIC QUALITY SCORES — valid ONLY for the model that actually served")
    print("each task. NOT a ground-truth quality measure. NOT comparable across a task and any")
    print("model that didn't run on it. See quality_by_bucket.py for the only valid cross-model")
    print("(cheap-vs-expensive) comparison, which is a bucket-level aggregate, not per-task.")
    print(f"wrote {out_path}")

if __name__ == "__main__": main()
