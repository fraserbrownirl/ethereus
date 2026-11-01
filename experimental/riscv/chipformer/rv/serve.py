"""OpenAI-compatible API over RvChat (docs/api-spec-clean.md), seeded from route-a/shim/server.py.

Standalone: keys, consent, traces and captures live on local disk. The Modal wrapper, Postgres, R2,
per-key files and signup are the API repo's and are not here. No streaming.

    ETHEREUS_KEYS=key1,key2 uvicorn --factory chipformer.rv.serve:from_env --port 8001

Env: MODEL, FIRMWARE, SNAPSHOT (optional), ETHEREUS_DATA (default ./serve-data), CAPTURE_HMAC_KEY.
"""

import hashlib
import hmac
import io
import json
import os
import re
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from pathlib import Path

from chipformer.rv import bridge as BR
from chipformer.rv import spill
from chipformer.rv.tools import ETH_TOOL

MAX_TOKENS_DEFAULT, MAX_TOKENS_MAX = 4096, 8192
SYNC_SECONDS = 120
RATE_MINUTE, RATE_DAY = 30, 600
CONSENTS = ("off", "traces", "full")
_TURN = re.compile(r"<tool_call>(.*?)</tool_call>\s*<tool_response>\n?(.*?)\n?</tool_response>\s*"
                   r"(?:<think>\s*</think>\s*)?", re.DOTALL)


class OverWindow(Exception):
    """The model closed a tool call longer than the capture window."""


class Deadline(Exception):
    """Generation passed the sync limit."""


class Watch:
    """generate's on_token: keeps the ids, and stops before an over-window call or past a deadline.
    Called before the token is stepped, so stopping at </tool_call> means the circuit never ran."""

    def __init__(self, T: BR.Text, deadline: float | None = None) -> None:
        self.T, self.deadline = T, deadline
        self.gen: list[int] = []
        self.start: int | None = None

    def __call__(self, t: int) -> None:
        self.gen.append(t)
        if self.deadline is not None and time.time() > self.deadline:
            raise Deadline
        if t == self.T.TCO:
            self.start = len(self.gen)
        elif t == self.T.TCC and self.start is not None:
            if len(self.gen) - 1 - self.start > spill.WINDOW:
                raise OverWindow
            self.start = None


@dataclass
class Turn:
    segments: list[dict] = field(default_factory=list)
    spilled: bool = False
    text: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: str = "stop"
    lock_error: object = None


class Engine:
    """One RvChat and its firmware, one generation at a time."""

    def __init__(self, model: str, firmware: str, executor: bool = True) -> None:
        from chipformer.rv.chat import RvChat

        self.chat = RvChat(model, executor=executor)
        self.firmware = Path(firmware)
        self.lock = threading.Lock()

    def _encode(self, s: str) -> list[int]:
        return self.chat.T.tok.encode(s, add_special_tokens=False).ids

    def _visible(self, ids: list[int]) -> list[int]:
        return [i for i in ids if i < self.chat.v0]

    def run(self, messages: list[dict], max_new: int, deadline: float | None = None) -> Turn:
        with self.lock:
            return self._run(messages, max_new, deadline)

    def _run(self, messages, max_new, deadline) -> Turn:
        chat, T = self.chat, self.chat.T
        ids = chat.prompt_ids(messages, (ETH_TOOL,))
        turn = Turn(prompt_tokens=len(ids))
        n0, files, must_close, left = len(ids), {}, False, max_new
        while True:
            fw = spill.firmware_with_files(self.firmware, files)
            lock = BR.Session(T, spill.parked(fw))
            lock.history(ids)
            watch = Watch(T, deadline)
            seg = {"prompt_ids": ids, "files": files}
            turn.segments.append(seg)
            try:
                r = chat.generate(ids, spill.parked(fw), left, lock=lock, on_token=watch)
            except OverWindow:
                seg["gen_ids"] = watch.gen[:-1]  # up to, not including, the </tool_call>
                seg["owner_mask"] = lock.own[len(ids):]
                left -= len(self._visible(watch.gen))
                head = self._visible(watch.gen[:watch.start])  # through <tool_call>
                call = T.tok.decode(self._visible(watch.gen[watch.start:-1]),
                                    skip_special_tokens=False)
                cmd = spill.parse_cmd(call)
                new, files = spill.rewrite(cmd or "", lambda s: len(self._encode(s)))
                if cmd is None or not files:  # nothing the host can move: the firmware answers
                    must_close, files = False, {}
                    ids = ids + head + self._encode(call)
                else:
                    must_close, turn.spilled = True, True
                    ids = ids + head + self._encode(spill.call_text(new))
                continue
            seg["gen_ids"] = r["tokens"]
            seg["owner_mask"] = lock.own[len(ids):]
            if must_close and (not r["tokens"] or r["tokens"][0] != T.TCC):
                raise RuntimeError("spill: the model did not close the rewritten call")
            turn.lock_error = r["lock_error"]
            turn.finish_reason = "length" if r["text_tokens"] >= left else "stop"
            visible = ids[n0:] + self._visible(r["tokens"])
            turn.text = T.tok.decode(visible, skip_special_tokens=False)
            turn.completion_tokens = len(visible)
            return turn

    def replay(self, trace: dict) -> list[dict]:
        """chat.replay on each segment of a trace, on the machine that segment ran."""
        from chipformer.rv.chat import replay

        return [replay(self.chat, s["prompt_ids"], s["gen_ids"],
                       spill.firmware_with_files(self.firmware, s["files"]))
                for s in trace["segments"]]


