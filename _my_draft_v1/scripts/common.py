"""Shared helpers for the Model Router baseline (TUM.ai x Viktor challenge).

No network calls, no GPU, no API key. Token counts are estimated with a
plain character-based heuristic (~4 chars/token) so the pipeline runs
anywhere with only the Python standard library.
"""
from __future__ import annotations

import gzip
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

CHARS_PER_TOKEN = 4.0
PER_ITEM_OVERHEAD_TOKENS = 4  # role/type wrapper, message envelope, etc.

# Naming-based fallback for inferring a relative cost tier when a model id
# is not (yet) listed in config/model_tiers.json. Lower rank = cheaper.
# This is a heuristic, not ground truth -- edit config/model_tiers.json with
# real numbers as soon as you know them.
_TIER_KEYWORDS = [
    ("nano", 0),
    ("mini", 1),
    ("lite", 1),
    ("small", 1),
    ("flash", 2),
    ("haiku", 2),
    ("base", 3),
    ("standard", 3),
    ("fable", 4),
    ("sonnet", 5),
    ("pro", 6),
    ("terra", 7),
    ("opus", 8),
    ("ultra", 9),
    ("max", 9),
]


def infer_tier_rank(model_id: str) -> int:
    """Best-effort relative cost rank from the model id's name alone."""
    name = model_id.lower()
    for keyword, rank in _TIER_KEYWORDS:
        if keyword in name:
            return rank
    return 5  # unknown naming pattern: assume mid-tier until corrected


def iter_jsonl_records(export_dir: Path) -> Iterator[dict]:
    """Yield every {model, input, tools} record from export_dir/*.jsonl(.gz)."""
    paths = sorted(export_dir.glob("*.jsonl")) + sorted(export_dir.glob("*.jsonl.gz"))
    if not paths:
        raise FileNotFoundError(
            f"No .jsonl or .jsonl.gz files found in {export_dir}. "
            f"Extract the dataset tarball there first, e.g.\n"
            f"  tar xzf trajectories_v1_01.jsonl.tar.gz -C {export_dir}/"
        )
    for path in paths:
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{line_no}: invalid JSON ({exc})") from exc
                record["_source_file"] = path.name
                record["_source_line"] = line_no
                yield record


def extract_text(node: Any) -> str:
    """Recursively pull every string value out of an input/tools item."""
    parts: list[str] = []

    def walk(n: Any) -> None:
        if isinstance(n, str):
            parts.append(n)
        elif isinstance(n, dict):
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(node)
    return " ".join(parts)


_WORD_RE = re.compile(r"\S+")


def estimate_tokens_for_text(text: str) -> int:
    if not text:
        return 0
    # Character heuristic, floored by a word-count heuristic so very
    # "symbol dense" text (code, JSON) doesn't get estimated as near-zero.
    by_chars = len(text) / CHARS_PER_TOKEN
    by_words = len(_WORD_RE.findall(text))
    return max(1, round(max(by_chars, by_words)))


def estimate_tokens_for_items(items: list[dict]) -> int:
    total = 0
    for item in items:
        total += PER_ITEM_OVERHEAD_TOKENS
        total += estimate_tokens_for_text(extract_text(item))
    return total


def estimate_tokens_for_tools(tools: list[dict]) -> int:
    if not tools:
        return 0
    return estimate_tokens_for_text(extract_text(tools))


def opening_key(input_items: list[dict], max_items: int = 2) -> str:
    """Grouping key for trajectory reconstruction.

    The system prompt is identical across every trajectory ("You are Viktor
    Ai..."), so it alone can't identify a task. The first *user* message is
    task-specific, so the key is "everything up to and including the first
    user message" -- capped at max_items extra items as a safety margin.
    """
    key_items = []
    seen_user = False
    for item in input_items:
        key_items.append(item)
        if item.get("role") == "user" or item.get("type") == "message" and item.get("role") == "user":
            seen_user = True
            break
    if not seen_user:
        key_items = input_items[:max_items]
    return json.dumps(key_items, sort_keys=True)


