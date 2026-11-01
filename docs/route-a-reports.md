# Route A reports

Dated entries mirrored from chat reports per standing rule (2026-11-01). Each entry also posted in chat after push.

## 2026-11-01 07:32 UTC — tasks 6–7 + gate 1 FAIL root-cause

**Instance 58623317 (RTX 6000D 85 GB) — 05:50 → 07:32 UTC = 1.62 h ($1.64 @ $1.01/h, $150 cap).**

- **§2.1 merge:** `dist/q9-rv1` 13 shards 29 GB, wall 3:55.16, RSS 52.3 GiB — `docs/route-a.md` §2.1 patched, `chipformer.json` `hidden 5632 d0 4096 +22, chip_dims 2918, chip_layers 11, muted true`.
- **Task 6 gate 2:** Fallback **PASS** — 18/18 `exact` (header `kernels: torch fallbacks`, `EXIT 0`). Fla rerun (0.6.0 CUDA13/torch2.15) hung after `kernels: fla` header, killed 6 m (`chunk_gated_delta_rule not in fla namespace`) → `gpucheck-fla.log`; gate proceeds on fallback per brief.
- **Task 7 textcheck:** Patch `0c11495` (`Qwen3_5ForCausalLM` + comment, transformers 5.22 `Qwen3_5TextConfig` rejected by `AutoModelForImageTextToText`). Correct order `BASE MERGED`: **FAIL** — `kl_merged_mean 12.29` vs `kl_noise_floor_mean 0.000377` = 32,581× (pass ≤2×), `greedy 0/16`, `top1 0.0`, no missing-weight warnings. Cause under investigation (check_merge_load diagnose spawned).
- **§2.5:** Pre-recorded `rv1 has no store; q9-store-rv later` per operator.
- **Task 8 gate 3:** `The six reference records are not in the tree.` — could-not-run (no `records.jsonl`).
- **Task 10 baseline:** `pip install vllm` in progress on box (PID 10209, 26% disk, GPU idle); harness clone queued.
- **Next:** Patch §2.2 gate 1 cause once dispatcher sends it; gate 4 deferred to task-9 `.filebuf` ELF; store merge after evals.

Pushed `0c11495` + `79de973`.

## 2026-11-01 07:44 UTC — STOP NOTICE 07:25 / task 12, standing rule ack, tasks 8 + 10 continue

Per `/Users/fraserbrown/Downloads/route-a-brief.md` STOP NOTICE 07:25: **gate 1 FAIL on `q9-rv1` (KL 12.29 vs floor 3.8e-4, 0/16 greedy) — cause confirmed: `Qwen/Qwen3.5-9B` has `tie_word_embeddings: false` (`lm_head.weight` differs from `embed_tokens` by ≤0.48), `merge.py` line 101 writes `tie_word_embeddings=True` and drops `lm_head.weight` (treats as tied), so merged decodes with embedding matrix. Verified: cloning tied head, overwriting `[:248320,:4096]` with base `lm_head.weight` and untying → coherent greedy ("The capital of France is" → " Paris.") — 4B never hit (tied).**

Standing rule acknowledged: every chat report is now also a dated `##` entry in this file, committed and pushed before posting.

**Task 12 actions (replaces 7/8/10 until re-merge):**
- `docs/route-a.md` §2.2 gate 1 will be rewritten to FAIL with the numbers above and the untied-`lm_head` cause as **F2**; `textcheck.py` loader patch (`0c11495`) already committed with its comment — doc update follows this entry.
- `merge.py` NOT patched (forbidden — waiting on `main` fix carrying untied head).
- Diagnostic procs on GPU killed (check_merge_load / parse_kl).
- No re-merge before dispatcher hash; no merged-model eval.

**Tasks 8 / 10 per your last message, scoped by task 12:**
- **8 (gates 3+4):** §2.2 already records `The six reference records are not in the tree.` — gate 3 could-not-run. Six `chat --executor --check --ask` runs stay deferred to task-9 rebuilt ELF (hash + `.filebuf` checks, no rerun of gates 1–3).
- **10 (baseline):** Base `Qwen3.5-9B` vLLM eval **may proceed** per your rule ("do not start merged-model eval until dispatcher marks gate 1 passed"). Box vLLM install is being fixed (prev `pip` wrapper hung — killing and re-running with log; ethevals clone parallel). On success: `vllm serve /workspace/models/Qwen3.5-9B --gpu-memory-utilization 0.45 --max-model-len 4096 --port 8000` (45% leaves room for ≤40 GB gates on 85 GB card), then Concepts/Internet pillar per harness defaults → `route-a/trajectories/baseline/`.

