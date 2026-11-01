# Ethereus API — Build Spec (clean)

Nov 1, 2026 · @Fraser Brown

This replaces *Ethereus API — Build Spec v1*. Build only what is here.

## Scope

An OpenAI-compatible HTTP API in front of `chipformer.rv.chat.RvChat` running the Ethereus model with `eth-bridge.elf` and `ethskills.fs`. Two tools, `eth` and `files`, always on. Per-key API keys, per-key files, async jobs, replayable traces, opt-in capture. Deployed on Modal. Nothing else.

## Components

| Component | Code | Runs on |
| --- | --- | --- |
| `serve.py` | FastAPI app: auth, endpoints, spill, capture. Built first for the eval stage, reused here unchanged except for the Modal wrapper. | Modal, `@modal.asgi_app`, CPU container |
| `Worker` | `@app.cls(gpu="L4")`; `@modal.enter` loads one `RvChat` with firmware + snapshot; `@modal.method run(session_blob, prompt_ids, max_new) -> (gen_ids, owner_mask, state_delta)`; `@modal.concurrent(max_inputs=8)` | Modal, GPU container |
| Weights + snapshot | `modal.Volume` `ethereus-models`, read-only: merged checkpoint, `eth-bridge.elf`, `ethskills.fs`, `builtins.gen.json` | Modal Volume |
| Postgres | tables `keys`, `jobs`, `files_index`; Neon or Supabase smallest tier | external |
| Object store | per-key file blobs, traces, captures; Cloudflare R2 bucket `ethereus-data`, mounted with `CloudBucketMount` | R2 |

Repo: `fraserbrownirl/ethereus-api`. Layout: `app.py` (Modal app, Worker class, asgi mount), `serve/` (FastAPI routes, copied from core `chipformer/rv/serve.py`), `db/` (SQL migrations), `tests/`. The core repo is a git submodule pinned to a commit; `/v1/version` reports that commit, the firmware hash and the snapshot hash.

## Request flow

`POST /v1/chat/completions`:

1. Read `Authorization: Bearer <key>`; look up the key in Postgres; 401 if missing or revoked.
2. Load the key's file rows from R2 (`keys/<key_id>/state.bin`); load the shared snapshot rows from the Volume (cached in the worker after first use). Union them into `session_blob`.
3. Tokenize messages with the Qwen chat template, tools = `eth` + `files` schemas from the core repo's `tools.py`. Ignore any client-supplied `tools`.
4. If `mode == "async"`: `Worker.run.spawn(...)`, insert a `jobs` row with the Modal call id, return `{"id": job_id, "status": "queued"}` with HTTP 202. Else call `Worker.run.remote(...)` and continue.
5. Worker generates. Each time the model emits `</tool_call>`, the worker applies spill (section 6) before the circuit runs the call. Generation ends at EOS, `max_new`, or 120 s wall; over 120 s in sync mode returns `408 use_async`.
6. Worker returns `gen_ids`, `owner_mask`, `state_delta`. If `state_delta` is non-empty, write it to R2 under a new version; on version conflict retry the whole request once, then `409 state_conflict`.
7. Write the trace (`traces/<trace_id>.json`: prompt ids, gen ids, owner mask, firmware hash, snapshot hash, key id, timestamp) to R2.
8. If the key's consent is `traces` or `full`, write the capture record (section 7).
9. Build the OpenAI response: assistant text, `tool_calls` and `tool` messages for each call the circuit answered, `usage`, `finish_reason`. Add `ethereus: {trace_id, spilled: bool}`. Return 200.

`GET /v1/jobs/{id}`: look up the Modal call id; `FunctionCall.from_id(id).get(timeout=0)`; on `TimeoutError` return 202 `{"status": "running"}`; on result run steps 6–9 and return the completion body.

## Endpoints

| Method, path | Auth | Request | Response |
| --- | --- | --- | --- |
| `POST /v1/chat/completions` | key | OpenAI chat body + optional `mode: "sync"\|"async"` (default sync), `max_tokens` (default 4096, max 8192) | OpenAI completion + `ethereus` field; 202 with job id in async mode |
| `GET /v1/jobs/{id}` | key, must own the job | — | 202 running, or the completion body |
| `GET /v1/traces/{id}` | key, must own the trace | — | the trace JSON |
| `GET /v1/models` | key | — | `[{"id": "ethereus-4b"}]` (id from config) |
| `GET /v1/keys/me` | key | — | `{key_id, created, consent, files: n, bytes: n, requests_today}` |
| `PUT /v1/keys/me/consent` | key | `{"consent": "off"\|"traces"\|"full"}` | 200 |
| `GET /v1/keys/me/files` | key | — | `[{name, bytes, updated}]` |
| `PUT /v1/keys/me/files/{name}` | key | raw body ≤ 16 KB | 200; 413 over the per-key cap; 403 if name starts with `ethskills/` or `builtin/` |
| `DELETE /v1/keys/me/files/{name}` | key | — | 200; 403 on reserved prefixes |
| `GET /v1/keys/me/captures` | key | — | zip of the key's capture records |
| `POST /v1/signup` | none | `{email, project, chain, consent}` | 201 `{key}` if under the cap; 202 `{waitlist_position}` otherwise. Rate-limited 5/hour/IP. |
| `GET /v1/version` | none | — | `{core_commit, firmware_sha256, snapshot_sha256, model_id}` |
| `GET /health` | none | — | 200 |

