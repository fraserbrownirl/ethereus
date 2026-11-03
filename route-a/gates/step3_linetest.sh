#!/bin/bash
# Fraser, 2026-11-03: after the Step 3 arms AND after the decode ban (<|im_start|>, <|endoftext|>) is deployed, the
# untrained model with and without one server-side line, on the same 10 Internet tasks (5 of them Build or Act), 2
# trials each, both fresh on the same server; option C (watchdog stop = timeout failure, no retry; one trial at a
# time). Question: does it call eth and then keep working with OpenCode's tools. Started by hand, not queued.
W=/workspace; G=$W/ethereus/route-a/gates
T=(tasks/concepts/signatures-permit2-witness-string tasks/concepts/smart-accounts-userop-hash-v08
   tasks/concepts/accounts-7739-typeddatasign-domain tasks/concepts/permit2-7702-signature
   tasks/concepts/evm-transfer-refund-to-safe tasks/build/token-launchpad tasks/build/signed-votes
   tasks/build/creator-coins tasks/act/treasury-eth-sale tasks/act/vault-v2-market-exit)
for cond in untrained untrained-line; do
  bash $G/serve_arm.sh $cond || { echo SERVER_FAILED $cond; exit 1; }
  bash $G/run_step3.sh opencode-ethereus-9b-untrained internet step3-linetest-$cond "${T[@]}"
  echo "LINETEST_DONE $cond $(date -u +%FT%TZ)"
done
echo LINETEST_ALL_DONE
