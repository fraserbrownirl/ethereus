#!/bin/bash
# Start serve.py for one arm on :8080, the only model server on the box: serve_arm.sh base|rv2
set -euo pipefail
W=/workspace; ARM=$1
pkill -f "chipformer.rv.serve:from_env" 2>/dev/null || true; sleep 3
if [ "$ARM" = rv2 ] && [ ! -f $W/gate4-keep1.ok ]; then  # old arms only
  # logits_to_keep=1 changed the driver: gate 4 must reproduce the pinned run's token counts first.
  bash $W/ethereus/route-a/gates/gate4_recheck.sh || { echo "gate 4 recheck failed, not starting rv2"; exit 1; }
fi
umask 077; [ -f $W/serve.token ] || head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n' > $W/serve.token
export ETHEREUS_KEYS=$(< $W/serve.token) ETHEREUS_BUFFERED_STREAM=1 ETHEREUS_SYNC_SECONDS=36000 ETHEREUS_RATE_MINUTE=100000 ETHEREUS_RATE_DAY=100000000
export PYTORCH_ALLOC_CONF=expandable_segments:True
export ETHEREUS_DATA=$W/serve-data/$ARM CORE_COMMIT=$(git -C $W/ethereus rev-parse --short HEAD)
export FIRMWARE=$W/ethereus/experimental/riscv/tests/data/rv/eth-bridge.elf
CODE=$W/ethereus/experimental/riscv
# Step 3 arms (Fraser, 2026-11-02): every arm on main's code and firmware (spill on, ELF 7c4175e6).
LINE="Files under es/ hold the ethskills reference; before answering, list them and get whatever is relevant."  # the training data's system line
MAIN=$W/ethereus-main/experimental/riscv
case "$ARM" in
  stock|untrained|untrained-line|v01|v01-skills)
    CODE=$MAIN; export FIRMWARE=$MAIN/tests/data/rv/eth-bridge.elf CORE_COMMIT=$(git -C $W/ethereus-main rev-parse --short HEAD)
    unset ETHEREUS_BASE SNAPSHOT ETHEREUS_SYSTEM_LINE ;;
esac
case "$ARM" in
  stock) export ETHEREUS_BASE=1 MODEL=$W/models/Qwen3.5-9B MODEL_ID=q9-base ;;
  untrained) export MODEL=$W/dist-store/q9-store-rv MODEL_ID=q9-rv2 SNAPSHOT=$MAIN/build/ethskills.fs.npz ;;
  untrained-line) export MODEL=$W/dist-store/q9-store-rv MODEL_ID=q9-rv2 SNAPSHOT=$MAIN/build/ethskills.fs.npz \
    ETHEREUS_SYSTEM_LINE="You have exact tools eth (keccak, ABI, uint256, EVM) and files (Ethereum reference); use them for any value they compute, then continue the task with your other tools." ;;
  v01) export MODEL=$W/dist-store/q9-store-rv-ft1b MODEL_ID=q9-rv2 ETHEREUS_SYSTEM_LINE="$LINE" ;;
  v01-skills) export MODEL=$W/dist-store/q9-store-rv-ft1b MODEL_ID=q9-rv2 ETHEREUS_SYSTEM_LINE="$LINE" SNAPSHOT=$MAIN/build/ethskills.fs.npz ;;
esac
if [[ "$ARM" =~ ^(stock|untrained|untrained-line|v01|v01-skills)$ ]]; then :
elif [ "$ARM" = base ]; then export ETHEREUS_BASE=1 MODEL=$W/models/Qwen3.5-9B MODEL_ID=q9-base
elif [ "$ARM" = store ]; then  # Internet+Skills: the store checkpoint, its files, one line about es/, on main's code
  unset ETHEREUS_BASE; CODE=$W/ethereus-main/experimental/riscv; export MODEL=$W/dist-store/q9-store-rv MODEL_ID=q9-rv2
  export FIRMWARE=$CODE/tests/data/rv/eth-bridge.elf SNAPSHOT=$CODE/build/ethskills.fs.npz
  export ETHEREUS_SYSTEM_LINE="$($W/venv/bin/python -c "import json;print(json.load(open('$CODE/scripts/smoke_questions.json'))['system_line'])")"
  export CORE_COMMIT=$(git -C $W/ethereus-main rev-parse --short HEAD)
else unset ETHEREUS_BASE; export MODEL=$W/ethereus/dist/q9-rv2 MODEL_ID=q9-rv2; fi
cd $CODE
PYTHONPATH=.:../.. setsid nohup $W/venv/bin/python -m uvicorn --factory chipformer.rv.serve:from_env --host 0.0.0.0 --port 8080 --workers 1 --timeout-keep-alive 3600 > $W/serve-$ARM.log 2>&1 < /dev/null &
for i in $(seq 1 60); do curl -fs --max-time 2 localhost:8080/health >/dev/null && { echo "serve $ARM up after $((i*5))s"; exit 0; }; sleep 5; done
echo "serve $ARM did not come up"; tail -20 $W/serve-$ARM.log; exit 1
