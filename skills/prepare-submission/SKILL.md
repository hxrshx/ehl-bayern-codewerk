---
name: prepare-submission
description: Package the team's solution into a formal submission asset — code, results, presentation, manifest — as one verified zip.
---

# /prepare-submission — package the solution

Submission format and deadline follow general TUM.ai rules announced at kickoff —
**confirm both in the challenge Discord before packaging.** Then:

## 1. Assemble `submission/`
- `SUBMISSION.md` — the manifest (template below)
- `router/` — the team's code, with the exact command that reproduces the headline numbers
- `results/` — `routes.jsonl`, frontier CSV/PNG, any judge-model scores; numbers from the
  **held-out split** clearly separated from the public split
- `presentation.html` (or PDF export)
- **Never include the dataset** — no `export/`, no raw trajectory lines beyond short quoted
  snippets needed to explain the signal. License: challenge use only, no redistribution.

## 2. SUBMISSION.md template
```markdown
# <Team name> — Viktor Challenge submission
Members: ...
Objective: <the trade-off we optimized and why>
Routing signal: <the structure in the traces our router uses>
Headline result (held-out, cache-aware): <e.g. −41% cost at ≥95% kept outcome>
Off-policy method: <matching / weighting / judge model> — weakest point: <named failure mode>
Reproduce: <exact commands, from a clean checkout, dataset path as argument>
```

## 3. Verify, then zip
- Rerun the reproduce commands from a clean copy — they must produce the headline numbers.
- `grep -r` the folder for anything that looks like raw dataset content; remove it.
- `zip -r <team-name>-viktor-challenge.zip submission/` and post the SHA-256 next to wherever
  you submit, so the panel can verify integrity.
