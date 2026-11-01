#!/usr/bin/env python3
"""Run the Ethereus test on one arm. Same prompt everywhere; greedy; thinking off.

    run.py {base,rv2,ceiling} MODEL FIRMWARE OUT.jsonl [HOST_ELF]

base     stock Qwen, no tool.
rv2      merged model, eth answered by the circuit in the weights.
ceiling  stock Qwen, eth answered by the host running the same firmware with no 80-token window.

Scores are separate: tool = the expected answer appears in a tool response; final = it appears in the
final text with exact bytes. Both are recorded per instance, plus whether removing whitespace rescues final.
"""
import json
import re
import sys
from pathlib import Path

from chipformer.rv import serve
from chipformer.rv.tools import ETH_TOOL

MAX_NEW, MAX_CALLS = 2000, 8
arm, model, firmware, outp = sys.argv[1:5]
items = json.loads((Path(__file__).with_name("instances.json")).read_text())["items"]


def host_answer(cmd, elf_path):
    """The non-bridge image: the cmd goes straight into eth_in, so no capture window applies."""
    from chipformer.rv import elf, ref

    m = ref.Machine.from_program(elf.program(elf_path))
    b = cmd.encode()[:2047] + b"\0"
    for i, x in enumerate(b):
        a = 0x100 + i
        m.ram[a >> 2] = m.ram[a >> 2] & ~(0xFF << 8 * (a & 3)) | x << 8 * (a & 3)
    m.run(3 * 10**6)
    return m.out.decode().strip()


def final_text(text):
    return re.sub(r"<\|(?:im_end|endoftext)\|>", "", serve._TURN.sub("", text)).strip()


if arm == "rv2":
    eng = serve.Engine(model, firmware)
else:
    eng = serve.BaseEngine(model)
with open(outp, "w") as f:
    for it in items:
        msgs = [{"role": "user", "content": it["prompt"]}]
        tools, calls, text = [], [], ""
        if arm == "rv2":
            t = eng.run(msgs, MAX_NEW)
            text = t.text
            msg, tm = serve.to_message(text)
            tools = [x["content"] for x in tm]
            calls = [json.loads(c["function"]["arguments"]).get("cmd", "") for c in msg.get("tool_calls", [])]
            finish, lock = t.finish_reason, t.lock_error
        elif arm == "base":
            t = eng.run(msgs, MAX_NEW)
            text, finish, lock = t.text, t.finish_reason, None
        else:
            finish, lock = "calls", None
            for _ in range(MAX_CALLS):
                t = eng.run(msgs, MAX_NEW, None, [ETH_TOOL])
                text = t.text
                if t.client_call is None:
                    finish = t.finish_reason
                    break
                cmd = t.client_call["arguments"].get("cmd", "")
                ans = host_answer(cmd, sys.argv[5])
                calls.append(cmd)
                tools.append(ans)
                msgs += [{"role": "assistant", "content": text, "tool_calls": [{"type": "function", "function": {
                    "name": "eth", "arguments": {"cmd": cmd}}}]}, {"role": "tool", "content": ans}]
        final = final_text(text)
        want = it["expected"].lower()
        row = {"id": it["id"], "kind": it["kind"], "arm": arm, "calls": calls, "tool_responses": tools,
               "tool_ok": any(want in x.lower() for x in tools), "final_ok": want in final.lower(),
               "final_ok_despaced": want in re.sub(r"\s+", "", final.lower()),
               "finish": finish, "lock_error": lock, "final": final[-400:]}
        f.write(json.dumps(row, default=str) + "\n")
        f.flush()
        print(it["id"], "tool", row["tool_ok"], "final", row["final_ok"], "despaced", row["final_ok_despaced"], "calls", len(calls), finish, flush=True)
print("ARM_DONE", arm)