@dataclass
class Call:
    model: str
    input: list[dict]
    tools: list[dict]
    source_file: str
    source_line: int
    call_index: int = 0
    input_tokens: int = 0       # tokens for the full input (uncached case)
    new_tokens: int = 0         # tokens added since the previous call in this trajectory
    cached_prefix_tokens: int = 0  # tokens shared with the previous call (0 for call_1)
    tools_tokens: int = 0


@dataclass
class Trajectory:
    trajectory_id: str
    logged_model: str
    calls: list[Call] = field(default_factory=list)


def reconstruct_trajectories(records: Iterable[dict]) -> tuple[list[Trajectory], int]:
    """Group raw call records into ordered trajectories.

    Records are bucketed by their opening-message key, then ordered by
    input length (each call's input is a strict superset of the previous
    call's input in the same trajectory -- see slide 4 "A trajectory").
    Returns (trajectories, num_prefix_mismatches) where the mismatch count
    is a best-effort sanity signal, not a hard error.
    """
    buckets: dict[str, list[dict]] = {}
    for rec in records:
        key = opening_key(rec["input"])
        buckets.setdefault(key, []).append(rec)

    trajectories: list[Trajectory] = []
    mismatches = 0

    for i, (key, group) in enumerate(buckets.items()):
        group.sort(key=lambda r: len(r["input"]))
        traj = Trajectory(trajectory_id=f"traj_{i:05d}", logged_model=group[0]["model"])
        prev_input: list[dict] | None = None
        for call_index, rec in enumerate(group, start=1):
            cur_input = rec["input"]
            if prev_input is not None and cur_input[: len(prev_input)] != prev_input:
                mismatches += 1
            call = Call(
                model=rec["model"],
                input=cur_input,
                tools=rec.get("tools", []),
                source_file=rec["_source_file"],
                source_line=rec["_source_line"],
                call_index=call_index,
            )
            traj.calls.append(call)
            prev_input = cur_input
        trajectories.append(traj)

    return trajectories, mismatches


def annotate_tokens(trajectories: list[Trajectory]) -> None:
    """Fill in input_tokens / new_tokens / cached_prefix_tokens / tools_tokens."""
    for traj in trajectories:
        prev_len_items = 0
        prev_tokens = 0
        for call in traj.calls:
            call.input_tokens = estimate_tokens_for_items(call.input)
            call.tools_tokens = estimate_tokens_for_tools(call.tools)
            if call.call_index == 1:
                call.new_tokens = call.input_tokens
                call.cached_prefix_tokens = 0
            else:
                new_items = call.input[prev_len_items:]
                call.new_tokens = estimate_tokens_for_items(new_items)
                call.cached_prefix_tokens = prev_tokens
            prev_len_items = len(call.input)
            prev_tokens = call.input_tokens


def trajectory_to_dict(traj: Trajectory) -> dict:
    return {
        "trajectory_id": traj.trajectory_id,
        "logged_model": traj.logged_model,
        "num_calls": len(traj.calls),
        "calls": [
            {
                "call_index": c.call_index,
                "model": c.model,
                "input_tokens": c.input_tokens,
                "new_tokens": c.new_tokens,
                "cached_prefix_tokens": c.cached_prefix_tokens,
                "tools_tokens": c.tools_tokens,
                "source_file": c.source_file,
                "source_line": c.source_line,
            }
            for c in traj.calls
        ],
    }


def load_model_tiers(config_path: Path) -> dict:
    if config_path.exists():
        return json.loads(config_path.read_text())
    return {}


def save_model_tiers(config_path: Path, tiers: dict) -> None:
    config_path.write_text(json.dumps(tiers, indent=2, sort_keys=True) + "\n")


def ensure_model_in_tiers(tiers: dict, model_id: str) -> dict:
    """Add a placeholder pricing entry for an unseen model id, in place."""
    if model_id in tiers:
        return tiers
    rank = infer_tier_rank(model_id)
    unit = 0.15  # arbitrary relative unit; edit config/model_tiers.json with real $/1K-token prices
    tiers[model_id] = {
        "tier_rank": rank,
        "input_price_per_1k": round(unit * (rank + 1), 4),
        "cached_input_price_per_1k": round(unit * (rank + 1) * 0.1, 4),
        "output_price_per_1k": round(unit * (rank + 1) * 3, 4),
        "_source": "auto-inferred from naming heuristic -- verify/replace",
    }
    return tiers
