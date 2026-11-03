#!/bin/bash
# Step 3 events for the dispatcher's watcher: arm done, server failure, watchdog stop, and any new trial exception.
# Trial exceptions already present when it starts are not repeated (kept in seen file across restarts).
SEEN=/workspace/step3/watch_seen.txt; touch $SEEN
P=$PPID  # the ssh session; when it ends, stop, so a stale copy never marks errors seen unseen by anyone
trap 'kill 0' EXIT
tail -n0 -F /workspace/step3/step3_rest.log /workspace/step3/watchdog.log 2>/dev/null \
  | grep -E --line-buffered 'ARM_DONE|SERVER_FAILED|STEP3_ALL_DONE|STOPPED|Traceback|Killed' &
while kill -0 $P 2>/dev/null; do
  python3 - "$SEEN" <<'PY'
import json, glob, os, sys
seen = set(open(sys.argv[1]).read().split())
new = []
for f in glob.glob("/workspace/results/step3-*/jobs/*/*/result.json"):
    trial = os.path.basename(os.path.dirname(f))
    key = f.split("/")[3] + "/" + trial
    if key in seen: continue
    try: e = json.load(open(f)).get("exception_info") or {}
    except Exception: continue
    if e.get("exception_type"):
        m = (e.get("exception_message") or "").split("\n")[0][:90]
        print(f"TRIAL_ERROR {key} {e['exception_type']}: {m}", flush=True); new.append(key)
open(sys.argv[1], "a").write("".join(k + "\n" for k in new))
PY
  sleep 120
done
exit 0
