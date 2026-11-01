# route-a/shim — OpenAI-compat shim for Ethereus RvChat

Thin FastAPI shim: `POST /v1/chat/completions` → `RvChat.generate` (tool schema from `chipformer/rv/tools.py` / `env/eth/` bridge `eth` tools). No other tools, no prompt engineering beyond harness system line.

## Run (on GPU host, after merge)

```bash
# 1) Merge must exist
ls dist/q9-rv1/config.json

# 2) Launch shim (reads MODEL + FIRMWARE from env)
MODEL=dist/q9-rv1 FIRMWARE=tests/data/rv/eth-bridge.elf \
  uvicorn shim.server:app --host 0.0.0.0 --port 8001 --workers 1 &

# 3) Smoke test (use 4B on a 24GB card while 9B merges)
MODEL=dist/q4-rv1 python -m pytest route-a/shim/test_smoke.py -v
# or curl:
curl -s http://localhost:8001/v1/models | jq
curl -s http://localhost:8001/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"ethereus","messages":[{"role":"user","content":"Use eth: what is the selector of transfer(address,uint256)?"}],"temperature":0}' | jq .choices[0].message.content
```

## Notes

- If Muse's gateway exists on `main` (`gateway/` or `route-a/gateway/`), use it instead and delete this shim.
- Temperature is forced to 0; streaming is not required (harness may request it — return non-streamed).
- Trajectories are written verbatim by the harness; this shim just proxies.
