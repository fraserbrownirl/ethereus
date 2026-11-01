#!/usr/bin/env python3
"""Gate 4 (and gate 3 by replaying its records): six eth calls inside Qwen on the pinned venv."""
import json
import sys
import time

from chipformer.rv import bridge as BR
from chipformer.rv import elf, ref
from chipformer.rv.chat import RvChat, firmware_tools, replay

MODEL, FW, OUT = sys.argv[1:4]
ASKS = ["Use eth: what is the selector of transfer(address,uint256)?",
        "Use eth: what is the keccak256 of the hex bytes 0x6161?",
        "Use eth: compute u256 0xffffffff * 0xffffffff.",
        "Use eth: encode transfer(address,uint256) to 0x000000000000000000000000000000000000dEaD with amount 100.",
        "Use eth: what address does deployer 0x000000000000000000000000000000000000dEaD create at nonce 1?",
        "Use eth with exactly this cmd: run erc20 0x70a08231000000000000000000000000" + "aa" * 20]
chat = RvChat(MODEL, executor=True)


def parked():
    m = ref.Machine.from_program(elf.program(FW))
    BR.park(m)
    return m


bad = 0
for n, q in enumerate(ASKS, 1):
    ids = chat.prompt_ids([{"role": "user", "content": q}], firmware_tools(FW))
    lock = BR.Session(chat.T, parked())
    lock.history(ids)
    t = time.time()
    r = chat.generate(ids, parked(), 1500, max_chip=1_000_000, check_head=True, lock=lock)
    rep = replay(chat, ids, r["tokens"], FW)
    ok = r["head_mismatch"] == 0 and not r["lock_error"] and rep["ok"] and r["chip_tokens"] > 0
    bad += not ok
    row = {"prompt": n, "ok": ok, "text": r["text_tokens"], "chip": r["chip_tokens"],
           "seconds": round(r["seconds"], 1), "chip_seconds": r["chip_seconds"],
           "text_seconds": r["text_seconds"], "head_mismatch": r["head_mismatch"],
           "lock_error": r["lock_error"], "replay": rep}
    print(json.dumps(row, default=str), flush=True)
    with open(f"{OUT}/chat-{n}.log", "w") as f:
        f.write(q + "\n" + chat.text_of(r["tokens"]) + "\n" + json.dumps(row, default=str) + "\n")
print("GATE4", "PASS" if not bad else f"FAIL {bad}")
sys.exit(bool(bad))
