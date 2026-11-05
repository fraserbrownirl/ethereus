"""OpenAI-compatible API over RvChat (docs/api-spec-clean.md), seeded from route-a/shim/server.py.

Standalone: keys, consent, traces and captures live on local disk. The Modal wrapper, Postgres, R2,
per-key files and signup are the API repo's and are not here.

Eval options (Fraser, 2026-11-01), recorded in docs/ethevals-submission.md. Client tools are offered
beside ours: our calls run in the circuit, a client tool call stops generation at its </tool_call>
and goes back as OpenAI ``tool_calls``. ``buffered_stream`` answers ``stream: true`` as SSE after
generation ends, with keepalive comments meanwhile, because OpenCode only speaks streaming; tokens
are not streamed.

    ETHEREUS_KEYS=key1,key2 uvicorn --factory chipformer.rv.serve:from_env --port 8001

Env: MODEL, FIRMWARE, SNAPSHOT (optional), ETHEREUS_DATA (default ./serve-data), CAPTURE_HMAC_KEY,
ETHEREUS_SYNC_SECONDS, ETHEREUS_BUFFERED_STREAM=1, ETHEREUS_BASE=1 (stock checkpoint, no circuit),
ETHEREUS_SYSTEM_LINE (the +Skills arm's one line about es/), ETHEREUS_RATE_MINUTE, ETHEREUS_RATE_DAY.
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
MAX_CHIP = 1_000_000  # generate defaults to 60k, which cuts off a run: balanceOf is ~110k
CONSENTS = ("off", "traces", "full")
# The loop guard (Fraser, 2026-11-05): a model can repeat a call the firmware rejects until the client times out
# (the untrained checkpoint sent `decode address uint256 @spill/0` 123 times in one request). Once the same call has
# had the same error LOOP_LIMIT times in a request, it no longer runs: the host answers LOOP_REPLY in its place.
# If the model sends a call it was already given LOOP_REPLY for, the turn ends there: the untrained checkpoint
# ignored the reply and sent the stopped call 115 times, one restart each, past the client's timeout.
LOOP_LIMIT = 3
LOOP_REPLY = "this call isn't supported; answer without it"
_RESPONSE = re.compile(r"<tool_call>(.*?)</tool_call>\s*<tool_response>\n?(.*?)\n?</tool_response>", re.DOTALL)
_TURN = re.compile(r"<tool_call>(.*?)</tool_call>\s*<tool_response>\n?(.*?)\n?</tool_response>\s*"
                   r"(?:<think>\s*</think>\s*)?", re.DOTALL)


# core.snapshot clears the capture when generation restarts, so a spilled command reaches the
# firmware through eth_in (spill.py). Calls the host cannot move get the firmware's error (too long).
SPILL_ON_GPU = True


class OverWindow(Exception):
    """The model closed a tool call longer than the capture window."""


class Deadline(Exception):
    """Generation passed the sync limit."""


class Stopped(Exception):
    """The model closed a call the loop guard no longer runs."""


def stopped_calls(text: str) -> set[str]:
    """The calls in ``text`` answered with the same error at least LOOP_LIMIT times."""
    n: dict[tuple[str, str], int] = {}
    for call, resp in _RESPONSE.findall(text):
        if resp.strip().startswith("error"):
            k = (call.strip(), resp.strip())
            n[k] = n.get(k, 0) + 1
    return {call for (call, _), c in n.items() if c >= LOOP_LIMIT}


class ClientCall(Exception):
    """The model closed a call to a tool the client runs, not the circuit."""


_FN = re.compile(r"<function=([^>\n]+)>")
_PARAM = re.compile(r"<parameter=([^>\n]+)>\n?(.*?)\n?</parameter>", re.DOTALL)


def parse_call(text: str, schemas: list[dict] = ()) -> dict | None:
    """Qwen's call text -> {name, arguments}. Non-string parameters are decoded per the schema."""
    m = _FN.search(text)
    if m is None:
        return None
    name, args = m.group(1).strip(), {}
    props = next((t["function"].get("parameters", {}).get("properties", {}) for t in schemas
                  if t.get("function", {}).get("name") == name), {})
    for k, v in _PARAM.findall(text):
        args[k] = v
        if props.get(k, {}).get("type", "string") != "string":
            try:
                args[k] = json.loads(v)
            except ValueError:
                pass
    return {"name": name, "arguments": args}


