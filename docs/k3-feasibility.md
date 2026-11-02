# Compiled layers in Kimi K3: feasibility

Fraser's priority after ft1b and the spill merge (2026-11-02). Crossing artefact: the firmware ELF and the circuit;
nothing from the 9B checkpoints. Facts below are read from `moonshotai/Kimi-K3` on Hugging Face (`config.json`,
`modeling_kimi_linear.py`, `encoding_k3.py`, `LICENSE`, model API) on 2026-11-02, not from blogs.

## What K3 is

| | Kimi K3 | Qwen3.5-9B (our merge) |
|---|---|---|
| Parameters | 2.78T (API `safetensors.total` 2,779,931,837,184) | 9B |
| Layers | 93: 69 Kimi Delta Attention (KDA), 24 gated MLA (every 4th, and the last) | 32 Gated DeltaNet + gated attention |
| Hidden | 7,168 | 4,096 |
| MLP | layer 0 dense (33,792); others MoE: 896 experts, 16 per token, 2 shared, expert width 3,072, sigmoid router | dense SwiGLU |
| Activation | `situ`: 4·tanh(g/4)·sigmoid(g) · 25·tanh(u/25) | SiLU · u |
| Residual | Attention Residuals: every 12 layers a block snapshot; each layer's input is a softmax mix of the snapshots and the running sum | plain sum |
| Precision on disk | routed experts MXFP4 (group 32); attention, shared experts, dense MLP, lm_head bf16 | bf16 |
| Tokenizer, tool calls | tiktoken, 163,840 tokens; `<\|open\|>`/`<\|sep\|>`/`<\|close\|>` message encoding | Qwen, `<tool_call>` XML |
| Loading | `trust_remote_code` | stock transformers |
| Licence | Kimi K3 License: modified MIT; derivatives and redistribution allowed with the notice; conditions only above $20M revenue or 100M MAU | Apache-2.0 |

## What carries over from the Qwen merge (`docs/q35.md`)

- **Memory is still the delta rule.** KDA is gated DeltaNet with a per-channel decay instead of per-head, and the
  same in-kernel L2 normalisation of q and k that Qwen3.5 already has. One-hot keys and `beta = 1` overwrite rows
  exactly, as now.
- **Muting K3 on chip tokens has the same handles.** KDA's `beta` (sigmoid of `b_proj`) and decay (`f_a_proj`,
  `f_b_proj`) take a column on the mute marker; MLA has an output gate (`mla_use_output_gate`) and a KV cache
  that needs the anti-sink key Qwen's attention needed. The short convolution (kernel 4) on q, k, v is the case
  the overlay already handles: chip tokens hold zeros in the host's dims.
- **Experts need no exactness.** The circuit never runs inside a routed expert, so MXFP4 does not touch it.
  Padding the hidden size adds whole zero groups of 32 to each expert's input, which quantise exactly; the
  circuit's added width should be a multiple of 32.
- **Licence permits it.** Merging and publishing a derivative needs the notice kept.

## What is new research

1. **No exact zero decay.** `gate_lower_bound = -5` bounds each step's decay to [e^-5, 1]. The circuit uses
   decay 0 for resets (counter heads, `core.py:224`) and 0.5 for halving (in range). Resets must become
   delta-rule overwrites. Tractable; changes the emitter.
2. **`situ` is not a ReLU.** The gate saturates at 4 for large inputs, so `situ(Kx)` is an exact 0/4 step, not the
   exact ReLU that SiLU gave. The value branch passes through 25·tanh(u/25), exact in bf16 only while u stays
   small (|u| below about 1.9 for 8 significant bits). Values must be scaled down before and back up after
   with powers of two. Needs a proof per kernel, as `docs/q35.md` has for SiLU.
3. **Attention Residuals.** Each layer reads a softmax mix of earlier block snapshots. Inside chip layers the
   mix must be exactly the current sum: a clock dim the circuit raises every layer, with a large score weight,
   makes the softmax one-hot (the others underflow to 0 in fp32). For text tokens, prepended chip layers add
   block snapshots and shift K3's own block boundaries, so K3's learned mix for text changes. Text then stays
   within a KL budget, not bit-identical, unless the chip layer count is a multiple of 12 and the added
   snapshots carry a marker scored at minus infinity. This is the main open question.
