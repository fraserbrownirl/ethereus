# Dispatcher log

One line per tick: time (UTC), what changed. Spend is omitted because Fraser monitors Vast himself (2026-11-01).

- 2026-11-01 07:38 Gate 1 PASS on 9B `dist/q9-rv2`: KL 6.34e-4, floor 3.77e-4, 1.68x, 12/16 greedy. Fix: keep the untied `lm_head` (`ab930ae` main, `1500fba` route-a).
- 2026-11-01 08:02 Clean checkout at `ab930ae`: store, sealed, filebuf, merge-head tests 32 passed. Main pushed.
- 2026-11-01 08:04 Gate 4 cause found: `chat.py` offered only the calc schema, 0 chip tokens. Fix `d700421` main, `1b9a213` route-a. Reported hang not reproduced; the same CLI finished in 69 s.
- 2026-11-01 08:06 Gate 4 prompt 1 clean: 6,006 chip tokens, 0 head mismatches, 0 wrong predictions on 6,028 owned positions. ELF `b320d31b` confirmed on the box.
- 2026-11-01 08:15 Addendum received and committed. Snapshot rebuild running. Next: `spill.py` + `serve.py`.
- 2026-11-01 08:30 Snapshot step done: `boot_fs` snap 6,091 owned positions exact, bytes unchanged, manifest names `b320d31b`, verify 136/136 on real kernels after fixing its empty reference (`c68e3aa`). `spill.py` and `serve.py` committed (`e30f129`, `8e2f944`), 14 model-free tests pass. GPU acceptance queued; needs a test-only merge from main because the store port changed the vocabulary.
- 2026-11-01 08:33 Gate 4 prompts 1-5 clean. Prompt 6 hit the token budget without calling `run` (model wrote calldata by hand), so a retry with explicit calldata is queued. Quiet ticks: 0.
- 2026-11-01 08:45 Gate 2 PASS on fla kernels (`dist/q9-rv2/gpucheck-fla2.log`), so the reported fla hang did not reproduce. Serve test merge started. Mac kept awake with `caffeinate`.
- 2026-11-01 08:58 Serve test `encode` replay failed at the exact stream end: the model repeated `encode` until the 1,500-token cap cut a run, no wrong token. `d3fd855` replays up to the unfinished run. Rerun queued after the current test.
- 2026-11-01 08:58 Gate 4 PASS on 9B `q9-rv2`: `run erc20` 114,544 chip tokens, 0 wrong on 114,627 owned positions; gates 3 and 4 complete. GPU hold lifted; Cursor agent ordered to prove one base `vanilla` trial from the Mac.
- 2026-11-01 09:05 Fraser's eval decisions in. Key audit: 9 tasks use the key, all grader-only, so 28 no-key, not 32. `serve.py` gains client tools, buffered streams and a base engine. Cursor agent ordered to run all modes on both arms.
- 2026-11-01 09:12 First serve run: `selector` and `run erc20` pass; `encode` hit the token cap (fixed, rerun in progress); spill `decode` lock error, a real design fault in GPU spill. Spill turned off, question filed.
- 2026-11-01 09:20 Docker cannot run on 58623317 (no CAP_SYS_ADMIN, unshare and mount denied). Rented harness VM 58640793 per Fraser; Vast has no GPU-less offers, so its GPU sits unused.
- 2026-11-01 09:25 Cursor agent retired; I own Route A. Inventory of 58623317: no trial ever ran, no harness venv, no model server, trajectories empty, ethevals clone at `cc3daaf`. Serve rerun: `selector`, `encode` pass so far. Task list moved to `docs/dispatcher-tasks.md`.
- 2026-11-01 09:30 Serve rerun on code with `d3fd855`: `selector`, `encode` (checked to the cut-off run), `run erc20` all replay clean; only the deferred spill case failed. Final three-call run started without it. Harness VM: Docker works, `ethevals` at `cc3daaf`. Link from VM to box declined by the permission system; option put to Fraser.
- 2026-11-01 09:35 Fraser chose one box, and all 37 tasks on Internet (28 then 9). Renting from offer 56403068. Old boxes stop only after gates are verified on the new one.
- 2026-11-01 10:05 Eval VM 58642243: gates 1, 2 on fla, 3, 4 pass on pinned venv (`docs/route-a.md`). Serve step 3 `RESULT PASS`. Old boxes stopped. First base trial ran end to end; found and fixed thinking-mode and end-token differences between the arms before any counted run. One trial per arm running.
- 2026-11-01 10:20 Counted Internet run started: 24 tasks need neither key nor RPC, base then circuit, 2 attempts. 4 no-key tasks deferred for lack of `MAINNET_RPC_URL`; 3 of the 9 judged need it too. Store merge started beside it at low priority.
- 2026-11-01 10:58 Store checkpoint `q9-store-rv`: gates 1 (1.07x), 2 (18/18), 4 (6/6) pass. Smoke 4/20 with store, 3/20 without. Verify rerun on GPU after an fla CPU error.
- 2026-11-01 11:08 Store checkpoint all pass, verify 136/136; `serve.py` store support committed, not yet run on GPU. Base arm continuing.
- 2026-11-01 12:10 Base partial: 12 of 46 scored trials, 6 of 23 tasks. Circuit server had failed to start on a store import (`a428992`); fixed and arm restarted before any circuit trial ran. Mixed `eth` plus client tools confirmed on the GPU. Witness-string task to rerun.
- 2026-11-01 13:25 Anthropic key on the VM. Publicnode rejects archive requests at the pinned blocks, so fork tasks stay deferred pending Alchemy. Queued after the circuit arm: witness rerun and the 6 judged tasks that need no RPC, both arms, harness budget reserve $288.
- 2026-11-01 14:10 Circuit arm complete: 6 of 24 tasks, 12 of 48 trials, identical to base where both scored; the circuit ran in no trial. Clean `.env` in place. Alchemy confirmed at all pinned blocks. Judged batch running; fork batch queued.
- 2026-11-01 14:55 Status console added. Ethereus test built; its first run hit no free VRAM because the base server holds 91 GB, so it now runs after the eval batches, still on ELF `b320d31b`. Judge cost so far $2.65.
- 2026-11-01 16:05 Fine-tune data generated. Counts and rejects in `experimental/riscv/data/ft1/manifest.json`; 30 knowledge examples in `docs/ft1-sample.md` for review.
- 2026-11-01 16:40 Fine-tune knowledge rebuilt: 468 distinct answers, largest file share 19.2%, 662 examples. New 30-example sample in `docs/ft1-sample.md`.
- 2026-11-01 16:22 Gate 4 recheck PASS after `logits_to_keep=1`: 6/6, token counts identical to the pinned run.
- 2026-11-01 16:22 Base judged batch: 0 of 7 tasks; base total 6 of 30 scored. Judge spend $2.71.
- 2026-11-01 16:55 Fraser's seven catchers added: three tasks in `docs/dispatcher-tasks.md`, rules in `docs/dispatcher-rules.md`.

