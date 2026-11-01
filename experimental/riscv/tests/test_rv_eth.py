"""Gate 1 for the eth firmware: ref.Machine == Python on keccak and u256.

Two paths, both through the author's code only:
- gate1 (tests/data/rv/eth.elf, non-bridge): driven exactly as calc.py drives
  calc.elf (cmd poked into eth_in at RAM 0x100). Fixed vectors + tiny fuzz with
  the Qwen-tokenizer window filter.
- bridge smoke (tests/data/rv/eth-bridge.elf): fed through bridge.Session with
  real Qwen-tokenized calls, 6 cases including one over-window call. This is the
  reference-level bridge check; IR teacher-forcing is gate 2.
"""

from pathlib import Path

import pytest

from chipformer.rv import bridge as BR
from chipformer.rv import elf as elfmod
from chipformer.rv import eth
from chipformer.rv import ref as refmod


def test_elf_inputs_live_where_calc_puts_them():
    # eth_in at RAM 0x100, like calc_in: poke it and read it back through a load.
    m = eth.machine("keccak 0x")
    lo = m.ram[0x100 >> 2] & 0xFF
    assert lo == ord("k")


@pytest.mark.parametrize("cmd,want", [
    ("keccak 0x", "0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470\n"),
    ("keccak 'abc'", "0x4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45\n"),
    ("keccak 0xZZ", "error (bad hex)\n"),
    ("u256 2**255 - 1", "57896044618658097711785492504343953926634992332820282019728792003956564819967\n"),
    ("u256 2**256 !", "error (overflow)\n"),
    ("u256 7 / 0", "error (div by zero)\n"),
    ("u256 255 hex", "0xff\n"),
    ("u256 2**255 - 1 hex !", "error (overflow)\n"),
    ("u256 0 hex", "0x0\n"),
    ("foo", "error (unknown cmd)\n"),
])
def test_fixed_vectors(cmd, want):
    val, st, raw = eth.run_one(cmd)
    assert val == want
    assert st["instructions"] > 0


def test_long_keccak_capability_vector():
    # 200 bytes = 2 permutations: over the 80-token call window (a Phase-2 file
    # input), but the non-bridge image runs anything in eth_in, so gate 1 still
    # proves the multi-permutation path exact here.
    data = bytes(range(200))
    cmd = "keccak 0x" + data.hex()
    ntok, _ = eth.captured_size(cmd)
    assert ntok > eth.CAP_TOKENS
    val, st, raw = eth.run_one(cmd)
    assert eth.agrees(val, eth.expected(cmd)), (cmd, val)


def test_tiny_fuzz_keccak_u256():
    stats: dict = {}
    n = 0
    for op, cmd, oracle in eth.cases(10, seed=0, only=["keccak", "u256"], stats=stats):
        val, st, raw = eth.run_one(cmd)
        assert val == oracle, (cmd, val, oracle)
        n += 1
    assert n == 20
    # The window filter must be live: natural keccak draws mostly exceed 80 tokens.
    assert stats["keccak"][0] > 0


def test_window_sizing_spots_fits_and_rejects():
    small, _ = eth.captured_size("u256 2**255-1")
    assert small <= eth.CAP_TOKENS
    big, _ = eth.captured_size("keccak 0x" + "ab" * 100)
    assert big > eth.CAP_TOKENS
    _, wide = eth.captured_size("u256 " + "116561349140646601840395311678085340105803" * 2)
    assert wide <= eth.CAP_ROW_BYTES


def _session_value(cmd: str) -> str:
    T = eth.text_model()
    m = refmod.Machine.from_program(elfmod.program(str(eth.BRIDGE_ELF)))
    BR.park(m)
    ses = BR.Session(T, m)
    ses.qwen(T.encode(eth._tool_call_text(cmd)))
    assert len(ses.runs) == 1 and m.fault is None
    text = T.decode_bytes(ses.runs[0]).decode()
    body = text.split("<tool_response>")[1].split("</tool_response>")[0]
    return body.strip("\n") + "\n"


@pytest.mark.parametrize("cmd", [
    "keccak 0xab12",
    "keccak 'hello world'",
    "keccak 0x" + "ab" * 100,  # over-window: bridge firmware must refuse it
    "u256 12345678*87654321",
    "u256 2**128 + 0xffffffffffffffff hex",
    "u256 10 / 0",
])
def test_bridge_session_reference(cmd):
    ntok, _ = eth.captured_size(cmd)
    want = "error (too long)\n" if ntok > eth.CAP_TOKENS else eth.expected(cmd)
    assert _session_value(cmd) == want


