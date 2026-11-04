#!/bin/bash
# Resume the eth-evals external run after a VM stop (Vast stopped 58642243 at ~10:40 UTC 2026-11-04, credit at zero):
# every model and track whose result file already exists is skipped; the rest run in the planned order (stock,
# untrained, v01, then untrained + hint if /workspace/ethevals-ext/add-line is set), at the same settings as
# run_ethevals_ext.sh: temperature 0 (the harness's), pass@1, --max-tokens 8192 (the server's cap).
W=/workspace; G=$W/ethereus/route-a/gates; X=$W/eth-evals-ext; OUT=$W/ethevals-ext; LOG=$OUT/run.log
echo "EXT_RESUME $(date -u +%FT%TZ)" >> $LOG
echo "harness $(git -C $X rev-parse HEAD)" >> $LOG
cd $X
for track in closed tools; do
  $W/venv/bin/python run_eval.py --self-test --track $track >> $LOG 2>&1 || { echo "EXT_FAILED self-test $track" >> $LOG; exit 1; }
done
export ETHEREUS_KEY=$(< $W/serve.token)
arms="stock untrained v01"; [ -f $OUT/add-line ] && arms="$arms untrained-line"
for arm in $arms; do
  todo=""
  for track in closed tools; do
    ls $X/results*/ext-$arm-$track.json >/dev/null 2>&1 || todo="$todo $track"
  done
  [ -n "$todo" ] || { echo "EXT_SKIP $arm (both tracks have results)" >> $LOG; continue; }
  bash $G/serve_arm.sh $arm >> $LOG 2>&1 || { echo "SERVER_FAILED $arm" >> $LOG; exit 1; }
  for track in $todo; do
    $W/venv/bin/python run_eval.py --name ext-$arm-$track --base-url http://127.0.0.1:8080/v1 --model ethereus \
      --api-key-env ETHEREUS_KEY --track $track --runs 1 --concurrency 1 --max-tokens 8192 > $OUT/$arm-$track.log 2>&1
    echo "EXT_DONE $arm $track rc=$? $(date -u +%FT%TZ)" >> $LOG
  done
done
echo "EXT_ALL_DONE $(date -u +%FT%TZ)" >> $LOG
[ -f $OUT/add-line ] && echo "EXT_LINE_DONE $(date -u +%FT%TZ)" >> $LOG
