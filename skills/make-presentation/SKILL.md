---
name: make-presentation
description: Build a Viktor-branded presentation of the team's solution, structured for the 5-minute judged defense.
---

# /make-presentation — the 5-minute defense deck

Judges score presentation clarity as: **one chart, one claim, one known weakness — in 5 minutes.**

## 1. Collect from the team (ask, don't invent)
- Team name + members
- The objective they chose (cost / latency / quality / own trade-off) and why
- The routing signal (what structure in the traces does the router use — must be nameable)
- Headline numbers from the **held-out split**, priced cache-aware
- The off-policy method (matching / weighting / judge model) and its weakest failure mode

## 2. Build from the template
- Copy `templates/presentation.html` and fill the marked `<!-- FILL -->` slots. It is
  self-contained (no network needed), arrow keys / space to navigate, `f` for fullscreen.
- Slide order is fixed on purpose:
  1. **Title** — team, one-line claim
  2. **Objective** — the trade-off chosen, in one sentence
  3. **The signal** — what the router looks at, shown on a real trace example
  4. **The frontier** — the one chart (embed `results/frontier.png` as base64 or an `<img>`)
  5. **Honesty** — how the off-policy estimate was made, and where it breaks
  6. **The ask/close** — one claim, one number, one weakness, next step
- Brand: violet `#6748FD` on navy `#150079`, peach `#FFBD9E` accents — already in the template.
  Don't add extra colors; don't add hype words. Short, active sentences.

## 3. Quality gate before you say "done"
- Every number on a slide traces to a script output the team can rerun.
- The weakness slide names a real failure mode (cache reset, selection bias, judge-model
  disagreement…) — an honest weakness scores; a hidden one costs.
- Open the file in a browser and step through all slides; check nothing overflows.
