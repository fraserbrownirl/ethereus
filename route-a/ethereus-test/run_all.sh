#!/bin/bash
# run_all.sh <tag>: all three arms in sequence, low priority so it does not slow an eval.
W=/workspace; R=$W/ethereus/route-a/ethereus-test; O=$W/results/ethereus-test/$1; mkdir -p $O
FW=$W/ethereus/experimental/riscv/tests/data/rv/eth-bridge.elf; HOST=$W/ethereus/experimental/riscv/tests/data/rv/eth.elf
cd $W; export PYTHONPATH=ethereus/experimental/riscv:ethereus; sha256sum $FW
for a in base rv2 ceiling; do
  M=$W/models/Qwen3.5-9B; [ $a = rv2 ] && M=$W/ethereus/dist/q9-rv2
  nice -n 10 $W/venv/bin/python -u $R/run.py $a $M $FW $O/$a.jsonl $HOST 2>&1 | grep -aE "tool |ARM_DONE|Traceback|Error"
done
$W/venv/bin/python $R/summarize.py $O/*.jsonl | tee $O/summary.txt
echo TEST_DONE
