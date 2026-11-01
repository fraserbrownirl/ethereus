#!/bin/bash
# Internet, no-key tasks, default attempts: base, then circuit. One server at a time. Resumable by output dir.
W=/workspace; G=$W/ethereus/route-a/gates
cd $W/ethevals
mapfile -t TASKS < <(uv run --frozen python - <<'PY'
import glob, os, tomllib
for f in sorted(glob.glob("tasks/*/*/task.toml")):
    t = tomllib.load(open(f, "rb"))
    if "ANTHROPIC_API_KEY" not in (t.get("verifier", {}).get("env") or {}):
        print(os.path.dirname(f))
PY
)
echo "tasks ${#TASKS[@]}"
for ARM in ${ARMS:-base rv2}; do
  echo "=== $(date -u) $ARM start"
  bash $G/serve_arm.sh $ARM
  bash $G/run_trials.sh $ARM internet-nokey-$ARM 2 "${TASKS[@]}"
  echo "=== $(date -u) $ARM done"
done
echo EVAL_DONE
