"""u256_sub, u256_sub_borrow, u256_shl, u256_shr, u256_sar and u256_divmod write their output before they finish
reading their inputs, so a call whose output is also an input reads its own partial result. The Solana lane's
narrowed u128_sub_borrow had this bug. No call in env/eth/ aliases today; this keeps it so."""

import re
from pathlib import Path

ENV = Path(__file__).resolve().parents[1] / "chipformer/rv/env/eth"
UNSAFE = {"u256_sub": [0], "u256_sub_borrow": [0], "u256_shl": [0], "u256_shr": [0], "u256_sar": [0],
          "u256_divmod": [0, 1]}
CALL = re.compile(r"\b(" + "|".join(UNSAFE) + r")\s*\(([^;{}]*?)\)\s*;")


def aliased_calls(text: str) -> list[str]:
    found = []
    for m in CALL.finditer(text):
        args = [a.replace(" ", "").lstrip("&") for a in m.group(2).split(",")]
        outs = UNSAFE[m.group(1)]
        if any(args[i] == args[j] for i in outs for j in range(len(args)) if j not in outs):
            found.append(m.group(0))
    return found


def test_the_check_catches_an_aliased_call():
    assert aliased_calls("u256_sub(&x, &x, &y);") and aliased_calls("u256_divmod(&q,&r,&q,&d);")
    assert not aliased_calls("u256_sub(&r, &a, &b);")


def test_no_aliased_calls_in_the_eth_firmware():
    bad = {f.name: aliased_calls(f.read_text()) for f in sorted(ENV.glob("*.c"))}
    assert not any(bad.values()), bad