4. **Tokenizer and tool-call format.** The bridge's text classes (digits, tool-call tags, function-name
   spellings) are Qwen token ids. They are rebuilt for K3's tokenizer and its message encoding, and the
   fine-tune data is re-rendered in that format.
5. **Stock or custom.** A merge loadable as plain K3 means chip layers built from KDA, MLA, `situ` and the
   attention-residual rule (items 1 to 3). A custom prefix module would sidestep 2 and part of 3, but breaks
   serving in vLLM and SGLang, which implement K3 natively. Stock is the target.

## Compute

Full K3 is 1,560,860,324,864 bytes (1.56 TB) in 96 shards, per Moonshot's `model.safetensors.index.json`. Every gate needs whole-model forward passes:
at least 8 GPUs of 192 GB (B200 or MI300X class) or 16 of 80 GB. The eval VM has one GPU.

## Plan

| Stage | What | Where | Needs |
|---|---|---|---|
| 0 | Emitter variants: resets by overwrite, `situ`-safe MLPs, attention-residual clock; exactness tests of the circuit in a K3-shaped reference (CPU) | Mac | nothing |
| 1 | Tiny K3: the real modeling code at small width, random weights, chip layers merged; text identity and circuit exactness | Mac or eval VM | nothing |
| 2 | Real K3 layers 0 to about 3 (dense layer plus first KDA and MoE layers), embedding and head; mute and text-identity checks on real weights | eval VM | about 60 GB download per layer slice |
| 3 | Full merge, gates 1 to 4, smoke, Ethereus test | 8x B200 class | Fraser: spend |

Stages 0 to 2 start now. Stage 3 is the first point that needs Fraser.

## Second source and three questions (2026-11-02)

Fraser added [FareedKhan-dev/kimi-k3-in-c](https://github.com/FareedKhan-dev/kimi-k3-in-c) (Apache-2.0, checked by the
GitHub API): a C engine that runs K3 from its released checkpoint. Its README (the config section and sections 6 to 9)
gives the layer map, the KDA recurrence and the MoE block. Every fact below was checked against Moonshot's own
`config.json`, `configuration_kimi_k3.py`, `modeling_kimi_linear.py` and `model.safetensors.index.json`, and the kernel
behaviour was measured on FLA 0.6.0's `chunk_kda` and `fused_recurrent_kda`, the kernels Moonshot's code calls.

| Fact | C repo | Moonshot's files |
|---|---|---|
| Layer map | 69 KDA, 24 MLA at 4, 8, ... 88, 92, 93, one-based | `kda_layers` 69 entries 1..91, `full_attn_layers` 24 ending 88, 92, 93; `is_kda_layer` tests `layer_idx + 1`, so one-based |
| Dense layer | layer 0 | `first_k_dense_replace` 1: the first layer (a KDA layer) has the one dense MLP, 33,792 wide; the other 92 are MoE |
| KDA decay | alpha = exp(-5 sigmoid(u)), per channel | `gate_lower_bound` -5; FLA computes `-5 * sigmoid(exp(A_log) * (u + dt_bias))` in fp32 with no clamp |
| KDA write | delta rule with beta | `beta = sigmoid(b_proj(x))` in the kernel (`use_beta_sigmoid_in_kernel`), q and k L2-normalised in the kernel |
| Shared experts | 2, full width, unweighted | `num_shared_experts` 2, built as one MLP 2 x 3,072 = 6,144 wide on the full hidden state, added as `y + shared_experts(x)` with no router weight; not quantised (`ignore` lists `shared_experts`) |
| Routed experts | 896, top 16, latent 3,584 | `num_experts` 896, `num_experts_per_token` 16, `routed_expert_hidden_size` 3,584 (down-projected latent), MXFP4 |
| Size | 1.56 TB | 1,560,860,324,864 bytes, 96 shards, 497,220 tensors (494,592 routed-expert) |

### 1. Exact alpha = 1 and beta = 1 for a one-hot RAM write

Yes, measured on the GPU (`/workspace/k3/kda_exact.py`).

- **alpha = 1 exactly.** The gate is computed in fp32. A raw gate input of -89 or below makes `sigmoid` exactly 0, so
  the log-decay is exactly 0 and alpha exactly 1 (checked at -200, -104, -90, -88). The documented range "[-5, 0)"
  is a limit, not a clamp: no code enforces it.
