#!/usr/bin/env python3
"""Load the redacted export and reconstruct trajectories for the rule-based router.

Same reconstruction idea as baseline/scripts/load_trajectories.py, but the group key
hashes BOTH the system message and the first user message, not just the first user
message. This matches the task's stated premise exactly (AGENTS.md: trajectories share
"the same opening messages (system + first user text)") and removes the (already rare)
risk of two different trajectories colliding because they happen to open with similar
user text under different system prompts/personas.

There are no trajectory ids in the export, so grouping is inherently a best-effort
reconstruction — say so in the writeup, don't present it as ground truth.

Usage: python rule_based_router/scripts/load_trajectories.py export/
Importable: iter_requests, group_trajectories, est_tokens, first_user_text, system_text.
"""
import json, sys, hashlib
from pathlib import Path
from collections import Counter, defaultdict

def iter_requests(export_dir):
    """Yield (chunk_name, line_no, request) for every line of every chunk."""
    chunks = sorted(Path(export_dir).glob("*.jsonl"))
    if not chunks:
        sys.exit(f"no *.jsonl chunks found in {export_dir}")
    for p in chunks:
        with open(p) as f:
            for i, line in enumerate(f):
                if line.strip():
                    yield p.name, i, json.loads(line)

def _message_text(item):
    """Text of a message item's content, handling both shapes seen in the export:
    a list of content parts (claude-style, {"type": "input_text", "text": ...}) or a
    plain string (gpt-style)."""
    c = item.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(p.get("text", "") for p in c if p.get("type") == "input_text")
    return ""

def system_text(req):
    """Text of the system message (role == 'system'), if present."""
    for item in req["input"]:
        if item.get("role") == "system":
            return _message_text(item)
    return ""

def first_user_text(req):
    """Text of the first user message — stable across all requests of a task."""
    for item in req["input"]:
        if item.get("role") == "user":
            return _message_text(item)
    return ""

def group_key(req):
    """Hash of (system text + first user text) — the task's opening-messages premise."""
    basis = system_text(req)[:2000] + "\x1f" + first_user_text(req)[:2000]
    return hashlib.sha1(basis.encode()).hexdigest()[:16]

def _is_prefix(a_input, b_input):
    """True if a_input is an exact item-by-item prefix of b_input — the only
    relationship that actually proves 'b is a later call of the same task as a'
    (per the export's premise: each call's input contains every item of the call
    before it)."""
    if len(a_input) >= len(b_input): return False
    return all(x == y for x, y in zip(a_input, b_input))

def _split_into_chains(candidates):
    """A hash bucket of same-opening-text requests can still contain MULTIPLE
    independent tasks — e.g. a recurring cron template run on different days is
    near-identical text every time, so text hashing alone over-merges. Verify
    the real relationship (prefix-containment) and split back into the actual
    chains; each request attaches to its nearest valid predecessor chain, or
    starts a new chain if none matches. This is why we don't trust the hash
    bucket as a trajectory by itself — see the schema-check note in main()."""
    candidates = sorted(candidates, key=lambda r: len(r["input"]))
    chains = []  # list of lists, each request list ordered oldest -> newest
    for req in candidates:
        best_chain, best_len = None, -1
        for chain in chains:
            tail = chain[-1]
            if _is_prefix(tail["input"], req["input"]) and len(tail["input"]) > best_len:
                best_chain, best_len = chain, len(tail["input"])
        if best_chain is not None:
            best_chain.append(req)
        else:
            chains.append([req])
    return chains

def group_trajectories(requests):
    """Group requests by task, order each group by input length (= call order),
    and split any hash bucket that merged unrelated tasks back into the real
    chains (see _split_into_chains). Trajectory keys with >1 chain in their
    bucket get a '#i' suffix."""
    buckets = defaultdict(list)
    for req in requests: buckets[group_key(req)].append(req)
    groups = {}
    for key, candidates in buckets.items():
        chains = _split_into_chains(candidates)
        for i, chain in enumerate(chains):
            traj_key = key if len(chains) == 1 else f"{key}#{i}"
            groups[traj_key] = chain
    return groups

def est_tokens(obj):
    """Crude token estimate: serialized chars / 4. There is NO usage field in the
    export — every token number in this repo is an estimate. State that in your writeup."""
    return len(json.dumps(obj)) // 4 if not isinstance(obj, str) else len(obj) // 4

def main():
    export = sys.argv[1] if len(sys.argv) > 1 else "export"
    reqs = [r for _, _, r in iter_requests(export)]
    models = Counter(r["model"] for r in reqs)
    print(f"requests={len(reqs)}")
    print(f"distinct model ids seen ({len(models)}): {dict(sorted(models.items(), key=lambda kv: -kv[1]))}")

    buckets = defaultdict(list)
    for req in reqs: buckets[group_key(req)].append(req)
    split_buckets = sum(1 for v in buckets.values() if len(_split_into_chains(v)) > 1)
    print(f"hash buckets that merged >1 independent task (split back apart): {split_buckets} / {len(buckets)}")

    groups = group_trajectories(reqs)
    sizes = sorted(len(v) for v in groups.values())
    n = len(sizes)
    mean = sum(sizes) / n
    print(f"reconstructed trajectories={n}")
    print(f"calls/trajectory  min={sizes[0]}  median={sizes[n//2]}  mean={mean:.1f}  max={sizes[-1]}")

    mixed = [k for k, v in groups.items() if len({r['model'] for r in v}) > 1]
    print(f"trajectories with >1 model in the log: {len(mixed)}"
          + ("  (premise says one model per trajectory in the LOG — inspect these)"
             if mixed else "  (matches the one-model-per-trajectory premise)"))

    total = sum(est_tokens(r["input"]) for r in reqs)
    print(f"est. input tokens (chars/4, no usage field in export): {total:,}")

    r = reqs[0]
    missing = [k for k in ("model", "input", "tools") if k not in r]
    extra = [k for k in r if k not in ("model", "input", "tools")]
    print(f"schema check on first request: missing={missing or 'none'} extra={extra or 'none'}")

if __name__ == "__main__": main()
