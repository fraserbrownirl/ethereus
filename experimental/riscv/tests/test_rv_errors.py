"""Malformed input per command. The firmware must answer an error line, never crash, hang or print garbage.

docs/dispatcher-rules.md rule 3. Over-window is checked through the bridge image, as the model reaches it.
"""

import random
import re
from pathlib import Path

import pytest

from chipformer.rv import bridge as BR
from chipformer.rv import eth, spill

ELF = eth.ELF
BRIDGE = Path(eth.BRIDGE_ELF)
A = "0x" + "11" * 20
rng = random.Random(20261101)


def junk(n):
    return "".join(rng.choice("zxqZ!@#$%^&*()_+=[]{}") for _ in range(n))


CASES = {
    "keccak": ["keccak", "keccak 0x", "keccak 0xabc", "keccak 0xzz", "keccak @", "keccak @missing", "keccak '", "keccak 0x" + "g" * 40],
    "u256": ["u256", "u256 +", "u256 1+", "u256 (1", "u256 1)", "u256 0xzz", "u256 1/0", "u256 1%0", "u256 " + "9" * 90, "u256 " + junk(30), "u256 2**", "u256 **2"],
    "selector": ["selector", "selector (", "selector foo", "selector foo(", "selector " + "a" * 400, "selector " + junk(20)],
    "encode": ["encode", "encode foo", "encode foo(", "encode foo(uint256)", "encode foo(uint256) x", "encode foo(uint256) 1 2", "encode foo(notatype) 1",
               "encode foo(address) 0x12", "encode foo(bool) 7", "encode foo(uint8) 999", "encode foo(uint256[) 1", "encode foo(bytes32) 0x12", "encode " + junk(25)],
    "decode": ["decode", "decode uint256", "decode uint256 0x", "decode uint256 0xzz", "decode uint256 0x12", "decode notatype 0x" + "00" * 32,
               "decode uint256,address 0x" + "00" * 32, "decode address 0x" + "ff" * 32, "decode bool 0x" + "02".rjust(64, "0"), "decode " + junk(20)],
    "create": ["create", f"create {A}", f"create {A} x", f"create {A} -1", "create 0x12 1", "create 0xzz 1", f"create {A} 1 2", "create " + junk(30)],
    "create2": ["create2", f"create2 {A}", f"create2 {A} 1", f"create2 {A} 1 0x12", f"create2 0x12 1 0x{'00' * 32}", f"create2 {A} x 0x{'zz' * 32}", f"create2 {A} 1 @missing"],
    "run": ["run", "run erc20", "run nosuch 0x", "run erc20 0xzz", "run erc20 0xabc", "run erc20 @missing", "run erc20 0x" + "00" * 900, "run amm 0x12", "run erc20 0x18160ddd x",
            "run " + junk(30)],
    "other": ["", " ", "\n", "foo", junk(50), "keccak\0", "0" * 300],
}
PARAMS = [(k, c) for k, v in CASES.items() for c in v]


def printable(s):
    return all(c == "\n" or 32 <= ord(c) < 127 for c in s)


@pytest.mark.parametrize(("kind", "cmd"), PARAMS, ids=[f"{k}-{i}" for i, (k, _) in enumerate(PARAMS)])
def test_malformed_input_never_crashes(kind, cmd):
    m = eth.machine(cmd, ELF)
    m.run(3 * 10**6)
    assert m.halted, f"hang: {cmd[:60]!r}"
    assert m.fault is None, f"fault {m.fault}: {cmd[:60]!r}"
    out = m.out.decode("latin-1")
    assert printable(out), f"garbage: {out[:80]!r}"
    assert out.endswith("\n"), f"no line end: {out[:80]!r}"


# A valid-looking result is allowed only where the oracle agrees it is valid: `keccak foo` hashes text.
@pytest.mark.parametrize(("kind", "cmd"), [p for p in PARAMS if p[1].strip() and "\0" not in p[1]], ids=lambda x: x if len(str(x)) < 24 else "c")
def test_malformed_result_agrees_with_the_oracle(kind, cmd):
    m = eth.machine(cmd, ELF)
    m.run(3 * 10**6)
    got = m.out.decode("latin-1")
    if got.startswith("error"):
        assert re.fullmatch(r"error \([^\n]*\)\n", got) or got == "error\n", got[:80]
        return
    if kind == "run" or cmd.split()[0] not in ("keccak", "u256", "selector", "encode", "decode", "create", "create2"):
        return
    assert eth.agrees(got, eth.expected(cmd)), f"{cmd[:50]!r}: firmware {got[:60]!r}, oracle {eth.expected(cmd)[:60]!r}"


