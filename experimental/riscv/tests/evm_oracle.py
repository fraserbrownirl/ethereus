"""py-evm oracle for the EVM gate: runtime code executed directly, no deploy.

Both sides run the same runtime bytecode against the same seeded storage:
state.set_code(addr, runtime), state.set_storage(...) for the fixture, then
apply_computation. No transaction, no CREATE, no intrinsic gas.
Gas compared is computation gas, pre-refund (gross), on both sides.
"""

import json
from pathlib import Path

import pytest

eth = pytest.importorskip("eth")

from eth import constants
from eth.chains.base import MiningChain
from eth.db.atomic import AtomicDB
from eth.vm.forks.cancun import CancunVM
from eth.vm.message import Message
from eth.vm.transaction_context import BaseTransactionContext
from eth_hash.auto import keccak

RV = Path(__file__).parents[1]
GEN = json.loads((RV / "tests/data/rv/eth.builtins.json").read_text())

ALICE = bytes.fromhex(GEN["alice"][2:])
BOB = bytes.fromhex(GEN["bob"][2:])
SPENDER = bytes.fromhex(GEN["spender"][2:])
ADDRS = {"alice": ALICE, "bob": BOB, "spender": SPENDER}
TOKEN = bytes.fromhex("4444444444444444444444444444444444444444")
AMM = bytes.fromhex("5555555555555555555555555555555555555555")
GAS = 10_000_000  # == EVM_GAS_LIMIT in evm.h

TestChain = MiningChain.configure(
    __name__="TestChain",
    vm_configuration=((constants.GENESIS_BLOCK_NUMBER, CancunVM),),
    chain_id=1,
)


def fresh_state(code_addr, runtime, fixture):
    genesis_state = {
        a: {"balance": 10**24, "nonce": 0, "code": b"", "storage": {}}
        for a in (ALICE, BOB, SPENDER)
    }
    chain = TestChain.from_genesis(
        AtomicDB(),
        {"gas_limit": GAS, "difficulty": 0, "timestamp": 0},
        genesis_state,
    )
    state = chain.get_vm().state
    state.set_code(code_addr, runtime)
    for k, v in fixture:
        state.set_storage(code_addr, int(k, 16), int(v, 16))
    state.persist()
    return state


def _run(state, addr, sender, calldata, value=0):
    msg = Message(
        gas=GAS,
        to=addr,
        sender=sender,
        value=value,
        data=calldata,
        code=state.get_code(addr),
        depth=0,
        should_transfer_value=value > 0,
    )
    comp = state.computation_class.apply_computation(
        state, msg, BaseTransactionContext(gas_price=0, origin=sender)
    )
    gas = GAS - comp.get_gas_meter().gas_remaining
    logs = [
        (tuple(topics), bytes(data).hex()) for (_, _, topics, data) in comp.get_raw_log_entries()
    ]
    return comp, gas, logs


def oracle_run(contract, sender_name, calldata, value=0):
    """Run a built-in contract call. Returns (output, gas, is_error, logs, slots)."""
    addr = TOKEN if contract == "erc20" else AMM
    rt = bytes.fromhex(GEN[contract]["runtime"])
    fix = GEN[contract]["fixture"]
    state = fresh_state(addr, rt, fix)
    comp, gas, logs = _run(state, addr, ADDRS[sender_name], calldata, value)
    orig = dict(fix)
    slots = {}
    for k in candidate_slots(contract):
        v = state.get_storage(addr, int(k, 16))
        if v != int(orig.get(k, "0"), 16):
            slots[k] = v
    return bytes(comp.output).hex(), gas, comp.is_error, logs, slots


def oracle_raw(code, calldata=b"", sender=ALICE, value=0):
    """Run raw bytecode with empty storage. Returns (output, gas, is_error)."""
    addr = bytes.fromhex("6666666666666666666666666666666666666666")
    state = fresh_state(addr, code, [])
    comp, gas, _ = _run(state, addr, sender, calldata, value)
    return bytes(comp.output).hex(), gas, comp.is_error


def candidate_slots(contract):
    keys = {k for k, _ in GEN["erc20"]["fixture"]}
    if contract == "erc20":
        for a in ADDRS.values():
            keys.add(keccak(a.rjust(32, b"\0") + (0).to_bytes(32, "big")).hex())
        for o in ADDRS.values():
            for s in ADDRS.values():
                inner = keccak(o.rjust(32, b"\0") + (1).to_bytes(32, "big"))
                keys.add(keccak(s.rjust(32, b"\0") + inner).hex())
    return keys


def sel(sig):
    return keccak(sig.encode())[:4]


def u256_bytes(v: int):
    return v.to_bytes(32, "big")


def uaddr(a: bytes):
    return a.rjust(32, b"\0")
