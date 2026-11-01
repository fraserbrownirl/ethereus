#!/usr/bin/env bash
set -euo pipefail
BASE=${1:-http://localhost:8001}
echo "GET $BASE/health"
curl -sf "$BASE/health" | python3 -m json.tool; echo
echo "GET $BASE/v1/models"
curl -sf "$BASE/v1/models" | python3 -m json.tool; echo
for prompt in \
  "Use eth: what is the selector of transfer(address,uint256)?" \
  "Use eth: keccak 0x616161" \
  "Use eth: u256 2**255 - 1" \
; do
  echo "POST $BASE/v1/chat/completions — $prompt"
  curl -sf "$BASE/v1/chat/completions" -H 'Content-Type: application/json' \
    -d "$(python3 -c "import json,sys; print(json.dumps({'model':'ethereus','messages':[{'role':'user','content':sys.argv[1]}],'temperature':0}))" "$prompt")" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['choices'][0]['message']['content'][:2000])"
  echo "---"
done