- **beta = 1 exactly** for a raw input of 17 or above (`sigmoid(16)` is 0.99999988, `sigmoid(17)` is 1.0 in fp32);
  beta = 0 exactly at -89 or below, which is how a token writes nothing.
- **One-hot writes and reads, K3's settings** (lower-bound gate, safe gate, in-kernel sigmoid beta and L2 norm, head
  dim 128, bf16 q/k/v, 200 tokens, 2 heads, 51,200 reads against a float64 reference):

| Values written | Chunk kernel (prefill) | Recurrent kernel (decode) |
|---|---|---|
| 0..127 | 51,200 of 51,200 bit-exact | 51,125 bit-exact, 0 wrong after rounding |
| 0..255 | 51,200 of 51,200 bit-exact | 51,168 bit-exact, 0 wrong after rounding |
| -256..256 | 1,238 wrong by up to 2 | 0 wrong after rounding |

  The chunk kernel loses a bit when an overwrite's difference `v_new - v_old` needs nine significant bits (the
  signed case). The circuit already keeps non-negative values of at most 8 significant bits, which is exact. The
  recurrent kernel's residues (about 1e-4) come from the L2-norm epsilon: a one-hot key normalises to just under 1.
  Without the in-kernel norm it is 51,200 of 51,200 bit-exact. Qwen3.5's Gated DeltaNet has the same norm, and the
  circuit's half-unit margins already absorb it there.

### 2. Chip MLP logic without touching routing

Yes, two ways, both outside the router.

- **Shared experts.** Always on, unweighted, full width, bf16, so they can carry circuit units. Their width is
  `moe_intermediate_size x num_shared_experts`, so `num_shared_experts` 2 -> N widens every MoE layer's shared MLP to
  3,072 N with a config change; added units are zero for K3's text. Cost: the widening applies to all 92 MoE layers.
- **Added dense layers.** Prepend the chip layers as KDA layers with dense MLPs: `num_hidden_layers` 93 + C,
  `first_k_dense_replace` 1 + C, both layer lists shifted by C. The dense width (33,792) covers the circuit's widest
  layer (9,401 units). With C = 12 the shift is exactly one attention-residual block (`attn_res_block_size` 12), so
  K3's own block boundaries stay aligned. The extra block snapshot still enters every later layer's softmax mix,
  which is the open question for text identity (section "What is new research", item 3). This is the route the Qwen
  merge used (chip layers first) and the stronger of the two.
- **SiTU is exact enough for logic.** Measured in fp32 then cast to bf16 as Moonshot's `SituAndMul` does: the gate
  is exactly 4.0 at 36 and above and exactly 0 at -89 and below, a clean step; the value branch `25 tanh(u/25)`
  returns every 8-bit value unchanged for |u| < 1 (scales 2^-14 to 2^-8) and fails from |u| about 2. So circuit
  units become step(gate) x value with values scaled by a power of two below 1 and scaled back in `down_proj`,
  exact in bf16. The emitter needs this rewrite; it does not need the router.

### 3. Hardware at 1.56 TB

- **Merge:** a CPU job, no GPU. It streams the 96 shards and writes new ones. Widening the hidden size by the
  circuit's text-visible dims (a multiple of 32, so MXFP4 groups stay whole and zero groups quantise exactly)
  rewrites every tensor that touches hidden, so the output is about the input's size again. Needs: about 3.5 TB of
  fast local NVMe (input plus output), tens of GB of RAM (one shard at a time), and a fast link to download 1.56 TB.
- **Serving and gates:** the whole model resident in GPU memory, about 1.6 to 1.7 TB with the widening, plus the
  24 MLA layers' cache and 626 MB of KDA state (C repo). 8 x B200 (8 x 192 GB = 1.54 TB) is not enough. Options:
  8 x B300 (2.3 TB), 8 x MI325X (2 TB), or 16 x H200 across two nodes (2.26 TB).
- **CPU streaming is not an option for the gates.** The C engine runs K3 on 8 to 224 GB of RAM at 19 to 33
  seconds a token (its memory ladder). One tool call is 6,000 to 115,000 circuit tokens (gate 4's six calls), so
  a single call would take days.
- **Speed is the real cost.** Each circuit token passes the whole 93-layer model (about 50 to 80 B active
  parameters by the config's widths, against 9 B now), so a 100,000-token call that takes minutes on the 9B takes
  far longer on K3 even on 8 GPUs. Measure on stage 2 before planning stage 3.
