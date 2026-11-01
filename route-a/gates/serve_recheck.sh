#!/bin/bash
# The three-call serve test on the eval VM: q9-rv2, pinned venv, replay on every trace.
# Waits for the Ethereus test's before run, so it never competes with it for VRAM.
W=/workspace
while ! grep -aq TEST_DONE $W/etest-before.log; do sleep 60; done
cd $W/ethereus/experimental/riscv
PYTHONPATH=.:../.. timeout 3000 $W/venv/bin/python -u scripts/serve_conversation.py --model $W/ethereus/dist/q9-rv2 --firmware tests/data/rv/eth-bridge.elf > $W/serve-test-evalvm.log 2>&1
echo "SERVE_RECHECK_EXIT=$?"
grep -aE '^(PASS|FAIL|RESULT)' $W/serve-test-evalvm.log | cut -c1-400
echo SERVE_RECHECK_DONE
