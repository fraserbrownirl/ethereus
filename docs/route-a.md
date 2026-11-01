# Route A — scale the Ethereus backbone and get an ETH Evals number

> Branch: `route-a` (commit `9dee31a`). Circuit commit: `9dee31a` (P3 rv-q4p2, 11 chip layers). Firmware: `tests/data/rv/eth-bridge.elf` SHA-256 `8a49492d9d27c81b577efd1f4b5bb95fbb46f2d2fa2364f7e15792d25cd736d9` (239 KB, `file` = ELF 32-bit LSB executable, RISC-V, `readelf` shows `ROM_BASE 0x40000`).

---

## 1. Backbone selection (Task 1)

**Date:** 2026-11-01. **Runner:** local macOS (M-series, 51 GB RAM, no NVIDIA GPU; `nvidia-smi` not found). Remote hosts `podminer-vps`/`hostinger-vps` have no GPU; `podbank-vast` unreachable. No 80 GB A100/H100 provisioned at report time — VRAM fit is calculated, not measured.

### 1.1 Enumeration

`HuggingFace API` search `Qwen3.5` (2026-11-01) returned 22 repos. Exhaustive dense + MoE set on `Qwen/` org:

- Dense: `Qwen/Qwen3.5-0.8B`, `Qwen/Qwen3.5-2B`, `Qwen/Qwen3.5-4B`, `Qwen/Qwen3.5-9B`, `Qwen/Qwen3.5-27B`
- MoE: `Qwen/Qwen3.5-35B-A3B` (active 3B), `Qwen/Qwen3.5-122B-A10B` (active 10B), `Qwen/Qwen3.5-397B-A17B` (not in scope, 397B not enumerated)
- Instruction-tuned and FP8/GPTQ variants exist but are excluded per brief (base only; `*FP8`, `*GPTQ-Int4`, `*Instruct`, `*Base` aliases resolve to same config).

No `Qwen/Qwen3.5-1.7B`, `-14B`, `-32B` repos exist (HTTP 401/404 on 2026-11-01).

### 1.2 Config.json inspection

Each `text_config` read via `hf_hub_download(..., "config.json")` (SHA shown):

| Model (HF repo) | HF SHA | Arch | `num_hidden_layers` | `hidden_size` (d0) | `layer_types` | DeltaNet? | `linear_num_key_heads` | `linear_num_value_heads` | `value/key` | `head_dim` | `num_attention_heads` | `num_key_value_heads` | `intermediate_size` | `model_type` |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `Qwen/Qwen3.5-0.8B` | `2fc0636` | `Qwen3_5ForConditionalGeneration` | 24 | 1024 | `linear_attention`×18 + `full_attention`×6 (`full_attention_interval=4`) | Yes (Gated DeltaNet + full) | 16 | 16 | **1.0** | 256 | 8 | 2 | 3584 | `qwen3_5` |
| `Qwen/Qwen3.5-2B` | `d9e8f2a`* | `Qwen3_5ForConditionalGeneration` | 24 | 2048 | 18L+6F | Yes | 16 | 16 | **1.0** | 256 | 8 | 2 | 6144 | `qwen3_5` |
| `Qwen/Qwen3.5-4B` | `851bf6e` | `Qwen3_5ForConditionalGeneration` | 32 | 2560 | 24L+8F | Yes | 16 | 32 | **2.0** | 256 | 16 | 4 | 9216 | `qwen3_5` |
| `Qwen/Qwen3.5-9B` | `9c1a3d7`* | `Qwen3_5ForConditionalGeneration` | 32 | 4096 | 24L+8F | Yes | 16 | 32 | **2.0** | 256 | 16 | 4 | 12288 | `qwen3_5` |
| `Qwen/Qwen3.5-27B` | `fc05dae` | `Qwen3_5ForConditionalGeneration` | 64 | 5120 | 48L+16F | Yes | 16 | 48 | **3.0** | 256 | 24 | 4 | 17408 | `qwen3_5` |
| `Qwen/Qwen3.5-35B-A3B` | `a3f7c22`* | `Qwen3_5MoeForConditionalGeneration` | 40 | 2048 | 30L+10F | Yes (MoE) | 16 | 32 | 2.0 | 256 | 16 | 2 | MoE (`moe_intermediate_size=512`, `num_experts=256`, `experts_per_tok=8`, `shared=512`) | `qwen3_5_moe` |
| `Qwen/Qwen3.5-122B-A10B` | `b8e41fa`* | `Qwen3_5MoeForConditionalGeneration` | 48 | 3072 | 36L+12F | Yes (MoE) | 16 | 64 | 4.0 | 256 | 32 | 2 | MoE (`moe_intermediate_size=1024`, `num_experts=256`, `experts_per_tok=8`) | `qwen3_5_moe` |

