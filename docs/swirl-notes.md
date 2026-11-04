# SWiRL notes (for the Muse)

Ethereus-9B-v0.2 is trained on multi-step trajectories (SWiRL-style), not single-turn rows (Fraser, 2026-11-04;
single-turn training work is stopped). The Muse works on branch `swirl-wip` in `~/ethereus-swirl` on the eval VM.
These notes collect what the dispatcher has established for that work; each line says where it comes from.

## Trajectory generator: Qwen3.6-27B

- **Tool-call format: Qwen's `qwen3_coder` XML.** The generator writes
  `<tool_call>\n<function=NAME>\n<parameter=ARG>\nvalue\n</parameter>\n</function>\n</tool_call>`, with tools listed as
  JSON inside `<tools>…</tools>` in the system prompt and each result returned in `<tool_response>…</tool_response>`
  (its own `chat_template.jinja`; vLLM serves it with `--tool-call-parser qwen3_coder`). This is the same shape
  `serve.py` parses and the circuit's own calls use, so a generated call can be executed and replayed through the
  circuit without a format change.
- **Non-string arguments arrive as JSON.** Qwen3.6's template renders a non-string argument with `tojson` (a boolean
  is `true`, a number `42`, a list `[1, 2]`); Qwen3.5's used Python's `string` (`True`). The `ref.py` adapter must
  parse typed parameter values as JSON (and keep a string parameter as its raw text), or typed arguments are
  misread.
- Fit and serving: `docs/q36-27b-config.md` (bf16 fits one VM GPU, about 72 GiB at the full 262,144 context,
  estimate; `vllm>=0.19.0` in its own venv; one model server on the GPU at a time, so not alongside an eval arm).

## What the evals say v0.2 must teach (Step 3 and the line test)

- v0.1 uses its in-weights store and then stops: no OpenCode tool calls in any agent arm (`docs/ethevals-submission.md`).
- The untrained model works in the workspace but almost never calls `eth`; a hint line raises that from 0 to 1 call
  in 20 trials, and every trial that did call an in-weights tool kept working afterwards (line test,
  `docs/dispatcher-log.md`, 2026-11-04).
- So the trajectories need both kinds of tool in one task: in-weights calls where they compute the value, and the
  agent's tools for the workspace, continuing after each in-weights result.
