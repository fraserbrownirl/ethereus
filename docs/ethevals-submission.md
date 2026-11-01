# ETH Evals submission (draft)

Status: configuration recorded before the runs. No scores yet; every number here will come from a run, with its source file. Nothing has been submitted or published.

## Decisions

Fraser, 2026-11-01:

- Internet passes OpenCode's own tools through, and ours are added on top.
- All 37 tasks on Internet, both arms: the 28 no-key tasks, then the 9 whose grader uses `ANTHROPIC_API_KEY` (list below). Fraser supplies the key.

## Exact configuration

| Item | Value |
| --- | --- |
| Harness | `BuidlGuidl/ethevals`, Harbor 0.24.0, attempts per `runner/ethevals/job.yaml` (`n_attempts: 2`). Commit to be recorded at the first run |
| Agent | OpenCode 1.18.33 via `ethevals.opencode:OpenCode`, no prompt changes |
| Vanilla agent | `ethevals.vanilla:VanillaAgent`, one call, no tools |
| Host | One Vast VM, 58642243: x86, RTX PRO 6000 Blackwell WS 97 GB, Docker 28.1.1. Harness and model on the same box |
| Model endpoint | `serve.py` on `172.17.0.1:8080`, one arm at a time, `route-a/gates/serve_arm.sh`. Job config `route-a/gates/job-ethereus.yaml` |
| Venv | `/workspace/venv`, lock `route-a/gates/venv.lock.txt`: torch 2.15.0+cu132, transformers 5.22.1 |
| Trial time limit | `[agent].timeout_sec = 2700` (45 min) in a copy of the tasks; nothing else differs. The board sets no limit, but its cost cap stops a runaway agent and its CI stops a step at 190 min; these local models have no cost. A timed-out trial is still verified on the work left behind. Base's 40 trials finished before the limit was added, none anywhere near it |
| Rate limit | `serve.py`'s limit is lifted for the eval. It returned 429 twice, both inside one looping base trial, which was rerun |
| Web search | No `EXA_API_KEY`, so no Exa search. The board treats it as optional; board rows on OpenCode may have it |
| Base arm | Stock `Qwen/Qwen3.5-9B` through `chipformer/rv/serve.py` with `ETHEREUS_BASE=1`, offering only the client's tools |
| Ethereus arm | `dist/q9-rv2` through `serve.py`, with `eth-bridge.elf` sha256 `b320d31b4fd722ac0adfdbcf1313892967ea1bf8e826bd318138cb167c06c685` |
| Tools, Ethereus | `eth` plus the client's tools. `files` joins once a store checkpoint is merged |
| Generation | Greedy on both arms, chat template with `enable_thinking=False` on both, because `RvChat.prompt_ids` fixes it and the driver is not changed. So these rows measure Qwen without its thinking mode |
| Server settings | `ETHEREUS_BUFFERED_STREAM=1`, `ETHEREUS_SYNC_SECONDS` raised for long turns |

How the Ethereus arm answers tools: a call to `eth` runs in the circuit and its response stays in the assistant content, so OpenCode sends it back as history and never sees it as a tool to run. A call to any other tool stops generation at `</tool_call>` before the circuit runs and goes to OpenCode as OpenAI `tool_calls`.

Deviations from `docs/api-spec-clean.md`, all for this eval:

- Client tools are honoured, where the spec says to ignore them (Fraser's decision).
- `stream: true` is answered after generation ends, with keepalive comments. OpenCode needs a streaming response; tokens are not actually streamed, so timing here is not comparable with a streaming provider.



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