def to_message(text: str) -> tuple[dict, list[dict]]:
    """Assistant text -> (OpenAI message with tool_calls, the tool messages the circuit answered)."""
    calls, tools = [], []
    for i, m in enumerate(_TURN.finditer(text)):
        cid = f"call_{i}"
        calls.append({"id": cid, "type": "function", "function": {
            "name": "eth", "arguments": json.dumps({"cmd": spill.parse_cmd(m.group(1)) or ""})}})
        tools.append({"role": "tool", "tool_call_id": cid, "content": m.group(2)})
    content = _TURN.sub("", text)
    content = re.sub(r"<\|im_end\|>\s*$", "", content).strip()
    msg = {"role": "assistant", "content": content}
    if calls:
        msg["tool_calls"] = calls
    return msg, tools


def _template_messages(messages: list[dict]) -> list[dict]:
    """OpenAI history -> what the chat template takes (its tool arguments are mappings)."""
    out = []
    for m in messages:
        m = {k: v for k, v in m.items() if v is not None}
        if "tool_calls" in m:
            m["tool_calls"] = [
                {**c, "function": {**c["function"],
                                   "arguments": json.loads(c["function"]["arguments"])}}
                if isinstance(c.get("function", {}).get("arguments"), str) else c
                for c in m["tool_calls"]]
        out.append(m)
    return out


def _sha256(path) -> str | None:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if path else None


