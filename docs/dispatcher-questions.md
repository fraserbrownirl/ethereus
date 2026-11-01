# Dispatcher questions

Open questions for Fraser, one paragraph each with a recommendation. Work that does not depend on them continues.

## 2026-11-01 08:20 UTC, open: client tools in the Internet eval (spec change)

ETH Evals' Internet mode runs OpenCode "as it ships, with the web, a shell, its environment" (`CONTEXT.md`, `docs/add-a-task.md` in the harness), so the model must be able to call OpenCode's tools. `docs/api-spec-clean.md` request-flow step 3 says to ignore client tools, and `route-a/shim/server.py` does the reverse: it offers `eth` only when the client sends no tools, so under OpenCode the model would never be told about `eth`. The firmware also runs on every `</tool_call>`, so a `bash` call would get a firmware response and never reach OpenCode. Either way the Internet row would not measure Ethereus. **Recommendation:** change step 3 for the eval: offer `eth` plus the client's tools, run `eth` calls in the circuit, and when the model calls a client tool, stop generation at `</tool_call>` before the circuit runs and return it as an OpenAI `tool_calls` response. The existing `on_token` hook allows this with no driver change. Until decided, `serve.py` follows the spec as written.

## 2026-11-01 08:20 UTC, open: where the Concepts eval runs

The harness runs trials through Harbor with Docker, and the job config uses `n_attempts: 2` and an Anthropic-paid judge (`runner/ethevals/job.yaml`, `key_env: ANTHROPIC_API_KEY`). The Vast box has no `docker` binary and no socket, and `route-a-brief.md` never accounted for this, so the Cursor agent's planned base and merged Concepts runs may be unable to start there. I have not yet tested whether a non-Docker Harbor environment exists. All 25 Concepts tasks also allow `vanilla` mode (one API call, no agent, no tools), which needs neither client tools nor OpenCode for the model call. **Recommendation:** get a first comparable number from `vanilla` for base and merged, which the board already supports, then run Internet from a Docker-capable machine pointed at the box's model endpoint through a tunnel. This needs an `ANTHROPIC_API_KEY` for the judge from you if the tasks use it.