\* SHA truncated from `model_info().sha` (unauthenticated HEAD 307 redirect seen for 0.8B/4B/27B).

Constraint 1 (**Gated DeltaNet layout**): all dense rows have `layer_types` with `linear_attention` majority and `full_attention_interval=4`; confirmed via `transformers` `Qwen3_5GatedDeltaNet` (`in_proj_qkv/z/a/b`, `conv1d` kernel=4, `A_log`/`dt_bias`/`norm`/`out_proj` per linear layer). MoE rows share identical DeltaNet shapes but use `Qwen3_5Moe*`.

Constraint 2 (**`hidden >= 2816` emitted standalone width**): the P2 circuit's standalone hidden (emitted without Qwen) is 2816 (`docs/rv32.md:195`, 11 layers, 1,040,595 nonzeros). Merged hidden is chosen by `chipformer/q35/merge.py:pick_hidden(d0, 1+alloc.n_slots)` with `alloc.n_slots=1265` (circuit built with `core.build(Target(k,v,inter), Text)` for each candidate):

| Base `d0` | `pick_hidden(d0, 1266)` → `hidden_merged` | `m` (ballast dims) | `hidden_merged >= 2816`? |
|---|---|---|---|
| 1024 (0.8B) | 2304 | 9 | **No** (also violates `value/key`) |
| 2048 (2B) | 3328 | 13 | Yes |
| 2560 (4B) | 3968 | 62 | Yes |
| 4096 (9B) | 5632 | 22 | Yes |
| 5120 (27B) | 6656 | 26 | Yes |
| 2048 (35B-A3B MoE) | 3328 | 13 | Yes but MoE unsupported |
| 3072 (122B) | 4608 | 18 | Yes but MoE unsupported |

Constraint 3 (**`value_heads / key_heads >= 2`**): `ir.py:Head` asserts `len(vx) < target.value_heads // target.key_heads`; the core's ROM heads use `vx` with 2 value heads per key. 0.8B/2B have `v/k=1.0` → `core.build(Target(16,16,…))` raises `AssertionError: more value heads than the key head has` (confirmed on 2026-11-01). 4B/9B (`2.0`) and 27B (`3.0`) pass.

### 1.3 VRAM budget (batch-1 generation, 4096-token static budget)

Per plan: **weights + fla recurrent state (every DeltaNet layer) + static KV cache (every full-attention layer) + ~10 GB headroom ≤ 80 GB**. Calculation at `dtype=bfloat16` (2 bytes/param); recurrent state and KV are bf16. If kernels hold state in fp32 add ~2× on the state line — still <1 GB.

- **Weights (merged):** `model.safetensors.index.json` → `metadata.total_size` (bf16 bytes) for base; merged estimated via parametric widening model (`chipformer/q35/merge.py` + `chipformer/q35/emit.py` `Layout` widening rules `IN_COLS`/`OUT_ROWS`/`WIDE_NORMS`, 11 chip layers, vocab padded to `⌈(v0+S.VOCAB)/64⌉·64` with `S.VOCAB=230324`). Calibrated against measured `divagr1925/Qwen3.5-4B-Qwenputer` merged `total_size=18,715,204,992` (18.72 GB) vs estimate 19.53 GB (ratio 0.958). Calibrated column shown.

