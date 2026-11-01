#!/bin/bash
# The store checkpoint on main's code and the pinned venv: gates 2, 1, 4 and 3, snapshot verify, smoke.
W=/workspace; V=$W/venv/bin/python; D=$W/dist-store/q9-store-rv; M=$W/models/Qwen3.5-9B; R=$W/ethereus-main/experimental/riscv
cd $R; export PYTHONPATH=.:../..; FW=$R/tests/data/rv/eth-bridge.elf; sha256sum $FW
git -C $W/ethereus-main log --format='main %h' -1
timeout 3600 nice -n 10 $V -u -m chipformer.rv.gpucheck --dtype bf16 --decode > $D/gpucheck.log 2>&1; echo "STORE_GATE2_EXIT=$?"
grep -m1 kernels $D/gpucheck.log; echo "exact=$(grep -c ' exact on ' $D/gpucheck.log) bad=$(grep -cE 'mismatch|not exact|Traceback' $D/gpucheck.log)"
timeout 3600 nice -n 10 $V -m chipformer.rv.textcheck $M $D --out $D/rv-text.json > $D/textcheck.log 2>&1; echo "STORE_GATE1_EXIT=$?"
$V -c "import json;d=json.load(open('$D/rv-text.json'));print('kl',d['kl_merged_mean'],'floor',d['kl_noise_floor_mean'],'ratio',d['kl_merged_mean']/d['kl_noise_floor_mean'],'greedy',sum(d['greedy_identical']),'/',len(d['greedy_identical']))"
timeout 7200 nice -n 10 $V -u $W/ethereus/route-a/gates/gate4.py $D $FW $D > $D/gate4.log 2>&1; echo "STORE_GATE4_EXIT=$?"
grep -aE '^\{|^GATE4|Traceback' $D/gate4.log | cut -c1-220
timeout 3600 nice -n 10 $V scripts/boot_fs.py --manifest build/ethskills.manifest.json --mode verify --snapshot build/ethskills.fs.npz > $D/verify.log 2>&1; echo "VERIFY_EXIT=$?"; grep -aE "^verify|Traceback|Error" $D/verify.log | cut -c1-200
timeout 7200 nice -n 10 $V -u scripts/smoke_knowledge.py --model $D --fs build/ethskills.fs.npz > $D/smoke.log 2>&1; echo "SMOKE_EXIT=$?"; tail -6 $D/smoke.log | cut -c1-220
echo STORE_GATES_DONE