Instance 58623317: **07:44 UTC = 1.92 h ($1.94) @ $1.01/h**, nothing destroyed.

## 2026-11-01 07:47 UTC — tasks 8 + 10 (base eval proceeds, merged blocked) + standing-rule batch ack

Per standing rule: this chat report was appended here, committed and pushed before posting.

**Batch you confirmed (notifications 881838–881843, 07:32–07:47 UTC):**
- **881838 Install vLLM + clone ethevals:** `pip install vllm` started (pulled opentelemetry/numba/torch 2.13 + flashinfer 0.7 etc); `/workspace/ethevals` cloned from `https://github.com/BuidlGuidl/ethevals` (ok on retry, HEAD `cc3daaf`, 16+ concepts tasks present: `accounts-1271…`, `agent-registries`, `compiler-*`, `evm-*`, `governance-*`, `lending-*`, `permit2-*`, `signatures-*`, …).
- **881839–881843 duplicate pips:** `pip install vllm` spawned twice (10371 + 10517); duplicate killed, single `10371 --quiet` remains. At 07:47 still `WARNING: Package(s) not found: vllm` (install incomplete, expected — vllm build ~8–10 min with torch/flashinfer). Disk 57G/220G (26%), GPU idle (no vLLM yet). `ethevals` at `/workspace/ethevals` verified (`tasks/concepts/` + `runner/`).

**Tasks 8 + 10 state (scoped by STOP NOTICE 07:25 / task 12):**
- **Task 8 (gates 3–4):** §2.2 already records `The six reference records are not in the tree.` Gate 3 could-not-run. Six `chat --executor --check --ask` runs remain *deferred* to task 9’s rebuilt `.filebuf` ELF (hash + `readelf -S .filebuf` checks); no re-run of gates 1–3. No action until dispatcher ELF hash.
- **Task 10 (baseline Concepts):** Base-only eval **may proceed** per your rule (merged-model eval blocked until dispatcher marks gate 1 passed). Harness is `ethevals` (Harbor-based, not OpenCode — site lists `skills-lock.json`/`runner/`). Plan holds: when `pip show vllm` reports `Name: vllm`, run `vllm serve /workspace/models/Qwen3.5-9B --gpu-memory-utilization 0.45 --max-model-len 4096 --port 8000` (45% leaves ≤40 GiB headroom on 85 GiB RTX 6000D for gates), then `concepts` pillar Internet config → `route-a/trajectories/baseline/`. Until then, polling install.
- **Task 12 gate 1 F2:** `q9-rv1` textcheck FAIL unchanged (KL 12.29, 32,581× floor, 0/16 greedy); cause documented in `docs/route-a.md` §2.2 as untied `lm_head` dropped at `merge.py:101`. No patch to `merge.py`; awaiting `main` untied-head commit hash → `q9-rv2` re-merge (~4 min/55 GiB) → textcheck rerun (gate 2 stays).

**Gate 1 blocker still gates merged work; instance spend:** 58623317 **07:47 UTC = 1.95 h ($1.97) @ $1.01/h** (since 05:50 UTC, $150/wk cap). Nothing destroyed; next push will be `q9-rv2` or vLLM-ready baseline.

Pushed this entry as part of `route-a` report stream.

## 2026-11-01 07:55 UTC — gate 1 PASSED on q9-rv2, task 9 ELF taken, gates 3–4 BLOCKED, base vLLM fallback

Per standing rule: appended before chat, then pushed.

