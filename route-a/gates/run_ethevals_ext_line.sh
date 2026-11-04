#!/bin/bash
# Fourth eth-evals model (Fraser, 2026-11-04): the untrained model plus the line-test hint, only if the line test showed
# the hint raises eth calls ("the training taught it" against "being told is enough"). Waits for run_ethevals_ext.sh to
# finish, then runs if /workspace/ethevals-ext/add-line exists (the dispatcher creates it from the line-test result);
# otherwise records the skip. Same settings as the other three: temperature 0, pass@1, closed then tools.
W=/workspace; G=$W/ethereus/route-a/gates; X=$W/eth-evals-ext; OUT=$W/ethevals-ext; LOG=$OUT/run.log
until grep -q '^EXT_ALL_DONE\|^EXT_FAILED\|^SERVER_FAILED' $LOG 2>/dev/null; do sleep 60; done
grep -q '^EXT_ALL_DONE' $LOG || exit 1
until [ -f $OUT/add-line ] || [ -f $OUT/skip-line ]; do sleep 60; done
if [ -f $OUT/skip-line ]; then echo "EXT_LINE_SKIPPED $(date -u +%FT%TZ)" >> $LOG; exit 0; fi
export ETHEREUS_KEY=$(< $W/serve.token); cd $X
bash $G/serve_arm.sh untrained-line >> $LOG 2>&1 || { echo "SERVER_FAILED untrained-line" >> $LOG; exit 1; }
for track in closed tools; do
  $W/venv/bin/python run_eval.py --name ext-untrained-line-$track --base-url http://127.0.0.1:8080/v1 --model ethereus \
    --api-key-env ETHEREUS_KEY --track $track --runs 1 --concurrency 1 --max-tokens 8192 > $OUT/untrained-line-$track.log 2>&1
  echo "EXT_DONE untrained-line $track rc=$? $(date -u +%FT%TZ)" >> $LOG
done
echo "EXT_LINE_DONE $(date -u +%FT%TZ)" >> $LOG
