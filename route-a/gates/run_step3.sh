#!/bin/bash
# run_step3.sh <agent key> <mode> <output> [task dirs...]: one Step 3 selection with job-step3.yaml (board parity).
W=/workspace; AG=$1; MODE=$2; OUT=$3; shift 3
cd $W/ethevals
export ETHEREUS_API_KEY=$(< $W/serve.token)
[ -f /root/.harness/anthropic.key ] && export ANTHROPIC_API_KEY=$(< /root/.harness/anthropic.key)
[ -f /root/.harness/mainnet_rpc ] && export MAINNET_RPC_URL=$(< /root/.harness/mainnet_rpc)
[ -s /root/.exa_key ] && export EXA_API_KEY=$(tr -d ' \n' < /root/.exa_key)
TASKS=(); [ $# -gt 0 ] && TASKS=(--tasks "$@")
uv run --frozen ethevals run --config $W/ethereus/route-a/gates/job-step3.yaml --agents $AG --modes $MODE --n-attempts ${N:-2} --budget ${BUDGET:-600} --output $W/results/$OUT "${TASKS[@]}"
echo "RUN_EXIT=$? $OUT"
