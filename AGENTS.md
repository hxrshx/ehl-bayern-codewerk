# Agent briefing — Viktor Challenge: Build the Router

You are helping a hackathon team at TUM.ai (22–23 Aug 2026). The task: from an offline export
of real agent trajectories, build a **model router** and an **honest off-policy evaluation** of it.

## Guided workflows (read the one you need, follow it step by step)

- `skills/setup/SKILL.md` — environment + dataset setup, sanity checks, first baseline
- `skills/make-presentation/SKILL.md` — Viktor-branded presentation of the solution
- `skills/prepare-submission/SKILL.md` — package the solution into a formal submission

## The dataset (redacted export of real Viktor trajectories)

The export ships as `trajectories_v1_<index>.jsonl.tar.gz` archives (links posted at
kickoff). Each extracts to one JSONL chunk:

```
export/
└─ trajectories_v1_*.jsonl   # one line = one real LLM request
```

Each JSONL line is one LLM request in the OpenAI-compatible Responses format, with exactly
three fields:

- `model` — **anonymized** id that served the call. Families and generations stay readable:
  `claude-opus-5`, `claude-sonnet-5`, `claude-fable-5`, `claude-opus-4-8`, `gpt-5.6-sol`,
  `gpt-5.6-terra`, `gpt-5.6-luna`, ... (~9 ids; real names hidden; known: `fable` is the most expensive tier, otherwise tier order not published)
- `input` — full request history: `message` items (system/user/assistant; content parts are
  `input_text` and `input_image`), `function_call` / `function_call_output`,
  `custom_tool_call` / `custom_tool_call_output` (e.g. `apply_patch`), and `reasoning`
  items (empty summaries; gpt-family only). Each call's input contains every item of the
  call before it.
- `tools` — the function definitions available to the call (the real Viktor toolset)

**There is no `output` and no `usage`.** Also no trajectory ids, no timing, no quality labels.
Redaction: entities appear as named placeholders — `<PERSON_ROBERT>`, `<PROJECT_NAME>`,
`<COMPANY_A>`, `<CHANNEL_NAME>`, ids as `<ID_13>` — consistent within a trajectory, so
references still resolve. Images are placeholder data URLs (`[base64 image redacted]`).

Key consequences:
- **Trajectories must be reconstructed**: group requests by their opening messages (same
  system + first user text), order by input length. `scripts/load_trajectories.py` does this.
- **Earlier outputs are recoverable**: what the model returned on call *i* appears inside call
  *i+1*'s input (the `function_call` / assistant items). Only the final call's output is truly gone.
- **All token counts are estimates** (chars/4 in this repo — or bring a tokenizer). Say so in
  the writeup; never present estimated tokens as measured.
- **The cache trap:** providers cache the shared input prefix across a task's calls; a model
  switch resets that cache — calls after a switch pay full price for the whole prefix. With no
  `usage.cached_tokens`, the cached share is *inferred* from item-level prefix overlap.
  `scripts/cost_model.py` implements this.
- **One model serves all calls of a trajectory** (premise); the model varies across
  trajectories — that variation is the natural experiment. The loader flags violations.
- **Pricing is an assumption**: model ids are anonymized, so no public price sheet applies.
  Use `scripts/pricing.json` if the organizers post prices; otherwise state your assumption.

## The twist (off-policy evaluation)

The log shows only the model that actually ran. Estimating what a different route would have
cost/kept is the depth of the challenge: matching or weighting across trajectories, judge-model
rescoring of single calls. Name where the estimate can fail — that is scored, not penalized.

## Judging (3 criteria, weights announced at kickoff)

1. **Routing insight** — real, nameable structure in the traces beats a black box
2. **Evaluation rigor** — honest off-policy estimate, cache-aware pricing
3. **Presentation clarity** — 5 minutes: one chart, one claim, one known weakness

Special prize: best off-policy-evaluation insight. Headline artifact: the **cost–quality frontier**, not a single point.

## Hard rules for you, the agent

- Dataset is **challenge use only, no redistribution** — never upload it anywhere or commit it
  to git (`export/` is gitignored; keep it there).
- If the organizers post schema notes or a price sheet that contradicts this document, trust them.
- Don't invent rules, deadlines, prizes, or credits — the team should ask in the challenge Discord.
- Keep everything runnable offline on a laptop: no GPUs, no API keys required
  (judge-model rescoring with the team's own keys is allowed).
