#!/usr/bin/env python3
"""Generate a synthetic export/ with the redacted trajectories schema.

Shape-identical to the real dataset (trajectories_v1_*.jsonl; one line = one LLM
request with `model`, `input`, `tools` — NO `output`, NO `usage`) so pipelines
built on it run unchanged on the real export. Content is synthetic filler —
do not draw conclusions from it.
"""
import json, random, argparse
from pathlib import Path

MODELS = ["claude-opus-5", "claude-sonnet-5", "claude-fable-5", "claude-opus-4-8",
          "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"]
TOOLS = [{"type": "function", "name": "run_shell", "description": "Run a shell command",
          "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]},
          "strict": False},
         {"type": "function", "name": "file_read", "description": "Read a file",
          "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
          "strict": False}]
IMG_PLACEHOLDER = "data:image/png;base64,[base64 image redacted]"

def msg(role, text):
    return {"type": "message", "role": role, "content": [{"type": "input_text", "text": text}]}

def make_task_requests(rng, task_id, model):
    """One task -> several requests. Each request's input = full history so far."""
    history = [
        {"role": "system", "content": f"You are an autonomous agent. Task context {task_id}. User is <PERSON_A> at <COMPANY_A>."},
        msg("user", f"Task {task_id}: do the synthetic thing for <PERSON_A> on <PROJECT_NAME>. " + "filler " * rng.randint(30, 400)),
    ]
    if rng.random() < 0.3:
        history[1]["content"].append({"type": "input_image", "image_url": IMG_PLACEHOLDER, "detail": "auto"})
    requests, n_calls = [], rng.randint(3, 10)
    for i in range(n_calls):
        requests.append({"model": model, "input": [json.loads(json.dumps(m)) for m in history], "tools": TOOLS})
        # what the model "did" on this call becomes part of the next call's input
        if model.startswith("gpt"):
            history.append({"type": "reasoning", "id": f"rs_{task_id}_{i}", "summary": []})
        call_id = f"toolu_{task_id}_{i}"
        history.append({"type": "function_call", "name": "run_shell",
                        "arguments": json.dumps({"cmd": f"step-{i} " + "x" * rng.randint(10, 200)}), "call_id": call_id})
        history.append({"type": "function_call_output", "call_id": call_id,
                        "output": f"step-{i} ok. " + "log " * rng.randint(10, 300)})
        if i == n_calls - 2:
            history.append(msg("assistant", "Both moved and verified."))
    return requests

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="export"); ap.add_argument("--n-tasks", type=int, default=25)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args(); rng = random.Random(a.seed)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    lines, per_model = [], {}
    for t in range(1, a.n_tasks + 1):
        model = rng.choice(MODELS); per_model[model] = per_model.get(model, 0) + 1
        lines += make_task_requests(rng, t, model)
    rng.shuffle(lines)  # real chunks give no ordering guarantee — do not rely on line order
    with open(out / "trajectories_v1_00.jsonl", "w") as f:
        for r in lines: f.write(json.dumps(r) + "\n")
    print(f"Wrote synthetic sample: {a.n_tasks} tasks, {len(lines)} requests -> {out}/trajectories_v1_00.jsonl")
    print("per-model task counts:", per_model)

if __name__ == "__main__": main()