class Watch:
    """generate's on_token: keeps the ids, and stops before an over-window call or past a deadline.
    Called before the token is stepped, so stopping at </tool_call> means the circuit never ran."""

    def __init__(self, T: BR.Text, deadline: float | None = None, ours: set[str] | None = None,
                 decode=None, movable=None, stop=None, open_call: list[int] | None = None) -> None:
        self.T, self.deadline, self.ours, self.decode, self.movable = T, deadline, ours, decode, movable
        self.stop = stop  # stop(call ids) is True for a call the loop guard no longer runs
        self.open = open_call  # a spilled call's ids, opened in the prompt: the model closes it first
        self.spill = SPILL_ON_GPU
        self.gen: list[int] = []
        self.start: int | None = None

    def __call__(self, t: int) -> None:
        self.gen.append(t)
        if self.deadline is not None and time.time() > self.deadline:
            raise Deadline
        if t == self.T.TCO:
            self.start = len(self.gen)
        elif t == self.T.TCC and self.start is not None:
            if self.ours is not None:  # client tools were offered
                m = _FN.search(self.decode(self.gen[self.start:-1]))
                if m is None or m.group(1).strip() not in self.ours:
                    raise ClientCall
            if self.stop is not None and self.stop(self.gen[self.start:-1]):
                raise Stopped
            if (self.spill and len(self.gen) - 1 - self.start > spill.WINDOW
                    and self.movable is not None and self.movable(self.gen[self.start:-1])):
                raise OverWindow
            self.start = None
        elif t == self.T.TCC and self.open is not None:
            if self.stop is not None and self.stop(self.open + self.gen[:-1]):
                raise Stopped
            self.open = None


@dataclass
class Turn:
    segments: list[dict] = field(default_factory=list)
    spilled: bool = False
    text: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: str = "stop"
    lock_error: object = None
    client_call: dict | None = None
    loop_guard: list[str] = field(default_factory=list)  # each call the loop guard answered
    loop_guard_ended: bool = False  # the model repeated a call it was told to answer without: no more calls this turn


def with_system_line(messages: list[dict], line: str | None) -> list[dict]:
    """The chat template allows one system message, first, and a client may send its own: the line joins it."""
    if not line:
        return messages
    if messages and messages[0].get("role") == "system":
        return [{**messages[0], "content": f"{line}\n\n{messages[0].get('content') or ''}"}, *messages[1:]]
    return [{"role": "system", "content": line}, *messages]