Errors: OpenAI shape, `{"error": {"code": ..., "message": ...}}`. Codes: `bad_key` 401, `forbidden` 403, `not_found` 404, `use_async` 408, `state_conflict` 409, `state_cap` 413, `bad_request` 422, `rate_limited` 429 (+ `Retry-After`), `no_workers` 503.

Rate limits per key: 30 requests/min, 600/day; configurable per key in Postgres. Headers `X-RateLimit-Remaining-Minute`, `X-RateLimit-Remaining-Day`.

## Per-key state and the shared snapshot

The model's filesystem is DeltaNet rows. A session is the union of two row sets:

| Layer | Source | Writable in session | Saved after the call |
| --- | --- | --- | --- |
| Shared | `ethskills.fs` on the Volume; names under `ethskills/` and `builtin/` | no | never |
| Key | `keys/<key_id>/state.bin` in R2, versioned | yes | only if `state_delta` is non-empty |

Rules:

- Per-key cap: 64 files, 16 KB total. Enforced in `serve.py` before the worker runs (`413 state_cap`).
- Reserved prefixes `ethskills/` and `builtin/`: writes rejected in `serve.py` and in `RvChat` (`403` / `error (read-only)`).
- Versioning: `state.bin` carries a version integer; the write in step 6 is conditional on the version read in step 2 (R2 `If-Match` on the ETag). Conflict → retry once → `409`.
- The worker caches the shared layer's prefilled state as a tensor after first load and copies it per request. Measure the per-request cost in the first deploy; if over 2 s, note it and keep going (optimisation is later).
- The conversation is not stored. Clients send full message history each call.

## Large arguments: spill

The firmware's tool-call capture is 80 Qwen tokens. The worker removes this limit from the model's point of view.

In the worker's generation loop, when the model emits `</tool_call>`:

1. Parse the call. If it tokenizes to ≤ 80 tokens, run it as is.
2. Otherwise, for each argument over 32 tokens: write its bytes to the key layer as `spill/<n>` and replace the argument with `@spill/<n>`. Re-prefill the rewritten call. Run it.
3. The firmware's `arg_bytes()` reads `@file` arguments from the bridge file buffer (core repo, Phase 2).
4. After the response, delete `spill/*` from the key layer. Spill files never count toward the per-key cap and never persist.
5. Mark the turn `spilled: true` in the `ethereus` response field. The returned `tool_calls` show the rewritten form.

The same helper is used by `serve.py` standalone (eval stage) and inside the Modal worker. One implementation, `chipformer/rv/spill.py` in the core repo.

## Capture and consent

Consent is a per-key value: `off` (default), `traces`, `full`. Set at signup or via `PUT /v1/keys/me/consent`.

| Level | Written to `captures/<date>/<key_hash>/<trace_id>.jsonl` |
| --- | --- |
| `off` | nothing |
| `traces` | prompt ids + text, generated ids + text, tool calls and responses, trace ids, owner mask, firmware hash, snapshot hash, chain (from signup), timestamp, consent level |
| `full` | same as `traces` (reserved for later fields; identical in v1) |

Rules:

- Record schema is `capture.schema.json` from the core repo; the local driver's `capture.py` writes the same schema.
- `key_hash` = HMAC-SHA256 of key\_id with a server secret; the key id itself never appears in a record.
- No IP, no email, no user agent in records.
- `GET /v1/keys/me/captures` returns the key's own records as a zip.
- Operational logs (request id, key id, timestamps, token counts, error code, latency) are written at every consent level, no content, 30-day retention.

## Modal deployment

`app.py`:

```python
import modal

image = (modal.Image.from_registry("nvidia/cuda:12.8.1-devel-ubuntu24.04", add_python="3.12")
         .entrypoint([])
         .apt_install("git")
         .pip_install("torch==<pin>", "transformers==<pin>", "triton==<pin>", gpu="L4")
         .pip_install("fastapi", "uvicorn", "psycopg[binary]", "boto3")
         .add_local_python_source("chipformer")   # from the core submodule
         .add_local_python_source("serve"))

models = modal.Volume.from_name("ethereus-models", create_if_missing=True)
data = modal.CloudBucketMount("ethereus-data", bucket_endpoint_url="<r2 endpoint>",
                              secret=modal.Secret.from_name("r2"))

app = modal.App("ethereus-api")

@app.cls(image=image, gpu="L4", volumes={"/models": models, "/data": data},
         secrets=[modal.Secret.from_name("ethereus")],
         timeout=600, scaledown_window=300,
         min_containers=0, max_containers=10)
@modal.concurrent(max_inputs=8)
class Worker:
    @modal.enter()
    def load(self):
        from chipformer.rv.chat import RvChat
        self.chat = RvChat("/models/ethereus", firmware="/models/eth-bridge.elf",
                           fs="/models/ethskills.fs", executor=True)

    @modal.method()
    def run(self, session_blob: bytes, prompt_ids: list[int], max_new: int):
        return self.chat.run(session_blob, prompt_ids, max_new)   # (gen_ids, owner_mask, state_delta)

@app.function(image=image, volumes={"/data": data},
              secrets=[modal.Secret.from_name("ethereus")], min_containers=1)
@modal.asgi_app()
def api():
    from serve.app import create_app
    return create_app(worker=Worker())
```

Config values:

| Setting | Value |
| --- | --- |
| GPU | `L4` for the 4B; `A100-80GB` if Route A's backbone is the one deployed |
| `max_containers` | open-call cap ÷ 8 (10 for 80 teams) |
| `min_containers` on `Worker` | 0 in `dev`; 1 during the open call |
| `scaledown_window` | 300 s |
| `timeout` | 600 s on `Worker.run`; HTTP sync path returns `408 use_async` at 120 s |
| Environments | `dev`, `hackathon` (`modal environment create`); Postgres URL and R2 creds differ per env via Secrets |
| Secrets | `ethereus` (DATABASE\_URL, CAPTURE\_HMAC\_KEY), `r2` (AWS\_ACCESS\_KEY\_ID, AWS\_SECRET\_ACCESS\_KEY) |
| Deploy | `modal deploy app.py --env hackathon`; URL `https://<workspace>--ethereus-api-api.modal.run`; custom domain requires Team plan, skip in v1 |
| Upload | `modal volume put ethereus-models <local checkpoint dir> /ethereus` etc.; record sha256 of each file in `/v1/version` config |

Two things to measure on the first `dev` deploy and record in the repo README: cold start of `Worker.load` from the Volume (seconds), and per-request shared-snapshot load cost. No action on the numbers in v1 beyond `min_containers=1`.

## Tests that must pass

All in `tests/`, run in CI against a `dev` deploy with one worker.

- [ ] Auth: no key 401; revoked key 401; valid key 200.
- [ ] Tools: a prompt asking for `transfer(address,uint256)`'s selector returns `0xa9059cbb` via an `eth` tool call; the trace's owner mask marks the response positions as circuit-owned; `replay.py` on the trace is clean.
- [ ] Files: `PUT /v1/keys/me/files/notes.txt`, then a prompt that calls `files get notes.txt` returns the content; `PUT` to `ethskills/x` is 403; the 65th file is 413.
- [ ] Shared snapshot: `files list` from a fresh key shows the `ethskills/` manifest; two keys cannot see each other's files.
- [ ] Spill: `eth decode` with 10 words of calldata and `eth run erc20` with real calldata both succeed; the response has `spilled: true`; `spill/*` is absent from the key's files afterwards.
- [ ] Async: a `run` in async mode returns 202 with a job id in under 2 s; polling returns 202 then the completion; the completion equals a sync call's.
- [ ] 408: a sync call that would exceed 120 s returns `use_async`.
- [ ] Concurrency: 20 parallel calls on one key that each `files put` a different name; all 64-cap-respecting writes land; at most one 409 per conflicting pair, retried.
- [ ] Capture: at `off` no record; at `traces` a record with the schema's fields and no key id, IP or email; a synthetic-secret string in the prompt appears in the record only if the user put it there (no redaction in v1; documented).
- [ ] Signup: under the cap returns a key; over the cap returns a waitlist position; 6th signup in an hour from one IP is 429.
- [ ] Version: `/v1/version` hashes match the Volume's files.
- [ ] Load: 50 simulated clients at 1 call/2 min for 30 min on 10 workers; p50 queue wait under 5 s; no 5xx.

## Not in v1

Signing, policies, signers, attestation, spend ledger, nonce tracking, broadcast, streaming, webhooks, custom domain, Prometheus, dashboard beyond signup, capture deletion, redaction, `verify` tool, non-EVM chains, backbones other than the one on the Volume.