- **Recurrent state:** per linear layer `v_heads · k_head_dim(128) · v_head_dim(128) · 2 B`. Chip layers inherit base's `v_heads`/`k_dim`/`v_dim` via `Target`. `merged_linear = base_linear + 11`.

- **KV cache:** per full-attention layer `2 · num_kv_heads · head_dim(256) · 4096 · 2 B`. `merged_full = base_full`.

| Model | Params (base) | bf16 size (base) | `value/key` | `hidden_merged` | Chip layers | Merged weights (est. / calibrated) | Merged `total_layers` | `merged_linear` | Recurrent state | KV cache (4096) | **Weights+state+KV** (calibrated) | Fits 80 GB with ~10 GB headroom? |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `Qwen/Qwen3.5-0.8B` | 0.87 B | **1.75 GB** | 16/16=1.0 | 2304 | 11 | 5.72 / 5.48 GB | 35 | 29 | 0.015 GB | 0.050 GB | **1.81 GB** | ❌ FAIL (`value/key<2`, `hidden_merged<2816`) |
| `Qwen/Qwen3.5-2B` | 2.27 B | **4.55 GB** | 16/16=1.0 | 3328 | 11 | 10.50/10.06 GB | 35 | 29 | 0.015 GB | 0.050 GB | **4.61 GB** | ❌ FAIL (`value/key<2`) |
| `Qwen/Qwen3.5-4B` | 4.66 B | **9.32 GB** | 32/16=2.0 | 3968 | 11 | 19.53/**18.72 GB** (measured) | 43 | 35 | 0.037 GB | 0.134 GB | **9.49 GB** (18.89 GB merged) | ✅ Yes (documented 31 GB merge RAM) |
| **`Qwen/Qwen3.5-9B`** | **9.65 B** | **19.31 GB** | **32/16=2.0** | **5632** | **11** | **34.36/32.92 GB** | **43** | **35** | **0.037 GB** | **0.134 GB** | **19.48 GB base / 33.09 GB merged (calibrated)** | **✅ Yes** |
| `Qwen/Qwen3.5-27B` | 27.78 B | **55.56 GB** | 48/16=3.0 | 6656 | 11 | 84.66/**81.10 GB** | 75 | 59 | 0.093 GB | 0.268 GB | **55.92 GB base / 81.46 GB merged** | ❌ No (merged >80 GB even before headroom; needs ~91 GB with 10 GB headroom) |
| `Qwen/Qwen3.5-35B-A3B` | 35 B (3 B active) | **71.90 GB** | 32/16=2.0 (MoE) | 3328 | — | — (MoE unsupported) | 51 | 41 | — | — | **≥72 GB base before experts** | ❌ No (MoE: `intermediate_size=None`, `merge.py` expects dense `inter`; 256 experts unsupported; also vLLM MoE sparse fit untested) |
| `Qwen/Qwen3.5-122B-A10B` | 122 B (10 B active) | **250.17 GB** | 64/16=4.0 (MoE) | 4608 | — | — | 59 | 47 | — | — | **>>80 GB** | ❌ No |

On-disk merged growth (dense): 9B 19.31 → 32.9 GB (+13.6 GB; 3.3 GB chip-layer zeros, 2.5 GB Qwen padding, 1.7 GB vocab extension, + alignment). Disk budget ~200 GB is fine; 27B merged 81 GB would still fit disk but not 80 GB VRAM.

### 1.4 Pick

**Selected backbone: `Qwen/Qwen3.5-9B` (base `Qwen/Qwen3.5-9B`, 9.65 B params, hidden 4096 → 5632 merged, 32 layers → 43 merged, `value/key = 2.0`, 11 chip layers).**

- It is the **largest dense Gated DeltaNet backbone that (a) satisfies all three merge constraints and (b) fits 80 GB VRAM at batch-1 / 4096 tokens with ~10 GB headroom to spare** (≈47 GB headroom on merged weights; ≈46 GB after KV+state). 4B also fits but is not maximal. 27B's merged weights alone exceed the 80 GB card (81.1 GB calibrated, 85.0 GB raw estimate; +10 GB headroom → 91 GB), matching the brief's warning that a 30 B-class bf16 model is ~60 GB before chip layers and the pick may land on 14B–27B.
- MoE sizes (35B-A3B, 122B-A10B) are disqualified: `merge.py` has no MoE path (`tc0["intermediate_size"]` is `None`, `inter` is required for `Target`; `emit.py` has no expert sharding), and the 35B base already exceeds 70 GB.
- Tie-break rule (larger `hidden`) not triggered; 9B is strictly larger than 4B.

**Base HF location:** `Qwen/Qwen3.5-9B` (`hf_hub_download` cache, `config.json` from `model.safetensors.index.json` total_size `19306216416`).
**Merge output (planned):** local `~/wrv2/dist/q9-rv1/` (or project `dist/q9-rv1/`), shard pattern `model-00001-of-0000N.safetensors`.

---

## 2. Merge and gate (Task 2)

### 2.1 Merge

**Command:** `python -m chipformer.rv.merge Qwen/Qwen3.5-9B --out dist/q9-rv1` (README verbatim). Records peak RAM via `/usr/bin/time -v` and wall time.

| | Value |
|---|---|
| Status | **Could not run — no execution environment with required resources** |
| Reason | Local host is macOS M-series (51 GB unified RAM, no NVIDIA GPU; `nvidia-smi` not found). Merge is CPU/RAM-bound but needs the 9B base weights (19.31 GB) on disk first; local download was stopped per operator (19 GB over unauthenticated connection). Remote hosts `podminer-vps`/`hostinger-vps` have no GPU; `podbank-vast` unreachable. No 80 GB GPU host provisioned for gates/evals. |
| Circuit (verified) | `circuit {layers: 11, dims: 2918, heads: 103, ffn_units: 42114, max_heads: 16, max_units: 8149}, slots 1265, hidden 5632 (m=22), 11 chip layers` — confirmed on 2026-11-01 via `core.build(Target(16,32,12288))` for `Qwen/Qwen3.5-9B` (same as 4B; circuit is model-size-independent for dense Qwen3.5). |
| Expected VRAM (merged) | 32.9 GB weights + 0.17 GB state+KV = 33.1 GB @4096; well under 80 GB. Gates require GPU (textcheck/gpucheck/execcheck/replay/chat) and must be run on a provisioned host. |
| Next step | Provision a GPU host (≥64 GB RAM, 80 GB VRAM) with authenticated HF access. On that host: `snapshot_download("Qwen/Qwen3.5-9B")` (19 GB), `python -m chipformer.rv.merge <base> --out dist/q9-rv1`, then gates §2.2. Record peak RAM (`/usr/bin/time -v` or `psutil`) and wall time. |

### 2.2 Gates

All four gates are **could not run** on 2026-11-01 (no merged checkpoint). Expected wiring and pass criteria are pre-registered:

1. **`rv/textcheck.py` (16 prompts):** `KL(merged||base)` vs `KL(base sdpa||eager)` noise floor; pass = within 2×. Prior P3 value on 4B: merged KL `6.1e-4`, floor `5.1e-4` (ratio 1.2×). Will record per-prompt KL and greedy-match count.
2. **`rv/gpucheck.py --decode`:** bf16 bit-exactness across circuit dims on fla kernels (`chunk_gated_delta_rule` + `fused_recurrent_gated_delta_rule`). Pass = bit-exact on every IR dim at every position. This is the route's stop risk (wider `5632` changes fla tiling; 4B at `3968` is bit-exact). On failure, will record `dims`/`positions` and stop per hard rule.
3. **`rv/execcheck.py` + `rv/replay.py` (6 reference records):** 0 lock errors, 0 head mismatches. `bridge.py` ownership model.
4. **`rv/chat.py --firmware tests/data/rv/eth-bridge.elf --ask …` (6 prompts):** `selector of transfer(address,uint256)`, `keccak`, `u256`, `encode`, `create`, `run erc20 balanceOf`; each replayed → clean. Plus `rv/eth.py --cases` spot-check.

No local `tokens/s` measured. P3 reference on L4: ~250 chip tok/s (HF) / 3,200 (executor); text tok ~68 ms at batch-1.

### 2.3 Shim (Task 4 dependency, built in merge window)

Per plan, `route-a/shim/` (thin OpenAI-compat `/v1/chat/completions` → `RvChat.generate` with `eth` tool schema) must be written and smoke-tested against the existing 4B merged model on any 24 GB card while the big merge runs. **Shim scaffold committed** at `route-a/shim/` (see §4); smoke test **could not run** (no GPU). **2026-11-01 fix:** shim had three blocking bugs — wrong `RvChat(..., firmware=...)` kwarg (class takes no firmware), missing `machine` argument to `generate()`, and concatenated messages stripped the chat template. Fixed to `RvChat(path)` + per-request `ref.Machine` from `eth-bridge.elf` + `BR.Session` lock + `prompt_ids(msgs, tools=[ETH_TOOL])` where `ETH_TOOL` is `name=eth, cmd: string` matching the `eth` firmware's `<function=eth><parameter=cmd>` capture. Validated `python -m py_compile`.

---

## 3. Baseline eval (Task 3)

| Field | Value |
|---|---|
| Status | **Could not run** (blocked on §2 GPU host) |
| Planned serving | Base `Qwen/Qwen3.5-9B` via **vLLM** OpenAI-compat `http://localhost:8000/v1` (`--dtype bfloat16`, `--max-model-len 4096`, `--gpu-memory-utilization 0.90`, `temperature=0`, determinism check: two identical prompts must return identical `choices[0].message.content`) |
| Harness | **OpenCode** (BuidlGuidl `ethevals` + `buidlguidl/ethevals-test`) — pillar *Concepts*, 25 tasks, *Internet* config, default trial count from `ethevals` (to be recorded from `harness --help` at run time) |
| Trajectories | `route-a/trajectories/baseline/` (verbatim, one JSON per task/trial as harness writes) |
| Score | `passed/scored (errors excluded)` as harness computes (e.g. `18/25 = 72%`) — to be filled |
| Next step | Start baseline in parallel with merge (plan § Verification): `pip install vllm opencode` on GPU host, `opencode --endpoint http://localhost:8000/v1 --pillar concepts --mode internet --out route-a/trajectories/baseline --save-trajectories verbatim` |

---

## 4. Ethereus eval (Task 4)

| Field | Value |
|---|---|
| Status | **Could not run** (blocked on §2 gates) |
| Planned serving | Merged `dist/q9-rv1` behind shim `route-a/shim/server.py` (`RvChat(model_path, firmware="tests/data/rv/eth-bridge.elf")`, OpenAI-compat, `temperature=0`) |
| Harness | Same 25 Concepts tasks, same Internet config/trial count; trajectories `route-a/trajectories/ethereus/` unedited |
| Skills variant | Re-run as Internet+Skills if `ethskills.fs` exists on `main` (check `ls main:ethskills.fs 2>/dev/null`); else skip and note |
| Shim | `route-a/shim/server.py` implements `ETH_TOOL` (`name=eth`, `cmd` string → `<function=eth><parameter=cmd>…>`). Per-request `elf.program(FIRMWARE)` → `BR.park(m)` → `prompt_ids(msgs, tools=[ETH_TOOL])` → `generate(ids, m, lock=Session)`. Smoke test: `MODEL=dist/q4-rv1 ./route-a/shim/test_smoke.sh` — pending GPU. **2026-11-01:** fixed 3 bugs (wrong RvChat kwargs, missing machine arg, broken prompt templating). |
| Next step | Once gate 4 is green, `./route-a/shim/run.sh &` then `opencode --endpoint http://localhost:8001/v1 ... --out route-a/trajectories/ethereus` |

Shim files staged:

```
route-a/shim/
  server.py      # FastAPI app, POST /v1/chat/completions → RvChat.generate
  requirements.txt
  README.md      # how to launch + curl smoke test
  test_smoke.sh  # curls /v1/models, /v1/chat/completions with eth tool
```

---

## 5. Report and checkpoint (Task 5)

| Artifact | Path / link | SHA-256 |
|---|---|---|
| Merged checkpoint (planned) | `dist/q9-rv1/` (shards `model-00001-of-00005.safetensors` …) | `sha256sum dist/q9-rv1/model-*.safetensors` — *pending merge* (record per-shard in this table once written) |
| Model card (deferred) | `ethereus/Qwen3.5-9B-Ethereus` on HF (publish deferred per operator; checkpoint kept on-box) | Must state: base `Qwen/Qwen3.5-9B`, circuit commit `9dee31a`, firmware hash `8a49492d…736d9`, four gate results, KL, speed, Apache-2.0 |
| Trajectories | `route-a/trajectories/baseline/` + `route-a/trajectories/ethereus/` (verbatim) | `sha256sum` per file |
| This report | `docs/route-a.md` (this file) | — |
| GPU + wall time | See §6 |  |

### Checkpoint retention (per operator instruction)

Do **not** push to HF yet. Keep on-box, record per-shard SHA-256 in this report, and publish later as a launch step. Publish target: `ethereus/Qwen3.5-9B-Ethereus` (Apache-2.0, model card with base/circuit/firmware/gates/KL/speed).

---

## 6. Per-step GPU + wall time

All steps beyond Task 1 and shim are **could not run** at 2026-11-01T14:05+04 (week clock, Task 1 complete). 9B merge requires authenticated HF download on a GPU host with ≥64 GB RAM (see §2.1). No download will be started from this Mac.

| Step | GPU | Wall time | Peak RAM/VRAM |
|---|---|---|---|
| Backbone enumeration | MacBook (no GPU) | 1.5 h | 51 GB RAM |
| Shim fix (3 bugs → ETH_TOOL + RvChat wiring) | MacBook | 0.5 h | — |
| Merge 9B | H100 80 GB host (to be provisioned) | Est. 2–6 h (4B: ~45 min/31 GB) | Est. 40–60 GB RAM (streams shards), 33 GB VRAM |
| textcheck (16 prompts) | H100 80 GB (provisioned) | Est. 0.5 h | VRAM < 40 GB |
| gpucheck --decode | same | Est. 1 h | VRAM < 40 GB |
| execcheck + replay (6 records) | same | Est. 0.5 h | VRAM < 40 GB |
| chat 6 prompts | same | Est. 0.5 h | VRAM < 40 GB |
| Baseline Concepts (25 tasks) | same (vLLM) | Est. 12–24 h (default trials × slow tool use) | VRAM 33 GB base, det. at temp 0 |
| Shim smoke (4B) | L4 24 GB | Est. 1 h | 19 GB |
| Ethereus Concepts (25 tasks) | same shim | Est. 12–36 h (chip bursts add) | 33 GB merged |
| Report + SHA | MacBook | 0.5 h | — |

### Failures / findings

- No failures on Task 1; table + VRAM budget are complete and reproducible via `experimental/riscv/.venv/bin/python` + unauthenticated HF Hub. Circuit for 9B verified (`dims 2918`, `heads 103`, `slots 1265`, `hidden 5632`). Attempted local 9B download stopped per operator — no 19 GB pull over unauthenticated connection; merge must run on a provisioned host.
- **Shim bugs fixed (2026-11-01):** `RvChat(firmware=…)` does not exist (firmware is per-request `ref.Machine`), `generate()` requires `machine` positional, and prompt construction must use `prompt_ids(msgs, tools=[ETH_TOOL])` not raw concatenation. Fixed in `route-a/shim/server.py`.
- MoE merge unsupported: `chipformer/rv/merge.py:build_merged` reads `text_config.intermediate_size` (MoE has `None`); no expert sharding exists. Filing as **F1: MoE merge path missing** — needed only if a future pick were MoE.
- No 80 GB runner configured for gates/evals. Filing as **Ops: provision runner with HF_TOKEN + Vast/Lambda credentials**. Gates `2–4` require CUDA (textcheck, gpucheck --decode, execcheck, chat).

---

## 7. Verification checklist (from plan)

- [x] `config.json` for every Qwen3.5 size read; table filled with `hidden`, `value/key`, `bf16 size`, DeltaNet presence
- [x] VRAM computed as **weights + fla recurrent state + static KV cache @4096**, ~10 GB headroom left
- [x] Largest fitting row picked (`Qwen/Qwen3.5-9B`); tie-break pre-registered
- [ ] Merge artifact freshness: `python -m chipformer.rv.merge` → `git diff --exit-code` (pending)
- [ ] `textcheck` KL ≤ 2× noise floor (pending)
- [ ] `gpucheck --decode` bit-exact (pending — stop risk)
- [ ] `execcheck`/`replay` 0 errors, `chat` 6 prompts clean, replay clean (pending)
- [ ] Baseline trajectories verbatim, `temperature=0` determinism check (pending)
- [ ] Ethereus trajectories verbatim behind shim (pending)
- [ ] Per-shard `sha256sum` in report, checkpoint path cited, `docs/route-a.md` + trajectory links published (this file is the stub; will be updated with numbers)

---

## Appendix A: Repro commands

```bash
# Task 1 — enumerate (run from repo root with experimental/riscv venv)
experimental/riscv/.venv/bin/python - <<'PY'
import sys, json, pathlib
sys.path.insert(0, 'experimental/riscv')
from huggingface_hub import hf_hub_download
from chipformer.q35.merge import pick_hidden
for mid in ["Qwen/Qwen3.5-0.8B","Qwen/Qwen3.5-2B","Qwen/Qwen3.5-4B","Qwen/Qwen3.5-9B","Qwen/Qwen3.5-27B","Qwen/Qwen3.5-35B-A3B","Qwen/Qwen3.5-122B-A10B"]:
  cfg=json.loads(pathlib.Path(hf_hub_download(mid,"config.json")).read_text())
  tc=cfg.get("text_config", cfg)
  print(mid, tc["hidden_size"], tc["linear_num_value_heads"]/tc["linear_num_key_heads"])
PY

# VRAM estimate (see §1.3 formula)
# Recurrent = merged_linear * v_heads*128*128*2, KV = merged_full*2*kv_heads*256*4096*2

# Task 2 — merge (on GPU host)
# huggingface-cli login --token $HF_TOKEN
# python -m chipformer.rv.merge /cache/Qwen3.5-9B --out dist/q9-rv1 2>&1 | tee dist/q9-rv1/build.log
# /usr/bin/time -v python -m chipformer.rv.merge ...   # or psutil

# Gates
# python -m chipformer.rv.textcheck dist/q9-rv1 /cache/Qwen3.5-9B
# python -m chipformer.rv.gpucheck --decode dist/q9-rv1
# python -m chipformer.rv.execcheck dist/q9-rv1 --records tests/data/rv/records.json
# python -m chipformer.rv.replay  dist/q9-rv1 --records tests/data/rv/records.json
# python -m chipformer.rv.chat dist/q9-rv1 --firmware tests/data/rv/eth-bridge.elf --ask "Use eth: what is the selector of transfer(address,uint256)?"
```

---

*Generated on 2026-11-01 for branch `route-a` plan `46eb9f38`. Numbers above are from `hf_hub_download` + `ir.py`/`emit.py`/`merge.py` inspection; downstream gate/eval numbers will be filled from runs only.*

