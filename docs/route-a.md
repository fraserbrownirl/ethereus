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
| Status | **Done on Vast.ai 58623317 (RTX 6000D 85 GB, 629 GiB RAM) — 2026-11-01 06:44:52 UTC** |
| Command | `cd /workspace && PYTHONPATH=ethereus/experimental/riscv:ethereus python3 -m chipformer.rv.merge /workspace/models/Qwen3.5-9B --out /workspace/ethereus/dist/q9-rv1` |
| Output | `circuit {layers: 11, dims: 2918, heads: 103, ffn_units: 42114, max_heads: 16, max_units: 8149}, slots 1265, hidden 5632 (m=22), 11 chip layers` — `{"hidden": 5632, "chip_layers": 11, "slots": 1265, "circuit": {"layers": 11, "dims": 2918, "heads": 103, "ffn_units": 42114, "max_units": 8149, "max_heads": 16}}` |
| `/usr/bin/time -v` | Wall 3:55.16, User 533.20s, System 589.32s (477% CPU), max RSS 54,793,460 kB (52.3 GiB), 44M minor faults, 570 major, FS 23M in / 60M out |
| Chipformer.json | `hidden 5632 (d0 4096 + 22 ballast), chip_dims_offset 4096, chip_token_offset 248320, chip_vocab 230324, qwen_layers_from 11, muted true, code_dims [4250..4270], tau 4273 mu 4272, nfam 5 npay 16` |
| On-disk | 13 shards `model-00001-of-00013` … `model-00013-of-00013` (29 GB), `sha256sums.txt` on box (first: `66cff6b8…`); `config.json` `hidden_size 5632` 43 layers (11 chip + 32 Qwen) |

### 2.2 Gates

**Current gate results: eval VM, pinned venv. These supersede every result below.** VM 58642243, RTX PRO 6000 Blackwell WS 97 GB, driver 580.95.05. Venv `/workspace/venv`, lock file `route-a/gates/venv.lock.txt`: torch 2.15.0+cu132, transformers 5.22.1, fla 0.6.0, triton 3.9.0. Checkpoint `dist/q9-rv2` re-merged there: exit 0, 1:20 wall, 58.5 GB peak RSS. ELF `b320d31b`. Run by `route-a/gates/run_gates.sh`.

| Gate | Result | Numbers | Source on the VM |
|---|---|---|---|
| 1, text fidelity | PASS | KL 4.592e-4, floor 4.592e-4, ratio 1.0001 (limit 2x), greedy identical 12/16 | `dist/q9-rv2/rv-text.json` |
| 2, bf16 on fla | PASS | 18/18 exact, 15,108 positions, 0 mismatches | `dist/q9-rv2/gpucheck.log` |
| 3, replay of gate 4's records | PASS | 0 wrong predictions on all six | `dist/q9-rv2/gate4.log` |
| 4, inside Qwen | PASS | 6/6, 0 head mismatches, no lock errors | `dist/q9-rv2/gate4.log`, `chat-<n>.log` |

Gate 4 recheck after `logits_to_keep=1` (`dbaf773`): PASS, 6/6 clean, 0 wrong on replay, every text and chip token count equal to the run above. Source: `dist/q9-rv2/gate4-keep1/gate4.log`.

The ratio's closeness to 1 is chance: per-prompt ratios run from 0.72 to 1.42 and no pair is equal. Earlier results below came from Vast 58623317, whose Python packages changed between gates when vLLM was installed, so they did not share one environment.

**Gates 1–4 PASS on `dist/q9-rv2` — dispatcher-verified 08:58 UTC. `dist/q9-rv1` is retired.**

| Gate | Result | Key numbers | Log |
|---|---|---|---|
| **1 textcheck** | **PASS** | KL merged 6.34e-4, floor 3.77e-4, ratio **1.68× ≤2×**; greedy **12/16**, top-1 0.989, per-prompt max 9.79e-4 | `dist/q9-rv2/rv-text.json` |
| **2 gpucheck --decode** | **PASS (fla)** | **18/18 exact**, **15,108 positions**, `kernels: fla` | `dist/q9-rv2/gpucheck-fla2.log` |
| **3 execcheck+replay** | **PASS** | **0 wrong** on **6,028 / 53,917 / 14,790 / 18,314 / 58,205 / 114,627** owned (replay of gate-4 records; no `records.jsonl` in tree) | gate-4 replay |
| **4 chat+replay** | **PASS** | 6/6: **head mismatches 0, lock error None, `ok: True` 0 wrong** (6c is `run erc20` 114,544 chip tok) | `dist/q9-rv2/chat-1..5.log`, `chat-6c.log` |

