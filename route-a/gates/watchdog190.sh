#!/bin/bash
# Stop any trial's agent still running 190 minutes after its container started, as ETH Evals' CI ends a trial step
# at 190 minutes. The agent is killed from the host by the PIDs `docker top` reports (a kill run inside the
# container did not reach OpenCode). A stop is logged only once no OpenCode process is left; the scoring counts these
# trials as timeouts (failures), listed apart.
LOG=${1:-/workspace/step3/watchdog.log}; LIMIT=$((190*60))
while true; do
  for c in $(docker ps --format '{{.Names}}' | grep -- '__env-main-1$'); do
    s=$(docker inspect -f '{{.State.StartedAt}}' "$c" 2>/dev/null) || continue
    age=$(( $(date +%s) - $(date -d "$s" +%s) ))
    [ $age -ge $LIMIT ] || continue
    pids=$(docker top "$c" -eo pid,args 2>/dev/null | awk 'NR>1 && /opencode/ && !/bash -c/ {print $1}')
    [ -n "$pids" ] || continue
    kill -TERM $pids 2>/dev/null; sleep 10
    left=$(docker top "$c" -eo pid,args 2>/dev/null | awk 'NR>1 && /opencode/ && !/bash -c/ {print $1}')
    [ -n "$left" ] && { kill -KILL $left 2>/dev/null; sleep 5; left=$(docker top "$c" -eo pid,args 2>/dev/null | awk 'NR>1 && /opencode/ && !/bash -c/ {print $1}'); }
    [ -z "$left" ] && echo "STOPPED $c age_s=$age $(date -u +%FT%TZ)" >> "$LOG"
  done
  sleep 60
done
