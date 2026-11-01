"""Gate 1 through the file buffer: full-width @file cases vs the oracles.

Each case asserts its inline equivalent would NOT fit the 80-token window,
proving the file path load-bearing, then runs the @file form through the RV
firmware with the content poked into file_buf (bridge-resolved, same
mechanism as the API gateway's spill) and compares against the same oracles
as the inline gate (eth_abi/eth_hash/py-evm).
"""
import random
import sys
from pathlib import Path

import pytest
from eth_abi import encode as abi_encode
from eth_hash.auto import keccak

sys.path.insert(0, str(Path(__file__).parent))

from chipformer.rv import eth, ref  # noqa: E402
from evm_oracle import ADDRS, oracle_run, sel, u256_bytes, uaddr  # noqa: E402
from test_rv_evm import parse_run  # noqa: E402


def assert_over_window(inline_cmd):
    ntok, _wide = eth.captured_size(inline_cmd)
    assert ntok > eth.CAP_TOKENS, (inline_cmd[:80], ntok)


def test_filebuf_decode_fullwidth():
    types = "uint256,address,bytes,uint256,bool,bytes32,bytes,uint256"
    vals = [2**200, "0x1111111111111111111111111111111111111111", b"A" * 300,
            7, True, b"B" * 32, b"C" * 129, 2**256 - 1]
    blob = "0x" + abi_encode(types.split(","), vals).hex()
    assert_over_window(f"decode {types} {blob}")
    out, _ = eth.run_with_files("decode " + types + " @blob", {"blob": blob})
    assert out == eth.expected_decode(f"{types} {blob}")


def test_filebuf_create2_full():
    dep = "0x5FbDB2315678afecb367f032d93F642f64180aa3"
    salt = "0x" + format(2**255 + 12345, "064x")
    h = "0x" + keccak(b"phase-2-initcode").hex()
    assert_over_window(f"create2 {dep} {salt} {h}")
    out, _ = eth.run_with_files("create2 " + dep + " @salt @hash",
                                {"salt": salt, "hash": h})
    assert out == eth.expected_create2(f"{dep} {salt} {h}")


def test_filebuf_run_transferfrom():
    cd = (sel("transferFrom(address,address,uint256)") + uaddr(ADDRS["alice"])
          + uaddr(ADDRS["bob"]) + u256_bytes(10)).hex()
    o_ret, o_gas, o_err, o_logs, o_slots = oracle_run(
        "erc20", "spender", bytes.fromhex(cd))
    out, _ = eth.run_with_files("run erc20 @cd 0 spender", {"cd": cd})
    f_err, f_ret, f_gas, f_logs, f_slots = parse_run(out)
    assert (f_err, f_ret, f_gas, f_logs) == (o_err, o_ret, o_gas, o_logs)


def test_filebuf_run_padded_calldata():
    # 300 bytes of trailing calldata: valid, ignored, over-window.
    raw = (sel("transfer(address,uint256)") + uaddr(ADDRS["bob"])
           + u256_bytes(100) + b"X" * 200)
    assert_over_window(f"run erc20 {raw.hex()} 0 alice")
    o_ret, o_gas, o_err, o_logs, _o_slots = oracle_run("erc20", "alice", raw)
    out, _ = eth.run_with_files("run erc20 @cd 0 alice", {"cd": raw.hex()})
    f_err, f_ret, f_gas, f_logs, _f_slots = parse_run(out)
    assert (f_err, f_ret, f_gas, f_logs) == (o_err, o_ret, o_gas, o_logs)


def test_filebuf_keccak_1k():
    pre = "0x" + "ab" * 1000
    assert_over_window(f"keccak {pre}")
    out, _ = eth.run_with_files("keccak @pre", {"pre": pre})
    assert out == eth.expected_keccak(pre)


def test_filebuf_encode_bytes():
    # The inline oracle's 255-char arg cap is a harness limit, not eth_abi's;
    # the file form answers to eth_abi directly.
    big = "0x" + "cd" * 400
    assert_over_window(f"encode f(bytes,uint256) {big} 9")
    out, _ = eth.run_with_files("encode f(bytes,uint256) @b 9", {"b": big})
    want = "0x" + keccak(b"f(bytes,uint256)")[:4].hex() + abi_encode(
        ["bytes", "uint256"], [bytes.fromhex("cd" * 400), 9]).hex() + "\n"
    assert out == want


def test_filebuf_quoted_at_is_literal():
    # A quoted "@b" is the literal string, not a file reference.
    out, _ = eth.run_with_files('encode f(string) "@b"', {"b": "0x1234"})
    assert out == eth.expected_encode('f(string) "@b"')


