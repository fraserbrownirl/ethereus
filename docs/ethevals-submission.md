# ETH Evals submission (draft)

Status: draft for Fraser's review, 2026-11-04. Nothing has been submitted, posted or published. Every number
below comes from a run on the eval VM, recorded in the evidence pack (`route-a/gates/evidence_pack.py`; a copy
with its `SHA256SUMS` is on the Mac at `~/Dev/ethereus-artifacts/2026-11-03/step3/evidence/step3-final/`). The
board is filled by ETH Evals' own CI, not by submitted numbers (`docs/ethevals-review.md`), so this is an evidence
pack for the maintainers plus a proposed `job.yaml` entry, not a results PR.

## What is being evaluated

Ethereus-9B puts a RISC-V computer inside the weights of Qwen3.5-9B. Its firmware gives the model exact Ethereum
tools (`eth`: keccak, ABI encode and decode, uint256 maths, an EVM; `files`: an Ethereum reference store) that run
in the forward pass, with no outside call. Ethereus-9B-v0.1 adds a LoRA fine-tune that teaches the model to decide
when to use them. Three models, three modes, the board's 37 tasks:

| Model | What it is | Served as |
|---|---|---|
| Ethereus-9B-v0.1 | Qwen3.5-9B with the circuit, plus the v0.1 fine-tune (LoRA, KL to stock 0.0078, MMLU-500 78.6 vs stock 78.0) | `frsrcom/Ethereus-9b-v0.1` (private), the same checkpoint |
| Ethereus-9B (untrained) | Qwen3.5-9B with the circuit, no fine-tune | the merged checkpoint with the file store |
| Qwen3.5-9B | stock `Qwen/Qwen3.5-9B`, no circuit | the base checkpoint |

## Results

All 37 tasks, 2 trials each, OpenCode 1.18.33, real RPC. "Model only" is the harness's vanilla mode: one call, no
agent tools, the 25 Concepts tasks only (50 trials). Passed / scored; errors and timeouts counted apart and not
scored as passes.

| Model | Mode | Concepts | Build | Act | Overall | Errors | Timeouts | eth calls | files calls | OpenCode's tools | Judge spend |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Ethereus-9B-v0.1 | Internet + Skills | 10/50 | 0/18 | 0/6 | **10/74** | 0 | 0 | 0 | 50 | 0 | $2.92 |
| Ethereus-9B-v0.1 | Internet | 12/50 | 0/18 | 0/6 | **12/74** | 0 | 0 | 0 | 50 | 0 | $2.96 |
| Ethereus-9B-v0.1 | Model only | 18/50 | – | – | **18/50** | 0 | 0 | 0 | 4 | – | $0.08 |
| Ethereus-9B (untrained) | Internet | 12/50 | 0/17 | 0/6 | **12/73** | 1 | 2 | 2 | 2 | 1,761 | $4.39 |
| Ethereus-9B (untrained) | Model only | 14/50 | – | – | **14/50** | 0 | 0 | 0 | 0 | – | $0.07 |
| Qwen3.5-9B | Internet | 12/50 | 0/17 | 0/6 | **12/73** | 1 | 0 | – | – | 2,969 | $3.39 |
| Qwen3.5-9B | Model only | 14/50 | – | – | **14/50** | 0 | 0 | – | – | – | $0.08 |

Judge (Claude Opus 5.5, high, $2 cap per trial) total for Step 3: $13.89.

What the numbers say:

- **Model only, where the built-in tool is the only tool, v0.1 leads**: 18 of 50 against 14 for both the untrained
  model and stock. It passes everything they pass plus `compiler-memory-bytes-delete` and
  `governance-arbitrum-block-clock`, both trials each.
- **With an agent (Internet), all three models score 12 and none passes a Build or Act task.** They fail in different
  ways. v0.1 answers in one turn: it looks things up in its store (`get` 36–38, `list` 12–14 per arm) and never calls
  OpenCode's tools. The untrained model and stock work in the workspace (1,761 and 2,969 OpenCode tool calls, mostly
  `bash`) but finish none of the build tasks, and the untrained model calls `eth` only twice.
