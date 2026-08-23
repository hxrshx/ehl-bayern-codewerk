#!/usr/bin/env python3
"""Tiny local demo UI: type a task, toggle what happened during it, see which
model the real router picks and why.

Stdlib only, no framework. Calls feature_extraction.py and router.py
UNMODIFIED on a synthetic call built from the form input — no rule logic is
reimplemented here, so the UI can never drift from what the router actually
does. A bare prompt alone can't exercise most of the router's real signals
(those need tool-call history, not just text) — the toggles construct real
synthetic function_call / function_call_output / reasoning items so the
genuine feature-extraction code has something to detect.

Run:    python3 validation/router_ui.py
Then:   open http://localhost:8787
"""
import html, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "rule_based_router" / "scripts"))
from feature_extraction import extract_features  # noqa: E402
from router import route_call, LADDER  # noqa: E402

PORT = 8787
LARGE_PAD_CHARS = 145_000  # pushes cumulative_input_tokens_est past router.LARGE_INPUT_TOKENS
FLAGS = ("stakes", "failed", "retry", "reasoning", "large", "resumed_wait")

def build_call(prompt, stakes, failed, retry, reasoning, large, resumed_wait):
    sys_text = "You are Viktor, an autonomous AI coworker."
    if large:
        sys_text += "x" * LARGE_PAD_CHARS
    items = [
        {"type": "message", "role": "system", "content": sys_text},
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": prompt}]},
    ]
    if reasoning:
        items.append({"type": "reasoning", "id": "r1", "summary": [{"type": "summary_text", "text": "thinking"}]})
    if retry:
        for i in range(3):
            items.append({"type": "function_call", "name": "bash", "call_id": f"retry{i}", "arguments": '{"command":"ls /work"}'})
            items.append({"type": "function_call_output", "call_id": f"retry{i}", "output": '{"content":"same","exit_code":0}'})
    if stakes:
        items.append({"type": "function_call", "name": "delete_record", "call_id": "s1", "arguments": "{}"})
        items.append({"type": "function_call_output", "call_id": "s1", "output": '{"success":true}'})
    if resumed_wait:
        items.append({"type": "function_call", "name": "wait_for_background_work", "call_id": "w1", "arguments": "{}"})
        items.append({"type": "function_call_output", "call_id": "w1", "output": '{"wake_reason":"condition_met"}'})
    if failed:
        items.append({"type": "function_call", "name": "bash", "call_id": "e1", "arguments": '{"command":"do_thing"}'})
        items.append({"type": "function_call_output", "call_id": "e1", "output": '{"content":"","exit_code":1}'})
    items.append({"type": "message", "role": "assistant", "content": [{"type": "input_text", "text": ""}]})
    return {"model": "unknown", "input": items, "tools": []}

def route(prompt, **flags):
    call = build_call(prompt, **flags)
    feats = extract_features("ui", [call])[0]
    model, rules = route_call(feats)
    return model, rules

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Router demo</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:640px;margin:40px auto;padding:0 16px;color:#0b0b0b}}
textarea{{width:100%;height:100px;font:inherit;padding:8px;box-sizing:border-box}}
label{{display:block;margin:6px 0;font-size:14px}}
button{{margin-top:12px;padding:8px 16px;font-size:14px;cursor:pointer}}
.result{{margin-top:24px;padding:16px;border:1px solid #ccc;border-radius:8px;background:#fafafa}}
.model{{font-size:20px;font-weight:700}}
.rules{{color:#555;font-size:13px;margin-top:6px}}
.ladder{{font-size:12px;color:#888;margin-top:10px;word-break:break-all}}
</style></head><body>
<h2>Rule-based router — live demo</h2>
<p style="color:#666;font-size:13px">Type a task, toggle what happened during it. The toggles build real
synthetic tool-call history so the actual router.py / feature_extraction.py code runs unmodified —
this isn't a mockup of the rules, it's the rules.</p>
<form method="POST">
<textarea name="prompt" placeholder="e.g. Delete the stale invoice records and confirm with finance">{prompt}</textarea>
<label><input type="checkbox" name="stakes" {stakes}> A stakes tool was called (delete/pay/publish/...)</label>
<label><input type="checkbox" name="failed" {failed}> Most recent tool result looked like an error</label>
<label><input type="checkbox" name="retry" {retry}> Stuck retrying the same tool call</label>
<label><input type="checkbox" name="reasoning" {reasoning}> Earlier turns used step-by-step reasoning (GPT-style)</label>
<label><input type="checkbox" name="large" {large}> Unusually large accumulated context</label>
<label><input type="checkbox" name="resumed_wait" {resumed_wait}> Just resumed from a background-wait check</label>
<button type="submit">Route it</button>
</form>
{result}
</body></html>"""

class Handler(BaseHTTPRequestHandler):
    def _render(self, prompt="", flags=None, result_html=""):
        flags = flags or {}
        checked = {k: ("checked" if flags.get(k) else "") for k in FLAGS}
        body = PAGE.format(prompt=html.escape(prompt), result=result_html, **checked)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())

    def do_GET(self):
        self._render()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        data = parse_qs(self.rfile.read(length).decode())
        prompt = data.get("prompt", [""])[0]
        flags = {k: (k in data) for k in FLAGS}
        result_html = ""
        if prompt.strip():
            model, rules = route(prompt, **flags)
            idx = LADDER.index(model)
            ladder_str = "  ".join(f"[{i}]{'*' if m == model else ''}{m}" for i, m in enumerate(LADDER))
            result_html = f"""<div class="result">
              <div class="model">{html.escape(model)}</div>
              <div class="rules">rules fired: {html.escape(', '.join(rules))}</div>
              <div class="ladder">ladder position {idx}/{len(LADDER) - 1} — {html.escape(ladder_str)}</div>
            </div>"""
        self._render(prompt, flags, result_html)

    def log_message(self, fmt, *args):
        pass  # keep the console quiet

def main():
    print(f"Router demo running at http://localhost:{PORT} (Ctrl+C to stop)")
    HTTPServer(("localhost", PORT), Handler).serve_forever()

if __name__ == "__main__":
    main()
