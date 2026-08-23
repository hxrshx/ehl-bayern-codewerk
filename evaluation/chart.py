#!/usr/bin/env python3
"""The one chart: what each dollar of saving is backed by.

Cost against outcome quality, with 95% bootstrap CIs on both. Quality is plotted
as a percentage of what Viktor gets today, so the dashed parity line is the thing
to read against. The arrow is the point of the chart: it shows the move from the
starter kit's baseline to this repo's router, which is the comparison the
challenge asks for ("build your router, beat the baseline").

Usage: python3 evaluation/chart.py
"""
import json
from pathlib import Path
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch

R = Path(__file__).resolve().parent / "results"
rows = {r["policy"]: r for r in json.loads((R / "scorecard.json").read_text())}
LOG  = rows["logged policy (what Viktor runs today)"]
BASE = rows["starter baseline_router"]
OUR  = rows["rule_based_router"]
DEG  = rows["route everything cheap"]
TOP  = rows["always the dearest model"]

V, N, PCH, GREY, GREEN = "#5B3BE8", "#12132B", "#E8845A", "#8E8AA8", "#2F8468"
fig, ax = plt.subplots(figsize=(12.5, 7.2), dpi=150)

# parity reference
ax.axhline(100, ls="--", lw=1.4, color=GREY, zorder=1)
ax.text(3, 100.07, "parity with what Viktor runs today", fontsize=11.5, color=GREY, va="bottom")

def pt(r, col, mk, ms, lab, z=6):
    ax.errorbar([r["cost_usd"]], [r["quality_pct"]],
                yerr=[[r["quality_pct"] - r["quality_ci"][0]],
                      [r["quality_ci"][1] - r["quality_pct"]]],
                fmt=mk, color=col, ms=ms, lw=0, capsize=5, elinewidth=1.7,
                ecolor=col, zorder=z, label=lab)

pt(LOG,  N,    "o", 11, "logged policy — what Viktor runs today")
pt(BASE, GREY, "s", 12, "starter kit baseline_router")
pt(OUR,  V,    "o", 15, "our router (rule_based_router)")
pt(DEG,  PCH,  "X", 14, "route everything cheap — no evidence")

# THE ARROW: baseline -> ours. This is the improvement the challenge asks about.
ax.add_patch(FancyArrowPatch(
    (BASE["cost_usd"], BASE["quality_pct"]), (OUR["cost_usd"], OUR["quality_pct"]),
    arrowstyle="-|>", mutation_scale=26, lw=3.2, color=GREEN,
    shrinkA=15, shrinkB=17, zorder=5, alpha=.9))
dc = BASE["cost_usd"] - OUR["cost_usd"]
dq = OUR["quality_pct"] - BASE["quality_pct"]
ax.annotate(f"−${dc:.2f} cheaper  and  +{dq:.2f}pp quality\nstrictly better than the baseline on both axes",
            ((BASE["cost_usd"] + OUR["cost_usd"]) / 2, (BASE["quality_pct"] + OUR["quality_pct"]) / 2),
            xytext=(34, -34), textcoords="offset points", ha="center",
            fontsize=12.5, fontweight="bold", color=GREEN)

# headline callout on our point
ax.annotate(f"${OUR['cost_usd']:.2f}  ·  {OUR['cost_pct']:.1f}% of today's bill\n"
            f"at {OUR['quality_pct']:.2f}% quality  ·  {OUR['rerouted']}/1000 rerouted",
            (OUR["cost_usd"], OUR["quality_pct"]), xytext=(0, 40), textcoords="offset points",
            ha="center", fontsize=12.5, fontweight="bold", color=V,
            arrowprops=dict(arrowstyle="-", color=V, lw=1.3))

ax.annotate(f"${BASE['cost_usd']:.0f} · {BASE['cost_pct']:.0f}%", (BASE["cost_usd"], BASE["quality_pct"]),
            xytext=(14, 14), textcoords="offset points", fontsize=11, color=GREY, fontweight="bold")
ax.annotate(f"${DEG['cost_usd']:.0f} · {DEG['cost_pct']:.0f}%\ncheaper still, but nothing behind it —\n"
            f"and our CI cannot fully separate us from it",
            (DEG["cost_usd"], DEG["quality_pct"]), xytext=(4, -62), textcoords="offset points",
            ha="left", fontsize=10.5, color=N, arrowprops=dict(arrowstyle="-", color=N, lw=1.1))
ax.annotate(f"always the dearest model → ${TOP['cost_usd']:.0f}  ({TOP['cost_pct']:.0f}% of today)\n"
            f"for +{TOP['quality_pct'] - 100:.1f}% quality",
            (0.985, 0.04), xycoords="axes fraction", ha="right",
            fontsize=11.5, color=PCH, fontweight="bold")

ax.set_xlim(0, 175); ax.set_ylim(97.75, 101.0)
ax.set_xlabel("cost for the whole corpus, USD   (cache-aware · tool + output tokens included · estimated tokens)",
              fontsize=13)
ax.set_ylabel("outcome quality, % of today", fontsize=13)
ax.set_title("Beating the baseline: cheaper and better, not cheaper instead of better",
             fontsize=17, color=N, fontweight="bold", pad=14)
ax.tick_params(labelsize=12); ax.grid(alpha=.2)
ax.legend(fontsize=11, loc="upper left", framealpha=.96, bbox_to_anchor=(0.012, 0.90))
ax.text(.005, -.135,
        "Bars are 95% bootstrap CIs over tasks (400 resamples, seeded). Quality is a constructed "
        "failure-counter signal, not a label; token counts are estimates.",
        transform=ax.transAxes, fontsize=9.5, color="#666")
fig.tight_layout(); fig.savefig(R.parent / "frontier.png", bbox_inches="tight")
print(f"wrote {R.parent / 'frontier.png'}")