class Engine:
    """One RvChat and its firmware, one generation at a time."""

    def __init__(self, model: str, firmware: str, executor: bool = True, snapshot=None,
                 system_line: str | None = None) -> None:
        from chipformer.rv.chat import RvChat

        self.chat = RvChat(model, executor=executor)
        self.firmware = Path(firmware)
        self.lock = threading.Lock()
        self.system_line = system_line
        self.store_tools: list[dict] = []
        self.ref_device = None
        if getattr(self.chat, "store_cfg", None) is not None:  # a store checkpoint: +Skills
            from chipformer.rv import store as ST

            self.store_tools = list(ST.TOOLS)
            if snapshot:
                from chipformer.rv.chat import load_store_file

                self.chat.store_snap = load_store_file(snapshot)
                man = Path(snapshot).with_name("ethskills.manifest.json")
                if man.exists():  # replay's reference holds the files the circuit was loaded with
                    self.ref_device = ST.device_from_manifest(self.chat.T, json.loads(man.read_text()), self.chat.store_cfg,
                                                              spill.parked(self.firmware))

    def _encode(self, s: str) -> list[int]:
        return self.chat.T.tok.encode(s, add_special_tokens=False).ids

    def _visible(self, ids: list[int]) -> list[int]:
        return [i for i in ids if i < self.chat.v0]

    def _echo(self, message: str) -> list[int]:
        """The text tokens the circuit echoes after a call that prints ``message``."""
        P, NL = self.chat.T.printable, self.chat.T.NL
        byte = lambda s: [P[b] for b in s.encode()]
        return [*byte("\n"), P[256], *byte(f"\n{message}\n"), P[257], NL]  # P[256:258]: <tool_response> pair

    def run(self, messages: list[dict], max_new: int, deadline: float | None = None,
            client_tools: list[dict] = ()) -> Turn:
        with self.lock:
            banned = self.chat.banned
            try:
                return self._run(messages, max_new, deadline, list(client_tools))
            finally:
                self.chat.banned = banned  # the loop guard bans <tool_call> for one turn only

    def _run(self, messages, max_new, deadline, client_tools) -> Turn:
        chat, T = self.chat, self.chat.T
        decode = lambda g: T.tok.decode(self._visible(g), skip_special_tokens=False)
        mine = (ETH_TOOL, *self.store_tools)
        names = {t["function"]["name"] for t in mine}
        ours = names if client_tools else None
        client_tools = [t for t in client_tools if t.get("function", {}).get("name") not in names]
        messages = with_system_line(messages, self.system_line)
        ids = chat.prompt_ids(messages, (*mine, *client_tools))
        turn = Turn(prompt_tokens=len(ids))
        n0, files, eth_in, left = len(ids), {}, None, max_new
        reply = self._echo(LOOP_REPLY)

        def stop(call: list[int]) -> bool:
            return decode(call).strip() in stopped_calls(decode(ids[n0:] + self._visible(watch.gen)))

        def plan(call: str):
            cmd = spill.parse_cmd(call)
            new, moved = spill.rewrite(cmd or "", lambda s: len(self._encode(s)))
            ok = cmd is not None and moved and len(new.encode()) < spill.ETH_IN_SIZE
            return (new, moved) if ok else None

        while True:
            fw = spill.firmware_with_files(self.firmware, files, eth_in)
            # The reference does not know a store loaded from a snapshot (chat.py: --memory)
            lock = None if self.store_tools else BR.Session(T, spill.parked(fw))
            if lock is not None:
                lock.history(ids)
            opened = ids[len(ids) - ids[::-1].index(T.TCO):] if eth_in is not None else None
            watch = Watch(T, deadline, ours, decode, lambda g: plan(decode(g)) is not None, stop, opened)
            seg = {"prompt_ids": ids, "files": files, "eth_in": eth_in}
            turn.segments.append(seg)
            try:
                r = chat.generate(ids, spill.parked(fw), left, max_chip=MAX_CHIP, lock=lock,
                                  on_token=watch)
            except ClientCall:
                seg["gen_ids"] = watch.gen[:-1]  # the circuit never saw this </tool_call>
                seg["owner_mask"] = lock.own[len(ids):] if lock is not None else []
                turn.client_call = parse_call(decode(watch.gen[watch.start:-1]), client_tools)
                turn.finish_reason = "tool_calls"
                visible = ids[n0:] + self._visible(watch.gen[:watch.start - 1])  # before <tool_call>
                turn.text = T.tok.decode(visible, skip_special_tokens=False)
                turn.completion_tokens = len(visible) + 1
                return turn
            except OverWindow:
                seg["gen_ids"] = watch.gen[:-1]  # up to, not including, the </tool_call>
                seg["owner_mask"] = lock.own[len(ids):] if lock is not None else []
                left -= len(self._visible(watch.gen))
                head = self._visible(watch.gen[:watch.start])  # through <tool_call>
                call = T.tok.decode(self._visible(watch.gen[watch.start:-1]),
                                    skip_special_tokens=False)
                eth_in, files = plan(call)
                turn.spilled = True
                ids = ids + head + self._encode(spill.call_text(eth_in))
                continue
            except Stopped:
                seg["gen_ids"] = watch.gen[:-1]  # the circuit never saw this </tool_call>
                seg["owner_mask"] = lock.own[len(ids):] if lock is not None else []
                full = ids + self._visible(watch.gen)  # through </tool_call>
                tco = len(full) - 1 - full[::-1].index(T.TCO)
                call = decode(full[tco + 1:-1]).strip()
                if call in turn.loop_guard and not turn.loop_guard_ended:
                    # told once already: no more calls this turn, so it answers in text (ending here sent
                    # clients an empty answer, which they retry)
                    turn.loop_guard_ended = True
                    chat.banned = (*chat.banned, T.TCO)
                left -= len(self._visible(watch.gen))
                ids = full
                turn.loop_guard.append(call)
                ids = ids + reply  # history: no run follows its </tool_call>
                files, eth_in = {}, None
                continue
            seg["gen_ids"] = r["tokens"]
            seg["owner_mask"] = lock.own[len(ids):] if lock is not None else []
            if eth_in is not None and (not r["tokens"] or r["tokens"][0] != T.TCC):
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

        kw = {"device": self.ref_device} if self.ref_device is not None else {}
        return [replay_segment(lambda g, s=s: replay(
                    self.chat, s["prompt_ids"], g,
                    spill.firmware_with_files(self.firmware, s["files"], s.get("eth_in")), **kw),
                s, self.chat.T.TCC) for s in trace["segments"]]


