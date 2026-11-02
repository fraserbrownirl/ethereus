#!/bin/bash
# After the freeze lifts: the eth_in firmware on both checkpoints. Start by hand once both eval configurations,
# the before test and the three-call rerun have been read. Never stops early: every step's result is reported.
W=/workspace; V=$W/venv/bin/python; RV=$W/ethereus/experimental/riscv; D=$W/ethereus/dist/q9-rv2; R=$D/gate4-spill; WANT=8f07bfe35689e789010e0f5424b67d9ae616a3fb0532eb890af4d5a6fb4f099a
FW=$RV/tests/data/rv/eth-bridge.elf; MR=$W/ethereus-main/experimental/riscv
ps -eo pid,args | grep "[c]hipformer.rv.serve:from_env" | awk '{print $1}' | while read p; do kill $p; done; sleep 5
git -C $W/ethereus fetch -q origin && git -C $W/ethereus checkout -q route-a-spill && git -C $W/ethereus pull -q --ff-only origin route-a-spill
git -C $W/ethereus-main fetch -q origin && git -C $W/ethereus-main checkout -q spill-eth-in && git -C $W/ethereus-main pull -q --ff-only origin spill-eth-in
for f in $FW $MR/tests/data/rv/eth-bridge.elf; do [ "$(sha256sum $f | cut -d' ' -f1)" = $WANT ] || { echo "SPILL_ABORT wrong ELF $f"; exit 2; }; done
echo ELF_OK; mkdir -p $R; $V $W/ethereus/route-a/gates/env_pin.py $R/env-pin.json
cd $W; PYTHONPATH=ethereus/experimental/riscv:ethereus timeout 7200 $V -u $W/ethereus/route-a/gates/gate4.py $D $FW $R > $R/gate4.log 2>&1; echo "GATE4_EXIT=$?"
$V - $R/gate4.log <<'PY'
import json, sys
b = [json.loads(x) for x in open(sys.argv[1], errors="replace") if x.startswith('{"prompt"')]
ok = len(b) == 6 and all(y["ok"] for y in b)
print("GATE4_SPILL", "PASS" if ok else "FAIL", [(y["text"], y["chip"], y["replay"].get("wrong_predictions")) for y in b])
PY
cd $RV; PYTHONPATH=.:../.. timeout 3000 $V -u scripts/serve_conversation.py --model $D --firmware $FW --spill > $R/spill-test.log 2>&1; echo "SPILL_TEST_EXIT=$?"
grep -aE '^(PASS|FAIL|RESULT)' $R/spill-test.log | cut -c1-500
bash $W/ethereus/route-a/gates/canary.sh
cd $MR; PYTHONPATH=.:../.. timeout 3600 $V scripts/boot_fs.py --manifest build/ethskills.manifest.json --mode verify --snapshot build/ethskills.fs.npz > $R/snapshot-verify.log 2>&1; echo "SNAP_VERIFY_EXIT=$?"; tail -1 $R/snapshot-verify.log | cut -c1-200
bash $W/ethereus/route-a/ethereus-test/run_all.sh after
echo SPILL_GATES_DONE
