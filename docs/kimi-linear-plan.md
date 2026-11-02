# Stepping stone: the circuit in Kimi-Linear-48B-A3B-Instruct

Fraser, 2026-11-02: merge into `moonshotai/Kimi-Linear-48B-A3B-Instruct` (MIT) before K3. Plan only; nothing
downloaded but the config and code. Facts are from its `config.json`, `configuration_kimi.py`, `modeling_kimi.py`
and `model.safetensors.index.json` on Hugging Face (2026-11-02). K3 stays parked until the overwrite test passes and
this plan is reviewed.

## The model against K3 and our 9B

| | Kimi Linear 48B-A3B | Kimi K3 | Qwen3.5-9B (merged today) |
|---|---|---|---|
| Layers | 27: 20 KDA, 7 MLA at 4, 8, 12, 16, 20, 24, 27 (one-based, `is_kda_layer` tests `layer_idx + 1`) | 93: 69 KDA, 24 MLA | 32 |
| Hidden | 2,304 | 7,168 | 4,096 (5,888 merged) |
| KDA gate | `-exp(A_log) * softplus(.)`, **no lower bound**: decay 0 and 1 both exact | lower bound -5: decay never 0 | GDN, same form as Kimi Linear |
| KDA kernel | `fused_recurrent` when the input is 64 tokens or fewer, `chunk` above | `chunk` for any prefill | FLA GDN |
| MLA | no output gate | output gate | gated attention |
| Attention residuals | **none** (no `attn_res_block_size`) | every 12 layers | none |
| MLP | SiLU; layer 1 dense, 9,216 wide; 26 MoE layers: 256 experts, top 8, 1 shared expert 1,024 wide, added unweighted | SiTU; 896 experts, 2 shared | SiLU dense |
| Weights | 49.1 B parameters, all bf16, 98.2 GB in 20 shards | 1.56 TB, routed experts MXFP4 | 9 B |
| Licence | MIT | modified MIT | Apache-2.0 |

Kimi Linear removes three of K3's four research items: no decay floor, SiLU instead of SiTU (our exact-ReLU trick
holds as it does on Qwen), and no attention residuals. Left: the tokenizer and tool-call format (Kimi's, shared
with K3, so this work carries straight over), and the chunked prefill kernel, which the overwrite test showed is
not exact for repeated writes to one cell (31,069 of 65,536 transitions); the token-by-token kernel is.

## Where the circuit goes

- **Chip layers first, as in the Qwen merge.** Prepend C KDA layers with dense MLPs: `num_hidden_layers` 27 + C,
  `first_k_dense_replace` 1 + C, `kda_layers` and `full_attn_layers` shifted by C. Routing is not touched.
- **Dense width.** The dense MLP width is one config scalar (`intermediate_size`, 9,216) shared with Kimi Linear's
  own first layer. The circuit's widest layer has 9,401 units: raise it to 9,472 and zero-pad Kimi's dense layer, or
  spread the widest chip layer over two.
- **The shared expert is too narrow** (1,024) to hold a chip layer; prepended dense layers are the route.
- **Muting.** KDA: beta and the gate take a column on the mute marker, as GDN does today. MLA has no output gate, so
  chip positions are hidden from text by the anti-sink key, and chip tokens' MLA output is cleared by the exit MLP.
- **Hidden width is the cost.** The circuit needs 3,753 dims; the overlay puts chip-only dims inside the host's, but
  Kimi Linear's host is only 2,304 wide, so the merged hidden is about 4,100 (the 9B went 4,096 to 5,888). Every
  tensor that reads the hidden state is zero-padded, the 256 x 26 experts included, so the merged checkpoint is
  about 1.8 times the base: roughly 175 GB in bf16. Reducing that is the first design question.

## Hardware

- **Merge:** CPU, about 300 GB of disk (98 GB in, about 175 GB out), the same streaming merge as the 9B.
- **Gates and serving:** about 175 GB resident plus small state. One B200 (192 GB) or two RTX PRO 6000 (96 GB each,
  needs tensor parallel in our driver), or an H200 pair. Our current GPU (96 GB) cannot hold it alone.
- **Speed:** about 3 B active parameters per token against 9 B today, so circuit tokens should run faster than now.

## The four gates

| Gate | Carries over | Changes |
|---|---|---|
| 1. Text drift: KL against stock | method unchanged | new floor measured on stock Kimi Linear |
| 2. Circuit exact on GPU kernels, 18 RV32 programs | the programs and the check unchanged | runs through KDA (`fused_recurrent`) instead of GDN; chip tokens must never take the chunk path (prefill over 64 tokens) |
| 3. Replay of gate 4's records | unchanged (the reference machine is model-free) | none |
| 4. The model calls the tool, 6 prompts with replay | prompts and replay unchanged | the bridge's text classes and tool-call parsing rebuilt for Kimi's tokenizer and message format |

## Steps

1. Emitter: chip layers as KDA layers (decay and beta as GDN today) with SiLU dense MLPs; unit tests on the Mac.
2. Tiny Kimi Linear: its own modeling code at small width, random weights, chip layers merged; text identity and
   circuit exactness on the Mac or the eval GPU.
3. Bridge for Kimi's tokenizer and tool format; the same work K3 needs.
4. Full merge (CPU), gates 1 to 4 on a 192 GB GPU. Fraser approves the spend.
