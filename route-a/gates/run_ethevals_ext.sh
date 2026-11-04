#!/bin/bash
# External test (Fraser, 2026-11-04: next on the GPU after the line test, ahead of anything else): clawdbotatg/eth-evals
# at 5b7284f, tool track (33 keccak tasks) and the closed-book set, on stock Qwen3.5-9B, Ethereus-9B (untrained) and
# Ethereus-9B-v0.1, temperature 0, pass@1 (the harness sends temperature 0; --runs 1). Each model is served exactly as
# in Step 3's Internet arms (route-a/gates/serve_arm.sh). Bare model: no client tools, so the tool track's "you have
# shell access and cast" prefix is answered without a shell; on the Ethereus models any keccak comes from the tool in
# the weights, which the submission discloses. Self-test first; the harness commit and manifest hash are recorded.
# --max-tokens 8192: the server's cap and Step 3's output limit (the harness default, 16000, is refused with 422).
W=/workspace; G=$W/ethereus/route-a/gates; X=$W/eth-evals-ext; OUT=$W/ethevals-ext; LOG=$OUT/run.log
mkdir -p $OUT
while pgrep -f "^bash $G/step3_linetest.sh" >/dev/null || pgrep -f "bash route-a/gates/step3_linetest.sh" >/dev/null; do sleep 60; done
echo "EXT_START $(date -u +%FT%TZ)" >> $LOG
[ -d $X/.git ] || git clone -q https://github.com/clawdbotatg/eth-evals.git $X
git -C $X checkout -q 5b7284f88fb58c0358c364598778f70136bfccf1 || { echo "EXT_FAILED checkout" >> $LOG; exit 1; }
echo "harness $(git -C $X rev-parse HEAD) dirty=$(git -C $X status --porcelain | wc -l)" >> $LOG
cd $X
for track in closed tools; do
  $W/venv/bin/python run_eval.py --self-test --track $track >> $LOG 2>&1 || { echo "EXT_FAILED self-test $track" >> $LOG; exit 1; }
done
export ETHEREUS_KEY=$(< $W/serve.token)
for arm in stock untrained v01; do
  bash $G/serve_arm.sh $arm >> $LOG 2>&1 || { echo "SERVER_FAILED $arm" >> $LOG; exit 1; }
  for track in closed tools; do
    $W/venv/bin/python run_eval.py --name ext-$arm-$track --base-url http://127.0.0.1:8080/v1 --model ethereus \
      --api-key-env ETHEREUS_KEY --track $track --runs 1 --concurrency 1 --max-tokens 8192 > $OUT/$arm-$track.log 2>&1
    echo "EXT_DONE $arm $track rc=$? $(date -u +%FT%TZ)" >> $LOG
  done
done
echo "EXT_ALL_DONE $(date -u +%FT%TZ)" >> $LOG
