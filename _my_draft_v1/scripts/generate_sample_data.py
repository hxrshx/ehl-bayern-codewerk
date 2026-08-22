#!/usr/bin/env python3
"""Generate synthetic sample data shaped like the real Viktor export.

This is NOT the challenge dataset -- the real one is shared at kickoff via
the Discord link on the "Contact" slide and dropped into export/. This
generator exists purely so you can smoke-test load_trajectories.py and
baseline_router.py right now, offline, before you have real data.

Usage:
    python scripts/generate_sample_data.py [--out export/] [--num-trajectories 40] [--seed 0]
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

MODEL_IDS = [
    "gpt-5-nano",
    "gpt-5-mini",
    "claude-haiku-4-5",
    "gpt-5",
    "claude-fable-5",
    "gpt-5.6-terra",
    "claude-sonnet-5",
    "gemini-3-pro",
    "claude-opus-5",
]

SYSTEM_PROMPT = "You are Viktor Ai (@viktor in slack and Microsoft Teams). Complete the user's task using the available tools."

TASKS = [
    "payment brief posted to {person} DM. Draft a summary and route it for approval.",
    "{person} asked for last week's expense report. Pull the numbers and reply in-thread.",
    "schedule a sync with {person} about the Q3 roadmap and send a calendar invite.",
    "{person} reported a bug in the billing dashboard. Reproduce and file a ticket.",
    "summarize the #general channel activity from today for {person}.",
    "{person} wants a status update on the migration project posted to their DM.",
]

TOOLS = [
    {"type": "function", "name": "bash", "description": "Run a shell command.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}}},
    {"type": "function", "name": "wait_for_background_work", "description": "Block until a background job finishes.", "parameters": {"type": "object", "properties": {"job_id": {"type": "string"}}}},
    {"type": "function", "name": "slack_post_message", "description": "Post a message to a Slack channel or DM.", "parameters": {"type": "object", "properties": {"channel": {"type": "string"}, "text": {"type": "string"}}}},
    {"type": "function", "name": "calendar_create_event", "description": "Create a calendar event.", "parameters": {"type": "object", "properties": {"title": {"type": "string"}, "attendees": {"type": "array"}}}},
]


def make_trajectory(rng: random.Random, traj_index: int) -> list[dict]:
    person = f"person_{rng.randint(1, 9)}"
    task = rng.choice(TASKS).format(person=person)
    model = rng.choice(MODEL_IDS)
    num_calls = rng.randint(2, 6)

    input_items: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": f"[traj {traj_index}] {task}"}]},
    ]

    lines = []
    for call_index in range(1, num_calls + 1):
        tools = rng.sample(TOOLS, k=rng.randint(1, len(TOOLS)))
        lines.append({
            "model": model,
            "input": [dict(item) for item in input_items],
            "tools": tools,
        })
        if call_index < num_calls:
            call_id = f"call_{traj_index}_{call_index}"
            input_items.append({
                "type": "function_call",
                "name": rng.choice(TOOLS)["name"],
                "call_id": call_id,
                "arguments": json.dumps({"detail": f"step {call_index} of task for {person}"}),
            })
            input_items.append({
                "type": "function_call_output",
                "call_id": call_id,
                "output": json.dumps({"status": "ok", "detail": f"result of step {call_index}"}),
            })
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=Path("export"))
    parser.add_argument("--num-trajectories", type=int, default=40)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--files", type=int, default=2, help="split output across this many .jsonl files, like the real export")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    args.out.mkdir(parents=True, exist_ok=True)

    all_lines = []
    for i in range(args.num_trajectories):
        all_lines.extend(make_trajectory(rng, i))
    rng.shuffle(all_lines)  # the real export is not trajectory-ordered either

    chunk_size = -(-len(all_lines) // args.files)
    for file_index in range(args.files):
        chunk = all_lines[file_index * chunk_size: (file_index + 1) * chunk_size]
        if not chunk:
            continue
        path = args.out / f"trajectories_v1_{file_index + 1:02d}.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for line in chunk:
                fh.write(json.dumps(line) + "\n")
        print(f"Wrote {len(chunk)} calls to {path}")

    print(f"Generated {args.num_trajectories} synthetic trajectories ({len(all_lines)} calls total) into {args.out}/")
    print("Reminder: this is SYNTHETIC data for smoke-testing only, not the real challenge dataset.")


if __name__ == "__main__":
    main()
