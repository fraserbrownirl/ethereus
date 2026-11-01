"""Host-level firmware checks: ASan/UBSan overflow probes + source audit.

The RV gate proves behavior; this file proves memory safety on paths the
machine tests cannot reach (oversize inputs past the eth_in window) and that
no unbounded string write exists in the firmware sources.
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ETH = Path(__file__).parent.parent / "chipformer" / "rv" / "env" / "eth"

sys.path.insert(0, str(Path(__file__).parent))
from evm_oracle import oracle_raw

STUB = Path(__file__).parent / "eth_host_stub"
MAIN = Path(__file__).parent / "eth_host_main.c"


@pytest.fixture(scope="module")
def fuzzbin(tmp_path_factory):
    cc = shutil.which("cc")
    if cc is None:
        pytest.skip("host cc required")
    d = tmp_path_factory.mktemp("eth_host")
    gen = d / "gen"
    gen.mkdir()
    r = subprocess.run(
        [sys.executable, str(ETH / "gen_selectors.py"),
         str(ETH / "selectors.txt"), str(gen / "selectors.h")],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    r = subprocess.run(
        [sys.executable, str(ETH / "gen_builtins.py"),
         str(ETH / "contracts"), str(gen)],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:]
    srcs = [MAIN, ETH / "eth.c", ETH / "abi.c", ETH / "u256.c",
            ETH / "keccak.c", ETH / "evm.c", ETH / "builtins.c",
            ETH / "xkcp" / "KeccakP-1600-inplace32BI.c"]
    missing = [str(s) for s in srcs if not s.exists()]
    assert not missing, missing
    exe = d / "fuzz"
    r = subprocess.run(
        [cc, "-std=c11", "-g", "-O1", "-DHOST_FUZZ",
         "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
         "-I", str(STUB), "-I", str(ETH), "-I", str(gen),
         *[str(s) for s in srcs], "-o", str(exe)],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-3000:]
    return exe


def _run(exe, *args):
    r = subprocess.run([str(exe), *args], capture_output=True, text=True)
    assert r.returncode == 0, (r.returncode, r.stderr[-2000:])
    assert "ERROR" not in r.stderr and "runtime error" not in r.stderr, r.stderr[-2000:]
    return r.stdout


def test_host_matches_rv_on_happy_path(fuzzbin):
    out = _run(fuzzbin, "cmd",
               "encode transfer(address,uint256)",
               "0x000000000000000000000000000000000000dEaD", "100")
    assert out == ("0xa9059cbb000000000000000000000000000000000000000000000000000000000000dead"
                   "0000000000000000000000000000000000000000000000000000000000000064\n")


def test_sig_overflow_is_an_error(fuzzbin):
    # Bug 1: unbounded memcpy into sig[512]. A 600-char sig must error cleanly.
    out = _run(fuzzbin, "cmd", "encode " + "f" * 300 + "(uint256) 1")
    assert out == "error (bad sig)\n"


def test_hex_overflow_is_an_error(fuzzbin):
    # Bug 2: unbounded hex_to_bytes into data[8192]. Unreachable via eth_in
    # (2048), so this goes straight at abi_decode with 18KB of hex.
    out = _run(fuzzbin, "decode", "uint256", "0x" + "00" * 9000)
    assert out == "rc=-1 err=hex too long out=(none)\n"


def test_blen_u32_wrap_is_an_error(fuzzbin):
    # off+32+blen in u32 arithmetic wraps when blen is near 2^32; blen must
    # be bounded before the add. offset=32, len=0xffffffff.
    blob = "00" * 31 + "20" + "ff" * 32
    out = _run(fuzzbin, "decode", "string", "0x" + blob)
    assert out == "rc=-1 err=bad data out=(none)\n"


def test_no_unbounded_string_writes():
    # Every char buffer in env/eth must use a bounded write. strcpy/strcat/
    # sprintf/gets/scanf are banned outright (word match, so snprintf and
    # strncpy are fine); memcpy/snprintf sites were audited one by one.
    banned = re.compile(r"\b(strcpy|strcat|sprintf|gets|scanf)\b")
    hits = []
    files = sorted(ETH.rglob("*.c")) + sorted(ETH.rglob("*.h"))
    assert files, "no firmware sources found"
    for p in files:
        for i, line in enumerate(p.read_text().splitlines(), 1):
            code = line.split("//")[0].split("/*")[0]
            m = banned.search(code)
            if m:
                hits.append(f"{p.name}:{i}: {line.strip()}")
    assert not hits, "\n".join(hits)


def _push(n: int):
    if n < 0:
        n %= 1 << 256
    ln = max(1, (n.bit_length() + 7) // 8)
    return bytes([0x5F + ln]) + n.to_bytes(ln, "big")


def _prog(*stack_top_first, op):
    # stack_top_first: values pushed so the first ends on top (pushed last).
    code = b""
    for v in reversed(stack_top_first):
        code += _push(v)
    code += bytes([op])
    return (code + bytes.fromhex("60005260206000f3")).hex()


DIVZERO = [
    ("div-zero", _prog(7, 0, op=0x04)),
    ("div-order", _prog(7, 2, op=0x04)),  # 7//2 pins operand order
    ("sdiv-zero", _prog((1 << 256) - 1, 0, op=0x05)),
    ("sdiv-neg", _prog((1 << 256) - 7, 2, op=0x05)),  # -7//2 == -3
    ("mod-zero", _prog(7, 0, op=0x06)),
    ("mod-order", _prog(7, 3, op=0x06)),  # 7%3 == 1
    ("smod-zero", _prog((1 << 256) - 1, 0, op=0x07)),
    ("smod-neg", _prog((1 << 256) - 7, 3, op=0x07)),  # -7%3 == -1
    ("addmod-zero", _prog(5, 6, 0, op=0x08)),
    ("addmod-nz", _prog(5, 6, 7, op=0x08)),  # 11%7 == 4
    ("mulmod-zero", _prog(5, 6, 0, op=0x09)),
    ("mulmod-nz", _prog(5, 6, 7, op=0x09)),  # 30%7 == 2
    ("shl-256", _prog(256, 1, op=0x1B)),
    ("shr-300", _prog(300, (1 << 256) - 1, op=0x1C)),
    ("sar-257-neg", _prog(257, (1 << 256) - 1, op=0x1D)),
    ("sar-257-pos", _prog(257, 1, op=0x1D)),
    ("signextend-31", _prog(31, 0xFF, op=0x0B)),  # x>=31: identity
    ("signextend-0", _prog(0, 0xFF, op=0x0B)),
]


@pytest.mark.parametrize("name,code", DIVZERO, ids=[c[0] for c in DIVZERO])
def test_raw_opcode_matches_pyevm(fuzzbin, name, code):
    # Division-by-zero is 0 (EVM, not RISC-V); shifts >=256 saturate.
    o_ret, o_gas, o_err = oracle_raw(bytes.fromhex(code))
    out = _run(fuzzbin, "raw", code)
    assert not o_err
    assert out == f"0x{o_ret}\n{o_gas}\n"


UNDERFLOW = [
    ("addmod-2deep", _prog(5, 6, op=0x08)),  # needs 3, the 0x0C-row bug
    ("mulmod-2deep", _prog(5, 6, op=0x09)),
    ("div-1deep", _prog(5, op=0x04)),
    ("iszero-0deep", "1560005260206000f3"),
]


@pytest.mark.parametrize("name,code", UNDERFLOW, ids=[c[0] for c in UNDERFLOW])
def test_raw_stack_underflow_matches_pyevm(fuzzbin, name, code):
    _o_ret, _o_gas, o_err = oracle_raw(bytes.fromhex(code))
    assert o_err
    assert _run(fuzzbin, "raw", code) == "error (evm)\n"