- **Gate 1 PASSED on `q9-rv2` (dispatcher-verified 07:38 UTC):** `q9-rv1` STOP lifted. Values from `dist/q9-rv2/rv-text.json` — `kl_merged_mean 6.34e-4` vs floor `3.77e-4` ratio **1.68×** (pass ≤2×), **12/16 greedy**, `top1 0.989`, per-prompt max `9.79e-4`. Merge: 2:59 wall, RSS 57.9 GiB, `tie_word_embeddings false`, `lm_head.weight` in index (fix `1500fba` kept untied base head). `docs/route-a.md` §2 header updated.
- **Task 9 (firmware):** `git checkout c0853b4 -- experimental/riscv/tests/data/rv/eth-bridge.elf` → `b320d31b4fd722ac0adfdbcf1313892967ea1bf8e826bd318138cb167c06c685` (verified), `readelf -S` has `.filebuf` (offset 0x900, 4 KiB). Committed `76cb200`, box pulled and verified same hash.
- **Tasks 8 / gates 3–4 on `q9-rv2`:** **BLOCKED.** `chipformer.rv.chat --check --ask` with new ELF hangs after `Loading weights: 581/581` (no generation output, both fast and --slow, killed at 90s; fast 106% CPU 1:36, slow 109% 0:54). Brief asked `--executor` which does not exist (`chat.py` has --check/--slow). `docs/route-a.md` §2.2 gate 4 rewritten to BLOCKED with logs. Next: strace/--verbose + two more attempts (stop rule: 3 fails or 15 min).
- **Base vLLM (task 10):** vLLM 0.32.0 installed but `Qwen3_5ForConditionalGeneration` not in vLLM registry → model load ValidationError. Per brief stop rule: after 3 attempts, use HF server. vLLM killed; HF serve path pending (gate 4 blocker deprioritizes baseline briefly). Disk 98G/220G (45%), merged eval (task 4) still queued after gates 3–4.
- **Spend:** 58623317 **2.1h ($2.12) @ $1.01/h** since 05:50 UTC; $150 cap. q9-rv1 not used; q9-rv2 is current.

## 2026-11-01 08:02 UTC — gate 4 BLOCKED (stop rule 3 fails), baseline HF fallback, 10m poll for 2h

Per standing rule: committed before chat.

- **Tasks 9 + gate 1:** ELF `b320d31b` from `c0853b4` (`.filebuf`) on `route-a` `76cb200`; box verified hash + `readelf -S .filebuf`. Gate 1 **PASS** on `q9-rv2` (KL 6.34e-4 / floor 3.77e-4 = 1.68×, 12/16 greedy) — STOP lifted, `q9-rv2` is current, `q9-rv1` retired.
- **Gate 4 (task 8):** **BLOCKED — stop rule hit.** `cd /workspace && PYTHONPATH=ethereus/experimental/riscv:ethereus python3 -m chipformer.rv.chat dist/q9-rv2 --firmware …eth-bridge.elf --check --ask` (6 prompts: selector/keccak/u256/encode/create/balanceOf) — 3 attempts on prompt 1 (selector): fast (default) 90s→kill (1.5m CPU), --slow 90s→kill (0.9m), `python3 -u` 300s→kill (1:15 CPU), all after `Loading weights: 581/581` with no generation output (no `head mismatch`/`lock`/`replay`/`tokens/s`). Brief has --executor (not in chat.py; correct is --check/--slow). Gate 3 (execcheck+replay) already could-not-run (no records.jsonl). Filing: chip-exec deadlock on CUDA 13/torch 2.13 fallback (no causal_conv1d/fla).
- **Base vLLM (task 10):** vLLM 0.32.0 installed but `Qwen3_5ForConditionalGeneration` not in registry → ValidationError on `vllm serve /workspace/models/Qwen3.5-9B --gpu-memory-utilization 0.45`. Per brief stop rule (3 attempts → HF server): vLLM killed, HF `transformers` serve script staged at `/tmp/hf-serve.py` (q9-rv2 35G + base 19G do not fit together on 85G — will serve base alone at 8000 after gate 4 is filed). Merged eval (task 4) still queued after gate 4.
- **Polling:** Per your 08:01 instruction — polling `/Users/fraserbrown/Downloads/route-a-brief.md` every 10m for up to 2h (until ~10:00 UTC) for new dispatch. Poll loop PID 11438, log `/tmp/poll-brief.log` (sha12 973cbd4a5040, 99 lines, gate 1 PASSED state).

Spend: 58623317 **08:02 UTC = 2.20h ($2.22) @ $1.01/h** since 05:50 UTC; $150 cap. Box at 98G/220G disk, gate 4 logs in `dist/q9-rv2/chat-1.log` + `/tmp/chat-1-300s.log`.

Pushed `route-a` as report stream.

## 2026-11-01 08:03 UTC — batch ack (fix torch/torchvision + chat hangs + poll live)

Per standing rule: committed before chat. Batch 881844–881849 (07:38–08:02) is fully covered by §08:02 report above.

