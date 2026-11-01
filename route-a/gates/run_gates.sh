#!/bin/bash
# Gates 2, 1, then 4 and 3, on the pinned venv. Never stops early: every gate's result is reported.
W=/workspace; V=$W/venv/bin/python; D=$W/ethereus/dist/q9-rv2; M=$W/models/Qwen3.5-9B
FW=$W/ethereus/experimental/riscv/tests/data/rv/eth-bridge.elf
cd $W; export PYTHONPATH=ethereus/experimental/riscv:ethereus
$V $W/ethereus/route-a/gates/env_pin.py $D/env-pin.json
timeout 3600 $V -u -m chipformer.rv.gpucheck --dtype bf16 --decode > $D/gpucheck.log 2>&1; echo "GATE2_EXIT=$?"
grep -m1 kernels $D/gpucheck.log; echo "exact=$(grep -c ' exact on ' $D/gpucheck.log) bad=$(grep -cE 'mismatch|not exact|Traceback' $D/gpucheck.log) positions=$(grep ' exact on ' $D/gpucheck.log | awk '{s+=$5} END{print s}')"
timeout 3600 $V -m chipformer.rv.textcheck $M $D --out $D/rv-text.json > $D/textcheck.log 2>&1; echo "GATE1_EXIT=$?"
$V - <<PY
import json; d=json.load(open("$D/rv-text.json"))
print("kl", d["kl_merged_mean"], "floor", d["kl_noise_floor_mean"], "ratio", d["kl_merged_mean"]/d["kl_noise_floor_mean"], "greedy", sum(d["greedy_identical"]), "/", len(d["greedy_identical"]))
PY
timeout 7200 $V -u $W/ethereus/route-a/gates/gate4.py $D $FW $D > $D/gate4.log 2>&1; echo "GATE4_EXIT=$?"
grep -vE "^\[transformers\]|Warning|warn_once|Loading weights" $D/gate4.log | cut -c1-260
echo GATES_DONE
