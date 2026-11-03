#!/bin/bash
# Fraser, 2026-11-03: after the Step 3 arms, the untrained model plus one server-side line on 10 Internet tasks,
# 2 trials each, the same settings as arm 4 (option C: watchdog stop = timeout failure, no retry). Compared with
# arm 4's trials on the same tasks: agent tool calls, eth calls, passes. Possibly v0.2 without training.
W=/workspace; G=$W/ethereus/route-a/gates
until grep -q STEP3_ALL_DONE $W/step3/step3_rest.log 2>/dev/null; do sleep 300; done
T=(tasks/concepts/signatures-permit2-witness-string tasks/concepts/smart-accounts-userop-hash-v08
   tasks/concepts/accounts-7739-typeddatasign-domain tasks/concepts/permit2-7702-signature
   tasks/concepts/evm-transfer-refund-to-safe tasks/build/token-launchpad tasks/build/signed-votes
   tasks/build/creator-coins tasks/act/treasury-eth-sale tasks/act/vault-v2-market-exit)
bash $G/serve_arm.sh untrained-line || { echo SERVER_FAILED untrained-line; exit 1; }
bash $G/run_step3.sh opencode-ethereus-9b-untrained internet step3-linetest-untrained "${T[@]}"
echo "LINETEST_DONE $(date -u +%FT%TZ)"
