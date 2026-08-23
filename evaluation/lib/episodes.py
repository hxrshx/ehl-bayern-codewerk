#!/usr/bin/env python3
"""Episode-level feature extraction over a redacted-trajectories export.

Reads every export/*.jsonl chunk (one line = one LLM request: model, input,
tools), reconstructs episodes by STRICT item-prefix chaining (request B
continues request A iff B.input[:len(A.input)] == A.input, compared by
per-item content hashes — no reliance on first-user-text collisions), and
emits one feature row per episode to results/episodes.jsonl plus the
normalized first-user payload to results/payloads.jsonl.

PII redaction in the export is numbered PER ROW (PII_URL_3 in row A is not
row B's PII_URL_3), so every cross-row feature goes through the redaction
normalizer below — raw PII tokens are never used as features.

Token counts are ESTIMATES (no usage in the export): chars/4 exactly as in
scripts/load_trajectories.est_tokens, plus tiktoken o200k_base on extracted
text. Deterministic: sorted iteration, no randomness, no timestamps.

Usage: python3 solution/episodes.py <export_dir> [--out results/episodes.jsonl]
"""
import argparse, difflib, hashlib, json, re, sys, zlib
from collections import Counter, defaultdict
from pathlib import Path
import tiktoken

ENC = tiktoken.get_encoding("o200k_base")

# ---------------------------------------------------------------- redaction
PII_RE = re.compile(r"PII_([A-Z]+(?:_[A-Z]+)*?)_\d+")      # PII_URL_3 -> PII_URL
BRACK_RE = re.compile(r"\[([a-z_]+?)_\d+\]")                # [base64_2] -> [base64]
ANGLE_RE = re.compile(r"<([A-Z]+)_[A-Z0-9]+>")              # <PERSON_ROBERT> -> <PERSON>

def normalize_redaction(text):
    """Map all documented per-row-numbered redaction styles to stable generic tokens."""
    text = PII_RE.sub(r"PII_\1", text)
    text = BRACK_RE.sub(r"[\1]", text)
    return ANGLE_RE.sub(r"<\1>", text)

def norm_text(text):
    """Redaction-normalized payload text: also collapse digits and whitespace."""
    text = normalize_redaction(text)
    text = re.sub(r"\d+", "0", text)
    return re.sub(r"\s+", " ", text).strip()

# ------------------------------------------------------------------ loading
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

def item_text(item):
    """Extractable text of one input item (content parts, args, outputs)."""
    if item.get("type") in ("function_call", "custom_tool_call"):
        args = item.get("arguments", item.get("input", ""))
        return f'{item.get("name", "")} {args if isinstance(args, str) else json.dumps(args)}'
    if item.get("type") in ("function_call_output", "custom_tool_call_output"):
        out = item.get("output", "")
        return out if isinstance(out, str) else json.dumps(out)
    c = item.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(p.get("text", "") for p in c if isinstance(p, dict) and "text" in p)
    return ""

# --------------------------------------------------------- request features
CLAUDE_TOOLS = {"bash", "file_read", "file_edit", "file_write"}
GPT_TOOLS = {"shell_command", "apply_patch"}
TRIG_RE = re.compile(r"Triggered by: `?([^`\n]+?)`?\s*$", re.M)
TRIG_FALLBACK_RE = re.compile(r"\bby: (cron|slack_dm|slack_mention|msteams\w*|trigger)\b")
PATH_RE = re.compile(r"- Path: `?([^`\n]+?)`?\s*$", re.M)
ID_RE = re.compile(r"^(?:PII_[A-Z_]+|[A-Za-z0-9]{8,})$")
TASK_RE = re.compile(r"- Task(?: \(from scheduler\))?: (.*?)(?=\n- [A-Z]|\n\n|\n#|$)", re.S)
LEARN_RE = re.compile(r'<auto_read_learnings path="([^"]+)"')

def vendor_of(tool_names):
    if tool_names & CLAUDE_TOOLS: return "claude"
    if tool_names & GPT_TOOLS: return "gpt"
    return "unknown"

