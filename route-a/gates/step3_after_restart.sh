#!/bin/bash
# Resume Step 3 after the VM was stopped (Vast stopped 58642243 on 2026-11-03 ~17:13 UTC, credit at zero) mid arm 6.
# Same option C as step3_rest.sh: watchdog first, then arm 6's remaining trials (the harness skips trials that have
# results), its resume pass without watchdog-stopped tasks, then arm 7. Started by hand with setsid nohup.
W=/workspace; G=$W/ethereus/route-a/gates
pgrep -f "^bash $G/watchdog190.sh" >/dev/null || setsid nohup bash $G/watchdog190.sh > $W/step3/watchdog.out 2>&1 < /dev/null &
source <(sed -n '/^W=/,/^arm() {/p' $G/step3_rest.sh | sed '$d')   # ALL, up, FROM, stopped_tasks, resume
arm() { FROM=$(( $(cat $W/step3/watchdog.log 2>/dev/null | wc -l) + 1 )); bash $G/run_step3.sh $1 $2 $3 "${ALL[@]}"; up || bash $G/serve_arm.sh $SERVER; resume "$@"; echo "ARM_DONE $3 $(date -u +%FT%TZ)"; }
SERVER=stock; bash $G/serve_arm.sh $SERVER || { echo SERVER_FAILED $SERVER; exit 1; }
arm opencode-qwen3.5-9b internet step3-stock-internet
arm vanilla-qwen3.5-9b vanilla step3-stock-modelonly
echo STEP3_ALL_DONE