**Gate 1 F2 (history):** `Qwen/Qwen3.5-9B` untied (`tie_word_embeddings: false`); `q9-rv1` dropped `lm_head.weight` (KL 32,581×). Fix `1500fba` keeps base head on Qwen rows/dims + embedding chip/RV32 rows. `q9-rv2` merge 2:59.74 wall, RSS 57.9 GB, `tie_word_embeddings false`, `lm_head.weight` in index.

**Gate 2:** dispatcher reran on fla 0.6.0 / CUDA 13 / torch 2.15 → 18/18 exact, 15,108 positions (signext 45/45, call 475/475, memcpy 160/160, rv32ui-lb 193/193, rv32ui-sh 624/624, rv32ui-jalr 100/100, rv32ui-sra 717/717, c-crc32 4166/4166, c-strops 1074/1074). My earlier fla hang (no output, killed 6 min) did not reproduce.

**Gate 3:** no `*.jsonl` records in `experimental/riscv/tests/data/rv/` (and nowhere in repo per `git log`/`find`); dispatcher confirms gate 3 is the replay of gate 4's records — 0 wrong on the six owned counts above.

**Gate 4 (6 prompts, ELF `b320d31b4fd722ac0adfdbcf1313892967ea1bf8e826bd318138cb167c06c685` from `c0853b4`, `.filebuf` PROGBITS 0x900 4 KiB):**
- `chat-1.log` (selector `transfer(address,uint256)`) 76t+6006c 6.0s — `0xa9059cbb`, replay 6028 owned 0 wrong.
- `chat-2.log` (`keccak 0x6161`) 191t+53839c 32.2s — `0xdfa57c…684f`, 53917 owned 0 wrong.
- `chat-3.log` (`u256 0xffffffff * 0xffffffff`) 101t+14758c 9.8s — `18446744065119617025`, 14790 owned 0 wrong.
- `chat-4.log` (`encode transfer … 0xdEaD 100`) 439t+18164c 26.0s — calldata `0xa9059cbb…dead…0064`, 18314 owned 0 wrong.
- `chat-5.log` (`create 0xdEaD 1`) 225t+58151c 35.2s — `0x2387b338…2642`, 58205 owned 0 wrong.
- `chat-6c.log` (`run erc20 0x70a08231…aaaa…` via `/workspace/gate4c.py` with `RvChat(..., executor=True)`, `max_chip=1000000`, full `eth` schema) 304t+**114544c** 68.5s — `0x00…00` + `2541`, **114627 owned 0 wrong** — gate 4 PASS for `run erc20`. `chat-6.log` (calc-only schema, no `run` → model did not call `run`) and `chat-6b.log` (60k chip cap → truncation at 60590) are **expected non-passing illustrations**, not gate failures.

**Correction:** my "chip exec deadlock" filing (08:02) was wrong — cause was **calc-only schema** (`chipformer/rv/tools.py` `CALCULATOR_TOOL` lacks `run`; gate 4 needs full firmware schema via `firmware_tools(F)`) + mid-run kills (selector ≈6000 chip tok / 6s on HF path; my 90s/300s kills were still generating). Withdrawn.

**Firmware:** `tests/data/rv/eth-bridge.elf` at sha `b320d31b…` / commit `c0853b4`, `.filebuf` 0x900 4 KiB, in `route-a` as `76cb200`.

1. **`rv/textcheck.py` on `dist/q9-rv1` — FAIL (history, superseded).** `cd /workspace && PYTHONPATH=... python3 -m chipformer.rv.textcheck /workspace/models/Qwen3.5-9B /workspace/ethereus/dist/q9-rv1 --out rv-text.json` → KL 12.29 / floor 3.77e-4 = 32,581×, greedy 0/16, top1 0.0. **F2 untied-head** cause verified; gate is now the `q9-rv2` PASS row above (1500fba).

2. **`rv/gpucheck.py --decode` on `dist/q9-rv2` — PASS (fla).** My fallback (07:06) `kernels: torch fallbacks` 18/18 exact (same counts). Dispatcher gate `kernels: fla` (fla 0.6.0) 18/18 exact, 15,108 positions in `gpucheck-fla2.log` — gate.