SELECTOR_VECTORS = [
    # (sig, selector) — independently known values, not from the generator
    ("transfer(address,uint256)", "0xa9059cbb\n"),
    ("balanceOf(address)", "0x70a08231\n"),
    ("approve(address,uint256)", "0x095ea7b3\n"),
    ("transferFrom(address,address,uint256)", "0x23b872dd\n"),
    ("totalSupply()", "0x18160ddd\n"),
    ("decimals()", "0x313ce567\n"),
    ("ownerOf(uint256)", "0x6352211e\n"),
    ("deposit()", "0xd0e30db0\n"),
    ("withdraw(uint256)", "0x2e1a7d4d\n"),
]


@pytest.mark.parametrize("sig,want", SELECTOR_VECTORS)
def test_selector_vectors(sig, want):
    val, st, raw = eth.run_one(f"selector {sig}")
    assert val == want


def test_demo_selectors_all_hit_cache():
    # Every demo sig must resolve at cache-hit speed (< 8k instr; a keccak
    # miss costs > 20k). Guards the DROM table against shrinkage.
    p = Path(__file__).parent / "data" / "eth" / "demo-selectors.txt"
    sigs = [l.strip() for l in p.read_text().splitlines()
            if l.strip() and not l.strip().startswith("#")]
    assert len(sigs) >= 20
    for sig in sigs:
        val, st, raw = eth.run_one(f"selector {sig}")
        assert st["instructions"] < 8000, (sig, st["instructions"])
        assert val == eth.expected(f"selector {sig}")


def test_selector_miss_falls_through():
    val, st, raw = eth.run_one("selector frobnicateThyWombat(uint256,address)")
    assert val == eth.expected("selector frobnicateThyWombat(uint256,address)")
    assert st["instructions"] > 15000  # paid for the keccak


ABI_ENCODE_VECTORS = [
    ("encode transfer(address,uint256) 0x000000000000000000000000000000000000dEaD 100",
     "0xa9059cbb000000000000000000000000000000000000000000000000000000000000dead0000000000000000000000000000000000000000000000000000000000000064\n"),
    ("encode balanceOf(address) 0x0000000000000000000000000000000000000001",
     "0x70a082310000000000000000000000000000000000000000000000000000000000000001\n"),
    # zero-arg call is valid: selector only
    ("encode f()", "0x26121ff0\n"),
]


@pytest.mark.parametrize("cmd,want", ABI_ENCODE_VECTORS)
def test_abi_encode_vectors(cmd, want):
    val, st, raw = eth.run_one(cmd)
    assert val == want


ABI_ERROR_VECTORS = [
    # (cmd, firmware error) — single-fault; the oracle must agree exactly.
    ("encode f(uint8) 256", "error (uint overflow)\n"),
    ("encode f(uint8) -1", "error (uint overflow)\n"),
    ("encode f(int8) 128", "error (int overflow)\n"),
    ("encode f(int8) -129", "error (int overflow)\n"),
    ("encode f(address) 0x1234", "error (bad address)\n"),
    ("encode f(bool) maybe", "error (bad bool)\n"),
    ("encode f(bool) TRUE", "error (bad bool)\n"),
    ("encode f(bytes2) 0x010203", "error (bad bytes)\n"),
    ("encode f(bytes2) 0xabc", "error (odd hex)\n"),
    ("encode f(bytes) 0x123", "error (odd hex)\n"),
    ("encode f(uint256[2]) [1]", "error (array len)\n"),
    ("encode f(uint256[]) [1]", "error (unsupported type)\n"),
    ("encode f(uint7) 1", "error (bad type)\n"),
    ("encode f(uint08) 1", "error (bad type)\n"),
    ("encode f(bytes33) 0x01", "error (bad type)\n"),
    ("encode f(foobar) 1", "error (bad type)\n"),
    ("encode f(uint256[65]) [1]", "error (array too long)\n"),
    ("encode f(uint256) 1 2", "error (arg count)\n"),
    ("encode f(uint256,uint256) 1", "error (arg count)\n"),
    ("encode f() 1", "error (arg count)\n"),
    ("encode f(", "error (bad encode)\n"),
    ("encode 9lives(uint256) 1", "error (bad sig)\n"),
    ("encode f(uint256,) 1", "error (bad sig)\n"),
    ("encode " + "f" * 300 + "(uint256) 1", "error (bad sig)\n"),  # sig > 511
    ("decode uint8 0x1234", "error (bad data)\n"),
    ("decode uint8 0x" + "00" * 31 + "ff" + "00" * 31 + "02", None),  # valid, below
    ("decode uint8 0x" + "ff" + "00" * 31, "error (bad padding)\n"),
    ("decode bool 0x" + "00" * 31 + "02", "error (bad padding)\n"),
    ("decode uint7 0x" + "00" * 32, "error (bad type)\n"),
    ("decode string 0x" + "00" * 31 + "20" + "00" * 31 + "01" + "ff" + "00" * 31,
     "error (bad string)\n"),
    ("decode nope", "error (bad decode)\n"),
    ("create 0x1234 7", "error (bad address)\n"),
    ("create 0x000000000000000000000000000000000000dEaD", "error (bad create)\n"),
    ("create 0x000000000000000000000000000000000000dEaD 7 8", "error (bad create)\n"),
    ("create 0x000000000000000000000000000000000000dEaD -1", "error (bad nonce)\n"),
    ("create 0x000000000000000000000000000000000000dEaD 18446744073709551616",
     "error (bad nonce)\n"),
    ("create2 0x000000000000000000000000000000000000dEaD 0x00", "error (bad create2)\n"),
]


