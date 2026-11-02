#!/bin/bash
# Fine-tune spec section 5 on q9-store-rv-ft1, main's code, pinned venv. Never stops early: every result is reported.
# LORA: lora.safetensors (default) or lora-epoch1.safetensors, the spec's fallback if KL or the red flags fail.
W=/workspace; V=$W/venv/bin/python; B=$W/dist-store/q9-store-rv; D=${DST:-$W/dist-store/q9-store-rv-ft1}; M=$W/models/Qwen3.5-9B; R=$W/ethereus-main/experimental/riscv
L=${LORA_PATH:-$W/dist/ft1-lora/${LORA:-lora.safetensors}}; SNAP=build/ethskills.fs.npz
# Fine-tune gate (Fraser, 2026-11-02): KL against stock <= 0.05 on generic prompts, and MMLU-500 within 1 point of stock.
cd $R; export PYTHONPATH=.:../..; FW=$R/tests/data/rv/eth-bridge.elf; sha256sum $FW $L | cut -c1-90
git -C $W/ethereus-main log --format='main %h' -1
$V $W/ethereus/route-a/gates/env_pin.py $W/ft1-env-pin.json
$V scripts/train_ft.py apply --merged $B --lora $L --dst $D; echo "FT_APPLY_EXIT=$?"
timeout 3600 $V -m chipformer.rv.textcheck $M $D --out $D/rv-text.json > $D/textcheck.log 2>&1; echo "FT_GATE1_EXIT=$?"
$V -c "import json;d=json.load(open('$D/rv-text.json'));print('FT_GATE1 kl',d['kl_merged_mean'],'floor',d['kl_noise_floor_mean'],'ratio',d['kl_merged_mean']/d['kl_noise_floor_mean'],'greedy',sum(d['greedy_identical']),'/',len(d['greedy_identical']),'cap 0.0012')"
$V -c "import json;d=json.load(open('$D/rv-text.json'));k=d['kl_merged_mean'];print('FT_KL_GATE','PASS' if k<=0.05 else 'FAIL',k,'cap 0.05')"
[ -f $W/mmlu-stock.json ] || timeout 3600 $V -u scripts/eval_mmlu.py $M --out $W/mmlu-stock.json > $W/mmlu-stock.log 2>&1
timeout 3600 $V -u scripts/eval_mmlu.py $D --out $D/mmlu.json > $D/mmlu.log 2>&1; echo "FT_MMLU_EXIT=$?"
$V -c "import json;s=json.load(open('$W/mmlu-stock.json'))['accuracy'];t=json.load(open('$D/mmlu.json'))['accuracy'];print('FT_MMLU_GATE','PASS' if t>=s-1 else 'FAIL','stock',s,'tuned',t)"
timeout 3600 $V -u -m chipformer.rv.gpucheck --dtype bf16 --decode > $D/gpucheck.log 2>&1; echo "FT_GATE2_EXIT=$?"
echo "FT_GATE2 exact=$(grep -c ' exact on ' $D/gpucheck.log) bad=$(grep -cE 'mismatch|not exact|Traceback' $D/gpucheck.log)"
timeout 7200 $V -u $W/ethereus/route-a/gates/gate4.py $D $FW $D > $D/gate4.log 2>&1; echo "FT_GATE4_EXIT=$?"
grep -aE '^\{|^GATE4|Traceback' $D/gate4.log | cut -c1-240
timeout 21600 $V -u scripts/smoke_knowledge.py --model $D --fs $SNAP --out $D/smoke.json > $D/smoke.log 2>&1; echo "FT_SMOKE_EXIT=$?"; grep -a TOTAL $D/smoke.log
timeout 14400 $V -u scripts/eval_ft.py --model $D --firmware $FW --fs $SNAP --out $D/eval > $D/eval.log 2>&1; echo "FT_EVAL_EXIT=$?"; grep -a '^EVAL_FT' $D/eval.log | cut -c1-500
timeout 14400 $V -u scripts/eval_ft.py --model $B --firmware $FW --fs $SNAP --out $B/eval > $B/eval.log 2>&1; echo "BASE_EVAL_EXIT=$?"; grep -a '^EVAL_FT' $B/eval.log | cut -c1-500
O=$W/results/ethereus-test/ft1; mkdir -p $O
timeout 14400 $V -u $W/ethereus/route-a/ethereus-test/run.py rv2 $D $FW $O/rv2.jsonl $R/tests/data/rv/eth.elf 2>&1 | grep -aE "tool |ARM_DONE|Traceback|Error"
cp $W/results/ethereus-test/after/base.jsonl $W/results/ethereus-test/after/ceiling.jsonl $O/
$V $W/ethereus/route-a/ethereus-test/summarize.py $O/*.jsonl | tee $O/summary.txt
echo FT_GATES_DONE
