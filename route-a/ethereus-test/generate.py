#!/usr/bin/env python3
"""Ethereus test instances, expected answers from independent oracles (eth_abi, eth_hash, py-evm).

Labelled an Ethereus test: this is ours, not ETH Evals. Run with the core repo's dev venv:
    PYTHONPATH=experimental/riscv:experimental/riscv/tests .venv/bin/python route-a/ethereus-test/generate.py
"""
import json
import random
from pathlib import Path

from eth_abi import encode
from eth_hash.auto import keccak
from evm_oracle import oracle_run

A, B, S = ("0x" + c * 40 for c in "123")
FORMAT = " Give the answer as 0x-prefixed lowercase hex on the last line of your reply, with nothing else on that line."
rng = random.Random(20261101)
items = []


def sel(sig):
    return keccak(sig.encode())[:4]


# 1. Byte-exact calldata for a multi-argument call. Short arguments, per Fraser.
CALLS = [("transfer(address,uint256)", ["address", "uint256"], lambda n: [B, n]),
         ("approve(address,uint256)", ["address", "uint256"], lambda n: [S, n]),
         ("transferFrom(address,address,uint256)", ["address", "address", "uint256"], lambda n: [A, B, n]),
         ("transfer(address,uint256)", ["address", "uint256"], lambda n: [A, n]),
         ("approve(address,uint256)", ["address", "uint256"], lambda n: [B, n])]
for i, (sig, types, args) in enumerate(CALLS):
    v = args(rng.randint(1, 999))
    want = "0x" + (sel(sig) + encode(types, v)).hex()
    items.append({"id": f"calldata-{i}", "kind": "calldata", "expected": want,
                  "prompt": f"What is the exact ABI-encoded calldata for calling {sig} with arguments "
                            f"{', '.join(str(x) for x in v)}?" + FORMAT})

# 2. A mapping storage slot: keccak of the 64-byte key and slot.
for i in range(5):
    key = "0x" + rng.randbytes(20).hex()
    slot = rng.randint(0, 9)
    pre = encode(["address", "uint256"], [key, slot])
    items.append({"id": f"slot-{i}", "kind": "slot", "expected": "0x" + keccak(pre).hex(),
                  "prompt": f"A Solidity contract has a mapping(address => uint256) at storage slot {slot}. "
                            f"What is the storage slot of the entry for key {key}? It is the keccak256 of the "
                            f"64-byte key and slot: 0x{pre.hex()}." + FORMAT})

# 3. The return value of an ERC-20 call on the built-in fixture.
ERC = [("balanceOf(address)", ["address"], [A]), ("balanceOf(address)", ["address"], [B]),
       ("totalSupply()", [], []), ("decimals()", [], []), ("allowance(address,address)", ["address", "address"], [A, S])]
for i, (sig, types, args) in enumerate(ERC):
    cd = sel(sig) + (encode(types, args) if types else b"")
    output, _gas, is_error, _logs, _slots = oracle_run("erc20", "alice", cd)
    assert not is_error, sig
    want = "0x" + output
    items.append({"id": f"erc20-{i}", "kind": "erc20", "expected": want, "calldata": "0x" + cd.hex(),
                  "prompt": f"The tool's built-in contract named erc20 has its fixed state: alice is {A}, bob is "
                            f"{B}, spender is {S}. What 32-byte return data does {sig}"
                            f"{'(' + ', '.join(args) + ')' if args else ''} return when called on it?" + FORMAT})

out = Path(__file__).with_name("instances.json")
out.write_text(json.dumps({"label": "Ethereus test, not ETH Evals", "items": items}, indent=1) + "\n")
print(len(items), "instances")
for x in items:
    print(x["id"], x["expected"][:26], x.get("calldata", "")[:12])