def replay_segment(run, seg: dict, tcc: int) -> dict:
    """``run(gen_ids)`` on a segment. A token cap can end a stream inside a run, which replay sees
    as the reference wanting tokens at the very end. That is no mismatch: check the stream up to
    the unfinished run's </tool_call>. Anything else stays a failure."""
    gen = seg["gen_ids"]
    r = run(gen)
    if r["ok"] or r.get("got") or r.get("where") != len(seg["prompt_ids"]) + len(gen):
        return r
    if tcc not in gen:
        return r
    cut = len(gen) - 1 - gen[::-1].index(tcc)
    return {**run(gen[:cut]), "truncated_run": True, "checked_tokens": cut}


class BaseEngine:
    """The stock checkpoint behind the same API and the same parsing, so the two arms differ only
    in the model. Offers the client's tools only: base Qwen has no circuit."""

    def __init__(self, model: str) -> None:
        import torch
        from transformers import AutoTokenizer, Qwen3_5ForCausalLM

        self.torch = torch
        self.tok = AutoTokenizer.from_pretrained(model)
        self.model = Qwen3_5ForCausalLM.from_pretrained(model, dtype=torch.bfloat16).to("cuda").eval()
        self.tcc = self.tok.convert_tokens_to_ids("</tool_call>")
        self.tco = self.tok.convert_tokens_to_ids("<tool_call>")
        self.lock = threading.Lock()

    def run(self, messages: list[dict], max_new: int, deadline: float | None = None,
            client_tools: list[dict] = ()) -> Turn:
        from transformers import StoppingCriteria, StoppingCriteriaList

        class _Stop(StoppingCriteria):
            def __call__(self, *a, **k):
                return deadline is not None and time.time() > deadline

        enc = self.tok.apply_chat_template(messages, tools=list(client_tools) or None,
                                           add_generation_prompt=True, enable_thinking=False,
                                           return_dict=True)  # as RvChat.prompt_ids: same prompt
        ids = list(enc["input_ids"])
        eos = [248046]  # RvChat.eos and RvChat.banned: both arms stop and ban on the same tokens
        with self.lock, self.torch.no_grad():
            out = self.model.generate(self.torch.tensor([ids], device="cuda"), do_sample=False,
                                      max_new_tokens=max_new, eos_token_id=[*eos, self.tcc],
                                      bad_words_ids=[[248045], [248044]],
                                      stopping_criteria=StoppingCriteriaList([_Stop()]))
        if deadline is not None and time.time() > deadline:
            raise Deadline
        gen = out[0, len(ids):].tolist()
        turn = Turn(prompt_tokens=len(ids), completion_tokens=len(gen),
                    segments=[{"prompt_ids": ids, "gen_ids": gen, "owner_mask": [], "files": {}}])
        if gen and gen[-1] == self.tcc and self.tco in gen:
            at = len(gen) - 1 - gen[::-1].index(self.tco)
            turn.client_call = parse_call(self.tok.decode(gen[at + 1:-1]), list(client_tools))
            turn.text = self.tok.decode(gen[:at], skip_special_tokens=False)
            turn.finish_reason = "tool_calls"
        else:
            turn.text = self.tok.decode(gen, skip_special_tokens=False)
            turn.finish_reason = "length" if len(gen) >= max_new else "stop"
        return turn


