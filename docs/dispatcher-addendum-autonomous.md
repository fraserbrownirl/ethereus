Dispatcher addendum — Muse is dropped; you build the core; run autonomously

Date: 2026-11-01. Supersedes the Muse parts of docs/dispatcher-commission.md. Everything else there stands (split rules with the Cursor agent, verify-before-report, spend cap, never-do list).

1. You own the core repo now

Muse is retired. Its remaining work is yours, in this order, each with its test:

ELF rebuild. build_eth.sh from current env/eth/; readelf -S lists .filebuf, nm lists file_buf; uv run pytest tests/test_rv_filebuf.py tests/test_rv_bridge.py -q green. Commit alone, message firmware: rebuild eth-bridge.elf with file_buf. Record the sha256 in docs/eth-bench.md and append it to route-a-brief.md task 9 so the Cursor agent pulls it.
rv-store port landed on main. Take Muse's worktree at ~/Dev/ethereus-phase2: commit the author's port verbatim (the 11 modified files plus store_rv.py, storecheck.py, storebench.py, storedbg.py, docs/rv-store.md) as one commit, then Muse's chat.py delta and sealedstore.py as another. Test: a fresh clone of main passes tests/test_rv_store.py tests/test_rv_sealed.py and scripts/boot_fs.py --mode check.
serve.py + spill.py per docs/api-spec-clean.md, seeded from route-a/shim/server.py. Test: the scripted three-call conversation (selector, encode, run erc20 balanceOf), replay.py clean on all three traces. No streaming, no Modal wrapper yet.
Snapshot rebuilt against the new eth-bridge.elf (boot_fs.py --mode snap); manifest firmware hash updated; commit build/.
ETH Evals on the 4B (the "baby" row) — only after Route A's 9B evals are running, on a separate L4 you rent (≤ $0.50/h): base 4B via vLLM, 4B + circuit via serve.py, all 37 tasks through OpenCode, trajectories saved. This is Muse's section 3; same rules.
docs/ethevals-submission.md draft when both backbones have numbers.

Working rules are the ones in the Muse brief: no compiler/IR/driver changes (the rv-store port is the author's code and is exempt), gate 1 before GPU time, numbers only from runs, bench file current, no hand-written JSON (generate it), no publishing.

2. Cadence

Run as a /loop every 30 minutes. Each tick:

git fetch; read docs/route-a.md and the Cursor agent's chat if visible; poll the Vast box (poll_instance.py, tail the gate and eval logs).
Verify anything the Cursor agent reported since last tick by running its command.
If the Cursor agent is blocked or off-brief, append a numbered task to route-a-brief.md with the stop rule. Do not edit route-a.
Advance your own task list (section 1) by the next concrete step. One step per tick is fine; a long step (a merge, an eval) runs in the background with nohup and is checked next tick.
Update the "Current state" section of the commission and docs/dispatcher-log.md (one line per tick: time, what changed, spend).
Report to Fraser only when section 3 says to.
3. When to report to Fraser, and when to stop

Report (and keep looping) on:

A gate result, pass or fail, for either backbone.
An eval score, any configuration.
Spend at 50% and 80% of the weekly cap.
A decision on the never-do list (publishing, spec change, feature, > $1.50/h rental). State the decision and your recommendation, then continue with other work that does not depend on it.

Stop the loop and report on:

Done: 9B gates green on fla kernels; base and Ethereus Concepts scores for the 9B in docs/route-a.md; serve.py test green; snapshot rebuilt; docs/ethevals-submission.md drafted. Post the final report with every number and its source file.
Stuck: the same step has failed three times after a steer, on either side. Report what was tried.
Cap: spend reaches the weekly cap.

Silence otherwise. Fraser reads docs/dispatcher-log.md when he wants detail.

4. The Cursor agent

Unchanged: it works route-a from route-a-brief.md; you append tasks, it reports in docs/route-a.md. The store merge (q9-store-rv) is its task after the Concepts evals are running, per the sequencing already agreed; it uses the snapshot you commit in step 4.