def surface_of(tool_names):
    if any("msteams" in n for n in tool_names): return "msteams"
    if "submit_subagent_result" in tool_names: return "subagent"
    return "slack"

def trigger_of(fu):
    """Classify the run trigger from the injected Thread info block."""
    m = TRIG_RE.search(fu)
    val = m.group(1).strip() if m else ""
    if not val:  # redaction sometimes eats "\n- Triggered" into a PII token
        m2 = TRIG_FALLBACK_RE.search(fu[:4000])
        val = m2.group(1) if m2 else ""
    pm = PATH_RE.search(fu)
    path = pm.group(1) if pm else ""
    if val == "cron":
        return "subagent" if "/agent_runs/subagents/" in path else "cron"
    if val in ("slack_dm", "slack_mention"): return val
    if val.startswith("msteams"): return "msteams"
    if val == "trigger": return "event"
    if val and ID_RE.match(val) and not val.endswith("_dm"):
        return "subagent"  # triggered by a parent thread/run id
    return "other"

TASK_KEYWORDS = [  # fixed order = deterministic tie-break
    ("monitoring", r"monitor|watch|alert|poll|check|scan|sweep|watchdog|uptime|heartbeat|detect|triage"),
    ("outreach", r"outreach|lead|follow[- ]?up|campaign|prospect|re[- ]?engag|nudge|cold email|\bsms\b|text message|setter"),
    ("qa_chat", r"\bdm from\b|mention in|question|answer|reply to|respond|support request|\bhelp\b|ticket|inquir"),
    ("reporting", r"report|digest|summary|recap|briefing|\beod\b|kpi|metrics|stats|dashboard|overview"),
    ("data_sync", r"\bsync\b|airtable|notion|hubspot|salesforce|\bcrm\b|spreadsheet|sheet|import|export|upsert|reconcil|backfill|filing"),
    ("reminders", r"remind|due date|deadline|calendar|meeting|schedule|birthday|upcoming"),
    ("coding", r"script|\bcode\b|\bbug\b|\bfix\b|deploy|repo|\bgit\b|build|patch|refactor|endpoint|debug"),
    ("research", r"research|scrape|search for|investigate|competitor|crawl|enrich|qualif"),
    ("content", r"content|blog|social|\bpost\b|linkedin|instagram|caption|clip|video|newsletter|creative|draft"),
]

def task_payload(fu):
    """Scheduler Task: text (or relayed-message tail) that describes the job."""
    m = TASK_RE.search(fu)
    return m.group(1) if m else fu[fu.find("Thread info"):][:4000] if "Thread info" in fu else fu[:4000]

def task_type_of(fu, trigger):
    t = norm_text(task_payload(fu)).lower()
    scores = [(len(re.findall(pat, t)), name) for name, pat in TASK_KEYWORDS]
    best = max(scores, key=lambda s: s[0])
    if best[0] > 0: return best[1]
    return "qa_chat" if trigger in ("slack_dm", "slack_mention", "msteams") else "monitoring"

def usable_job_path(path):
    """Usable iff some segment is distinctive: a lowercase multi-word slug, not a
    placeholder. Kills generic paths (/heartbeat, crons/PII_PROJECT/PII_URL) that
    would otherwise merge unrelated orgs' jobs into one fake family."""
    return any("PII_" not in s and ("-" in s or "_" in s) and any(c.islower() for c in s)
               for s in normalize_redaction(path).split("/"))

def job_key_of(sys_text, fu, trigger):
    """Recurring-job key: the job's learnings-file path (anchored attribute — a free
    crons/ regex hits boilerplate docs in most system prompts), else the Thread-info
    path with its per-run /threads/<ts> tail stripped, else interactive:<trigger>."""
    if trigger in ("cron", "event", "subagent"):
        m = LEARN_RE.search(fu) or LEARN_RE.search(sys_text)
        if m and usable_job_path(m.group(1)):
            return normalize_redaction(m.group(1))
        pm = PATH_RE.search(fu)
        if pm:
            p = re.sub(r"/threads/.*$", "", pm.group(1).strip())
            if usable_job_path(p):
                return normalize_redaction(p)
    return f"interactive:{trigger}"

