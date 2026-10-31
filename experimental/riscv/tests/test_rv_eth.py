"""Gate 1 for the eth firmware: ref.Machine == Python on keccak and u256.

Two paths, both through the author's code only:
- gate1 (tests/data/rv/eth.elf, non-bridge): driven exactly as calc.py drives
  calc.elf (cmd poked into eth_in at RAM 0x100). Fixed vectors + tiny fuzz with
  the Qwen-tokenizer window filter.
- bridge smoke (tests/data/rv/eth-bridge.elf): fed through bridge.Session with
  real Qwen-tokenized calls, 6 cases including one over-window call. This is the
  reference-level bridge check; IR teacher-forcing is gate 2.
"""

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
    assert val == eth.expected(cmd)


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


def test_instruction_counts_reported():
    # The harness must report per-call instruction counts (the eth-bench input).
    _, st, _ = eth.run_one("keccak 0x")
    assert st["instructions"] > 1000
    assert 1.0 < st["tokens"] / st["instructions"] < 3.0
