#!/usr/bin/env python3
"""The one chart: cost vs outcome quality on the HELD-OUT chunk, 95% bootstrap CIs.
Quality is % of what Viktor gets today, so the dashed line is parity."""
import json
from pathlib import Path
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

R=Path(__file__).resolve().parent/"results"
d=json.loads((R/"scorecard_both.json").read_text())["chunk02_heldout"]
LOG,BASE,OUR,DEG,TOP=d["logged"],d["baseline"],d["router"],d["allcheap"],d["dearest"]
V,N,PCH,GREY=" #5B3BE8".strip(),"#12132B","#E8845A","#8E8AA8"
fig,ax=plt.subplots(figsize=(12.5,7),dpi=150)
ax.axhline(100,ls="--",lw=1.4,color=GREY,zorder=1)
ax.text(3,100.55,"parity with what Viktor runs today",fontsize=11.5,color=GREY,va="bottom")
def pt(r,col,mk,ms,lab):
    ax.errorbar([r["cost"]],[r["q"]],
        yerr=[[r["q"]-r["ci"][0]],[r["ci"][1]-r["q"]]],fmt=mk,color=col,ms=ms,lw=0,
        capsize=5,elinewidth=1.7,ecolor=col,zorder=6,label=lab)
pt(LOG,N,"o",11,"logged policy — what Viktor runs today")
pt(BASE,GREY,"s",12,"starter kit baseline_router")
pt(OUR,V,"o",15,"our router")
pt(DEG,PCH,"X",14,"route everything cheap — no evidence")
ax.annotate(f"${OUR['cost']:.2f} · {OUR['cost_pct']}% of the bill\nat {OUR['q']}% quality · {OUR['rerouted']}/1000 rerouted",
    (OUR["cost"],OUR["q"]),xytext=(0,-62),textcoords="offset points",ha="center",
    fontsize=13,fontweight="bold",color=V,arrowprops=dict(arrowstyle="-",color=V,lw=1.4))
ax.annotate(f"baseline ${BASE['cost']:.0f} · {BASE['cost_pct']}%\nquality {BASE['q']}% — better than us by 0.53pp",
    (BASE["cost"],BASE["q"]),xytext=(-8,30),textcoords="offset points",ha="right",
    fontsize=11,color=GREY,fontweight="bold")
ax.annotate(f"${DEG['cost']:.0f} · {DEG['cost_pct']}% · quality {DEG['q']}%\nscores ABOVE us — we think that is\nselection bias, and we can't disprove it",
    (DEG["cost"],DEG["q"]),xytext=(-4,30),textcoords="offset points",ha="center",
    fontsize=10.5,color=N,arrowprops=dict(arrowstyle="-",color=N,lw=1.1))
ax.annotate(f"always the dearest model → ${TOP['cost']:.0f}  ({TOP['cost_pct']}% of the bill)\nfor +{TOP['q']-100:.1f}% quality",
    (0.985,0.945),xycoords="axes fraction",ha="right",va="top",fontsize=11.5,color=PCH,fontweight="bold")
ax.set_xlim(0,165); ax.set_ylim(97.9,101.5)
ax.set_xlabel("cost for the whole held-out corpus, USD   (cache-aware · tool + output tokens · estimated tokens)",fontsize=13)
ax.set_ylabel("outcome quality, % of today",fontsize=13)
ax.set_title("Held-out chunk: a large cost cut, and no quality win we can prove",
             fontsize=17,color=N,fontweight="bold",pad=14)
ax.tick_params(labelsize=12); ax.grid(alpha=.2)
ax.legend(fontsize=10.5,loc="lower left",framealpha=.96,bbox_to_anchor=(0.005,0.005))
ax.text(.005,-.135,"95% bootstrap CIs over tasks (400 resamples, seeded). Quality is a constructed failure-counter "
        "signal, not a label; token counts are estimates.",transform=ax.transAxes,fontsize=9.5,color="#666")
fig.tight_layout(); fig.savefig(R.parent/"frontier.png",bbox_inches="tight")
print("wrote",R.parent/"frontier.png")
