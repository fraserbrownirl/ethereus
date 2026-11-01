#!/bin/bash
# One command after any firmware, snapshot, driver or venv change: the three-call conversation, replay on every trace.
#   canary.sh          run it on q9-rv2; needs the GPU free. Writes /workspace/canary.json
#   canary.sh --due    no GPU: says whether anything changed since the last pass
W=/workspace; R=$W/ethereus/route-a/gates; RV=$W/ethereus/experimental/riscv; V=$W/venv/bin/python
MODEL=${MODEL:-$W/ethereus/dist/q9-rv2}; FW=${FW:-$RV/tests/data/rv/eth-bridge.elf}
INPUTS="$FW $MODEL/config.json $RV/chipformer/rv/chat.py $RV/chipformer/rv/serve.py $RV/chipformer/rv/bridge.py"
FP=$($V $R/env_pin.py --fingerprint $INPUTS)
if [ "${1:-}" = "--due" ]; then
  [ -f $W/canary.json ] || { echo "CANARY DUE: no pass recorded"; exit 1; }
  $V - "$FP" $W/canary.json <<'PY'
import json, sys
c = json.load(open(sys.argv[2]))
ok = c["result"] == "PASS" and c["fingerprint"] == sys.argv[1]
print("CANARY CURRENT" if ok else f"CANARY DUE: last {c['result']} at {c['at']}, inputs {'same' if c['fingerprint'] == sys.argv[1] else 'changed'}")
sys.exit(0 if ok else 1)
PY
  exit $?
fi
if ss -ltn 2>/dev/null | grep -q ':8080 '; then echo "CANARY REFUSED: a server holds the GPU"; exit 2; fi
LOG=$W/canary-$(date -u +%Y%m%dT%H%M).log
$V $R/env_pin.py $W/canary-env.json
cd $RV; PYTHONPATH=.:../.. timeout 3000 $V -u scripts/serve_conversation.py --model $MODEL --firmware $FW > $LOG 2>&1
rc=$?
grep -aE '^(PASS|FAIL|RESULT)' $LOG | cut -c1-300
RES=FAIL; [ $rc = 0 ] && grep -aq '^RESULT PASS' $LOG && RES=PASS
$V -c "import json,sys,time;json.dump(dict(result=sys.argv[1],fingerprint=sys.argv[2],log=sys.argv[3],at=time.strftime('%Y-%m-%d %H:%M',time.gmtime())),open('$W/canary.json','w'))" $RES "$FP" "$LOG"
echo "CANARY $RES $LOG"; [ $RES = PASS ]
