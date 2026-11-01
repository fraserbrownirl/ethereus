#!/bin/bash
# Gate 4 and 3 after logits_to_keep=1. Pass: all rows ok, and text and chip counts equal the pinned run.
W=/workspace; D=$W/ethereus/dist/q9-rv2; R=$D/gate4-keep1; mkdir -p $R
FW=$W/ethereus/experimental/riscv/tests/data/rv/eth-bridge.elf
$W/venv/bin/python $W/ethereus/route-a/gates/env_pin.py $R/env-pin.json
cd $W; PYTHONPATH=ethereus/experimental/riscv:ethereus timeout 7200 $W/venv/bin/python -u $W/ethereus/route-a/gates/gate4.py $D $FW $R > $R/gate4.log 2>&1
$W/venv/bin/python - "$D/gate4.log" "$R/gate4.log" <<'PY'
import json, sys
def rows(p): return [json.loads(x) for x in open(p, errors="replace") if x.startswith('{"prompt"')]
a, b = rows(sys.argv[1]), rows(sys.argv[2])
same = len(a) == len(b) == 6 and all((x["text"], x["chip"]) == (y["text"], y["chip"]) for x, y in zip(a, b))
ok = all(y["ok"] for y in b)
print("GATE4_KEEP1", "PASS" if same and ok else "FAIL", [(y["text"], y["chip"], y["replay"].get("wrong_predictions")) for y in b])
sys.exit(0 if same and ok else 1)
PY
rc=$?; [ $rc = 0 ] && touch $W/gate4-keep1.ok; exit $rc
