# Dispatcher questions

Open questions for Fraser, one paragraph each with a recommendation. Work that does not depend on them continues.

## 2026-11-01 08:20 UTC, decided: client tools in the Internet eval (spec change)

Fraser: yes, pass them through.

ETH Evals' Internet mode runs OpenCode "as it ships, with the web, a shell, its environment" (`CONTEXT.md`, `docs/add-a-task.md` in the harness), so the model must be able to call OpenCode's tools. `docs/api-spec-clean.md` request-flow step 3 says to ignore client tools, and `route-a/shim/server.py` does the reverse: it offers `eth` only when the client sends no tools, so under OpenCode the model would never be told about `eth`. The firmware also runs on every `</tool_call>`, so a `bash` call would get a firmware response and never reach OpenCode. Either way the Internet row would not measure Ethereus. **Recommendation:** change step 3 for the eval: offer `eth` plus the client's tools, run `eth` calls in the circuit, and when the model calls a client tool, stop generation at `</tool_call>` before the circuit runs and return it as an OpenAI `tool_calls` response. The existing `on_token` hook allows this with no driver change. Until decided, `serve.py` follows the spec as written.

## 2026-11-01 08:20 UTC, decided: where the Concepts eval runs (updated 08:45)

Fraser: on x86 in the cloud, not the Mac. The GPU box cannot run Docker, so the harness runs on a second rented VM, 58640793. The recommendation below is superseded.

Harbor runs every trial and its verifier in containers, including `vanilla`, whose model call itself is one plain API request (`runner/ethevals/vanilla.py`, `docs/harbor.md`). The Vast box has no Docker, so no mode can run there. This Mac has a running Docker daemon (29.4.1, arm64, about 8 GB allocated), and whether the tasks' images run on arm64 is untested. Of the 25 Concepts tasks, 5 name `ANTHROPIC_API_KEY` in `task.toml` and the other 20 do not; the one I read scores with `rewardkit` and no judge. **Recommendation:** run Harbor on this Mac and keep the models on the box, reached through an SSH tunnel. Do `vanilla` first, because it needs no client tools and so does not depend on the question above; run the 20 tasks without the key now and the 5 once you supply a key. Then run Internet the same way once the client-tools question is decided. I am having the Cursor agent prove one base `vanilla` trial this way, which spends nothing beyond the running box. Larger runs wait for your answer.

## 2026-11-01 09:12 UTC, decided: spill does not work on the GPU

Fraser: agreed. Off through the evals, then (a) with its own gate 4 and a rerun of the spec's spill test. No ELF change until both eval configurations have finished on the current one. Recorded in `experimental/riscv/docs/eth-bench.md` as not met, deferred.

The API spec's spill re-prefills a rewritten call, but `core.snapshot` resets the circuit's capture state when generation starts, so the circuit sees an empty call while the lockstep reference sees the rewritten one: a lock error right after `GO`. The `@file` mechanism itself is exact on the reference machine (`test_rv_filebuf.py`, `test_rv_spill.py`); only getting a rewritten call into the running circuit fails. I turned GPU spill off (`serve.SPILL_ON_GPU`), so an over-window call gets the firmware's exact `error (too long)` and the evals do not depend on it. Two fixes exist: (a) the host writes the rewritten command into `eth_in`, already present in the bridge image, and the firmware prefers it, which is small but changes the ELF and means rerunning gate 4; (b) compute the capture state with `ir.evaluate` and inject it as the heads' initial state, as `boot_fs` does for the store, which needs no firmware change but is more delicate. **Recommendation:** leave it off through the current evals, because changing the ELF mid-run would split the rows, then do (a) after the 9B evals with its own gate 4. The cost is that the API spec's spill test stays unmet until then, and long hex through the model gets an error rather than a result.