ERR_RE = re.compile(r'"exit_code":\s*[1-9]|Exit code: [1-9]|Traceback \(most recent'
                    r"|No such file|command not found|ENOENT")

def behavior_of(items):
    outs = {it.get("call_id"): it.get("output", "") for it in items
            if it.get("type") in ("function_call_output", "custom_tool_call_output")}
    calls = []
    for it in items:
        if it.get("type") not in ("function_call", "custom_tool_call"): continue
        args = it.get("arguments", it.get("input", ""))
        if not isinstance(args, str): args = json.dumps(args, sort_keys=True)
        out = outs.get(it.get("call_id"), "")
        if not isinstance(out, str): out = json.dumps(out)
        calls.append({"name": it.get("name", ""),
                      "args_sha8": hashlib.sha1(norm_text(args).encode()).hexdigest()[:8],
                      "is_err": bool(ERR_RE.search(out[:4000]))})
    last = items[-1] if items else {}
    return {"n_calls": len(calls), "calls": calls,
            "n_user_msgs": sum(1 for it in items if it.get("role") == "user"),
            "final_type": f'{last.get("type") or "message"}:{last.get("role", "")}'.rstrip(":"),
            "ends_silent": not any(c["name"].startswith("coworker_send_") for c in calls)}

def model_meta(model, ladders):
    """(tier rank within vendor, generation) parsed from the anonymized model id."""
    m = re.match(r"(claude|gpt)-(?:([0-9.]+)-)?([a-z]+)(?:-([0-9-]+))?$", model)
    if not m: return 0, ""
    _, gen_pre, subfam, gen_post = m.groups()
    return ladders.get(subfam, 0), gen_pre or gen_post or ""

def load_ladders(pricing_path):
    """Price rank per subfamily (1=cheapest) from pricing.json; spec ladder fallback."""
    ranks = {}
    try:
        pricing = json.loads(Path(pricing_path).read_text())
        fams = {"claude": {}, "gpt": {}}
        for mid, (pu, _, _) in ((k, v) for k, v in pricing.items() if k != "_default"):
            m = re.match(r"(claude|gpt)-(?:[0-9.]+-)?([a-z]+)$", mid)  # family-level ids only
            if m: fams[m.group(1)][m.group(2)] = pu
        for fam in fams.values():
            for rank, sub in enumerate(sorted(fam, key=fam.get), 1): ranks[sub] = rank
    except OSError:
        pass
    return ranks or {"sonnet": 1, "opus": 2, "fable": 3, "luna": 1, "terra": 2, "sol": 3}

