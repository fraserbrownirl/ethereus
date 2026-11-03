#!/bin/bash
# Step 3 from arm 4 on, Fraser's option C (2026-11-03): one trial at a time on the one server; a watchdog stop at
# 190 minutes is a timeout failure with no retry. Each arm: a full pass, then a resume pass over the tasks that had
# no watchdog stop (so the harness still retries ordinary errors once, but never a stopped trial).
W=/workspace; G=$W/ethereus/route-a/gates; mapfile -t ALL < $W/lists/step3/all37
up() { $W/venv/bin/python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health', timeout=2)" 2>/dev/null; }
FROM=1  # first watchdog-log line that belongs to the current arm
stopped_tasks() {  # tasks of trials the watchdog stopped during the current arm
  tail -n +$FROM $W/step3/watchdog.log 2>/dev/null | grep '^STOPPED ' | awk '{print $2}' | sed -E 's/__[a-z0-9]+__env-main-1$//' | sort -u
}
resume() {  # agent mode output: resume pass without watchdog-stopped tasks
  local skip; skip=$(stopped_tasks)
  local T=(); for t in "${ALL[@]}"; do  # container names cut long task names short, so match the stub as a prefix
    n=$(basename $t); keep=1; for s in $skip; do [[ "$n" == "$s"* ]] && keep=0; done; [ $keep = 1 ] && T+=("$t"); done
  echo "RESUME $3: ${#T[@]} of ${#ALL[@]} tasks (skipped for watchdog stops: $(echo $skip | tr '\n' ' '))"
  up || bash $G/serve_arm.sh $SERVER
  bash $G/run_step3.sh $1 $2 $3 "${T[@]}"
}
arm() {
  FROM=$(( $(cat $W/step3/watchdog.log 2>/dev/null | wc -l) + 1 ))
  bash $G/run_step3.sh $1 $2 $3 "${ALL[@]}"
  up || bash $G/serve_arm.sh $SERVER
  resume "$@"
  echo "ARM_DONE $3 $(date -u +%FT%TZ)"
}
# Arm 4's first pass is still running under step3_all.sh; wait for it, then its resume pass.
SERVER=untrained
while [ -n "$(ps -eo args | grep 'ethevals run' | grep 'step3-untrained-internet' | grep -v grep)" ]; do sleep 60; done
resume opencode-ethereus-9b-untrained internet step3-untrained-internet
echo "ARM_DONE step3-untrained-internet $(date -u +%FT%TZ)"
up || bash $G/serve_arm.sh $SERVER
arm vanilla-ethereus-9b-untrained vanilla step3-untrained-modelonly
SERVER=stock; bash $G/serve_arm.sh $SERVER || { echo SERVER_FAILED $SERVER; exit 1; }
arm opencode-qwen3.5-9b internet step3-stock-internet
arm vanilla-qwen3.5-9b vanilla step3-stock-modelonly
echo STEP3_ALL_DONE
