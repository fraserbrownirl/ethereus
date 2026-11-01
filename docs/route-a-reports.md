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

