#!/usr/bin/env python3
"""The one chart: cost against outcome quality, with 95% bootstrap CIs.
Quality is plotted as a percentage of what Viktor gets today, so the dashed
parity line is the thing to read against. Usage: python3 evaluation/chart.py"""
import json
from pathlib import Path
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

R = Path(__file__).resolve().parent / "results"
rows = {r["policy"]: r for r in json.loads((R / "scorecard.json").read_text())}
LOG = rows["logged policy (what Viktor runs today)"]
OUR = rows["rule_based_router"]
DEG = rows["route everything cheap"]
TOP = rows["always the dearest model"]
V, N, PCH, GREY = "#5B3BE8", "#12132B", "#E8845A", "#9E99B8"

fig, ax = plt.subplots(figsize=(12, 7), dpi=150)
ax.axhline(100, ls="--", lw=1.4, color=GREY, zorder=1)
ax.text(3, 100.09, "parity with what Viktor runs today", fontsize=11.5, color=GREY, va="bottom")

for r, col, mk, lab in ((LOG, N, "o", "logged policy (today)"),
                        (OUR, V, "o", "rule_based_router")):
    ax.errorbar([r["cost_usd"]], [r["quality_pct"]],
                yerr=[[r["quality_pct"] - r["quality_ci"][0]], [r["quality_ci"][1] - r["quality_pct"]]],
                fmt=mk, color=col, ms=13 if r is OUR else 10, lw=0, capsize=6,
                elinewidth=1.8, ecolor=col, zorder=6, label=lab)
ax.plot([LOG["cost_usd"], OUR["cost_usd"]], [LOG["quality_pct"], OUR["quality_pct"]],
        "-", color=V, lw=2.4, alpha=.55, zorder=4)
ax.errorbar([DEG["cost_usd"]], [DEG["quality_pct"]],
            yerr=[[DEG["quality_pct"] - DEG["quality_ci"][0]], [DEG["quality_ci"][1] - DEG["quality_pct"]]],
            fmt="X", color=PCH, ms=15, lw=0, capsize=5, elinewidth=1.4, ecolor=PCH,
            zorder=6, label="route everything cheap (no evidence)")

ax.annotate(f"−{100 - OUR['cost_pct']:.0f}% cost   at {OUR['quality_pct']:.1f}% of today's quality\n"
            f"{OUR['rerouted']} of 1,000 tasks rerouted, all 9 models still in play",
            (OUR["cost_usd"], OUR["quality_pct"]), xytext=(-16, 46), textcoords="offset points", ha="center",
            fontsize=13, fontweight="bold", color=V,
            arrowprops=dict(arrowstyle="-", color=V, lw=1.4))
ax.annotate(f"${DEG['cost_usd']:.0f} · {DEG['quality_pct']:.1f}%\ncheaper, but no evidence behind it —\nand our CI cannot fully separate it from us",
            (DEG["cost_usd"], DEG["quality_pct"]), xytext=(30, 34), textcoords="offset points", ha="left",
            fontsize=10.5, color=N, arrowprops=dict(arrowstyle="-", color=N, lw=1.1))
ax.annotate(f"always the dearest model → ${TOP['cost_usd']:.0f}  ({TOP['cost_pct']:.0f}% of today)\n"
            f"for +{TOP['quality_pct'] - 100:.1f}% quality",
            (0.985, 0.055), xycoords="axes fraction", ha="right", fontsize=11.5,
            color=PCH, fontweight="bold")

ax.set_xlim(0, 175); ax.set_ylim(97.9, 101.0)
ax.set_xlabel("cost for the whole corpus, USD   (cache-aware · tool + output tokens included · estimated tokens)", fontsize=13)
ax.set_ylabel("outcome quality, % of today", fontsize=13)
ax.set_title("What each dollar of saving is backed by", fontsize=17, color=N, fontweight="bold", pad=14)
ax.tick_params(labelsize=12); ax.grid(alpha=.2)
ax.legend(fontsize=11.5, loc="lower right", framealpha=.96, bbox_to_anchor=(1.0, 0.14))
ax.text(.005, -.135, "Bars are 95% bootstrap CIs over tasks (400 resamples, seeded). Quality is a constructed "
        "failure-counter signal, not a label; token counts are estimates.",
        transform=ax.transAxes, fontsize=9.5, color="#666")
fig.tight_layout(); fig.savefig(R.parent / "frontier.png", bbox_inches="tight")
print(f"wrote {R.parent / 'frontier.png'}")
