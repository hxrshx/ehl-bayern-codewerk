#!/usr/bin/env python3
"""Per-row token mass of MODEL-GENERATED items — the output-cost proxy.

One streaming pass over an export dir (every *.jsonl chunk; one line = one LLM
request). For each row it emits {row_id, gen_bpe, gen_chars4} to
results/gen_tokens.jsonl, where row_id = "<chunk_name>:<line_no>" exactly as in
solution/episodes.py.

WHAT IS COVERED — items in the row's `input` echo that the model itself wrote:
  * assistant messages: any item with role == "assistant" (with or without an
    explicit "type": "message"); text is the joined text parts / raw string,
    the same extraction convention as solution/episodes.item_text.
  * tool calls emitted by the model: type "function_call" or
    "custom_tool_call"; text is "<name> <arguments>" (item_text convention).

WHAT IS NOT COVERED — stated precisely, because this proxy is a LOWER BOUND:
  * function_call_output / custom_tool_call_output (tool-authored, billed as
    input, never as output) and user/system items (human/scaffold-authored).
  * "reasoning" items (gpt rows): hidden reasoning tokens ARE billed as output
    by the provider, but the export only carries lossy summaries of them (and
    ~44%% of summaries on chunk 01 are empty). They are excluded here, so the
    output mass of gpt models is systematically UNDERestimated.
  * generation that is never echoed back: a request's own generation is only
    visible once a LATER request's input echoes it. In a multi-request chain
    the final request's generation would therefore be invisible — EXCEPT that
    on chunk 01 every row's input already ends with the final assistant
    message (verified: 1000/1000 rows end "message:assistant"), i.e. the rows
    echo the complete conversation including the final answer, so on this
    chunk the visible-generation coverage is complete. On a held-out chunk
    whose last request does NOT echo its own final answer, gen_* for that
    row is a strict lower bound on the episode's true output mass.

Units mirror the episode segment estimators exactly:
  * gen_chars4: len(json.dumps(item)) summed over generated items, // 4 at the
    end (same serialized-JSON chars/4 convention as tok_chars4 segments).
  * gen_bpe: tiktoken o200k_base tokens of the extracted generated TEXT (same
    convention as tok_bpe segments — JSON scaffolding not tokenized).

Deterministic: sorted chunk iteration, no randomness, stdlib + tiktoken only.

Usage: python3 solution/gen_tokens.py <export_dir> [--out results/gen_tokens.jsonl]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from episodes import ENC, item_text  # noqa: E402  (same dir; keeps conventions identical)

GEN_CALL_TYPES = ("function_call", "custom_tool_call")


def is_generated(item):
    """True iff the model itself wrote this input item (see module docstring)."""
    if item.get("type") in GEN_CALL_TYPES:
        return True
    return item.get("role") == "assistant"


def row_gen_tokens(req):
    """(gen_bpe, gen_chars4) for one export row."""
    chars = 0
    bpe = 0
    for item in req.get("input", ()):
        if not is_generated(item):
            continue
        chars += len(json.dumps(item))
        text = item_text(item)
        if text:
            bpe += len(ENC.encode(text, disallowed_special=()))
    return bpe, chars // 4


def iter_rows(export_dir):
    """Yield (row_id, request) streaming — never holds a chunk in memory."""
    chunks = sorted(Path(export_dir).glob("*.jsonl"))
    if not chunks:
        sys.exit(f"no *.jsonl chunks found in {export_dir}")
    for p in chunks:
        with open(p) as f:
            for i, line in enumerate(f):
                if line.strip():
                    yield f"{p.name}:{i}", json.loads(line)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("export_dir")
    ap.add_argument("--out", default="results/gen_tokens.jsonl")
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    tot_bpe = 0
    tot_c4 = 0
    with open(out, "w") as f:
        for row_id, req in iter_rows(args.export_dir):
            gen_bpe, gen_chars4 = row_gen_tokens(req)
            f.write(json.dumps({"row_id": row_id, "gen_bpe": gen_bpe,
                                "gen_chars4": gen_chars4}) + "\n")
            n += 1
            tot_bpe += gen_bpe
            tot_c4 += gen_chars4
    print(f"rows={n}  gen_bpe total={tot_bpe:,}  gen_chars4 total={tot_c4:,}  -> {out}")


if __name__ == "__main__":
    main()