def to_message(text: str, client_call: dict | None = None,
               keep_ours: bool = False) -> tuple[dict, list[dict]]:
    """Assistant text -> (OpenAI message with tool_calls, the tool messages the circuit answered).

    ``keep_ours``: client tools were offered, so the client would try to run any tool_calls it
    sees, and it sends this content back as history. Our calls stay in the content as written."""
    calls, tools = [], []
    for i, m in enumerate(_TURN.finditer(text)):
        cid = f"call_{i}"
        fn = _FN.search(m.group(1))
        calls.append({"id": cid, "type": "function", "function": {
            "name": fn.group(1).strip() if fn else "eth", "arguments": json.dumps(dict(_PARAM.findall(m.group(1))))}})
        tools.append({"role": "tool", "tool_call_id": cid, "content": m.group(2)})
    content = text if keep_ours else _TURN.sub("", text)
    content = re.sub(r"(?:\s*<\|(?:im_end|endoftext)\|>)+\s*$", "", content).strip()
    if keep_ours:
        calls = []
    if client_call is not None:
        calls.append({"id": f"call_{uuid.uuid4().hex[:12]}", "type": "function", "function": {
            "name": client_call["name"], "arguments": json.dumps(client_call["arguments"])}})
    msg = {"role": "assistant", "content": content}
    if calls:
        msg["tool_calls"] = calls
    return msg, tools


