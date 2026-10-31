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
