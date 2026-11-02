#!/bin/bash
# Step 3, all arms in Fraser's order on one server, each through ETH Evals' runner with job-step3.yaml.
# Per selection: a full pass, then a resume pass (the harness retries errored trials once). ARM_DONE marks each arm.
W=/workspace; G=$W/ethereus/route-a/gates; mapfile -t T < $W/lists/step3/all37
( while true; do docker stats --no-stream --format '{{.Name}} {{.MemUsage}}' 2>/dev/null | grep env-main | sed "s/^/$(date -u +%H:%M:%S) /"; sleep 20; done ) > $W/step3/docker-mem.log 2>&1 &
up() { $W/venv/bin/python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health', timeout=2)" 2>/dev/null; }
arm() {  # agent mode output
  bash $G/run_step3.sh $1 $2 $3 "${T[@]}"
  up || bash $G/serve_arm.sh $SERVER
  bash $G/run_step3.sh $1 $2 $3 "${T[@]}"
  echo "ARM_DONE $3 $(date -u +%FT%TZ)"
}
SERVER=v01-skills; bash $G/serve_arm.sh $SERVER || { echo SERVER_FAILED $SERVER; exit 1; }
arm opencode-ethereus-9b-v0.1 skills step3-v01-skills
arm opencode-ethereus-9b-v0.1 internet step3-v01-internet
arm vanilla-ethereus-9b-v0.1 vanilla step3-v01-modelonly
SERVER=untrained; bash $G/serve_arm.sh $SERVER || { echo SERVER_FAILED $SERVER; exit 1; }
arm opencode-ethereus-9b-untrained internet step3-untrained-internet
arm vanilla-ethereus-9b-untrained vanilla step3-untrained-modelonly
SERVER=stock; bash $G/serve_arm.sh $SERVER || { echo SERVER_FAILED $SERVER; exit 1; }
arm opencode-qwen3.5-9b internet step3-stock-internet
arm vanilla-qwen3.5-9b vanilla step3-stock-modelonly
echo STEP3_ALL_DONE