# Each of these used to answer instead of erroring, and the oracle agreed.
MUST_ERROR = [
    "keccak",
    "keccak @nosuchfile",
    "u256 " + "9" * 90,
    "u256 115792089237316195423570985008687907853269984665640564039457584007913129639936",
    "selector (",
    "selector foo",
    "selector foo(",
    "selector foo)(",
    "selector (uint256)",
    "selector foo(uint256)x",
    "selector f(" + "a" * 300 + ")",
    "encode foo(bytes32) 0x12",
]


@pytest.mark.parametrize("cmd", MUST_ERROR, ids=[c[:20] for c in MUST_ERROR])
def test_must_answer_error(cmd):
    m = eth.machine(cmd, ELF)
    m.run(3 * 10**6)
    got = m.out.decode("latin-1")
    assert got.startswith("error"), got[:60]  # by design: asserted, not compared with an oracle
    assert eth.expected(cmd) == eth.REJECT


@pytest.mark.parametrize("cmd", [
    "keccak 0x", "keccak foo", "u256 115792089237316195423570985008687907853269984665640564039457584007913129639935",
    "u256 0-1", "selector foo()", "selector permit (address, ((address, uint160), uint256))",
    "encode foo(bytes32) 0x" + "12" * 32, "encode foo(bytes1) 0x12",
])
def test_neighbours_of_those_still_answer(cmd):
    m = eth.machine(cmd, ELF)
    m.run(3 * 10**6)
    got = m.out.decode("latin-1")
    assert not got.startswith("error"), got[:60]
    assert got == eth.expected(cmd)


# Found by the over-window gate: a positive decimal was read as two's complement.
@pytest.mark.parametrize("cmd", [
    f"encode f(int256) {2**255}", f"encode f(int8) {2**256 - 1}", f"encode f(int256) {2**255 - 1}", f"encode f(int256) {-2**255}",
    "encode f(int256) 0x" + "8" + "0" * 63, "encode f(int256) 0x" + "f" * 64,
    f"encode f(uint256) {2**256}", f"encode f(int256) {2**256}",
])
def test_integer_boundaries_agree_with_the_oracle(cmd):
    m = eth.machine(cmd, ELF)
    m.run(3 * 10**6)
    assert m.out.decode("latin-1") == eth.expected(cmd)


def test_boundary_slice_covers_every_width():
    cases = list(eth.boundary_cases())
    assert len(cases) == 32 * 2 * 4 + 32 * 2
    assert sum(w == eth.REJECT for _, w in cases) == 32 * 2 * 2 + 32
    for w in (8, 128, 256):
        for t in ("int", "uint"):
            assert sum(c.startswith(f"encode f({t}{w}) ") for c, _ in cases) == 4


def test_boundary_slice_on_the_firmware():
    bad = []
    for cmd, want in eth.boundary_cases():
        m = eth.machine(cmd, ELF)
        m.run(3 * 10**6)
        if not eth.agrees(m.out.decode("latin-1"), want):
            bad.append((cmd[:40], m.out.decode("latin-1")[:40]))
    assert not bad, bad


def test_answering_a_by_design_input_fails_the_gate():
    assert eth.agrees("error (bad sig)\n", eth.REJECT)
    assert not eth.agrees("0x41b1a064\n", eth.REJECT)


def test_gate_exits_nonzero_on_a_mismatch(monkeypatch):
    monkeypatch.setattr(eth, "expected", lambda cmd: "wrong\n")
    monkeypatch.setattr("sys.argv", ["eth", "--cases", "2", "--seed", "1", "--only", "keccak"])
    with pytest.raises(SystemExit) as e:
        eth.main()
    assert e.value.code == 1


def test_elf_override_reaches_the_default_path(monkeypatch, tmp_path):
    monkeypatch.setattr(eth, "ELF", tmp_path / "missing.elf")
    with pytest.raises(FileNotFoundError):
        eth.machine("keccak 0x")


def test_over_window_call_answers_too_long():
    T = BR.Text(BR.default_tokenizer())
    ids = T.tok.encode(spill.call_text("keccak 0x" + "12" * 200), add_special_tokens=False).ids
    assert len(ids) > spill.WINDOW
    s = BR.Session(T, spill.parked(BRIDGE))
    s.qwen([T.TCO, *ids, T.TCC])
    assert "error (too long)" in T.tok.decode(s.runs[-1], skip_special_tokens=False)
