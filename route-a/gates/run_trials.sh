#!/bin/bash
# run_trials.sh <arm: base|rv2> <out-name> <attempts> [task dirs...]. Output under /workspace/results.
W=/workspace; ARM=$1; OUT=$2; N=$3; shift 3
cd $W/ethevals
export ETHEREUS_API_KEY=$(< $W/serve.token)
[ -f /root/.harness/anthropic.key ] && export ANTHROPIC_API_KEY=$(< /root/.harness/anthropic.key)
AG=opencode-q9-base; [ "$ARM" = rv2 ] && AG=opencode-q9-rv2
TASKS=(); [ $# -gt 0 ] && TASKS=(--tasks "$@")
uv run --frozen ethevals run --config $W/ethereus/route-a/gates/job-ethereus.yaml --agents $AG --modes internet --n-attempts $N --budget ${BUDGET:-0} --output $W/results/$OUT "${TASKS[@]}"
echo "RUN_EXIT=$? $OUT"
