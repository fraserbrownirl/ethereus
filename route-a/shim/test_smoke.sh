#!/usr/bin/env bash
set -e
BASE=${1:-http://localhost:8001}
echo "GET $BASE/v1/models"
curl -sf "$BASE/v1/models" | head -c 500; echo
echo "POST $BASE/v1/chat/completions (selector prompt)"
curl -sf "$BASE/v1/chat/completions" -H 'Content-Type: application/json' \
  -d '{"model":"ethereus","messages":[{"role":"user","content":"Use eth: what is the selector of transfer(address,uint256)?"}],"temperature":0}' \
| python3 -c "import sys,json; d=json.load(sys.stdin); print(d['choices'][0]['message']['content'][:1000])"
