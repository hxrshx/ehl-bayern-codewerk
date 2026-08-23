#!/usr/bin/env python3
"""Chart: does the router generalize to data it never saw?

Reads validation/results/annotated_v1_01.jsonl and annotated_v1_02.jsonl (run
validation/annotate.py against both chunks first) and plots logged vs. proposed
cost side by side for the build chunk (01) and the held-out chunk (02) — the
generalization check, not the headline cost-quality frontier (see evaluation/
for that).

Usage: python3 validation/chart.py
Writes validation/results/generalization.png
"""
import json
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

RESULTS = Path(__file__).resolve().parent / "results"

def summarize(annotated_path):
    rows = [json.loads(l) for l in open(annotated_path)]
    logged = sum(r["cost_logged_usd"] for r in rows)
    proposed = sum(r["cost_proposed_usd"] for r in rows)
    rerouted = sum(r["proposed_model"] != r["logged_model"] for r in rows)
    return logged, proposed, rerouted, len(rows)

def main():
    chunks = [("Chunk 01\n(build)", RESULTS / "annotated_v1_01.jsonl"),
              ("Chunk 02\n(held-out)", RESULTS / "annotated_v1_02.jsonl")]
    labels, logged_vals, proposed_vals, notes = [], [], [], []
    for label, path in chunks:
        if not path.exists():
            raise SystemExit(f"missing {path} — run validation/annotate.py on that chunk first")
        logged, proposed, rerouted, n = summarize(path)
        labels.append(label)
        logged_vals.append(logged)
        proposed_vals.append(proposed)
        notes.append(f"{(proposed / logged - 1) * 100:+.1f}%\n{rerouted}/{n} rerouted")

    x = range(len(labels))
    w = 0.32
    fig, ax = plt.subplots(figsize=(6, 4.2))
    b1 = ax.bar([i - w / 2 for i in x], logged_vals, width=w, label="Logged (as-run)", color="#c9c6e0")
    b2 = ax.bar([i + w / 2 for i in x], proposed_vals, width=w, label="Router proposed", color="#6748FD")

    for i, (bar_l, bar_p, note) in enumerate(zip(b1, b2, notes)):
        ax.text(bar_l.get_x() + bar_l.get_width() / 2, bar_l.get_height() + 1.5,
                f"${bar_l.get_height():.0f}", ha="center", fontsize=9, color="#555")
        ax.text(bar_p.get_x() + bar_p.get_width() / 2, bar_p.get_height() + 1.5,
                f"${bar_p.get_height():.0f}", ha="center", fontsize=9, color="#6748FD", fontweight="bold")
        ax.text(i, max(bar_l.get_height(), bar_p.get_height()) + 9, note,
                ha="center", fontsize=8.5, color="#333")

    ax.set_xticks(list(x)); ax.set_xticklabels(labels)
    ax.set_ylabel("Total cost (USD, est. input tokens)")
    ax.set_title("Router generalizes: build chunk vs. genuinely held-out chunk")
    ax.set_ylim(0, max(logged_vals) * 1.35)
    ax.legend(frameon=False, loc="upper right")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()

    out = RESULTS / "generalization.png"
    fig.savefig(out, dpi=150)
    print(f"wrote {out}")

if __name__ == "__main__":
    main()
