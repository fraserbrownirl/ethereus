# Qwen3.6-27B: does its config take the circuit as Qwen3.5-9B does?

Config check, 2026-11-04, after Step 3 (Fraser). Read from the model's own public files on Hugging Face
(`Qwen/Qwen3.6-27B`, last modified 2026-04-24, licence Apache-2.0) against `Qwen/Qwen3.5-9B`, the base Ethereus-9B
is built on. Nothing downloaded beyond config, tokenizer metadata and the chat template; nothing merged or run.

## Verdict

Same family and the same architecture class (`Qwen3_5ForConditionalGeneration`, `model_type: qwen3_5`), with an
identical tokenizer: the circuit's design carries over, scaled up. Two items must be proven, not assumed, before a
merge, and one is a capacity question.

## Same as Qwen3.5-9B

| Field | Value |
|---|---|
| Layer pattern | 3 linear-attention (gated delta) : 1 full attention, `full_attention_interval: 4` |
| Heads | `head_dim` 256, 4 KV heads, 16 linear key heads, linear key and value head dim 128, conv kernel 4 |
| Attention output gate | `attn_output_gate: true` |
| RoPE | default, theta 1e7, `partial_rotary_factor` 0.25, interleaved mRoPE sections [11, 11, 10] (the 9B keeps the factor inside `rope_parameters`) |
| Norm, activation | RMSNorm eps 1e-6, SiLU MLP |
| Embeddings | not tied in either |
| MTP | 1 multi-token-prediction layer in both |
| Context | 262,144 positions |
| Tokenizer | byte-identical: `tokenizer.json` 5f9e4d49…, `vocab.json` 0aa0ce06…, `merges.txt` a494e019…, vocab 248,320, the same end-of-turn token 248044 |

The identical tokenizer matters most: the firmware's token tables, the bridge's text buffers and the fine-tune
data's token ids carry over unchanged.

## Different

| Field | Qwen3.5-9B | Qwen3.6-27B |
|---|---|---|
| Hidden size | 4,096 | 5,120 |
| Layers | 32 (24 linear, 8 full) | 64 (48 linear, 16 full) |
| MLP intermediate | 12,288 | 17,408 |
| Attention heads | 16 | 24 |
| Linear value heads | 32 | 48 |
| `output_gate_type` | not set | `swish` |
| `bos_token_id` | not set | 248044 |
| Weights on disk (bf16) | 19.31 GB, 4 shards | 55.56 GB, 15 shards (FP8 release 30.87 GB) |

## To prove before a merge

1. **`output_gate_type: swish`.** Our pinned transformers (5.19.0 on the Mac, 5.22.1 on the eval VM) has no such
   field in its Qwen3.5 code; it applies sigmoid to the full-attention output gate and hard-codes `silu` (= swish)
   in the linear-attention gated norm. The field most likely names that existing norm gate, so the 9B leaves it
   unset on the same code path. That is an inference: the check is stock Qwen3.6-27B on our stack against the
   reference logits, as the 9B's gates did, before any circuit work.
2. **Chat template.** Two changes from 3.5: an optional `preserve_thinking`, and tool-call arguments rendered with
   `tojson` unless they are strings (3.5 used Python's `string`, so a boolean printed `True`, now `true`). Our server
   and the fine-tune data format tool calls themselves; the three-call canary with replay would catch a mismatch.

## Capacity (estimates, not measured)

- Merged size: the 9B circuit widened the hidden size from 4,096 to 5,888 and grew the checkpoint about 1.5 times.
  For 27B the same circuit lanes on 5,120 give about 6,900 (×1.35), roughly 75–80 GB in bf16.
- Serving on one 97 GB GPU: weights about 78 GB plus about 4 GB of full-attention KV cache at 65,536 tokens (16 full
  layers × 4 KV heads × 256 × 2 × 2 bytes per token); likely fits, with little headroom.
- Merge on the CPU: the 9B merge peaked at 54.8 GB RSS; scaled, about 150–170 GB.
- Fine-tune: a LoRA on stock 27B (55.6 GB of bf16 weights) needs activation checkpointing on one 97 GB GPU, or a
  larger card. The FP8 release cannot carry the circuit's exact arithmetic as is; whether Qwen's own layers could
  stay in FP8 is an open question.

## Order if Fraser goes ahead

Stock 27B exactness on our stack (item 1), then the merge with `chipformer.rv.merge --store`, gate 1 (unchanged
firmware), gate 4 and the canary on GPU, then the fine-tune.

## As the trajectory generator (Fraser, 2026-11-04): inference fit on the eval VM, tool-call format

Not downloaded. VM 58642243: one RTX PRO 6000 Blackwell Workstation Edition, 97,887 MiB (95.6 GiB); 96 GB RAM;
251 GB free disk.

| Item | Value (estimate unless stated) |
|---|---|
| Weights, bf16 | 55.56 GB = 51.7 GiB (15 shards, measured from the repo listing; includes the vision encoder, which `--language-model-only` skips) |
| KV cache | only the 16 full-attention layers keep one: 16 × 2 × 4 KV heads × 256 × 2 bytes = 64 KiB per token; 4 GiB at 65,536 tokens, 16 GiB at 262,144 |
| Linear-attention state | fixed per sequence: 48 layers × 48 value heads × 128 × 128 × 4 bytes (fp32) ≈ 150 MB |
| One full-context sequence | ≈ 72 GiB with activations and CUDA graphs: fits one card |
| Headroom | vLLM's default `--gpu-memory-utilization 0.9` gives ≈ 86 GiB, so ≈ 30 GiB of KV after weights: about 7 parallel sequences at 65,536 tokens |
| Alongside an eval server | no: the eval's model server holds ≈ 32 GiB, so the generator and an eval arm run one at a time |
| Serving | model card: `vllm>=0.19.0` (or `sglang>=0.5.10`), `--reasoning-parser qwen3`, `--enable-auto-tool-choice --tool-call-parser qwen3_coder`; its examples use 8 GPUs for 262,144-token high-throughput serving. vLLM is not installed on the VM; it goes in its own venv, not the eval venv |

Tool-call format (the model's own chat template): the Qwen3.5 XML style, unchanged —

```
<tool_call>
<function=NAME>
<parameter=ARG>
value
</parameter>
</function>
</tool_call>
```

tools listed as JSON inside `<tools>…</tools>` in the system prompt, results returned in `<tool_response>…</tool_response>`.
It is the same shape serve.py parses (`<function=…>`, `<parameter=…>`) and the circuit's own calls use, so calls the
generator writes can be executed by `ref.py` and replayed through the circuit without a format change. One template
change from 3.5: non-string arguments are rendered with `tojson` (booleans as `true`), so `ref.py` must read typed
values as JSON.