def sse_chunks(body: dict) -> list[str]:
    """A finished completion as OpenAI stream chunks."""
    choice = body["choices"][0]
    msg = choice["message"]
    base = {k: body[k] for k in ("id", "created", "model")} | {"object": "chat.completion.chunk"}
    delta: dict = {"role": "assistant", "content": msg["content"]}
    if msg.get("tool_calls"):
        delta["tool_calls"] = [{"index": i, **c} for i, c in enumerate(msg["tool_calls"])]
    frames = [{**base, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
              {**base, "choices": [{"index": 0, "delta": {},
                                    "finish_reason": choice["finish_reason"]}],
               "usage": body["usage"], "ethereus": body["ethereus"]}]
    return [f"data: {json.dumps(f)}\n\n" for f in frames] + ["data: [DONE]\n\n"]


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
               hmac_key: bytes = b"", sync_seconds: float = SYNC_SECONDS,
               buffered_stream: bool = False):
    import asyncio

    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse, Response, StreamingResponse

    data = Path(data_dir)
    (data / "traces").mkdir(parents=True, exist_ok=True)
    consent_file = data / "consent.json"
    consent: dict = json.loads(consent_file.read_text()) if consent_file.exists() else {}
    key_ids = {k: hashlib.sha256(k.encode()).hexdigest()[:16] for k in keys}
    version = {"core_commit": core_commit, "firmware_sha256": _sha256(firmware),
               "snapshot_sha256": _sha256(snapshot), "model_id": model_id,
               "loop_guard": {"limit": LOOP_LIMIT, "reply": LOOP_REPLY}}
    pool = ThreadPoolExecutor(1)
    jobs: dict = {}
    hits: dict = {}
    created = {k: time.time() for k in key_ids.values()}
    app = FastAPI(title="ethereus")

    class ApiError(Exception):
        def __init__(self, status, code, message, headers=None):
            self.status, self.code, self.message, self.headers = status, code, message, headers

    @app.exception_handler(Exception)
    async def _crash(_, e: Exception):
        return JSONResponse({"error": {"code": "internal", "message": f"{type(e).__name__}: {e}"}}, 500)

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

    def finish(turn: Turn, kid: str, model: str, client_tools: list = ()) -> dict:
        tid = uuid.uuid4().hex
        trace = {"trace_id": tid, "key_id": kid, "timestamp": time.time(),
                 "prompt_ids": turn.segments[0]["prompt_ids"],
                 "gen_ids": turn.segments[-1]["gen_ids"],
                 "owner_mask": turn.segments[-1]["owner_mask"], "segments": turn.segments,
                 "firmware_sha256": version["firmware_sha256"],
                 "snapshot_sha256": version["snapshot_sha256"], "loop_guard": turn.loop_guard,
                 "loop_guard_ended": turn.loop_guard_ended}
        (data / "traces" / f"{tid}.json").write_text(json.dumps(trace))
        if turn.loop_guard:  # every firing, for the results' disclosure
            with open(data / "loop-guard.jsonl", "a") as f:
                f.write(json.dumps({"timestamp": trace["timestamp"], "trace_id": tid, "calls": turn.loop_guard,
                                    "ended": turn.loop_guard_ended}) + "\n")
        msg, tools = to_message(turn.text, turn.client_call, keep_ours=bool(client_tools))
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
                "ethereus": {"trace_id": tid, "spilled": turn.spilled, "tool_messages": tools,
                             "loop_guard": turn.loop_guard}}

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
        stream = bool(body.get("stream"))
        if stream and not buffered_stream:
            raise ApiError(422, "bad_request", "streaming is not supported")
        client_tools = [t for t in body.get("tools") or [] if t.get("type") == "function"]
        if not 1 <= max_new <= MAX_TOKENS_MAX:
            raise ApiError(422, "bad_request", f"max_tokens must be 1..{MAX_TOKENS_MAX}")
        model = body.get("model", model_id)
        if body.get("mode", "sync") == "async":
            jid = uuid.uuid4().hex
            jobs[jid] = (kid, model, client_tools,
                         pool.submit(engine.run, messages, max_new, None, client_tools))
            return JSONResponse({"id": jid, "status": "queued"}, 202, headers)
        fut = pool.submit(engine.run, messages, max_new, time.time() + sync_seconds, client_tools)
        if stream:
            async def frames():
                done = asyncio.wrap_future(fut)
                while True:
                    finished, _ = await asyncio.wait({done}, timeout=15)
                    if finished:
                        break
                    yield ": keepalive\n\n"
                try:
                    chunks = sse_chunks(finish(done.result(), kid, model, client_tools))
                except Deadline:
                    chunks = ['data: {"error": {"code": "use_async", "message": "over the limit"}}\n\n']
                except ApiError as e:
                    chunks = [f"data: {json.dumps({'error': {'code': e.code, 'message': e.message}})}\n\n"]
                except Exception as e:  # noqa: BLE001 - the client must see it, not an empty stream
                    msg = f"{type(e).__name__}: {e}"
                    chunks = [f"data: {json.dumps({'error': {'code': 'internal', 'message': msg}})}\n\n"]
                for c in chunks:
                    yield c

            return StreamingResponse(frames(), media_type="text/event-stream", headers=headers)
        try:
            turn = await asyncio.wrap_future(fut)
        except Deadline as e:
            raise ApiError(408, "use_async", f"over {sync_seconds:.0f} s; use mode=async") from e
        return JSONResponse(finish(turn, kid, model, client_tools), 200, headers)

    @app.get("/v1/jobs/{jid}")
    def job(jid: str, req: Request):
        kid = auth(req)
        j = jobs.get(jid)
        if j is None or j[0] != kid:
            raise ApiError(404, "not_found", "no such job")
        try:
            turn = j[3].result(timeout=0)
        except FutureTimeout:
            return JSONResponse({"status": "running"}, 202)
        return finish(turn, kid, j[1], j[2])

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
    global RATE_MINUTE, RATE_DAY
    RATE_MINUTE = int(os.environ.get("ETHEREUS_RATE_MINUTE", RATE_MINUTE))
    RATE_DAY = int(os.environ.get("ETHEREUS_RATE_DAY", RATE_DAY))
    firmware = os.environ.get("FIRMWARE")
    keys = [k for k in os.environ.get("ETHEREUS_KEYS", "").split(",") if k]
    engine = (BaseEngine(os.environ["MODEL"]) if os.environ.get("ETHEREUS_BASE")
              else Engine(os.environ["MODEL"], firmware, executor=True, snapshot=os.environ.get("SNAPSHOT"),
                          system_line=os.environ.get("ETHEREUS_SYSTEM_LINE")))
    return create_app(engine, keys, os.environ.get("ETHEREUS_DATA", "serve-data"), firmware,
                      os.environ.get("SNAPSHOT"), os.environ.get("MODEL_ID", "ethereus"),
                      os.environ.get("CORE_COMMIT"),
                      os.environ.get("CAPTURE_HMAC_KEY", "").encode(),
                      float(os.environ.get("ETHEREUS_SYNC_SECONDS", SYNC_SECONDS)),
                      bool(os.environ.get("ETHEREUS_BUFFERED_STREAM")))
