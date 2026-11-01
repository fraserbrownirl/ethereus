#!/usr/bin/env python3
"""Measure how many Qwen tokens each natural `run` call takes, for docs/eth-bench.md.

The bridge captures 80 tokens per tool call, and Qwen spends one token per digit.
    .venv/bin/python scripts/bench_window.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from eth_hash.auto import keccak  # noqa: E402

from chipformer.rv import bridge as BR  # noqa: E402
from chipformer.rv import spill  # noqa: E402

T = BR.Text(BR.default_tokenizer())


def tokens(cmd):
    return len(T.tok.encode(spill.call_text(cmd), add_special_tokens=False).ids)


def sel(sig):
    return keccak(sig.encode())[:4].hex()


def word(v):
    return f"{v:064x}"


A, B, S = (("0" * 24) + c * 40 for c in "123")
CALLS = [("run totalSupply()", "erc20", sel("totalSupply()")),
         ("run decimals()", "erc20", sel("decimals()")),
         ("run balanceOf(alice)", "erc20", sel("balanceOf(address)") + A),
         ("run allowance(alice,spender)", "erc20", sel("allowance(address,address)") + A + S),
         ("run transfer(bob,100)", "erc20", sel("transfer(address,uint256)") + B + word(100)),
         ("run approve(spender,50)", "erc20", sel("approve(address,uint256)") + S + word(50)),
         ("run transferFrom (revert)", "erc20", sel("transferFrom(address,address,uint256)") + A + B + word(10)),
         ("run getAmountOut(1000,50000,60000)", "amm", sel("getAmountOut(uint256,uint256,uint256)") + word(1000) + word(50000) + word(60000))]
print("| call | tokens | fits 80 |\n| --- | --- | --- |")
for name, contract, cd in CALLS:
    n = tokens(f"run {contract} 0x{cd}")
    print(f"| {name} | {n} | {'yes' if n <= spill.WINDOW else 'no'} |")
