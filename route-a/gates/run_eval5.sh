#!/bin/bash
# Internet+Skills: q9-store-rv with its files in the weights, Internet mode, the same tasks and limits as the
# other arms, in the same order: no-key, judged and witness-string, mainnet-fork. Uses the q9-rv2 agent entry,
# whose model name the server ignores; the results folder names the arm.
W=/workspace; G=$W/ethereus/route-a/gates; export BUDGET=${BUDGET:-600}
up() { ss -ltn | grep -q ":8080 " || bash $G/serve_arm.sh store; }
bash $G/serve_arm.sh store || { echo "SERVER_FAILED store"; exit 1; }
mapfile -t A < $W/lists/tasks5a; mapfile -t B < $W/lists/tasks5b; mapfile -t C < $W/lists/tasks5c  # 37 distinct tasks
bash $G/run_trials.sh rv2 internet-nokey-store 2 "${A[@]}"
up; bash $G/run_trials.sh rv2 internet-rest-store 2 "${B[@]}"
up; bash $G/run_trials.sh rv2 internet-fork-store 2 "${C[@]}"
echo EVAL5_DONE
