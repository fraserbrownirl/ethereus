#!/bin/bash
# run_trials.sh <arm: base|rv2> <out-name> <attempts> [task dirs...]. Output under /workspace/results.
W=/workspace; ARM=$1; OUT=$2; N=$3; shift 3
cd $W/ethevals
export ETHEREUS_API_KEY=$(< $W/serve.token)
[ -f /root/.harness/anthropic.key ] && export ANTHROPIC_API_KEY=$(< /root/.harness/anthropic.key)
[ -f /root/.harness/mainnet_rpc ] && export MAINNET_RPC_URL=$(< /root/.harness/mainnet_rpc)
AG=opencode-q9-rv2; [[ "$ARM" =~ ^(base|stock)$ ]] && AG=opencode-q9-base
TASKS=(); [ $# -gt 0 ] && TASKS=(--tasks "$@")
uv run --frozen ethevals run --config $W/ethereus/route-a/gates/job-ethereus.yaml --agents $AG --modes internet --n-attempts $N --budget ${BUDGET:-0} --output $W/results/$OUT "${TASKS[@]}"
echo "RUN_EXIT=$? $OUT"