@pytest.mark.parametrize("cmd,want", [c for c in ABI_ERROR_VECTORS if c[1] is not None])
def test_abi_error_vectors(cmd, want):
    val, st, raw = eth.run_one(cmd)
    assert val == want
    assert val == eth.expected(cmd)  # oracle must agree, not just firmware


def test_encode_extra_arg_rejected():
    # Strict: trailing args are an error, never silently ignored.
    v1, _, _ = eth.run_one("encode f(uint256) 1 2")
    assert v1 == "error (arg count)\n"
    v2, _, _ = eth.run_one("encode f() 1")
    assert v2 == "error (arg count)\n"


def test_decode_trailing_data_ignored():
    v1, _, _ = eth.run_one("decode uint8 0x" + "00" * 31 + "ff" + "00" * 31 + "02")
    v2, _, _ = eth.run_one("decode uint8 0x" + "00" * 31 + "ff")
    assert v1 == v2 == "255\n"


def test_tiny_fuzz_abi():
    # Happy path + single-fault error paths; the oracle cross-checks every
    # value-level rejection against eth_abi itself (xchecks raise on drift).
    # Generator draws run unfiltered (the non-bridge image runs anything in
    # eth_in); the 80-token window is enforced in gate runs, not here, since
    # full-width cases (create2, wide decodes) never fit it.
    import random
    rng = random.Random(5)
    n = 0
    op_errs: dict[str, int] = {}
    gens = [eth.selector_cases, eth.encode_cases, eth.decode_cases,
            eth.create_cases, eth.create2_cases]
    for gen in gens:
        for op, cmd, oracle in gen(8, rng):
            val, st, raw = eth.run_one(cmd)
            assert val == oracle, (cmd, val, oracle)
            if oracle.startswith("error "):
                op_errs[op] = op_errs.get(op, 0) + 1
            n += 1
    assert n == 40
    # Every fault-bearing op must fire its injector (selector never faults).
    for op in ("encode", "decode", "create", "create2"):
        assert op_errs.get(op, 0) >= 1, (op, op_errs)


def test_instruction_counts_reported():
    # The harness must report per-call instruction counts (the eth-bench input).
    _, st, _ = eth.run_one("keccak 0x")
    assert st["instructions"] > 1000
    assert 1.0 < st["tokens"] / st["instructions"] < 3.0


def test_dispatch_budget():
    # Profiler in the test flow: dispatch ('foo', no eval) must stay at
    # calc.c scale. The 2026-10-31 strncpy-padding regression (16,920 instr,
    # 97% memset) must fail HERE, not in review. Deterministic machine.
    import shutil
    if not shutil.which("riscv64-elf-nm"):
        pytest.skip("riscv64-elf-nm required")
    from chipformer.rv import profile as P
    counts, out, steps = P.histogram("foo")
    assert out == "error (unknown cmd)\n"
    assert steps <= 600, f"dispatch budget blown: {steps}"
    assert counts.get("memset", 0) <= 50, dict(counts)
