#!/bin/bash
# Prove our tasks with ETH Evals' own free check (oracle must score 1, doing nothing must score 0), on Mac Docker.
# Our tasks keep their ethereus/... names and their own folders (Fraser, 2026-11-03); ETH Evals' lint wants a
# buidlguidl/... name and a parent folder named after the pillar, so this script makes those two changes in a scratch
# proof copy only, never in the task's own folder.
#
#   scripts/prove_task.sh OUT_DIR TASK_DIR [TASK_DIR ...]
#
# Needs an ethevals clone at the eval's commit (ETHEVALS, default ~/Dev/ethevals-mac) and the pinned chain image
# loaded in Docker. Writes OUT_DIR/<pillar>/<task>/ (the proof copies), OUT_DIR/results/ and OUT_DIR/free-check.log.
set -euo pipefail
OUT=$1; shift
ETHEVALS=${ETHEVALS:-$HOME/Dev/ethevals-mac}
[ ! -e "$OUT" ] || { echo "OUT_DIR $OUT exists; free-check needs a fresh folder" >&2; exit 2; }
mkdir -p "$OUT"
copies=()
for t in "$@"; do
  t=$(cd "$t" && pwd)
  pillar=$(python3 -c "import re,sys; s=open(sys.argv[1]).read(); print(re.search(r'^pillar\s*=\s*\"([a-z]+)\"', s, re.M).group(1))" "$t/task.toml")
  dst="$OUT/$pillar/$(basename "$t")"
  mkdir -p "$OUT/$pillar"; cp -R "$t" "$dst"
  sed -i '' -E 's|^name = "ethereus/|name = "buidlguidl/|' "$dst/task.toml"   # proof copy only
  echo "$(basename "$t"): proof copy at $dst ($(grep -m1 '^name' "$dst/task.toml"), pillar $pillar)"
  copies+=("$dst")
done
cd "$ETHEVALS"
echo "ethevals $(git rev-parse --short HEAD), image $(docker image inspect ghcr.io/buidlguidl/ethevals-chain:inputs-d0ef1955a98ec678 --format '{{.Os}}/{{.Architecture}}')"
env -u OPENROUTER_API_KEY -u ANTHROPIC_API_KEY -u OPENAI_API_KEY -u ANTHROPIC_AUTH_TOKEN -u EXA_API_KEY \
  uv run ethevals free-check --tasks "${copies[@]}" --output "$OUT/results" 2>&1 | grep -v LiteLLM | tee "$OUT/free-check.log"
