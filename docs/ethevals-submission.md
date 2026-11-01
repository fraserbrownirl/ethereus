# ETH Evals submission (draft)

Status: configuration recorded before the runs. No scores yet; every number here will come from a run, with its source file. Nothing has been submitted or published.

## Decisions

Fraser, 2026-11-01:

- Internet and Skills pass OpenCode's own tools through, and ours are added on top.
- The 9 tasks whose grader uses `ANTHROPIC_API_KEY` are not run yet (list below).
- The 28 tasks whose grader does not need the key run in every mode they allow.

## Exact configuration

| Item | Value |
| --- | --- |
| Harness | `BuidlGuidl/ethevals`, Harbor 0.24.0, attempts per `runner/ethevals/job.yaml` (`n_attempts: 2`). Commit to be recorded at the first run |
| Agent | OpenCode 1.18.33 via `ethevals.opencode:OpenCode`, no prompt changes |
| Vanilla agent | `ethevals.vanilla:VanillaAgent`, one call, no tools |
| Trial host | Mac with Docker 29.4.1, arm64. The GPU box has no Docker |
| Model host | Vast RTX 6000D 85 GB, reached through `ssh -N -L` |
| Base arm | Stock `Qwen/Qwen3.5-9B` through `chipformer/rv/serve.py` with `ETHEREUS_BASE=1`, offering only the client's tools |
| Ethereus arm | `dist/q9-rv2` through `serve.py`, with `eth-bridge.elf` sha256 `b320d31b4fd722ac0adfdbcf1313892967ea1bf8e826bd318138cb167c06c685` |
| Tools, Ethereus | `eth` plus the client's tools. `files` joins once a store checkpoint is merged |
| Generation | Greedy on both arms |
| Server settings | `ETHEREUS_BUFFERED_STREAM=1`, `ETHEREUS_SYNC_SECONDS` raised for long turns |

How the Ethereus arm answers tools: a call to `eth` runs in the circuit and its response stays in the assistant content, so OpenCode sends it back as history and never sees it as a tool to run. A call to any other tool stops generation at `</tool_call>` before the circuit runs and goes to OpenCode as OpenAI `tool_calls`.

Deviations from `docs/api-spec-clean.md`, all for this eval:

- Client tools are honoured, where the spec says to ignore them (Fraser's decision).
- `stream: true` is answered after generation ends, with keepalive comments. OpenCode needs a streaming response; tokens are not actually streamed, so timing here is not comparable with a streaming provider.

Not recorded yet, to be filled from the first trial: the OpenCode provider config, web search setup (`EXA_API_KEY` on the board), and whether the task images run on arm64.

## Task coverage

37 tasks: 25 Concepts, 9 Build, 3 Act.

| Set | Concepts | Build | Act | Total |
| --- | --- | --- | --- | --- |
| No key, runs now | 20 | 6 | 2 | 28 |
| Grader uses the key, held | 5 | 3 | 1 | 9 |

No-key tasks per mode: vanilla 20, internet 28, skills 28.

In all 9 held tasks the key is in `[verifier.env]`, no agent or environment section has it, the instruction does not mention an LLM, and an Opus-judged criteria file grades the result.

## Results

None yet.