## 2026-11-01 17:20 UTC

- Oracle independence: flipped keccak constant fails gate 1 (keccak 40/40, create 37/40, selector 15/40); control passes. `experimental/riscv/docs/logs/oracle-independence.log`. My first driver reported a false pass because `path=ELF` bound its default at definition; fixed in `eth.py`, which also now exits 1 on a failed gate.
- Malformed input: no crash, hang or garbage on the frozen ELF. `experimental/riscv/docs/logs/malformed-input.log`. Six lenient forms are fixed on `spill-eth-in`.
- Provenance: both ELFs rebuild to the same loaded bytes, locally and on GitHub's runner. `experimental/riscv/docs/logs/provenance.log`. `gh` defaults to the upstream repo in this checkout; pass `--repo fraserbrownirl/ethereus`.
- Environment pin: `ENV_PIN OK` on the VM, tampered lock reads DRIFT. `dist/q9-rv2/env-pin.json` on the VM. Canary script refuses while a server holds the GPU; no run yet.
- Fraser: inputs the firmware rejects by design are asserted as errors, not compared. Now rule 5. On the branch these go through `ByDesign`/`REJECT`, and `keccak @name` with no file joined them after a 200-case gate found it.
- Branch `spill-eth-in`, worktree `~/Dev/ethereus-spill`: bridge prefers `eth_in`, `serve.py` interrupts only calls it can move, `SPILL_ON_GPU` on. Bridge ELF there is not b320d31b and stays off main until the freeze lifts. Full gate 1 running: `docs/logs/gate1-spill.log` in that worktree.
- 2026-11-01 17:40 Circuit judged batch: 0 of 7 tasks, 0 of 14 trials, no errors (`results/internet-rest-rv2`). Totals: base 6 of 30, circuit 6 of 30, same passes. Fork batch started on base. Judge spend read as $5.45, which may count duplicates (`/tmp/j.py`).
- 2026-11-01 17:40 Correction to my own read: the one circuit run in the rv2 traces is my server check at 12:08:39, not a trial. The circuit has run in no eval trial.
- 2026-11-01 17:40 Spill branch gate 1: 10,910 cases, 0 mismatches, 6 by-design rejects asserted (`docs/logs/gate1-spill.log` on `spill-eth-in`). Full suite 306 passed, 2 failed, both stale tests; those two files then 93 of 93.
- 2026-11-01 17:55 Over-window gate on `spill-eth-in` failed, 12 of 14,000: 5 were a real bug, `encode f(int256) 2**255` answered with -2**255, in the frozen ELF too but over the window so unreachable there; 7 were my parser change altering a message. Both fixed on the branch; full rerun running.
- 2026-11-01 17:55 Fraser: boundary slice in gate 1. 320 cases, every intN and uintN at min, max, max+1, min-1 and every bytesN at N and N+1 bytes. Out of range asserted as errors, in range compared with `eth_abi`. Branch 0 bad; frozen ELF 1 bad, the int256 case, so the slice catches it.
- 2026-11-01 Over-window gate on `spill-eth-in` rerun after the fixes: see `docs/logs/gate1-spill-nowindow.log` and `gate1-spill-boundary.log` on that branch; suites in `spill-tests.log`.
- 2026-11-01 Fine-tune regenerated on `spill-eth-in`: `run` 400 rows, 393 spilled; `abi` 800, 314 spilled; `create` 200, with 56 `create2`, all spilled. All 763 spilled rows match the oracle, or for `run` a direct run on the non-spill image. `data/ft1/manifest.json` there. The generator's short `create2` salt was always an error, hidden by the window until now, so earlier data had no `create2`.

