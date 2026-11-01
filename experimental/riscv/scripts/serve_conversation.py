#!/usr/bin/env python3
"""The serve.py acceptance test (addendum step 3), on a GPU box. Prints one line per call, exits 1
on any failure. Three calls in one conversation, each replayed.

  python scripts/serve_conversation.py --model DIR --firmware tests/data/rv/eth-bridge.elf
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from fastapi.testclient import TestClient

from chipformer.rv import serve

ALICE = "0x" + "aa" * 20
ASKS = [("Use eth: what is the selector of transfer(address,uint256)?", "selector", "0xa9059cbb"),
        (f"Use eth: encode transfer(address,uint256) to {ALICE} with amount 100.", "encode", "a9059cbb"),
        (f"Use eth with exactly this cmd: run erc20 0x70a08231{'00' * 12}{ALICE[2:]}", "run erc20", None)]
SPILL = ("Use eth to decode this as ten uint256 values: 0x"
         + "".join(f"{i * 7 + 1:064x}" for i in range(10)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--firmware", required=True)
    ap.add_argument("--max-tokens", type=int, default=1500)
    ap.add_argument("--spill", action="store_true", help="also the spec's spill test: it must report spilled")
    args = ap.parse_args()
    engine = serve.Engine(args.model, args.firmware)
    app = serve.create_app(engine, ["k"], tempfile.mkdtemp(), args.firmware, sync_seconds=3600)
    c = TestClient(app)
    head = {"Authorization": "Bearer k"}
    bad, history = 0, []

    def turn(ask, want_cmd, want, messages):
        nonlocal bad
        messages.append({"role": "user", "content": ask})
        r = c.post("/v1/chat/completions", headers=head,
                   json={"messages": messages, "max_tokens": args.max_tokens})
        if r.status_code != 200:
            print(f"FAIL {want_cmd}: HTTP {r.status_code} {r.text[:300]}")
            bad += 1
            return None
        j = r.json()
        msg, tools = j["choices"][0]["message"], j["ethereus"]["tool_messages"]
        calls = [json.loads(x["function"]["arguments"])["cmd"] for x in msg.get("tool_calls", [])]
        replays = engine.replay(c.get(f"/v1/traces/{j['ethereus']['trace_id']}", headers=head).json())
        ok = (any(want_cmd in x for x in calls) and all(p["ok"] for p in replays)
              and (want is None or any(want in t["content"] for t in tools)))
        bad += not ok
        print("PASS" if ok else "FAIL", want_cmd, json.dumps(
            {"replay": replays, "spilled": j["ethereus"]["spilled"], "usage": j["usage"],
             "finish": j["choices"][0]["finish_reason"], "calls": calls[:3],
             "tool": [t["content"][:90] for t in tools[:3]]}, default=str)[:1500], flush=True)
        messages.append({k: v for k, v in msg.items()})
        messages += [{k: v for k, v in t.items()} for t in tools]
        return j

    for ask, cmd, want in ASKS:
        turn(ask, cmd, want, history)
    if args.spill:
        j = turn(SPILL, "decode", None, [])
        if j is not None and not j["ethereus"]["spilled"]:  # the spec's test is spilled: true
            print("FAIL spill case did not spill")
            bad += 1
    print("RESULT", "PASS" if not bad else f"FAIL {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
