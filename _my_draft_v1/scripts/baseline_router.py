#!/usr/bin/env python3
"""Baseline router + cache-aware cost/quality frontier (idea/01 + idea/04).

Usage:
    python scripts/baseline_router.py export/ [options]

What this does, matching "Starter ideas" in the challenge deck:
  - idea/01 "Heuristic router" (the safe bet): route by prompt length and
    call position, swept over an aggressiveness parameter alpha to trace a
    frontier instead of a single point.
  - idea/04 "Cache-aware costing": a call is only priced at the cheap
    "cached" rate for the prefix it shares with the *previous* call in the
    same trajectory, and only if that previous call used the same model.
    Any model switch pays full price for the whole input again.

Honesty notice: the dataset ships no output tokens, no usage, and no
quality label (see "The signal" slide). This script:
  - estimates output tokens with a flat constant (--output-tokens, default
    150) -- there is no signal to do better offline.
  - uses a PLACEHOLDER quality proxy: routed_tier_rank / max_tier_rank,
    i.e. it assumes quality scales with tier. That assumption is exactly
    the "open off-policy problem" the deck asks you to improve on
    (idea/02 learned router, idea/03 honest evaluation). Replace
    `quality_proxy()` with a real signal (judge-model rescoring,
    cross-trajectory matching, etc.) for anything beyond a cost floor.

Outputs (written into export_dir):
  - baseline_frontier.json  raw (alpha, avg_cost, avg_quality) points +
                             reference points (logged / random / always-top)
  - baseline_report.md      human-readable table
  - baseline_frontier.png   chart, if matplotlib is installed (optional)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

DEFAULT_OUTPUT_TOKENS = 150
LENGTH_CAP_TOKENS = 4000  # input length beyond which the heuristic treats a call as "long"


def load_trajectories(export_dir: Path) -> list[dict]:
    traj_path = export_dir / "trajectories.json"
    if not traj_path.exists():
        print(f"{traj_path} not found -- running load_trajectories.py first.")
        subprocess.run([sys.executable, str(Path(__file__).parent / "load_trajectories.py"), str(export_dir)], check=True)
    return json.loads(traj_path.read_text())


def build_tiers(trajectories: list[dict], config_path: Path) -> dict:
    tiers = common.load_model_tiers(config_path)
    seen_models = set()
    for traj in trajectories:
        for call in traj["calls"]:
            seen_models.add(call["model"])
    added = []
    for model_id in sorted(seen_models):
        if model_id not in tiers:
            added.append(model_id)
        common.ensure_model_in_tiers(tiers, model_id)
    common.save_model_tiers(config_path, tiers)
    if added:
        print(f"Added {len(added)} model id(s) to {config_path} with inferred (placeholder) "
              f"pricing -- edit that file with real numbers when you have them: {', '.join(added)}")
    return tiers


def cost_of_routing(calls: list[dict], model_ids: list[str], tiers: dict, output_tokens: int) -> float:
    total = 0.0
    prev_model = None
    for call, model_id in zip(calls, model_ids):
        price = tiers[model_id]
        if prev_model is None or model_id != prev_model:
            input_cost = call["input_tokens"] * price["input_price_per_1k"] / 1000
        else:
            input_cost = (
                call["cached_prefix_tokens"] * price["cached_input_price_per_1k"]
                + call["new_tokens"] * price["input_price_per_1k"]
            ) / 1000
        output_cost = output_tokens * price["output_price_per_1k"] / 1000
        total += input_cost + output_cost
        prev_model = model_id
    return total


def quality_proxy(model_ids: list[str], tiers: dict, max_rank: int) -> float:
    if not model_ids or max_rank == 0:
        return 0.0
    return sum(tiers[m]["tier_rank"] / max_rank for m in model_ids) / len(model_ids)


def heuristic_route(calls: list[dict], tiers_sorted: list[str], alpha: float) -> list[str]:
    """idea/01: route by call position and prompt length, at aggressiveness alpha in [0,1].

    Cache-aware by construction (idea/04): the tier only ever moves up
    within a trajectory, and only when the position/length score actually
    demands it. A router that re-evaluates the "right" tier independently
    per call ends up switching models on almost every call as the score
    drifts, which resets the provider's prompt cache constantly -- that
    switch-cost trap is exactly what "The twist" slide warns about. Never
    downgrading, and only switching when strictly required, keeps the
    model (and therefore the cache) stable across calls whenever the
    heuristic doesn't have a reason to escalate.
    """
    t = len(tiers_sorted)
    n = len(calls)
    escalation = 0.25 + 1.75 * alpha  # alpha=0 -> stays cheap; alpha=1 -> escalates fast
    routed = []
    current_tier_idx = -1
    for call in calls:
        pos_frac = (call["call_index"] - 1) / max(1, n - 1)
        len_frac = min(1.0, call["input_tokens"] / LENGTH_CAP_TOKENS)
        score = 0.5 * pos_frac + 0.5 * len_frac
        effective = min(1.0, score * escalation)
        target_tier_idx = round(effective * (t - 1))
        current_tier_idx = max(current_tier_idx, target_tier_idx)
        routed.append(tiers_sorted[current_tier_idx])
    return routed


def expected_random_cost(calls: list[dict], tiers: dict, output_tokens: int) -> float:
    """Closed-form expected cost of picking a model uniformly at random per call."""
    models = list(tiers.keys())
    T = len(models)
    total = 0.0
    for call in calls:
        avg_uncached_full = sum(
            call["input_tokens"] * tiers[m]["input_price_per_1k"] / 1000
            + output_tokens * tiers[m]["output_price_per_1k"] / 1000
            for m in models
        ) / T
        if call["call_index"] == 1:
            total += avg_uncached_full
        else:
            avg_cached_partial = sum(
                (
                    call["cached_prefix_tokens"] * tiers[m]["cached_input_price_per_1k"]
                    + call["new_tokens"] * tiers[m]["input_price_per_1k"]
                ) / 1000
                + output_tokens * tiers[m]["output_price_per_1k"] / 1000
                for m in models
            ) / T
            total += (1 / T) * avg_cached_partial + (1 - 1 / T) * avg_uncached_full
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("export_dir", type=Path)
    parser.add_argument("--config", type=Path, default=None, help="model pricing config (default: <repo>/config/model_tiers.json)")
    parser.add_argument("--output-tokens", type=int, default=DEFAULT_OUTPUT_TOKENS, help="flat output-token estimate per call (no usage data ships)")
    parser.add_argument("--alpha-steps", type=int, default=11, help="number of aggressiveness points to sweep for the router frontier")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    config_path = args.config or (repo_root / "config" / "model_tiers.json")

    trajectories = load_trajectories(args.export_dir)
    if not trajectories:
        print("No trajectories to evaluate.", file=sys.stderr)
        sys.exit(1)

    tiers = build_tiers(trajectories, config_path)
    tiers_sorted = sorted(tiers.keys(), key=lambda m: tiers[m]["tier_rank"])
    max_rank = max(t["tier_rank"] for t in tiers.values())
    top_model = tiers_sorted[-1]

    n_traj = len(trajectories)

    logged_cost = quality_num = quality_den = 0.0
    random_cost = 0.0
    always_top_cost = 0.0
    always_top_calls = 0

    for traj in trajectories:
        calls = traj["calls"]
        logged_models = [traj["logged_model"]] * len(calls)
        for m in logged_models:
            common.ensure_model_in_tiers(tiers, m)
        logged_cost += cost_of_routing(calls, logged_models, tiers, args.output_tokens)
        random_cost += expected_random_cost(calls, tiers, args.output_tokens)
        always_top_cost += cost_of_routing(calls, [top_model] * len(calls), tiers, args.output_tokens)
        always_top_calls += len(calls)

    logged_quality = quality_proxy(
        [c for traj in trajectories for c in [traj["logged_model"]] * len(traj["calls"])], tiers, max_rank
    )

    frontier = []
    for i in range(args.alpha_steps):
        alpha = i / (args.alpha_steps - 1) if args.alpha_steps > 1 else 0.0
        total_cost = 0.0
        quality_sum = 0.0
        quality_count = 0
        for traj in trajectories:
            calls = traj["calls"]
            routed = heuristic_route(calls, tiers_sorted, alpha)
            total_cost += cost_of_routing(calls, routed, tiers, args.output_tokens)
            for m in routed:
                quality_sum += tiers[m]["tier_rank"] / max_rank
                quality_count += 1
        frontier.append({
            "alpha": round(alpha, 3),
            "avg_cost_per_trajectory": total_cost / n_traj,
            "avg_quality_proxy": quality_sum / quality_count,
        })

    references = {
        "logged_as_ran": {
            "avg_cost_per_trajectory": logged_cost / n_traj,
            "avg_quality_proxy": logged_quality,
            "note": "the model that actually served each trajectory in the log",
        },
        "random_routing": {
            "avg_cost_per_trajectory": random_cost / n_traj,
            "avg_quality_proxy": (max_rank / 2) / max_rank if max_rank else 0.0,
            "note": "closed-form expected cost of a uniform-random model per call",
        },
        "always_top_tier": {
            "avg_cost_per_trajectory": always_top_cost / n_traj,
            "avg_quality_proxy": 1.0,
            "note": f"always route to {top_model} (tier_rank={tiers[top_model]['tier_rank']}) -- no cache resets, ceiling cost",
        },
    }

    result = {
        "num_trajectories": n_traj,
        "output_tokens_estimate": args.output_tokens,
        "models_by_tier": tiers_sorted,
        "frontier": frontier,
        "references": references,
    }

    out_json = args.export_dir / "baseline_frontier.json"
    out_json.write_text(json.dumps(result, indent=2))

    out_md = args.export_dir / "baseline_report.md"
    lines = [
        "# Baseline router report",
        "",
        f"Trajectories evaluated: {n_traj}",
        f"Models (cheapest -> most expensive, by inferred tier): {', '.join(tiers_sorted)}",
        f"Output-token estimate per call: {args.output_tokens} (flat -- no usage data ships)",
        "",
        "Quality proxy is a PLACEHOLDER (routed tier rank / max tier rank). It assumes",
        "quality scales with tier, which is exactly the assumption the challenge wants you",
        "to replace with a real signal (judge-model rescoring, matched/weighted trajectory",
        "comparison across models, etc.) -- see 'The signal' and 'Evaluation' slides.",
        "",
        "## Reference points",
        "",
        "| policy | avg cost / trajectory | quality proxy | note |",
        "|---|---|---|---|",
    ]
    for name, ref in references.items():
        lines.append(f"| {name} | {ref['avg_cost_per_trajectory']:.4f} | {ref['avg_quality_proxy']:.3f} | {ref['note']} |")
    lines += [
        "",
        "## Heuristic router frontier (idea/01, swept over aggressiveness alpha)",
        "",
        "| alpha | avg cost / trajectory | quality proxy |",
        "|---|---|---|",
    ]
    for pt in frontier:
        lines.append(f"| {pt['alpha']:.2f} | {pt['avg_cost_per_trajectory']:.4f} | {pt['avg_quality_proxy']:.3f} |")
    out_md.write_text("\n".join(lines) + "\n")

    print(f"Evaluated {n_traj} trajectories against {len(tiers_sorted)} models.")
    print(f"  logged (as-ran):   cost={references['logged_as_ran']['avg_cost_per_trajectory']:.4f}  quality~{logged_quality:.3f}")
    print(f"  random routing:    cost={references['random_routing']['avg_cost_per_trajectory']:.4f}")
    print(f"  always top tier:   cost={references['always_top_tier']['avg_cost_per_trajectory']:.4f}  quality~1.000")
    print(f"  heuristic router:  alpha=0.0 cost={frontier[0]['avg_cost_per_trajectory']:.4f} "
          f"-> alpha=1.0 cost={frontier[-1]['avg_cost_per_trajectory']:.4f}")
    print(f"Wrote {out_json}")
    print(f"Wrote {out_md}")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(7, 5))
        xs = [p["avg_cost_per_trajectory"] for p in frontier]
        ys = [p["avg_quality_proxy"] for p in frontier]
        ax.plot(xs, ys, marker="o", color="#6748FD", label="heuristic router (idea/01)")

        rx, ry = references["random_routing"]["avg_cost_per_trajectory"], references["random_routing"]["avg_quality_proxy"]
        ax.plot([0, rx * 2], [0, ry * 2], linestyle="--", color="gray", label="random routing (reference)")

        ax.scatter(*[references["logged_as_ran"][k] for k in ("avg_cost_per_trajectory", "avg_quality_proxy")],
                    color="black", zorder=5, label="logged / as-ran")
        ax.scatter(*[references["always_top_tier"][k] for k in ("avg_cost_per_trajectory", "avg_quality_proxy")],
                    color="#FF7A59", zorder=5, label="always top tier")

        ax.set_xlabel("avg cost per task (relative units)")
        ax.set_ylabel("quality proxy (placeholder)")
        ax.set_title("Baseline cost-quality frontier")
        ax.legend(loc="lower right", fontsize=8)
        fig.tight_layout()
        out_png = args.export_dir / "baseline_frontier.png"
        fig.savefig(out_png, dpi=150)
        print(f"Wrote {out_png}")
    except ImportError:
        print("matplotlib not installed -- skipping baseline_frontier.png "
              "(baseline_frontier.json/.md still written). `pip install matplotlib` to get the chart.")


if __name__ == "__main__":
    main()
