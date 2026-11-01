# route-a/shim — OpenAI-compat shim for Ethereus RvChat

Thin FastAPI shim: `POST /v1/chat/completions` → `RvChat.generate` with the
`eth` tool (`eth: <cmd>` → `eth-bridge.elf` via the RV32 bridge). No other
tools, no prompt engineering beyond the harness system line.

- Tool schema is `ETH_TOOL` in `server.py` (`name=eth`, `cmd` string); it
  matches `chipformer/rv/eth.py::_tool_call_text` which the bridge firmware
  parses (`<function=eth><parameter=cmd>…</parameter></function>`).
- Each request builds a fresh `ref.Machine` from `FIRMWARE`, runs `prompt_ids`
  with `tools=[ETH_TOOL]`, and calls `RvChat.generate(ids, machine, lock=…)`
  with a `BR.Session` lock so mismatches surface immediately.

## Run (on GPU host, after merge)

```bash
# 1) Merge must exist (or use an existing 4B for the smoke test)
ls dist/q9-rv1/config.json   # or dist/q4-rv1 for the 4B smoke test

# 2) From repo root — the shim imports chipformer.rv from experimental/riscv
MODEL=dist/q9-rv1 FIRMWARE=experimental/riscv/tests/data/rv/eth-bridge.elf \
  uvicorn route-a.shim.server:app --host 0.0.0.0 --port 8001 --workers 1 &

# 3) Health check
curl -s http://localhost:8001/health | jq
curl -s http://localhost:8001/v1/models | jq

# 4) Smoke test
./route-a/shim/test_smoke.sh http://localhost:8001

# Or with an explicit MODEL for the 4B smoke test while 9B merges:
MODEL=dist/q4-rv1 uvicorn route-a.shim.server:app --port 8001 --workers 1 &
MODEL=dist/q4-rv1 ./route-a/shim/test_smoke.sh
```

Notes

- If Muse's gateway exists on `main` (`gateway/` or `route-a/gateway/`), use
  it instead and delete this shim.
- Temperature is forced to 0; streaming is rejected (harness must send
  `stream:false`). Trajectories are written verbatim by the harness.
