#!/usr/bin/env bash
# Reproduce every number in evaluation/EVALUATION.md, offline, ~4 min.
#   ./evaluation/run.sh export/
# Needs the challenge dataset extracted to export/ (never committed - licence).
set -euo pipefail
EXPORT="${1:-export}"
HERE="$(cd "$(dirname "$0")" && pwd)"; cd "$HERE/.."
OUT="$HERE/results"; mkdir -p "$OUT"
[ -d "$EXPORT" ] || { echo "no $EXPORT/ - extract the dataset there first"; exit 1; }

echo "== 1/5 episodes"      && python3 "$HERE/lib/episodes.py"   "$EXPORT" --out "$OUT/episodes.jsonl"
echo "== 2/5 outcome signal" && python3 "$HERE/lib/quality.py"    "$OUT/episodes.jsonl" --out "$OUT/quality.jsonl" \
  && python3 "$HERE/lib/quality_v2.py" "$OUT/episodes.jsonl" --export "$EXPORT" --out "$OUT/quality_v2.jsonl"
echo "== 3/5 output-token proxy" && python3 "$HERE/lib/gen_tokens.py" "$EXPORT" --out "$OUT/gen_tokens.jsonl"
echo "== 4/5 cost model"    && python3 "$HERE/lib/cost2.py" "$OUT/episodes.jsonl" --gen "$OUT/gen_tokens.jsonl" --out "$OUT/costs_summary.json"
echo "== 5/5 scoring rule_based_router" && python3 "$HERE/score_router.py" "$EXPORT"
echo && echo "done - see $OUT/scorecard.json"