3. **`rv/execcheck.py` + `rv/replay.py` — see gate 3 row above (0 wrong on six owned counts via gate-4 replay; no standalone records file).**

4. **`rv/chat.py` — see gate 4 row above (6/6 PASS on `q9-rv2`). My 07:55/08:02 BLOCKED is withdrawn — re-detailed in gate 4 table.

Measured on RTX 6000D (gate-4 HF path, 08:43 UTC): chip ≈ 1700–1700 tok/s burst (e.g. chat-2 53839c/32.2s = 1671c/s, chat-5 58151c/35.2s = 1652c/s, chat-6c 114544c/68.5s = 1672c/s, chat-1 6006c/6.0s = 1001c/s with short run), text tok batch-1 ≈ 30–40 ms (chat-1 76t/6.0s with chip overlapped). P3 L4 reference 250 chip tok/s / 3200 (executor) still cited for context.

### 2.3 Shim (Task 4 dependency, built in merge window)

Per plan, `route-a/shim/` (thin OpenAI-compat `/v1/chat/completions` → `RvChat.generate` with `eth` tool schema) must be written and smoke-tested against the existing 4B merged model on any 24 GB card while the big merge runs. **Shim scaffold committed** at `route-a/shim/` (see §4); smoke test **could not run** (no GPU). **2026-11-01 fix:** shim had three blocking bugs — wrong `RvChat(..., firmware=...)` kwarg (class takes no firmware), missing `machine` argument to `generate()`, and concatenated messages stripped the chat template. Fixed to `RvChat(path)` + per-request `ref.Machine` from `eth-bridge.elf` + `BR.Session` lock + `prompt_ids(msgs, tools=[ETH_TOOL])` where `ETH_TOOL` is `name=eth, cmd: string` matching the `eth` firmware's `<function=eth><parameter=cmd>` capture. Validated `python -m py_compile`.

---

### 2.5 Store checkpoint `q9-store-rv`

VM 58642243, pinned venv, `main` code, `route-a/gates/run_store_gates.sh`. Merge: exit 0, 0:55 wall, 63.2 GB peak RSS, layout `rv1-store`, 12 chip layers, hidden 5888, 37 GB.

| Check | Result | Numbers | Source on the VM |
|---|---|---|---|
| Gate 1 | PASS | KL 4.912e-4, floor 4.592e-4, 1.07x, greedy identical 10/16 | `dist-store/q9-store-rv/rv-text.json` |
| Gate 2 on fla | PASS | 18/18 exact, 0 mismatches | `.../gpucheck.log` |
| Gates 3 and 4 | PASS | 6/6, 0 head mismatches, no lock errors, 0 wrong on replay; token counts equal `q9-rv2` | `.../gate4.log` |
| Snapshot verify | PASS | `es/blocks.md`, 136 tokens, equals the reference | `.../verify.log` |
| Knowledge smoke | Recorded | 4/20 with the store, 3/20 without | `.../smoke.log` |

Smoke reading: `list` returns the right 16 files. The model often answers from memory after listing, invents names such as `es/Fusaka` (`not found`), or hits the 400-token limit while reading `es/index.md`. Mechanics work; the model rarely gets to the right file. No prompt changes, per the rules.

---

## 3. Baseline eval (Task 3)

**Base with the judged batch (2026-11-01): 6 of 30 tasks scored, the same 6 passes as below; the 7 tasks in the judged and rerun batch all scored 0.** Source: `results/internet-rest-base`. `voting-page` has one scored attempt; the other's judge reply did not parse. The 7 fork tasks are pending.

**Current base result, partial (2026-11-01): Internet, stock Qwen3.5-9B through `serve.py`, thinking off, greedy: 12 of 46 scored trials passed, on 23 of the 24 tasks that need neither the grader key nor an RPC.** Source: `results/internet-nokey-base` and `results/internet-nokey-base-capped` on VM 58642243. Each task ran 2 attempts, which repeat exactly under greedy decoding, so this is 6 of 23 tasks. `signatures-permit2-witness-string` has no score: its first run looped on one `grep` for 189 calls and was stopped, then both limited attempts hit `AgentSetupTimeoutError`; to be rerun. 13 tasks wait on Fraser's RPC URL and Anthropic key. The table below is the earlier plan.


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