- **Internet + Skills** (v0.1 with the harness's skills mode) scores 10, below Internet.

So v0.1 learned to use its tools on questions and did not learn to keep working with an agent's tools. The fix,
agent trajectories that mix both kinds of tool, is the v0.2 data proposal (`docs/v02-data-proposal.md`).

## Exact configuration

| Item | Value |
|---|---|
| Harness | `BuidlGuidl/ethevals` cc3daaf519c5953396d48fa17c1951d4a3889f31, Harbor 0.24.0 |
| Job file | `route-a/gates/job-step3.yaml`, sha256 b9e83c448134d1c9976d9fd38a2e612534c38938c7b61d44e596a4cdf6cff4ea |
| Agent | OpenCode 1.18.33 via `ethevals.opencode:OpenCode`, no prompt changes; vanilla agent for Model only |
| Board parity | Exa search (`EXA_API_KEY`), `OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX: '+131072'`, effort high, `n_attempts: 2`, `max_retries: 1`, judge as the board (`docs/ethevals-review.md`) |
| Provider entry | OpenAI-compatible, context 65,536, output 8,192, `tool_call: true` |
| Trial time | no harness limit; each trial bounded at 190 minutes, as the board's CI ends a step, by a host watchdog. From arm 4 a watchdog stop is a timeout failure with no retry. Arms 1–3 ran before the watchdog; their longest trial was 10.5 minutes |
| Host | one Vast VM, x86, RTX PRO 6000 Blackwell Workstation Edition 97 GB; harness and model on the same box, one trial at a time |
| Model server | `chipformer/rv/serve.py` (core repo), one model at a time on :8080 (`route-a/gates/serve_arm.sh`) |
| Firmware | `eth-bridge.elf` sha256 7c4175e6…, the same on every arm (recorded by the server in each arm's traces) |
| File store | snapshot c2f24eca… on all Ethereus arms; none on stock |
| System line, v0.1 | the training data's line, set by our server, not the agent: "Files under es/ hold the ethskills reference; before answering, list them and get whatever is relevant." The untrained model and stock get none |
| Generation | greedy, chat template with `enable_thinking=False` on every model, so these rows measure Qwen without its thinking mode |
| Streaming | `stream: true` is answered after generation ends, with keepalive comments; tokens are not streamed, so timing is not comparable with a streaming provider |

How a tool call is handled: a call to `eth` or `files` runs in the circuit during generation and its response stays
in the assistant text, so OpenCode sends it back as history and never runs it. A call to any other tool stops
generation at `</tool_call>` before the circuit sees it and goes to OpenCode as an OpenAI `tool_calls` entry. With no
client tools (Model only) the server returns our calls as tool messages, which the vanilla agent does not record, so
the Model only in-weights call counts above come from the server's own traces.

## Disclosures

- **VM stop.** On 2026-11-03 at 17:13 UTC the provider stopped the VM (account credit at zero) during the stock
  Internet arm, after 22 of its trials had results. The same VM came back on the same GPU at 18:33 and the arm
  resumed at 18:36; the trial in flight was lost and rerun. Every finished arm's files matched their hashes after the
  restart. No result mixes hardware.
- **Errors under the board's limits.** Stock Internet: one `swap-page` trial still failed after its retry; OpenCode
  itself was killed at the task's 4,096 MB memory limit (exit 137, kernel OOM log). Three more such kills on that arm
  (`aave-savings-page` once, `swap-page` twice) were retried once each and the retries were scored. Untrained Internet: `voting-page` ended with no reward file after its retry (the agent hit the
  8,192-token output cap). Both tasks' limits are ETH Evals' own and were not changed.
- **Watchdog stops.** Untrained Internet: `treasury-eth-sale` and `swap-page` were stopped at 190 minutes, counted
  as timeouts (failures), not retried.
- **Server changes after the eval, not in these numbers.** A decode ban on `<|im_start|>` and `<|endoftext|>`
  (`<|im_end|>` the only end of turn), and firmware fixes for a 32-bit wraparound in ABI decode bounds checks and a
  faster keccak. None of them ran in Step 3.
- **What is local.** Our numbers come from our runs, not the board's CI. The model is not on OpenRouter; see the
  open item below.

## Proposed `job.yaml` entry

An OpenCode entry like the board's others, pointing at a hosted OpenAI-compatible endpoint for Ethereus-9B-v0.1,
with the same parity settings as above. Open item for Fraser: where the endpoint is hosted and who pays for it;
the board's CI calls models through OpenRouter, so this needs either an OpenRouter listing or the maintainers'
agreement to a direct endpoint. Draft, not sent:

```yaml
models:
  ethereus-9b-v0.1:
    name: <provider>/ethereus-9b-v0.1   # to be set once hosted
    effort: high
agents:
  opencode-ethereus-9b-v0.1:
    name: opencode
    import_path: ethevals.opencode:OpenCode
    version: 1.18.33
    model: ethereus-9b-v0.1
    modes: [internet, skills]
    env:
      EXA_API_KEY: ${EXA_API_KEY}
      OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX: '+131072'
```

## Evidence pack contents

`table.md` (the results table, per-arm errors and tool calls), `config.md` (harness commit, job file hash, core
commit, firmware and snapshot per arm from the server traces), `SHA256SUMS` (755 files: our rows and trajectories).
Raw results per arm on the Mac with their sha256, `~/Dev/ethereus-artifacts/2026-11-03/step3/`; the stock Internet
copy leaves out the trials' workspace snapshots, which stay on the VM in a full archive.