@pytest.mark.parametrize("cmd,files", [
    ("decode uint256 @zz", {}),
    ("decode uint256 @zz", {"other": "0x00"}),
    ("keccak @", {"": "0x00"}),
    ("create2 0x5FbDB2315678afecb367f032d93F642f64180aa3 @s @h", {"s": "0x01"}),
])
def test_filebuf_missing_is_error(cmd, files):
    out, _ = eth.run_with_files(cmd, files)
    assert out == "error (no file)\n"


N_GATE = 200


def test_filebuf_gate_decode():
    rng = random.Random(20260701)
    instr = 0
    for i in range(N_GATE):
        nbig = rng.randint(150, 800)
        nsmall = rng.randint(0, 40)
        types = "uint256,bytes,address,bytes,bool"
        vals = [rng.getrandbits(256), rng.randbytes(nbig),
                "0x" + rng.randbytes(20).hex(), rng.randbytes(nsmall),
                bool(rng.getrandbits(1))]
        blob = "0x" + abi_encode(types.split(","), vals).hex()
        inline = f"decode {types} {blob}"
        assert_over_window(inline)
        out, toks = eth.run_with_files(f"decode {types} @b", {"b": blob})
        instr += ref.trace_stats(toks)["instructions"]
        assert out == eth.expected_decode(f"{types} {blob}"), i
    print(f"\nfile decode: {N_GATE} cases, {instr / N_GATE:.0f} instr avg")


def test_filebuf_gate_create2():
    rng = random.Random(20260702)
    instr = 0
    for i in range(N_GATE):
        dep = "0x" + rng.randbytes(20).hex()
        salt = "0x" + rng.randbytes(32).hex()
        h = "0x" + rng.randbytes(32).hex()
        assert_over_window(f"create2 {dep} {salt} {h}")
        out, toks = eth.run_with_files(f"create2 {dep} @s @h",
                                       {"s": salt, "h": h})
        instr += ref.trace_stats(toks)["instructions"]
        assert out == eth.expected_create2(f"{dep} {salt} {h}"), i
    print(f"\nfile create2: {N_GATE} cases, {instr / N_GATE:.0f} instr avg")


def test_filebuf_gate_run():
    rng = random.Random(20260703)
    instr = 0
    fns = ["transfer(address,uint256)", "transferFrom(address,address,uint256)",
           "approve(address,uint256)"]
    for i in range(N_GATE):
        fn = fns[i % 3]
        sender = rng.choice(["alice", "spender"])
        to = rng.choice(["alice", "bob", "spender"])
        pad = rng.randbytes(rng.randint(64, 400))
        if fn.startswith("transferFrom"):
            cd = (sel(fn) + uaddr(ADDRS["alice"]) + uaddr(ADDRS[to])
                  + u256_bytes(rng.randint(1, 10)) + pad)
        elif fn.startswith("transfer("):
            cd = (sel(fn) + uaddr(ADDRS[to])
                  + u256_bytes(rng.randint(0, 1200)) + pad)
        else:
            cd = (sel(fn) + uaddr(ADDRS[to])
                  + u256_bytes(rng.getrandbits(256)) + pad)
        inline = f"run erc20 {cd.hex()} 0 {sender}"
        assert_over_window(inline)
        o_ret, o_gas, o_err, o_logs, _ = oracle_run("erc20", sender, cd)
        out, toks = eth.run_with_files(f"run erc20 @cd 0 {sender}",
                                       {"cd": cd.hex()})
        instr += ref.trace_stats(toks)["instructions"]
        f_err, f_ret, f_gas, f_logs, _ = parse_run(out)
        assert (f_err, f_ret, f_gas, f_logs) == (o_err, o_ret, o_gas, o_logs), i
    print(f"\nfile run: {N_GATE} cases, {instr / N_GATE:.0f} instr avg")


def test_filebuf_fuzz_decode_create2():
    rng = random.Random(11)
    for i in range(8):
        nbytes = rng.choice([64, 200, 500, 900])
        blob = "0x" + rng.randbytes(nbytes).hex()
        types = "bytes32,uint256,bytes"
        vals = [rng.randbytes(32), rng.getrandbits(256), bytes.fromhex(blob[2:])]
        enc = "0x" + abi_encode(types.split(","), vals).hex()
        out, _ = eth.run_with_files(f"decode {types} @b{i}", {f"b{i}": enc})
        assert out == eth.expected_decode(f"{types} {enc}"), i
    for i in range(8):
        dep = "0x" + rng.randbytes(20).hex()
        salt = "0x" + rng.getrandbits(256).to_bytes(32, "big").hex()
        h = "0x" + rng.randbytes(32).hex()
        out, _ = eth.run_with_files(f"create2 {dep} @s{i} @h{i}",
                                    {f"s{i}": salt, f"h{i}": h})
        assert out == eth.expected_create2(f"{dep} {salt} {h}"), i