def create_app(engine, keys: list[str], data_dir, firmware=None, snapshot=None,
               model_id: str = "ethereus", core_commit: str | None = None,
               hmac_key: bytes = b"", sync_seconds: float = SYNC_SECONDS):
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse, Response

    data = Path(data_dir)
    (data / "traces").mkdir(parents=True, exist_ok=True)
    consent_file = data / "consent.json"
    consent: dict = json.loads(consent_file.read_text()) if consent_file.exists() else {}
    key_ids = {k: hashlib.sha256(k.encode()).hexdigest()[:16] for k in keys}
    version = {"core_commit": core_commit, "firmware_sha256": _sha256(firmware),
               "snapshot_sha256": _sha256(snapshot), "model_id": model_id}
    pool = ThreadPoolExecutor(1)
    jobs: dict = {}
    hits: dict = {}
    created = {k: time.time() for k in key_ids.values()}
    app = FastAPI(title="ethereus")

    class ApiError(Exception):
        def __init__(self, status, code, message, headers=None):
            self.status, self.code, self.message, self.headers = status, code, message, headers

    @app.exception_handler(ApiError)
    async def _api_error(_, e: ApiError):
        return JSONResponse({"error": {"code": e.code, "message": e.message}}, e.status, e.headers)

    def auth(req: Request) -> str:
        h = req.headers.get("authorization", "")
        kid = key_ids.get(h[7:]) if h.startswith("Bearer ") else None
        if kid is None:
            raise ApiError(401, "bad_key", "missing or unknown API key")
        return kid

    def limit(kid: str) -> dict:
        now = time.time()
        seen = hits[kid] = [t for t in hits.get(kid, []) if now - t < 86400]
        minute = sum(now - t < 60 for t in seen)
        if minute >= RATE_MINUTE or len(seen) >= RATE_DAY:
            raise ApiError(429, "rate_limited", "rate limit reached", {"Retry-After": "60"})
        seen.append(now)
        return {"X-RateLimit-Remaining-Minute": str(RATE_MINUTE - minute - 1),
                "X-RateLimit-Remaining-Day": str(RATE_DAY - len(seen))}

    def finish(turn: Turn, kid: str, model: str) -> dict:
        tid = uuid.uuid4().hex
        trace = {"trace_id": tid, "key_id": kid, "timestamp": time.time(),
                 "prompt_ids": turn.segments[0]["prompt_ids"],
                 "gen_ids": turn.segments[-1]["gen_ids"],
                 "owner_mask": turn.segments[-1]["owner_mask"], "segments": turn.segments,
                 "firmware_sha256": version["firmware_sha256"],
                 "snapshot_sha256": version["snapshot_sha256"]}
        (data / "traces" / f"{tid}.json").write_text(json.dumps(trace))
        msg, tools = to_message(turn.text)
        level = consent.get(kid, "off")
        if level != "off":
            khash = hmac.new(hmac_key, kid.encode(), hashlib.sha256).hexdigest()
            d = data / "captures" / time.strftime("%Y-%m-%d") / khash
            d.mkdir(parents=True, exist_ok=True)
            rec = {k: v for k, v in trace.items() if k != "key_id"}
            rec.update(text=turn.text, tool_calls=msg.get("tool_calls", []),
                       tool_messages=tools, consent=level)
            (d / f"{tid}.jsonl").write_text(json.dumps(rec) + "\n")
        if turn.lock_error:
            raise ApiError(500, "lock_error", f"trace {tid}: {turn.lock_error}")
        return {"id": f"chatcmpl-{tid[:12]}", "object": "chat.completion",
                "created": int(time.time()), "model": model,
                "choices": [{"index": 0, "message": msg, "finish_reason": turn.finish_reason}],
                "usage": {"prompt_tokens": turn.prompt_tokens,
                          "completion_tokens": turn.completion_tokens,
                          "total_tokens": turn.prompt_tokens + turn.completion_tokens},
                "ethereus": {"trace_id": tid, "spilled": turn.spilled, "tool_messages": tools}}

    def owned_trace(tid: str, kid: str) -> dict:
        p = data / "traces" / f"{tid}.json"
        trace = json.loads(p.read_text()) if re.fullmatch(r"[0-9a-f]{32}", tid) and p.exists() else None
        if trace is None or trace["key_id"] != kid:
            raise ApiError(404, "not_found", "no such trace")
        return trace

    @app.post("/v1/chat/completions")
    async def completions(req: Request):
        kid = auth(req)
        headers = limit(kid)
        try:
            body = await req.json()
            messages = _template_messages(body["messages"])
            max_new = int(body.get("max_tokens") or MAX_TOKENS_DEFAULT)
        except (ValueError, KeyError, TypeError) as e:
            raise ApiError(422, "bad_request", f"bad request: {e}") from e
        if body.get("stream"):
            raise ApiError(422, "bad_request", "streaming is not supported")
        if not 1 <= max_new <= MAX_TOKENS_MAX:
            raise ApiError(422, "bad_request", f"max_tokens must be 1..{MAX_TOKENS_MAX}")
        model = body.get("model", model_id)
        if body.get("mode", "sync") == "async":
            jid = uuid.uuid4().hex
            jobs[jid] = (kid, model, pool.submit(engine.run, messages, max_new))
            return JSONResponse({"id": jid, "status": "queued"}, 202, headers)
        fut = pool.submit(engine.run, messages, max_new, time.time() + sync_seconds)
        import asyncio

        try:
            turn = await asyncio.wrap_future(fut)
        except Deadline as e:
            raise ApiError(408, "use_async", f"over {sync_seconds:.0f} s; use mode=async") from e
        return JSONResponse(finish(turn, kid, model), 200, headers)

    @app.get("/v1/jobs/{jid}")
    def job(jid: str, req: Request):
        kid = auth(req)
        j = jobs.get(jid)
        if j is None or j[0] != kid:
            raise ApiError(404, "not_found", "no such job")
        try:
            turn = j[2].result(timeout=0)
        except FutureTimeout:
            return JSONResponse({"status": "running"}, 202)
        return finish(turn, kid, j[1])

    @app.get("/v1/traces/{tid}")
    def get_trace(tid: str, req: Request):
        return owned_trace(tid, auth(req))

    @app.get("/v1/models")
    def models(req: Request):
        auth(req)
        return {"object": "list", "data": [{"id": model_id, "object": "model"}]}

    @app.get("/v1/keys/me")
    def me(req: Request):
        kid = auth(req)
        now = time.time()
        return {"key_id": kid, "created": created[kid], "consent": consent.get(kid, "off"),
                "files": 0, "bytes": 0,
                "requests_today": sum(now - t < 86400 for t in hits.get(kid, []))}

    @app.put("/v1/keys/me/consent")
    async def set_consent(req: Request):
        kid = auth(req)
        try:
            level = (await req.json())["consent"]
        except (ValueError, KeyError, TypeError) as e:
            raise ApiError(422, "bad_request", "body must be {\"consent\": ...}") from e
        if level not in CONSENTS:
            raise ApiError(422, "bad_request", f"consent must be one of {CONSENTS}")
        consent[kid] = level
        consent_file.write_text(json.dumps(consent))
        return {"consent": level}

    @app.get("/v1/keys/me/captures")
    def captures(req: Request):
        kid = auth(req)
        khash = hmac.new(hmac_key, kid.encode(), hashlib.sha256).hexdigest()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for p in (data / "captures").glob(f"*/{khash}/*.jsonl"):
                z.write(p, p.name)
        return Response(buf.getvalue(), media_type="application/zip")

    @app.get("/v1/version")
    def get_version():
        return version

    @app.get("/health")
    def health():
        return {"ok": True}

    return app


def from_env():
    firmware = os.environ["FIRMWARE"]
    keys = [k for k in os.environ.get("ETHEREUS_KEYS", "").split(",") if k]
    return create_app(Engine(os.environ["MODEL"], firmware, executor=True), keys,
                      os.environ.get("ETHEREUS_DATA", "serve-data"), firmware,
                      os.environ.get("SNAPSHOT"), os.environ.get("MODEL_ID", "ethereus"),
                      os.environ.get("CORE_COMMIT"),
                      os.environ.get("CAPTURE_HMAC_KEY", "").encode())