def request_features(chunk, line, req):
    """All per-request features, computed in one pass (no raw input retained)."""
    items, tools = req["input"], req.get("tools", [])
    names = {t.get("name", "") for t in tools}
    sys_items = [it for it in items if it.get("role") == "system"]
    fu_item = next((it for it in items if it.get("role") == "user"), None)
    sys_text = "\n".join(item_text(it) for it in sys_items)
    fu = item_text(fu_item) if fu_item else ""
    trigger = trigger_of(fu)
    hist_text = "\n".join(item_text(it) for it in items
                          if it is not fu_item and it.get("role") != "system")
    chars = {"sys": sum(len(json.dumps(it)) for it in sys_items),
             "tools": len(json.dumps(tools)),
             "user_ctx": len(json.dumps(fu_item)) if fu_item else 0,
             "total": len(json.dumps(items))}
    chars["hist"] = chars["total"] - chars["sys"] - chars["user_ctx"]
    bpe = {"sys": len(ENC.encode(sys_text, disallowed_special=())),
           "tools": len(ENC.encode(json.dumps(tools, sort_keys=True), disallowed_special=())),
           "user_ctx": len(ENC.encode(fu, disallowed_special=())),
           "hist": len(ENC.encode(hist_text, disallowed_special=()))}
    bpe["total"] = bpe["sys"] + bpe["user_ctx"] + bpe["hist"]
    fu_norm = norm_text(fu[:40000])  # full-text similarity (capped), not a short prefix:
    # different jobs share long boilerplate openings that a prefix would falsely merge
    return {"row_id": f"{chunk}:{line}", "model": req.get("model", ""),
            "vendor": vendor_of(names),
            "tool_fp": hashlib.sha1(json.dumps(sorted(names)).encode()).hexdigest()[:12],
            "surface": surface_of(names), "trigger": trigger,
            "task_type": task_type_of(fu, trigger),
            "job_key": job_key_of(sys_text, fu, trigger),
            "sys_sha": hashlib.sha1(normalize_redaction(sys_text).encode()).hexdigest(),
            "fu_norm": fu_norm[:20000], "first_user_len": len(fu),
            "payload_norm": fu_norm[:4096],
            "tok_chars4": {k: v // 4 for k, v in chars.items()},
            "tok_bpe": bpe, "behavior": behavior_of(items),
            "item_hashes": [hashlib.sha1(json.dumps(it, sort_keys=True).encode()).hexdigest()
                            for it in items]}

# ----------------------------------------------------------------- chaining
class UnionFind:
    def __init__(self, n): self.p = list(range(n))
    def find(self, x):
        while self.p[x] != x: self.p[x] = self.p[self.p[x]]; x = self.p[x]
        return x
    def union(self, a, b): self.p[self.find(a)] = self.find(b)

def chain_episodes(rows):
    """Group requests into episodes by strict item-prefix chaining.

    Cumulative content hashes make the prefix test O(1) per length: request B
    continues A iff B's length-len(A) cumulative hash equals A's full-input hash."""
    cums, full = [], defaultdict(list)
    for i, r in enumerate(rows):
        c, cc = "", []
        for h in r["item_hashes"]:
            c = hashlib.sha1((c + h).encode()).hexdigest()
            cc.append(c)
        cums.append(cc)
        full[cc[-1]].append(i)
    uf = UnionFind(len(rows))
    for i, cc in enumerate(cums):
        for c in cc:  # every prefix (full length included: exact dups group too)
            for j in full.get(c, ()):
                if j != i: uf.union(i, j)
    groups = defaultdict(list)
    for i in range(len(rows)): groups[uf.find(i)].append(i)
    return [sorted(g, key=lambda i: (len(rows[i]["item_hashes"]), rows[i]["row_id"]))
            for g in groups.values()]

# ----------------------------------------------------------------- families
def shingles(text, k=5):
    words = re.findall(r"\w+", text.lower())
    return {zlib.crc32(" ".join(words[i:i + k]).encode())
            for i in range(max(1, len(words) - k + 1))}

def assign_families(eps):
    """Union-find over exact-system-sha / job_key / >=0.9 first-user similarity."""
    uf = UnionFind(len(eps))
    for key_of in (lambda e: "sys:" + e["sys_sha"],
                   lambda e: "job:" + e["job_key"]
                   if not e["job_key"].startswith("interactive:") else None):
        seen = {}
        for i, e in enumerate(eps):
            k = key_of(e)
            if k is None: continue
            if k in seen: uf.union(i, seen[k])
            else: seen[k] = i
    # similarity leg: minhash-bucket prefilter, Jaccard gate, difflib confirm
    shs = [shingles(e["fu_norm"]) for e in eps]
    buckets = defaultdict(list)
    for i, sh in enumerate(shs):
        for h in sorted(sh)[:12]: buckets[h].append(i)
    for members in buckets.values():
        for a_pos, i in enumerate(members):
            for j in members[a_pos + 1:]:
                if uf.find(i) == uf.find(j): continue
                inter = len(shs[i] & shs[j])
                if inter < 0.5 * len(shs[i] | shs[j]): continue
                sm = difflib.SequenceMatcher(None, eps[i]["fu_norm"], eps[j]["fu_norm"])
                if sm.quick_ratio() >= 0.9 and sm.ratio() >= 0.9: uf.union(i, j)
    fams = defaultdict(list)
    for i in range(len(eps)): fams[uf.find(i)].append(i)
    for members in fams.values():
        fid = min(eps[i]["row_id"] for i in members)
        for i in members: eps[i]["family_id"] = fid
    return fams

# --------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("export_dir")
    ap.add_argument("--out", default="results/episodes.jsonl")
    args = ap.parse_args()

    rows, req_chars4_total = [], 0
    for chunk, line, req in iter_requests(args.export_dir):
        rows.append(request_features(chunk, line, req))
        req_chars4_total += rows[-1]["tok_chars4"]["total"]
    print(f"requests={len(rows)}  est input tokens (chars/4, all requests): {req_chars4_total:,}")

    ladders = load_ladders(Path(__file__).resolve().parent.parent / "scripts" / "pricing.json")
    episodes = []
    for group in sorted(chain_episodes(rows), key=lambda g: rows[g[-1]]["row_id"]):
        e = dict(rows[group[-1]])  # longest request echoes the whole loop
        e["n_requests"] = len(group)
        e["tier"], e["generation"] = model_meta(e["model"], ladders)
        # Per-request ledger for cache-aware chain pricing (WP-C). Request k of a
        # chain re-sends every item of request k-1, so its shared prefix is the
        # FULL token count of k-1; only the delta is new. Single-request episodes
        # get one entry with shared=0. Held-out chunks with real chains price
        # correctly off this; chunk-01 is all singletons so it is a no-op.
        ledger = []
        for pos, ri in enumerate(group):
            r = rows[ri]
            prev = rows[group[pos - 1]] if pos else None
            ledger.append({
                "model": r["model"],
                "chars4": r["tok_chars4"]["total"],
                "bpe": r["tok_bpe"]["total"],
                "shared_chars4": prev["tok_chars4"]["total"] if prev else 0,
                "shared_bpe": prev["tok_bpe"]["total"] if prev else 0,
            })
        e["req_ledger"] = ledger
        del e["item_hashes"]
        episodes.append(e)
    fams = assign_families(episodes)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    keep = ("row_id", "n_requests", "vendor", "tool_fp", "surface", "trigger", "task_type",
            "job_key", "family_id", "model", "tier", "generation", "tok_chars4", "tok_bpe",
            "behavior", "first_user_len", "req_ledger")
    with open(out, "w") as f:
        for e in episodes:
            f.write(json.dumps({k: e[k] for k in keep}) + "\n")
    with open(out.parent / "payloads.jsonl", "w") as f:
        for e in episodes:
            f.write(json.dumps({"row_id": e["row_id"], "payload_norm": e["payload_norm"]}) + "\n")

    # ---- summary (all counts recomputed from the emitted episodes) ----
    vend, trig, fps = Counter(), Counter(), Counter()
    bpe_v, ch4_v = Counter(), Counter()
    for e in episodes:
        vend[e["vendor"]] += 1; trig[e["trigger"]] += 1; fps[e["tool_fp"]] += 1
        bpe_v[e["vendor"]] += e["tok_bpe"]["total"]; ch4_v[e["vendor"]] += e["tok_chars4"]["total"]
    multi = [m for m in fams.values() if len(m) >= 2]
    print(f"episodes={len(episodes)}  singletons={sum(1 for e in episodes if e['n_requests'] == 1)}"
          f"  multi-request={sum(1 for e in episodes if e['n_requests'] > 1)}")
    print(f"vendor: {dict(vend.most_common())}   tool_fps={len(fps)}")
    print(f"trigger: {dict(trig.most_common())}")
    print(f"task_type: {dict(Counter(e['task_type'] for e in episodes).most_common())}")
    print(f"families: total={len(fams)}  recurring(>=2)={len(multi)}"
          f"  rows in recurring={sum(len(m) for m in multi)}")
    print("tokens per vendor (episode totals): " + "  ".join(
        f"{v}: chars4={ch4_v[v]:,} bpe={bpe_v[v]:,}" for v in sorted(vend)))
    print(f"wrote {out} and {out.parent / 'payloads.jsonl'}")

if __name__ == "__main__":
    main()
