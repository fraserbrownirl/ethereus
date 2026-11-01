#!/bin/bash
# Start serve.py for one arm on :8080, the only model server on the box: serve_arm.sh base|rv2
set -euo pipefail
W=/workspace; ARM=$1
pkill -f "chipformer.rv.serve:from_env" 2>/dev/null || true; sleep 3
umask 077; [ -f $W/serve.token ] || head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n' > $W/serve.token
export ETHEREUS_KEYS=$(< $W/serve.token) ETHEREUS_BUFFERED_STREAM=1 ETHEREUS_SYNC_SECONDS=36000 ETHEREUS_RATE_MINUTE=100000 ETHEREUS_RATE_DAY=100000000
export PYTORCH_ALLOC_CONF=expandable_segments:True
export ETHEREUS_DATA=$W/serve-data/$ARM CORE_COMMIT=$(git -C $W/ethereus rev-parse --short HEAD)
export FIRMWARE=$W/ethereus/experimental/riscv/tests/data/rv/eth-bridge.elf
if [ "$ARM" = base ]; then export ETHEREUS_BASE=1 MODEL=$W/models/Qwen3.5-9B MODEL_ID=q9-base
else unset ETHEREUS_BASE; export MODEL=$W/ethereus/dist/q9-rv2 MODEL_ID=q9-rv2; fi
cd $W/ethereus/experimental/riscv
PYTHONPATH=.:../.. setsid nohup $W/venv/bin/python -m uvicorn --factory chipformer.rv.serve:from_env --host 0.0.0.0 --port 8080 --workers 1 --timeout-keep-alive 3600 > $W/serve-$ARM.log 2>&1 < /dev/null &
for i in $(seq 1 60); do curl -fs --max-time 2 localhost:8080/health >/dev/null && { echo "serve $ARM up after $((i*5))s"; exit 0; }; sleep 5; done
echo "serve $ARM did not come up"; tail -20 $W/serve-$ARM.log; exit 1
