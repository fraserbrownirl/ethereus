"""OpenAI-compat shim: /v1/chat/completions -> RvChat.generate.

Env: MODEL (merged checkpoint dir), FIRMWARE (eth-bridge.elf path).
If MODEL is not set or does not exist, the app still starts and returns
503 with a diagnostic body so the harness can report "could not run".

The only tools exposed are the `eth` tools (keccak / u256 / selector /
encode / decode / create / create2 / run) via the RvChat bridge +
eth-bridge firmware. No other tools, no prompt engineering beyond the
harness system line.
"""
from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

ETH_TOOL = {
    "type": "function",
    "function": {
        "name": "eth",
        "description": (
            "Ethereum helpers. One command per call: "
            "keccak <hex|text>, u256 <expr>, selector <sig>, "
            "encode <sig> [args...], decode <types> <hex>, "
            "create <deployer> <nonce>, create2 <deployer> <salt> <initcodehash>, "
            "run <contract> <calldata>. Returns the exact result or error(...)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "cmd": {
                    "type": "string",
                    "description": (
                        "e.g. 'selector transfer(address,uint256)' or "
                        "'keccak 0x6161' or 'u256 2**255 - 1' or "
                        "'encode transfer(address,uint256) 0x000000000000000000000000000000000000dEaD 100' or "
                        "'create 0x000000000000000000000000000000000000dEaD 7' or "
                        "'run 0x... 0x...'"
                    ),
                }
            },
            "required": ["cmd"],
        },
    },
}

app = FastAPI(title="ethereus-shim", version="0.1.0")

MODEL = os.environ.get("MODEL", "")
FIRMWARE = os.environ.get(
    "FIRMWARE", "experimental/riscv/tests/data/rv/eth-bridge.elf"
)
MODEL_PATH = Path(MODEL) if MODEL else None
FIRMWARE_PATH = Path(FIRMWARE)

_chat = None
_machine = None
_chat_error: str | None = None


def _load_chat():
    global _chat, _machine, _chat_error
    if _chat is not None or _chat_error is not None:
        return
    if not MODEL_PATH or not MODEL_PATH.exists():
        _chat_error = f"MODEL not set or not found: {MODEL!r} (set MODEL=dist/q9-rv1)"
        return
    try:
        # RvChat lives under experimental/riscv/chipformer/rv/chat.py.
        # The repo root must be on sys.path OR we run with cwd=repo root and
        # insert experimental/riscv so that `chipformer.rv` resolves to the
        # experimental overlay (which has rv/). The editable install handles
        # the q35/overlay; the insert covers the agent's bare-python case.
        import sys

        if "experimental/riscv" not in sys.path:
            sys.path.insert(0, "experimental/riscv")
        from chipformer.rv.chat import RvChat  # type: ignore
        from chipformer.rv import elf, ref  # type: ignore

        fw = FIRMWARE_PATH
        if not fw.exists():
            # try from repo root explicitly
            alt = Path("experimental/riscv/tests/data/rv/eth-bridge.elf")
            if alt.exists():
                fw = alt
            else:
                raise FileNotFoundError(f"firmware not found: {FIRMWARE!r}")

        # RvChat has no firmware kwarg; you park a ref.Machine and pass it to
        # generate(). We do it once here and reuse the snapshot.
        _chat = RvChat(str(MODEL_PATH))
        prog = elf.program(str(fw))
        m = ref.Machine.from_program(prog)
        from chipformer.rv import bridge as BR  # type: ignore

        BR.park(m)
        _machine = m
        _chat_error = None
    except Exception as e:
        _chat_error = f"RvChat load failed: {e}"
        raise


class ChatMessage(BaseModel):
    role: str
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None


class ChatRequest(BaseModel):
    model: str = "ethereus"
    messages: list[ChatMessage]
    temperature: float | None = 0
    max_tokens: int | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any | None = None
    stream: bool | None = False
    n: int | None = 1


@app.get("/v1/models")
def list_models():
    return {
        "object": "list",
        "data": [
            {
                "id": "ethereus",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "ethereus",
            }
        ],
    }


@app.get("/health")
def health():
    return {
        "model": MODEL,
        "firmware": FIRMWARE,
        "firmware_exists": FIRMWARE_PATH.exists()
        or Path("experimental/riscv/tests/data/rv/eth-bridge.elf").exists(),
        "ready": _chat is not None,
        "error": _chat_error,
    }


@app.post("/v1/chat/completions")
def chat_completions(req: ChatRequest):
    if req.stream:
        raise HTTPException(
            status_code=400, detail="streaming not supported; send stream:false"
        )

    if _chat is None and _chat_error is None:
        try:
            _load_chat()
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)) from e
    if _chat_error is not None:
        raise HTTPException(status_code=503, detail=_chat_error)

    # Preserve the harness system line verbatim. RvChat.prompt_ids handles the
    # Qwen chat template and tool schemas (tools=[ETH_TOOL]).
    # If the caller supplied tools, honour them (the harness may); otherwise
    # inject ETH_TOOL so the model can emit <function=eth>.
    msgs: list[dict[str, str]] = [
        {"role": m.role, "content": m.content or ""}
        for m in req.messages
        if m.role in ("system", "user", "assistant", "tool")
    ]
    tools = req.tools if req.tools else [ETH_TOOL]

    try:
        # Fresh machine snapshot per request (generate mutates state).
        from chipformer.rv import bridge as BR
        from chipformer.rv import elf, ref

        fw = FIRMWARE_PATH
        if not fw.exists():
            fw = Path("experimental/riscv/tests/data/rv/eth-bridge.elf")
        m = ref.Machine.from_program(elf.program(str(fw)))
        BR.park(m)

        ids = _chat.prompt_ids(msgs, tools=tools)  # type: ignore[arg-type]
        # Keep a copy for lockstep verification if the harness wants it; the
        # shim itself just verifies lock==ok to surface firmware bugs.
        m2 = ref.Machine.from_program(elf.program(str(fw)))
        BR.park(m2)
        lock = BR.Session(_chat.T, m2)
        lock.history(ids)

        max_new = req.max_tokens or 4096
        # RvChat.generate(ids, machine, max_new, max_chip, check_head, lock)
        r = _chat.generate(ids, m, max_new=max_new, max_chip=1000000, check_head=False, lock=lock)
        text = _chat.text_of(r["tokens"])
        # Surface lock errors as content prefix so trajectories stay verbatim &
        # failures are obvious (do not fabricate a tool response).
        if r.get("lock_error"):
            text = f"[lock_error={r['lock_error']}] " + text
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"RvChat.generate failed: {e}") from e

    cid = f"chatcmpl-{uuid.uuid4().hex[:8]}"
    now = int(time.time())
    return {
        "id": cid,
        "object": "chat.completion",
        "created": now,
        "model": req.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": len(ids),
            "completion_tokens": len(r.get("tokens", [])),
            "total_tokens": len(ids) + len(r.get("tokens", [])),
        },
    }


@app.get("/")
def root():
    return {
        "ok": True,
        "docs": "/docs",
        "models": "/v1/models",
        "chat": "/v1/chat/completions",
        "health": "/health",
    }