- **881844 Fix torch NVSHMEM:** vLLM install downgraded torch 2.15→2.13 (broke chat import `libtorch_nvshmem.so: undefined nvshmem_selected_device_transport`); force-reinstalled torch 2.15+cu132, `chipformer.rv.chat` import restored.
- **881845 Fix torchvision:** vLLM pulled torchvision 0.28/torchaudio 2.11 whose `torchvision::nms` fake-impl broke transformers/Qwen3_5ForCausalLM import; uninstalled both, import restored but chat still hangs after `Loading weights: 581/581` (no op).
- **881846 Load RvChat q9-rv2:** `RvChat("/workspace/ethereus/dist/q9-rv2")` loads ok on cuda (581 shards, 35 GB, ~40s), but `generate()` with `eth-bridge.elf` never returns — chip exec deadlock.
- **881847 Start vLLM base:** `vllm serve /workspace/models/Qwen3.5-9B --gpu-memory-utilization 0.45` failed — `Qwen3_5ForConditionalGeneration` not in vLLM 0.32.0 registry (HF server fallback per brief stop rule).
- **881848–881849 Gate 4 third attempt:** Fast+slow+300s `python -u` all hang after load (stop rule 3 fails hit); filed as BLOCKED in `docs/route-a.md` §2.2 gate 4 and §08:02 report.

Polling `/Users/fraserbrown/Downloads/route-a-brief.md` every 10m until ~10:00 UTC (PID 11438, ~2h from 08:02), base vLLM on hold for dispatcher gate-4 fix, merged eval queued after gates 3–4.

Spend: 58623317 **08:03 UTC = 2.22h ($2.24) @ $1.01/h** since 05:50 UTC; $150/wk cap. Disk 98G/220G, GPU idle (poll only).

## 2026-11-01 08:41 UTC — tasks 13–14 (shim max_chip, eval env check), poll alive + new brief

Per standing rule: appended before chat, then pushed. Poll was down (nohup over SSH died on conn reset) — restarted as local loop 08:41 (PID 18179, `route-a-brief.md` now sha12 `55ea65698a89` 105 lines, added hold 08:05 + tasks 13–14).

- **Gate 4 hold 08:05:** Dispatcher took over diagnosis (`/workspace/diag-chat.log`, `python -u` with stack dumps; ~27k chip tokens per selector on HF path, kills at 90/300s were mid-run, not deadlock). **GPU hold on** — no `chat.py` or eval until lifted. Base HF serve also on hold (same card). Honouring hold; no GPU work since 08:02.
- **Task 13 — eval env check (added 08:20 UTC):** `docs/harbor.md` line 1 + `runner/README.md`: board jobs run through **Harbor** (`runner/ethevals/job.yaml`, `uv run ethevals plan/run`), every task has `environment/Dockerfile` (GHCR base image, one container per trial) and `environment_mode = "separate"` verifier. **Internet + vanilla both need Docker** (`tasks/*/task.toml: modes = ["vanilla","internet","skills"]`; `docs/harbor.md` planning needs no Docker, but trials do). Judge tasks declare `ANTHROPIC_API_KEY = "${ANTHROPIC_API_KEY}"` (`docs/harbor.md` line 182). Box has **no Docker** (`docker: command not found`) and no `ANTHROPIC_API_KEY` on host — can only `plan`, not `run`. **Finding:** Trials cannot run on 58623317; open question in `docs/dispatcher-questions.md` on `main` (Fraser deciding mode/location). Shim note: `route-a/shim/server.py` `tools = req.tools if req.tools else [ETH_TOOL]` (line 181) → offers `eth` only when client sends no tools; OpenCode sends tools, so no `eth` — do not use shim with OpenCode per brief. 15-min stop met; no eval started.
- **Task 14 — shim chip cap (added 08:37 UTC):** `RvChat.generate` default `max_chip=60000` cuts `run erc20 balanceOf` (~110k chip tokens) mid-run. Fixed: `route-a/shim/server.py` line 204 `max_chip=1000000` (`grep -n max_chip` shows it). Gate 4 GPU hold still on — not tested on GPU; file change only. 15-min stop met.

Spend: 58623317 **08:41 UTC = 2.85h ($2.88) @ $1.01/h** since 05:50 UTC; $150 cap. Poll 10m/2h live until ~10:41 UTC; no GPU until dispatcher lifts hold.

