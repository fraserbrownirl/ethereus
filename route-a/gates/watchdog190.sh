#!/bin/bash
# Stop any trial's agent still running 190 minutes after its container started, as ETH Evals' CI ends a trial step
# at 190 minutes. Each stop is logged; the scoring counts these trials as timeouts (failures), listed apart.
LOG=${1:-/workspace/step3/watchdog.log}; LIMIT=$((190*60))
while true; do
  for c in $(docker ps --format '{{.Names}}' | grep -- '__env-main-1$'); do
    s=$(docker inspect -f '{{.State.StartedAt}}' "$c" 2>/dev/null) || continue
    age=$(( $(date +%s) - $(date -d "$s" +%s) ))
    if [ $age -ge $LIMIT ] && ! grep -q "^STOPPED $c " "$LOG" 2>/dev/null; then
      docker exec "$c" sh -c 'for p in /proc/[0-9]*; do grep -q opencode $p/cmdline 2>/dev/null && kill -TERM ${p#/proc/}; done' 2>/dev/null
      echo "STOPPED $c age_s=$age $(date -u +%FT%TZ)" >> "$LOG"
    fi
  done
  sleep 60
done
