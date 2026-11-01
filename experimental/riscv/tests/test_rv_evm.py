"""EVM gate 1: firmware `run` == py-evm on the built-in contracts.

Each case runs real solc 0.8.38 runtime bytecode (see env/eth/contracts/)
through the RV firmware and through py-evm's apply_computation with the same
seeded storage, and compares return data, gross gas, revert status, logs,
and the storage diff.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from evm_oracle import ADDRS, GEN, oracle_run, sel, u256_bytes, uaddr

from chipformer.rv import eth

CASES = [
    ("totalSupply", "erc20", "alice", sel("totalSupply()"), 0),
    ("decimals", "erc20", "alice", sel("decimals()"), 0),
    ("balanceOf-alice", "erc20", "alice", sel("balanceOf(address)") + uaddr(ADDRS["alice"]), 0),
    ("balanceOf-bob", "erc20", "alice", sel("balanceOf(address)") + uaddr(ADDRS["bob"]), 0),
    (
        "transfer-ok",
        "erc20",
        "alice",
        sel("transfer(address,uint256)") + uaddr(ADDRS["bob"]) + u256_bytes(100),
        0,
    ),
    (
        "transfer-short",
        "erc20",
        "alice",
        sel("transfer(address,uint256)") + uaddr(ADDRS["bob"]) + u256_bytes(10000),
        0,
    ),
    (
        "approve",
        "erc20",
        "alice",
        sel("approve(address,uint256)") + uaddr(ADDRS["spender"]) + u256_bytes(50),
        0,
    ),
    (
        "allowance-0",
        "erc20",
        "alice",
        sel("allowance(address,address)") + uaddr(ADDRS["alice"]) + uaddr(ADDRS["spender"]),
        0,
    ),
    (
        "transferFrom-revert",
        "erc20",
        "spender",
        sel("transferFrom(address,address,uint256)")
        + uaddr(ADDRS["alice"])
        + uaddr(ADDRS["bob"])
        + u256_bytes(10),
        0,
    ),
    (
        "transfer-bob-revert",
        "erc20",
        "bob",
        sel("transfer(address,uint256)") + uaddr(ADDRS["alice"]) + u256_bytes(1),
        0,
    ),
    (
        "amm-quote",
        "amm",
        "alice",
        sel("getAmountOut(uint256,uint256,uint256)")
        + u256_bytes(1000)
        + u256_bytes(50000)
        + u256_bytes(60000),
        0,
    ),
    (
        "amm-zero-revert",
        "amm",
        "alice",
        sel("getAmountOut(uint256,uint256,uint256)")
        + u256_bytes(0)
        + u256_bytes(50000)
        + u256_bytes(60000),
        0,
    ),
]


def parse_run(out):
    lines = out.strip().split("\n")
    reverted = lines[0].startswith("REVERT")
    ret = lines[0].split("0x", 1)[1]
    gas = int(lines[1])
    logs, slots = [], {}
    for ln in lines[2:]:
        if ln.startswith("LOG "):
            parts = ln.split(" ")
            n = int(parts[1])
            logs.append(
                (
                    tuple(int(parts[2 + i], 16) for i in range(n)),
                    parts[2 + n] if len(parts) > 2 + n else "",
                )
            )
        elif ln.startswith("SLOT "):
            _, k, v = ln.split(" ")
            slots[k] = int(v, 16)
    return reverted, ret, gas, logs, slots


@pytest.mark.parametrize("name,contract,sender,calldata,value", CASES, ids=[c[0] for c in CASES])
def test_run_matches_pyevm(name, contract, sender, calldata, value):
    o_ret, o_gas, o_err, o_logs, o_slots = oracle_run(contract, sender, calldata, value)
    out, _toks = eth.run(f"run {contract} {calldata.hex()} {value} {sender}")
    f_err, f_ret, f_gas, f_logs, f_slots = parse_run(out)
    assert f_err == o_err, (out, o_ret)
    assert f_ret == o_ret, out
    assert f_gas == o_gas, out
    assert f_logs == o_logs, out
    if not o_err:
        assert f_slots == o_slots, out


def test_builtins_json_matches_firmware_inputs():
    # The oracle's exact-input record travels with the ELF (build_eth.sh).
    assert GEN["solc"] == "0.8.38"
    assert len(bytes.fromhex(GEN["erc20"]["runtime"])) < 24576
    assert len(GEN["erc20"]["fixture"]) == 2
