"""OpenAI-compat shim: /v1/chat/completions -> RvChat.generate.

Env: MODEL (merged checkpoint dir), FIRMWARE (eth-bridge.elf path).
If MODEL is not set or does not exist, the app still starts and returns
503 with a diagnostic body so the harness can report "could not run".

The only tools exposed are the `eth` tools (abi/keccak/selector/encode/
decode/create/create2/evm/run) via the RvChat bridge + firmware. No other
tools, no prompt engineering beyond the harness system line.
"""
from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

app = FastAPI(title="ethereus-shim", version="0.1.0")

MODEL = os.environ.get("MODEL", "")
FIRMWARE = os.environ.get("FIRMWARE", "experimental/riscv/tests/data/rv/eth-bridge.elf")
MODEL_PATH = Path(MODEL) if MODEL else None

_chat = None
_chat_error: str | None = None


def _load_chat():
    global _chat, _chat_error
    if _chat is not None or _chat_error is not None:
        return
    if not MODEL_PATH or not MODEL_PATH.exists():
        _chat_error = f"MODEL not set or not found: {MODEL!r} (set MODEL=dist/q9-rv1)"
        return
    try:
        import sys
        sys.path.insert(0, "experimental/riscv")
        from chipformer.rv.chat import RvChat  # type: ignore

        fw = Path(FIRMWARE)
        if not fw.exists():
            fw = Path("experimental/riscv/tests/data/rv/eth-bridge.elf")
        _chat = RvChat(str(MODEL_PATH), firmware=str(fw), executor=True)
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
    return {"object": "list", "data": [{"id": "ethereus", "object": "model", "created": int(time.time()), "owned_by": "ethereus"}]}


@app.get("/health")
def health():
    return {"model": MODEL, "firmware": FIRMWARE, "ready": _chat is not None, "error": _chat_error}


@app.post("/v1/chat/completions")
def chat_completions(req: ChatRequest):
    # Harness may send temperature>0; force 0 for determinism per plan
    if req.stream:
        raise HTTPException(status_code=400, detail="streaming not supported by this shim")

    # Lazy init
    if _chat is None and _chat_error is None:
        try:
            _load_chat()
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)) from e
    if _chat_error is not None:
        raise HTTPException(status_code=503, detail=_chat_error)

    # Build prompt: concatenate messages with simple role tags; RvChat expects the user content
    # that contains the tool-call trigger. We preserve the harness system line verbatim.
    prompt = ""
    for m in req.messages:
        if m.role == "system":
            prompt += m.content or ""
            prompt += "\n"
        elif m.role == "user":
            prompt += m.content or ""
            prompt += "\n"
        elif m.role == "assistant" and m.content:
            prompt += m.content + "\n"
        elif m.role == "tool":
            prompt += m.content or ""
            prompt += "\n"

    # Delegate to RvChat
    try:
        # RvChat.generate signature: generate(prompt, max_new_tokens=..., temperature=0)
        max_new = req.max_tokens or 4096
        out = _chat.generate(prompt, max_new_tokens=max_new, temperature=0.0)  # type: ignore[attr-defined]
        # Normalize output: RvChat may return (text, stats) or just text
        if isinstance(out, tuple):
            text = out[0]
        else:
            text = out
        if not isinstance(text, str):
            text = str(text)
    except AttributeError:
        # Fallback: try chat() or __call__
        try:
            text = _chat.chat(prompt)  # type: ignore[attr-defined]
        except Exception as e2:
            raise HTTPException(status_code=500, detail=f"RvChat call failed: {e2}") from e2
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"RvChat.generate failed: {e}") from e

    cid = f"chatcmpl-{uuid.uuid4().hex[:8]}"
    now = int(time.time())
    return {
        "id": cid,
        "object": "chat.completion",
        "created": now,
        "model": req.model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


@app.get("/")
def root():
    return {"ok": True, "docs": "/docs", "models": "/v1/models", "chat": "/v1/chat/completions"}