**Circuit with the judged batch (2026-11-01): 6 of 30 tasks scored, the same 6 passes as base; the 7 tasks in the judged and rerun batch all scored 0, in all 14 trials, with no errors.** The circuit ran in no eval trial: of 367 requests with `eth` offered, one has a circuit run, and that is the server check `Use eth: what is the selector of transfer(address,uint256)?` at 12:08:39, before the first trial. So this arm has not yet shown a difference from base. Sources on the VM: `results/internet-rest-rv2`, `results/internet-nokey-rv2`, `serve-data/rv2/traces/`. The fork batch of 7 tasks is still to come.

**Current circuit result (2026-11-01): Internet, `dist/q9-rv2` through `serve.py`, `eth` offered beside OpenCode's tools: 12 of 48 trials passed, 6 of 24 tasks that need neither the grader key nor an RPC.** Source: `results/internet-nokey-rv2` on VM 58642243. Same 6 tasks as base, and every task scored the same on both arms; `signatures-permit2-witness-string` is unscored on base, pending rerun. **The circuit did not run in any of these trials:** of 133 server requests, the one that ran it was a manual test. So this row measures base Qwen with one extra tool on offer, not the circuit's effect. The table below is the earlier plan.


| Field | Value |
|---|---|
| Status | **Could not run** (blocked on §2 gates) |
| Planned serving | Merged `dist/q9-rv1` behind shim `route-a/shim/server.py` (`RvChat(model_path, firmware="tests/data/rv/eth-bridge.elf")`, OpenAI-compat, `temperature=0`) |
| Harness | Same 25 Concepts tasks, same Internet config/trial count; trajectories `route-a/trajectories/ethereus/` unedited |
| Skills variant | Re-run as Internet+Skills if `ethskills.fs` exists on `main` (check `ls main:ethskills.fs 2>/dev/null`); else skip and note |
| Shim | `route-a/shim/server.py` implements `ETH_TOOL` (`name=eth`, `cmd` string → `<function=eth><parameter=cmd>…>`). Per-request `elf.program(FIRMWARE)` → `BR.park(m)` → `prompt_ids(msgs, tools=[ETH_TOOL])` → `generate(ids, m, lock=Session)`. Smoke test: `MODEL=dist/q4-rv1 ./route-a/shim/test_smoke.sh` — pending GPU. **2026-11-01:** fixed 3 bugs (wrong RvChat kwargs, missing machine arg, broken prompt templating). |
| Next step | Once gate 4 is green, `./route-a/shim/run.sh &` then `opencode --endpoint http://localhost:8001/v1 ... --out route-a/trajectories/ethereus` |

### 2.5 Bootstrapped firmware skills (extra task)

**Runner:** `scripts/boot_fs.py --manifest build/ethskills.manifest.json --mode verify` + `scripts/smoke_knowledge.py` on `dist/q9-rv1` with the store+eth firmware (once task 9 ELF lands).

**Finding (pre-run, recorded per operator):** `dist/q9-rv1` is layout **`rv1`** (11 chip layers, no store circuit); `chipformer.json` is `layout rv1, hidden 5632, chip_layers 11, chip_dims 2918` with no `store`/`ethskills` keys. `ethskills.fs.npz` (store circuit) **cannot load into it** — the store merge (`python -m chipformer.rv.merge BASE --out dist/q9-store --store`) is a separate artifact, scheduled for after evals per task 9's `stop rule: do not start the "Internet + Skills" variant on dist/q9-rv1`. Will record `boot_fs` skip as `rv1 has no store` and `smoke_knowledge` as `base vs --fs N/A` pending `q9-store-rv` build.

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
- **2026-11-01 08:58 gates 1–4 PASS on `dist/q9-rv2` (dispatcher, RTX 6000D):** gate 1 KL 1.68× (12/16 greedy), gate 2 18/18 exact 15,108 pos (fla), gate 3 0 wrong on 6,028/53,917/14,790/18,314/58,205/114,627 (replay; no records.jsonl in tree), gate 4 6/6 head 0 lock None (chat-6c 114,544 chip tok `run erc20` via gate4c.py). My 08:02 "deadlock" was wrong — calc-only schema cause. `q9-rv2` merge 2:59.74 57.9 GB RSS. My 07:06 gpucheck fallback 18/18 exact superseded by dispatcher fla-PASS; q9-rv1 F2 kept as history.

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

