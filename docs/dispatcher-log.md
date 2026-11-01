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
