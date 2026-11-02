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

Full K3 is about 1.4 TB on disk (MXFP4 experts) plus the bf16 parts. Every gate needs whole-model forward passes:
at least 8 GPUs of 192 GB (B200 or MI300X class) or 16 of 80 GB. The eval VM has one GPU.

## Plan

| Stage | What | Where | Needs |
|---|---|---|---|
| 0 | Emitter variants: resets by overwrite, `situ`-safe MLPs, attention-residual clock; exactness tests of the circuit in a K3-shaped reference (CPU) | Mac | nothing |
| 1 | Tiny K3: the real modeling code at small width, random weights, chip layers merged; text identity and circuit exactness | Mac or eval VM | nothing |
| 2 | Real K3 layers 0 to about 3 (dense layer plus first KDA and MoE layers), embedding and head; mute and text-identity checks on real weights | eval VM | about 60 GB download per layer slice |
| 3 | Full merge, gates 1 to 4, smoke, Ethereus test | 8x B200 class | Fraser: spend |

Stages 0 to 2 start now. Stage 3 is the first point that needs Fraser.
