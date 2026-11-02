"""The eth firmware (env/eth/eth.elf): keccak, u256, selector/abi, create/create2, evm.

This is the Gate-1 harness: ref.Machine vs Python (eth_hash / eth_abi / py-evm) on the
NON-BRIDGE eth.elf, driven exactly as calc.py drives calc.elf (the cmd line poked into
eth_in at RAM 0x100; stdout is the value + newline). No bridge, no TEXT layout.

The 80-token capture window is enforced by SIZING, not by emulation: every generated
case's full tool call is encoded with the real Qwen tokenizer and cases over 80
captured tokens (or with a token over 16 bytes, which the row would truncate) are
rejected the way the bridge firmware would, counted, and reported. The bridge path
itself is tested separately through bridge.Session (tests/test_rv_eth.py), never with
a hand-built TEXT layout.

Usage (from experimental/riscv):
  python -m chipformer.rv.eth --cases 2000           # 2000 accepted cases per op
  python -m chipformer.rv.eth --cases 2000 --only keccak,u256
  python -m chipformer.rv.eth --expr "keccak 0x6161"
  python -m chipformer.rv.eth --expr "u256 2**255 - 1"
"""

import argparse
import random
import re
import sys
import time
from pathlib import Path

from chipformer.rv import bridge as BR
from chipformer.rv import elf as elfmod
from chipformer.rv import ref

try:
    from eth_abi import decode as _abi_decode
    from eth_abi import encode as _abi_encode
    from eth_abi.exceptions import NonEmptyPaddingBytes as _NonEmptyPad
    from eth_abi.registry import registry as _registry
except ImportError:  # pragma: no cover
    _abi_encode = _abi_decode = _NonEmptyPad = _registry = None
try:
    import rlp as _rlp
except ImportError:  # pragma: no cover
    _rlp = None

ELF = Path(__file__).parents[2] / "tests" / "data" / "rv" / "eth.elf"
BRIDGE_ELF = Path(__file__).parents[2] / "tests" / "data" / "rv" / "eth-bridge.elf"
ETH_IN, ETH_IN_SIZE = 0x100, 2048
FILE_BUF, FILE_BUF_SIZE = 0x900, 4096

OPS = ["keccak", "u256"]
ALL_OPS = ["keccak", "u256", "selector", "encode", "decode", "create", "create2", "run"]

def _poke(m: ref.Machine, base: int, b: bytes):
    for i, x in enumerate(b):
        a = base + i
        w = a >> 2
        m.ram[w] = m.ram[w] & ~(0xFF << 8 * (a & 3)) | x << 8 * (a & 3)


def machine(cmd: str, path=None) -> ref.Machine:
    """The non-bridge firmware's snapshot with the cmd line in eth_in (cf calc.py)."""
    m = ref.Machine.from_program(elfmod.program(path or ELF))
    b = cmd.encode() + b"\0"
    assert len(b) <= ETH_IN_SIZE, f"cmd too long for eth_in: {len(b)}"
    _poke(m, ETH_IN, b)
    return m


def run(cmd: str, path=None) -> tuple[str, list[int]]:
    m = machine(cmd, path)
    toks = m.run(3 * 10**6)
    assert m.halted and m.fault is None, (cmd, m.fault, m.out)
    return m.out.decode(), toks


def machine_with_files(cmd: str, files: dict[str, str], path=None) -> ref.Machine:
    """machine() plus bridge-resolved @file args poked into file_buf.

    files maps name -> hex content; written as name\\0hex\\0 pairs plus a
    final NUL. Large arguments never go through the token window (same
    mechanism as the API gateway's spill).
    """
    m = machine(cmd, path)
    blob = b""
    for name, content in files.items():
        assert "\0" not in name and "\0" not in content
        blob += name.encode() + b"\0" + content.encode() + b"\0"
    blob += b"\0"
    assert len(blob) <= FILE_BUF_SIZE, f"files too big: {len(blob)}"
    _poke(m, FILE_BUF, blob)
    return m


def run_with_files(cmd: str, files: dict[str, str], path=None) -> tuple[str, list[int]]:
    m = machine_with_files(cmd, files, path)
    toks = m.run(3 * 10**6)
    assert m.halted and m.fault is None, (cmd, m.fault, m.out)
    return m.out.decode(), toks


# ---------------------------------------------------------------------------
# Capture-window sizing with the real Qwen tokenizer.
#
# The bridge captures the tokens strictly between <tool_call> and </tool_call>
# (Session.qwen: TCO resets, TCC disarms, neither is stored), one token per
# 16-byte row, 80 rows max. A call the model can never send must never count as
# a gate-1 case, so the harness measures each case and rejects over-window ones
# the way the firmware would (it prints "error (too long)").

def _tool_call_text(cmd: str) -> str:
    # The full turn the model would emit; sizing counts what the bridge captures.
    return (
        "<tool_call>\n"
        "<function=eth>\n"
        f"<parameter=cmd>\n{cmd}\n</parameter>\n"
        "</function>\n"
        "</tool_call>"
    )


_T = None

def text_model() -> "BR.Text":
    global _T
    if _T is None:
        _T = BR.Text(BR.default_tokenizer())
    return _T


def captured_size(cmd: str) -> tuple[int, int]:
    """(captured tokens, widest token in bytes) for cmd's tool call."""
    T = text_model()
    q = T.encode(_tool_call_text(cmd))
    assert q[0] == T.TCO and q[-1] == T.TCC, q[:3]
    inner = q[1:-1]
    # The call's own text must not contain the wrapper tags (generators never emit them).
    assert T.TCO not in inner and T.TCC not in inner
    wide = 0
    for t in inner:
        wide = max(wide, len(T.bytes[t]))
    return len(inner), wide


# ---------------------------------------------------------------------------
# Python oracles

def _py_keccak(data: bytes) -> str:
    from eth_hash.auto import keccak as k
    return "0x" + k(data).hex()

def _parse_keccak_hex_or_text(s: str) -> bytes:
    s = s.strip()
    if s.startswith("0x") or s.startswith("0X"):
        h = s[2:]
        if len(h) % 2 or any(c not in "0123456789abcdefABCDEF" for c in h):
            return None  # type: ignore[return-value]
        return bytes.fromhex(h)
    if len(s) >= 2 and ((s.startswith("'") and s.endswith("'")) or (s.startswith('"') and s.endswith('"'))):
        return s[1:-1].encode()
    return s.encode()

class ByDesign(BaseException):
    """The firmware rejects this input as a decision of ours, stricter than eth_abi or hashlib would
    be. There is nothing to compare: the gate asserts an error and never asks the oracle's libraries."""


REJECT = "<rejected by design>"


def agrees(got: str, oracle: str) -> bool:
    return got.startswith("error") if oracle == REJECT else got == oracle


def expected_keccak(arg: str) -> str:
    # Mirror handle_keccak exactly: trim, hex? (odd/invalid -> bad hex), quoted?
    # (len>=2), else raw; the 1100 cap applies to DECODED data length.
    a = arg.strip()
    if not a or a.startswith("@"):  # @name is a file argument; with no such file, an error
        raise ByDesign
    data = _parse_keccak_hex_or_text(a)
    if data is None:
        return "error (bad hex)\n"
    if len(data) > 1100:
        return "error (too long)\n"
    return _py_keccak(data) + "\n"

# -- u256 oracle (Python big-int, mod 2**256 wraps) -------------------------

MASK256 = (1 << 256) - 1

def _u256_wrap(v: int) -> int:
    return v & MASK256

def _parse_u256_token(tok: str) -> int | None:
    tok = tok.strip()
    if tok.startswith("0x") or tok.startswith("0X"):
        try:
            return int(tok, 16) & MASK256
        except ValueError:
            return None
    if tok.lstrip("-+").isdigit():
        # decimal: two's complement from signed string, then & MASK
        try:
            v = int(tok)
        except ValueError:
            return None
        if abs(v) > MASK256:
            raise ByDesign
        return v & MASK256
    return None

# Shunting-yard in Python matching firmware's prec table exactly
_U256_PREC = {"**": 6, "*": 5, "/": 5, "%": 5, "+": 4, "-": 4, "<<": 3, ">>": 3, "&": 2, "^": 1, "|": 0}
_U256_RIGHT = {"**"}

def _u256_tokenize(s: str):
    toks = []
    i = 0
    expect_operand = True
    depth, wrap_pending, wraps = 0, False, []  # unary -(E) is a wrapped (0-(E)), as handle_u256 does
    while i < len(s):
        if s[i].isspace():
            i += 1
            continue
        if expect_operand:
            if s[i] == "(":
                toks.append("("); depth += 1
                if wrap_pending:
                    wraps.append(depth); wrap_pending = False
                i += 1; expect_operand = True; continue
            if s[i] in "+-":
                j = i + 1
                while j < len(s) and s[j].isspace():
                    j += 1
                if j < len(s) and s[j] == "(":
                    toks.append("("); depth += 1; wrap_pending = True
                    toks.append(("num", "0")); toks.append(s[i]); i += 1; expect_operand = True; continue
            if s[i:i+2].lower() == "0x":
                j = i + 2
                while j < len(s) and s[j] in "0123456789abcdefABCDEF":
                    j += 1
                if j == i + 2:
                    return None
                toks.append(("num", s[i:j])); i = j; expect_operand = False; continue
            if s[i].isdigit():
                j = i
                while j < len(s) and s[j].isdigit():
                    j += 1
                toks.append(("num", s[i:j])); i = j; expect_operand = False; continue
            return None
        else:
            if s[i] == ")":
                toks.append(")"); depth -= 1
                if wraps and wraps[-1] == depth + 1:
                    toks.append(")"); depth -= 1; wraps.pop()
                i += 1; expect_operand = False; continue
            if s[i:i+2] in ("**", "<<", ">>"):
                toks.append(s[i:i+2]); i += 2; expect_operand = True; continue
            if s[i] in "+-*/%&|^":
                toks.append(s[i]); i += 1; expect_operand = True; continue
            return None
    return toks

def _u256_parse_to_rpn(toks):
    out, stack = [], []
    prec = _U256_PREC
    for t in toks:
        if isinstance(t, tuple) and t[0] == "num":
            out.append(t)
        elif t == "(":
            stack.append(t)
        elif t == ")":
            while stack and stack[-1] != "(":
                out.append(stack.pop())
            if not stack:
                return None
            stack.pop()
        else:
            p1 = prec[t]
            while stack and stack[-1] != "(":
                p2 = prec.get(stack[-1], -1)
                if p2 == -1:
                    break
                if (t in _U256_RIGHT and p1 < p2) or (t not in _U256_RIGHT and p1 <= p2):
                    out.append(stack.pop())
                else:
                    break
            stack.append(t)
    while stack:
        if stack[-1] == "(":
            return None
        out.append(stack.pop())
    return out

def expected_u256(expr: str) -> str:
    # Mirror handle_u256's suffix loop exactly: trailing '!' (overflow check) and
    # a trailing 'hex' word (space-separated, 0x output) in any order.
    s = expr.strip()
    check_overflow = False
    print_hex = False
    while True:
        s = s.rstrip()
        if s.endswith("!"):
            check_overflow = True
            s = s[:-1]
            continue
        if len(s) > 3 and s.endswith("hex") and s[-4].isspace():
            print_hex = True
            s = s[:-3]
            continue
        break
    s = s.strip()
    if not s:
        return "error (parse)\n"
    toks = _u256_tokenize(s)
    if toks is None:
        return "error (parse)\n"
    rpn = _u256_parse_to_rpn(toks)
    if rpn is None:
        return "error (mismatch)\n"
    stack: list[int] = []
    overflow = False
    for t in rpn:
        if isinstance(t, tuple):
            v = _parse_u256_token(t[1])
            if v is None:
                return "error (bad dec)\n"
            stack.append(v & MASK256)
        else:
            if len(stack) < 2:
                return "error (stack)\n"
            b = stack.pop(); a = stack.pop()
            if t == "+":
                r = a + b
                if r >> 256:
                    overflow = True
                stack.append(r & MASK256)
            elif t == "-":
                stack.append((a - b) & MASK256)
            elif t == "*":
                r = a * b
                if r >> 256:
                    overflow = True
                stack.append(r & MASK256)
            elif t == "/":
                if b == 0:
                    return "error (div by zero)\n"
                stack.append((a // b) & MASK256)
            elif t == "%":
                if b == 0:
                    return "error (div by zero)\n"
                stack.append((a % b) & MASK256)
            elif t == "**":
                # exponentiation with per-square overflow tracking (matches firmware)
                # firmware tracks overflow on every intermediate mul that exceeds 256 bits
                # We emulate exactly: square-and-multiply with overflow flag
                ov = 0
                res, base, exp = 1, a, b
                while exp:
                    if exp & 1:
                        prod = res * base
                        if prod >> 256:
                            ov = 1
                        res = prod & MASK256
                    sq = base * base
                    if sq >> 256:
                        ov = 1
                    base = sq & MASK256
                    exp >>= 1
                if ov:
                    overflow = True
                stack.append(res)
            elif t == "&":
                stack.append(a & b)
            elif t == "|":
                stack.append(a | b)
            elif t == "^":
                stack.append(a ^ b)
            elif t == "<<":
                sh = b
                if sh >= 256:
                    sh = 256
                # overflow if any bit shifted out
                if sh >= 1 and a != 0:
                    # bitlen(a) + sh > 256 => overflow
                    bl = a.bit_length()
                    if bl and bl + sh > 256:
                        overflow = True
                stack.append((a << sh) & MASK256 if sh < 256 else 0)
            elif t == ">>":
                sh = b
                if sh >= 256:
                    stack.append(0)
                else:
                    stack.append((a >> sh) & MASK256)
            else:
                return "error (op)\n"
    if len(stack) != 1:
        return "error (expr)\n"
    if check_overflow and overflow:
        return "error (overflow)\n"
    if print_hex:
        return hex(stack[0]) + "\n"  # u256_to_hex_trim: 0x + trimmed lowercase
    return str(stack[0]) + "\n"


# ---------------------------------------------------------------------------
# ABI oracles (reference: eth_abi / eth_utils; firmware: env/eth/abi.c)
#
# Two error formats: "error (X)" (firmware direct puts) vs "error <errb>"
# (errb path, no parens). Replicated exactly.

_HEXCHARS = set("0123456789abcdefABCDEF")
_MAXU256 = (1 << 256) - 1


def _keccak_hex(b: bytes) -> str:
    from eth_hash.auto import keccak as _k
    return _k(b).hex()


def _sig_shape_ok(s: str) -> bool:
    if s[0] == "(":
        return False
    depth, seen = 0, False
    for c in s:
        if c == "(":
            depth, seen = depth + 1, True
        elif c == ")":
            depth -= 1
            if depth < 0:
                return False
        elif seen and depth == 0 and c not in " \t\n\r\v\f":
            return False
    return seen and depth == 0


def expected_selector(arg: str) -> str:
    t = arg.strip(" \t\n\r\v\f")
    if not t:
        return "error (bad sig)\n"
    if len(arg) > 255 or not _sig_shape_ok(t):
        raise ByDesign
    return "0x" + _keccak_hex(re.sub(r"[ \t\n\r\v\f]+", "", t).encode())[:8] + "\n"


def _split_encode_args(rest: str, ntypes: int):
    """Mirror handle_encode's splitter. Returns (args, None) or (None, err)
    with err in {"arg count", "arg too long"}."""
    args: list[str] = []
    p, n = 0, len(rest)
    while p < n and len(args) < ntypes:
        while p < n and (rest[p] in _WS or rest[p] == ","):
            p += 1
        if p >= n:
            break
        if rest[p] in "\"'":
            q = rest[p]
            p += 1
            buf: list[str] = []
            while p < n and rest[p] != q and len(buf) < 255:
                buf.append(rest[p])
                p += 1
            if len(buf) == 255 and (p >= n or rest[p] != q):
                return None, "arg too long"
            if p < n and rest[p] == q:
                p += 1
            tok = "".join(buf)
        else:
            depth = 0
            buf = []
            while p < n and len(buf) < 255:
                c = rest[p]
                if c == "[":
                    depth += 1
                elif c == "]" and depth > 0:
                    depth -= 1
                elif depth == 0 and (c in _WS or c == ","):
                    break
                buf.append(c)
                p += 1
            if len(buf) == 255 and p < n and (depth > 0 or not (rest[p] in _WS or rest[p] == ",")):
                return None, "arg too long"
            tok = "".join(buf)
        # NOTE: quoted "" is a real empty arg (only "" can yield bi==0 here).
        args.append(tok)
    if len(args) != ntypes:
        return None, "arg count"
    # Strict: anything after the last arg is an error, not ignored.
    while p < n and (rest[p] in _WS or rest[p] == ","):
        p += 1
    if p < n:
        return None, "arg count"
    return args, None


def _split_sig(sig: str):
    """Strict signature split. Returns (name, types), ("", None) with reason
    "bad sig" / "too many types" / "bad type" encoded as (None, reason)."""
    lp, rp = sig.find("("), sig.rfind(")")
    if lp < 0 or rp < 0 or rp < lp:
        return None, "bad sig"
    name = sig[:lp].strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or len(name) >= 128:
        return None, "bad sig"
    inside = sig[lp + 1:rp]
    if inside.strip() == "":
        return name, []
    types: list[str] = []
    depth, cur = 0, ""
    for c in inside:
        if c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        if c == "," and depth == 0:
            t = cur.strip()
            if t == "":
                return None, "bad sig"
            if len(types) >= 16:
                return None, "too many types"
            if len(t) >= 64:
                return None, "bad type"
            types.append(t)
            cur = ""
        else:
            cur += c
    t = cur.strip()
    if t == "":
        return None, "bad sig"
    if len(types) >= 16:
        return None, "too many types"
    if len(t) >= 64:
        return None, "bad type"
    types.append(t)
    return name, types


# ---------------------------------------------------------------------------
# ABI oracle: eth_abi/eth_utils plus a thin normalizer.
#
# Type judgments, in order (same rule both sides):
#   1. v1 scope gate (_unsupported_shape): tuples, fixed-point, dynamic and
#      multi-dimension arrays are well-formed but out of v1 scope.
#   2. eth_abi's own registry: canonical-only; anything it rejects is bad.
#   3. Static [N] with N > 64: the firmware's array cap.
# Values are normalized to Python natives (whitespace trim, 0x prefix,
# decimal vs hex) and encoded/decoded by eth_abi itself.

_WS = " \t\n\r\v\f"
_FIXED_SCALAR_RE = re.compile(r"^u?fixed([1-9][0-9]*x[1-9][0-9]*)?$")
_FIXED_ARRAY_RE = re.compile(r"^u?fixed([1-9][0-9]*x[1-9][0-9]*)?(\[[0-9]+\]|\[\])(\[[0-9]+\]|\[\])*$")


def _unsupported_shape(tp: str) -> bool:
    """v1 scope gate: True for well-formed-but-out-of-scope shapes."""
    if "(" in tp or ")" in tp:
        return True
    if _FIXED_SCALAR_RE.match(tp) or _FIXED_ARRAY_RE.match(tp):
        return True
    if tp.count("[") > 1:
        return True
    if len(tp) > 2 and tp.endswith("[]"):
        pre = tp[:-2]
        if "[" not in pre and "]" not in pre:
            return True
    return False


def _gate_type(tp: str, for_encode: bool):
    """Judge one type. Returns None if v1-usable, else a reason string."""
    if _unsupported_shape(tp):
        return "unsupported type"
    try:
        _registry.get_encoder(tp) if for_encode else _registry.get_decoder(tp)
    except Exception:
        return "bad type"
    m = re.fullmatch(r"(.*)\[([0-9]+)\]", tp)
    if m and int(m.group(2)) > 64:
        return "array too long"
    return None


def _array_elem_n(tp: str):
    """(elem, n) for a gated static array type."""
    lb = tp.rfind("[")
    return tp[:lb], int(tp[lb + 1:-1])


def _scalar_bits(tp: str):
    """bits/N for a gated scalar: uint/int (bare = 256), bytesN."""
    if tp in ("uint", "int"):
        return 256
    if tp.startswith("uint"):
        return int(tp[4:])
    if tp.startswith("int"):
        return int(tp[3:])
    if tp.startswith("bytes"):
        return int(tp[5:])
    return None


def _uint_value(bits: int, arg: str):
    """Normalize a uint arg to int. Returns (int, None) or (None, reason)."""
    t = arg.strip(_WS)
    if t.startswith("-"):
        return None, "uint overflow"
    if t[:2] in ("0x", "0X"):
        body = t[2:]
        if body == "" or any(c not in _HEXCHARS for c in body):
            return None, "bad uint"
        if len(body) > 64:
            return None, "bad uint"
        v = int(body, 16)
    else:
        if not re.fullmatch(r"\+?[0-9]+", t):
            return None, "bad uint"
        v = int(t)
    if v.bit_length() > bits:
        return None, "uint overflow"
    return v, None


def _int_value(bits: int, arg: str):
    """Normalize an int arg to int. Hex is a bit pattern (values below
    2^(bits-1) or full-width two's complement accepted); decimal is signed.
    Returns (int, None) or (None, reason)."""
    t = arg.strip(_WS)
    neg = t.startswith("-")
    plus = t.startswith("+")
    mag = t[1:] if (neg or plus) else t
    is_hex = mag[:2] in ("0x", "0X")
    if is_hex and (neg or plus):
        return None, "bad int"
    if is_hex:
        body = mag[2:]
        if body == "" or any(c not in _HEXCHARS for c in body):
            return None, "bad int"
        if len(body) > 64:
            return None, "bad int"
        v = int(body, 16)
        lo = (1 << 256) - (1 << (bits - 1))
        if v < (1 << (bits - 1)):
            return v, None
        if v >= lo:
            return v - (1 << 256), None
        return None, "int overflow"
    if not re.fullmatch(r"[0-9]+", mag):
        return None, "bad int"
    mv = int(mag)
    if neg:
        if mv > (1 << (bits - 1)):
            return None, "int overflow"
        return -mv, None
    if mv >= (1 << (bits - 1)):
        return None, "int overflow"
    return mv, None


def _encode_value(tp: str, arg: str):
    """Normalize one scalar arg to a Python native for eth_abi. The type is
    already gated. Returns (value, None) or (None, reason)."""
    if tp == "uint" or tp.startswith("uint"):
        return _uint_value(_scalar_bits(tp), arg)
    if tp == "int" or tp.startswith("int"):
        return _int_value(_scalar_bits(tp), arg)
    if tp == "address":
        t = arg.strip(_WS)
        if t[:2] in ("0x", "0X"):
            t = t[2:]
        if len(t) != 40 or any(c not in _HEXCHARS for c in t):
            return None, "bad address"
        return "0x" + t.lower(), None
    if tp == "bool":
        t = arg.strip(_WS)
        if t in ("true", "1"):
            return True, None
        if t in ("false", "0"):
            return False, None
        return None, "bad bool"
    if tp.startswith("bytes") and tp != "bytes":
        n = _scalar_bits(tp)
        t = arg.strip(_WS)
        if t[:2] in ("0x", "0X"):
            t = t[2:]
        if any(c not in _HEXCHARS for c in t):
            return None, "bad hex"
        if len(t) % 2:
            return None, "odd hex"
        if len(t) < 2 * n:
            raise ByDesign
        if len(t) > 2 * n:
            return None, "bad bytes"
        return bytes.fromhex(t), None
    if tp == "bytes":
        t = arg.strip(_WS)
        if t[:2] in ("0x", "0X"):
            t = t[2:]
        if any(c not in _HEXCHARS for c in t):
            return None, "bad hex"
        if len(t) % 2:
            return None, "odd hex"
        return bytes.fromhex(t), None
    if tp == "string":
        return arg, None
    return None, "bad type"


_SKIP = object()


def _xcheck_encode(tp: str, xvalue) -> None:
    """True-differential check: eth_abi must also reject. Raises AssertionError
    (oracle bug) if eth_abi accepts something firmware rejects. _SKIP = no
    eth_abi-equivalent input (firmware lexical/tool layer)."""
    if xvalue is _SKIP:
        return
    types, values = [tp], [xvalue]
    try:
        _abi_encode(types, values)
    except Exception:
        return
    raise AssertionError(f"eth_abi accepted firmware-rejected encode: {types} {values!r}")


def _xcheck_decode(types, data: bytes) -> None:
    try:
        _abi_decode(types, data)
    except Exception:
        return
    raise AssertionError(f"eth_abi accepted firmware-rejected decode: {types}")


def _xvalue(tp: str, arg: str, errmsg: str):
    """Best-effort eth_abi input for a firmware error, or _SKIP/_TYPECHECK.

    Only value-level rejections are probed (eth_abi must also reject them).
    Lexical failures (bad hex, bad uint, shapes, caps) have no eth_abi
    equivalent; bad type is already established by the registry gate itself.
    """
    if errmsg in ("uint overflow", "int overflow"):
        t = arg.strip(_WS)
        try:
            if t[:2] in ("0x", "0X"):
                v = int(t[2:] or "0", 16)
            else:
                v = int(t)
        except ValueError:
            return _SKIP
        return v
    if errmsg in ("bad address", "bad bool"):
        return arg
    if errmsg == "bad bytes":
        t = arg.strip(_WS)
        if t[:2] in ("0x", "0X"):
            t = t[2:]
        try:
            return bytes.fromhex(t)
        except ValueError:
            return _SKIP
    return _SKIP


def _split_array_arg(a: str):
    """Mirror the firmware's [v1,v2] split. Returns raw spans or None (shape
    only; per-element trim/quotes/length happen in order in the caller)."""
    t = a.strip()
    if len(t) < 2 or not t.startswith("[") or not t.endswith("]"):
        return None
    return t[1:-1].split(",")


def _array_elem(s: str):
    """Trim + quote-strip one element. Returns (elem, too_long)."""
    e = s.strip()
    if len(e) >= 2 and e[0] == e[-1] and e[0] in "\"'":
        e = e[1:-1]
    return e, len(e) >= 512


def expected_encode(arg: str) -> str:
    s = arg[:2047].lstrip(_WS)
    lp = s.find("(")
    rp = s.find(")", lp) if lp >= 0 else -1
    if lp < 0 or rp < 0:
        return "error (bad encode)\n"
    sig, rest = s[:rp + 1], s[rp + 1:].lstrip(_WS)
    if len(sig) > 511:
        return "error (bad sig)\n"
    name, types = _split_sig(sig)
    if name is None:
        return f"error ({types})\n"
    args, err = _split_encode_args(rest, len(types))
    if err == "arg too long":
        return "error (arg too long)\n"
    if err == "arg count" or args is None:
        return "error (arg count)\n"
    # Types first (in order), then values: statics/arrays in order, dynamic
    # values deferred to the tail pass, head capacity before any value.
    for tp in types:
        reason = _gate_type(tp, True)
        if reason:
            return f"error ({reason})\n"
    head = sum(_head_slots(tp) for tp in types)
    head_end = 4 + head * 32
    if head_end > 4096:
        return "error (out too small)\n"
    values: list = [None] * len(types)
    dyn: list[tuple[int, str, str]] = []  # (index, type, arg)
    for i, (tp, av) in enumerate(zip(types, args)):
        if tp in ("bytes", "string"):
            dyn.append((i, tp, av))
            continue
        if "[" in tp:
            elem, need = _array_elem_n(tp)
            parts = _split_array_arg(av)
            if parts is None:
                return "error (bad array)\n"
            if len(parts) >= 64:
                return "error (array too long)\n"
            if len(parts) != need:
                _xcheck_encode(tp, [[0] * len(parts)])
                return "error (array len)\n"
            evs = []
            for span in parts:
                e, toolong = _array_elem(span)
                if toolong:
                    return "error (array elem too long)\n"
                v, verr = _encode_value(elem, e)
                if verr:
                    _xcheck_encode(elem, _xvalue(elem, e, verr))
                    return f"error ({verr})\n"
                evs.append(v)
            values[i] = evs
        else:
            v, verr = _encode_value(tp, av)
            if verr:
                _xcheck_encode(tp, _xvalue(tp, av, verr))
                return f"error ({verr})\n"
            values[i] = v
    tail_off = head_end
    for i, tp, av in dyn:
        v, verr = _encode_value(tp, av)
        if verr:
            _xcheck_encode(tp, _xvalue(tp, av, verr))
            return f"error ({verr})\n"
        data_len = len(av.encode()) if tp == "string" else len(v)
        tail_off += 32 + ((data_len + 31) & ~31)
        if tail_off > 4096:
            return "error (out too small)\n"
        values[i] = v
    try:
        data = _abi_encode(types, values)
    except Exception as e:  # accepted by firmware but not eth_abi: oracle bug
        raise AssertionError(f"eth_abi rejected firmware-accepted encode: {types} {e}")
    return "0x" + _keccak_hex(re.sub(r"[ \t\n\r\v\f]+", "", sig).encode())[:8] + data.hex() + "\n"


def _head_slots(tp: str) -> int:
    """Head slots for a gated type: 1, or N for a static array."""
    if "[" in tp:
        return _array_elem_n(tp)[1]
    return 1


def expected_decode(arg: str) -> str:
    s = arg[:4095].strip(_WS)
    sp = max(s.rfind(c) for c in _WS)
    if sp < 0:
        return "error (bad decode)\n"
    types_raw, hexd = s[:sp], s[sp + 1:].lstrip(_WS)
    # Type list: comma split, per-token trim, empty segments malformed.
    if types_raw.strip(_WS) == "":
        types: list[str] = []
    else:
        types = []
        for seg in types_raw.split(","):
            t = seg.strip(_WS)
            if t == "":
                return "error (bad type)\n"
            if len(types) >= 32:
                return "error (too many types)\n"
            if len(t) >= 64:
                return "error (bad type)\n"
            types.append(t)
    for tp in types:
        reason = _gate_type(tp, False)
        if reason:
            return f"error ({reason})\n"
    t = hexd.strip(_WS)
    if t[:2] in ("0x", "0X"):
        t = t[2:]
    if len(t) > 16384:
        return "error (hex too long)\n"
    if len(t) % 2:
        return "error (odd hex)\n"
    if any(c not in _HEXCHARS for c in t):
        return "error (bad hex)\n"
    data = bytes.fromhex(t)
    head = sum(_head_slots(tp) for tp in types)
    if len(data) % 32:
        _xcheck_decode(types, data)
        return "error (bad data)\n"
    if len(data) < head * 32:
        _xcheck_decode(types, data)
        return "error (data too short)\n"
    # Dynamic offsets in encounter order (structural; single-fault inputs only
    # for multi-fault precedence: a bad offset before a bad slot).
    cursor = 0
    for tp in types:
        if tp in ("bytes", "string"):
            if cursor + 32 > len(data):
                _xcheck_decode(types, data)
                return "error (bad offset)\n"
            off = int.from_bytes(data[cursor + 28:cursor + 32], "big")
            cursor += 32
            if off + 32 > len(data):
                _xcheck_decode(types, data)
                return "error (bad offset)\n"
            blen = int.from_bytes(data[off + 28:off + 32], "big")
            if blen > len(data) or off + 32 + blen > len(data):
                _xcheck_decode(types, data)
                return "error (bad data)\n"
        else:
            cursor += _head_slots(tp) * 32
    try:
        vals = _abi_decode(types, data)
    except _NonEmptyPad:
        return "error (bad padding)\n"
    except UnicodeDecodeError:
        return "error (bad string)\n"
    lines = []
    for tp, v in zip(types, vals):
        if "[" in tp:
            elem = tp[:tp.rfind("[")]
            for e in v:
                lines.append(_fmt_abi_value(elem, e))
        else:
            lines.append(_fmt_abi_value(tp, v))
    # Output capacity, in order (firmware emits per line into 4096 bytes).
    pos = 0
    for l in lines:
        bl = len(l.encode())
        if pos + bl + 2 >= 4096:
            return "error (out too small)\n"
        pos += bl + 1
    return "".join(l + "\n" for l in lines)


def _fmt_abi_value(tp: str, v) -> str:
    if tp == "address":
        return "0x" + v.lower()[2:] if v.startswith("0x") else "0x" + v.lower()
    if tp == "bool":
        return "true" if v else "false"
    if tp.startswith("uint") or tp.startswith("int"):
        return str(v)
    if tp.startswith("bytes") and tp not in ("bytes", "string"):
        return "0x" + bytes(v).hex()
    if tp == "bytes":
        return "0x" + bytes(v).hex()
    if tp == "string":
        return v if isinstance(v, str) else bytes(v).decode()
    return f"unsupported {tp}"


def _strict_nonce(tok: str):
    """Strict uint64: decimal or 0x-hex, full match, no sign. Else None."""
    t = tok.strip(_WS)
    if t[:2] in ("0x", "0X"):
        body = t[2:]
        if body == "" or any(c not in _HEXCHARS for c in body):
            return None
        v = int(body, 16)
    else:
        if not re.fullmatch(r"[0-9]+", t):
            return None
        v = int(t)
    return v if v < (1 << 64) else None


def _split_ws_comma(s: str) -> list[str]:
    return [p for p in re.split(r"[ \t\n\r\v\f,]+", s.strip(_WS)) if p != ""]


def _parse_hex_fixed(s: str, nbytes: int):
    t = s.strip(_WS)
    if t[:2] in ("0x", "0X"):
        t = t[2:]
    if len(t) != 2 * nbytes or any(c not in _HEXCHARS for c in t):
        return None
    return bytes.fromhex(t)


def expected_create(arg: str) -> str:
    toks = _split_ws_comma(arg[:511])
    if len(toks) != 2:
        return "error (bad create)\n"
    dep = _parse_hex_fixed(toks[0], 20)
    if dep is None:
        return "error (bad address)\n"
    nonce = _strict_nonce(toks[1])
    if nonce is None:
        return "error (bad nonce)\n"
    addr = _keccak_hex(_rlp.encode([dep, nonce]))[24:]
    return "0x" + addr + "\n"


def expected_create2(arg: str) -> str:
    toks = _split_ws_comma(arg[:511])
    if len(toks) != 3:
        return "error (bad create2)\n"
    dep = _parse_hex_fixed(toks[0], 20)
    salt = _parse_hex_fixed(toks[1], 32)
    ih = _parse_hex_fixed(toks[2], 32)
    if dep is None or salt is None or ih is None:
        return "error (bad hex)\n"
    addr = _keccak_hex(b"\xff" + dep + salt + ih)[24:]
    return "0x" + addr + "\n"


# ---------------------------------------------------------------------------
# Command dispatch

def expected(cmd: str) -> str:
    """Python oracle for one eth cmd line: what the firmware prints, or REJECT. Check with agrees()."""
    try:
        return _expected(cmd)
    except ByDesign:
        return REJECT


def _expected(cmd: str) -> str:
    parts = cmd.strip().split(None, 1)
    if not parts:
        return "error (unknown cmd)\n"
    head = parts[0]
    arg = parts[1] if len(parts) > 1 else ""
    if head == "eth":
        sub = arg.strip().split(None, 1)
        if not sub:
            return "error (unknown cmd)\n"
        cmd2, arg2 = sub[0], sub[1] if len(sub) > 1 else ""
        if cmd2 == "keccak":
            return expected_keccak(arg2)
        if cmd2 == "u256":
            return expected_u256(arg2)
        if cmd2 == "selector":
            return expected_selector(arg2)
        if cmd2 == "encode":
            return expected_encode(arg2)
        if cmd2 == "decode":
            return expected_decode(arg2)
        if cmd2 == "create":
            return expected_create(arg2)
        if cmd2 == "create2":
            return expected_create2(arg2)
        return "error (unknown cmd)\n"
    if head == "keccak":
        return expected_keccak(arg)
    if head == "u256":
        return expected_u256(arg)
    if head == "selector":
        return expected_selector(arg)
    if head == "encode":
        return expected_encode(arg)
    if head == "decode":
        return expected_decode(arg)
    if head == "create":
        return expected_create(arg)
    if head == "create2":
        return expected_create2(arg)
    return "error (unknown cmd)\n"

# ---------------------------------------------------------------------------
# Generators

def _rand_bytes(rng: random.Random, n: int) -> bytes:
    return bytes(rng.randint(0, 255) for _ in range(n))

def _rand_hex(rng: random.Random, nbytes: int) -> str:
    return "0x" + _rand_bytes(rng, nbytes).hex()

def keccak_cases(n: int, rng: random.Random):
    for _ in range(n):
        mode = rng.choice(["empty", "text", "text_long", "hex_short", "hex_long", "hex_boundary"])
        if mode == "empty":
            data = b""
            arg = "0x"
            yield ("keccak", f"keccak {arg}", _py_keccak(data) + "\n")
        elif mode == "text":
            # random ascii / utf-8ish string (not 0x-prefixed, not quoted)
            k = rng.randint(0, 200)
            # include tricky cases: strings that look hex but aren't prefixed
            s = "".join(chr(rng.randint(32, 126)) for _ in range(k))
            # avoid leading 0x that would be parsed as hex
            if s.strip().startswith("0x") or s.strip().startswith("0X"):
                s = "q" + s[1:]
            arg = s
            yield ("keccak", f"keccak {arg}", expected(f"keccak {arg}"))
        elif mode == "hex_short":
            k = rng.randint(0, 136)
            data = _rand_bytes(rng, k)
            arg = "0x" + data.hex()
            yield ("keccak", f"keccak {arg}", _py_keccak(data) + "\n")
        elif mode == "hex_long":
            # Usually over the 80-token window (rejected + counted by the sizer);
            # kept so the reject count measures the natural draw distribution.
            k = rng.randint(137, 606)
            data = _rand_bytes(rng, k)
            arg = "0x" + data.hex()
            yield ("keccak", f"keccak {arg}", _py_keccak(data) + "\n")
        elif mode == "text_long":
            # Long raw text: straddles the firmware's 1100-byte data cap both
            # ways. Mostly over-window (rejected + counted); in-window draws
            # still exercise the cap's lower edge.
            k = rng.randint(900, 1214)
            s = "".join(chr(rng.randint(32, 126)) for _ in range(k))
            if s.strip().startswith("0x") or s.strip().startswith("0X"):
                s = "q" + s[1:]
            yield ("keccak", f"keccak {s}", expected(f"keccak {s}"))
        elif mode == "hex_boundary":
            # lengths around sponge rate (136)
            for k in rng.sample([0, 1, 55, 135, 136, 137, 200, 272], 1):
                data = _rand_bytes(rng, k)
                arg = "0x" + data.hex()
                yield ("keccak", f"keccak {arg}", _py_keccak(data) + "\n")
        # Also sprinkle quoted strings
        if rng.random() < 0.15:
            s = "".join(chr(rng.randint(32, 126)) for _ in range(rng.randint(0, 60)))
            s = s.replace("'", "").replace('"', "")
            quoted = "'" + s + "'"
            yield ("keccak", f"keccak {quoted}", expected(f"keccak {quoted}"))

def _rand_u256_literal(rng: random.Random) -> str:
    # Hex is the primary input form: decimals run ~1 char/token, so anything
    # over ~40 digits never fits the 80-token window. Full-width values go hex;
    # small values stay mixed so both decimal and hex parse paths get fuzzed.
    v = rng.randint(0, MASK256)
    if rng.random() < 0.2:
        v = rng.choice([0, 1, 2, 255, 256, 0xFF, 0x100, (1 << 128), MASK256, MASK256 - 1])
    if v > 10**40 or rng.random() >= 0.55:
        h = hex(v)
        # randomly uppercase
        if rng.random() < 0.3:
            h = h.upper().replace("X", "x")
        return h
    return str(v)

def u256_cases(n: int, rng: random.Random):
    int_ops = ["+", "-", "*", "/", "%", "&", "|", "^", "<<", ">>"]
    for _ in range(n):
        k = rng.choice([1, 1, 2, 2, 3, 3])
        # build RPN-like expression with k operands
        expr = _rand_u256_literal(rng)
        for _ in range(k - 1):
            op = rng.choice(int_ops)
            # avoid divide-by-zero in ~half the div/mod cases; keep some to test error path
            if op in ("/", "%") and rng.random() < 0.5:
                rhs = _rand_u256_literal(rng)
                # force nonzero divisor when we want a non-error case
                if rng.random() < 0.5:
                    rhs = str(rng.randint(1, 1000)) if rng.random() < 0.5 else hex(rng.randint(1, 1000))
                else:
                    # allow zero to test error message
                    pass
                expr += f" {op} {rhs}"
            elif op in ("<<", ">>"):
                # shift amount usually small; sometimes big to test >=256 path
                if rng.random() < 0.8:
                    rhs = str(rng.randint(0, 65))
                else:
                    rhs = str(rng.choice([0, 1, 255, 256, 300, 1000])) if rng.random() < 0.5 else hex(rng.choice([0, 1, 255, 256, 300]))
                expr += f" {op} {rhs}"
            else:
                expr += f" {op} {_rand_u256_literal(rng)}"
        # parenthesize sometimes
        if k == 3 and rng.random() < 0.35:
            # wrap first two in parens
            parts = expr.split()
            # parts like [a, op, b, op2, c]
            if len(parts) == 5:
                expr = f"({parts[0]} {parts[1]} {parts[2]}) {parts[3]} {parts[4]}"
            elif len(parts) == 5 and rng.random() < 0.5:
                expr = f"{parts[0]} {parts[1]} ({parts[2]} {parts[3]} {parts[4]})"
        # occasional ** (keep exponent tiny to avoid insane runtimes)
        if rng.random() < 0.06:
            base = str(rng.randint(2, 10**6)) if rng.random() < 0.5 else hex(rng.randint(2, 1000))
            exp = str(rng.randint(0, 20))
            expr = f"{base} ** {exp}"
        # occasional suffixes in either order: '!' (overflow check), 'hex' (0x out)
        sufs = []
        if rng.random() < 0.08:
            sufs.append("!")
        if rng.random() < 0.10:
            sufs.append("hex")
        rng.shuffle(sufs)
        for sfx in sufs:
            expr += " " + sfx
        oracle = expected(f"u256 {expr}")
        yield ("u256", f"u256 {expr}", oracle)


# ---------------------------------------------------------------------------
# ABI / create generators (values valid unless stated; error fuzz is
# single-fault so oracle/firmware error precedence agrees)

_ABI_BITS = [8, 32, 64, 128, 256]
_ABI_BYTESN = [1, 4, 20, 32]
_TABLE_SIGS: list[str] | None = None


def _table_sigs() -> list[str]:
    global _TABLE_SIGS
    if _TABLE_SIGS is None:
        p = Path(__file__).parent / "env" / "eth" / "selectors.txt"
        _TABLE_SIGS = [l.strip() for l in p.read_text().splitlines()
                       if l.strip() and not l.strip().startswith("#")]
    return _TABLE_SIGS


def _spaced_sig(sig: str) -> str:
    name, rest = sig.split("(", 1)
    return f"{name} ({rest[:-1].replace(',', ', ')})" if rest[:-1] else f"{name} ()"


def selector_cases(n: int, rng: random.Random):
    table = _table_sigs()
    for _ in range(n):
        r = rng.random()
        if r < 0.40:
            sig = rng.choice(table)
        elif r < 0.50:
            sig = _spaced_sig(rng.choice(table))
        else:
            name = "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rng.randint(3, 10)))
            k = rng.randint(0, 3)
            ts = [rng.choice(["uint256", "address", "bool", "bytes32", "string", "uint8"]) for _ in range(k)]
            sig = f"{name}({','.join(ts)})"
        yield ("selector", f"selector {sig}", expected(f"selector {sig}"))


def _rand_abi_type(rng: random.Random, allow_dyn=True, allow_arr=True) -> str:
    r = rng.random()
    if r < 0.55 or not (allow_dyn or allow_arr):
        b = rng.choice(_ABI_BITS)
        return rng.choice([f"uint{b}", f"int{b}", "address", "bool", f"bytes{rng.choice(_ABI_BYTESN)}"])
    if r < 0.75 and allow_arr:
        b = rng.choice(_ABI_BITS)
        e = rng.choice([f"uint{b}", f"int{b}", "address", "bool", f"bytes{rng.choice(_ABI_BYTESN)}"])
        return f"{e}[{rng.randint(1, 3)}]"
    if allow_dyn:
        return rng.choice(["bytes", "string"])
    return "uint256"


def _rand_scalar_arg(rng: random.Random, tp: str) -> str:
    """A valid cmd-line arg for a scalar type."""
    if tp.startswith("uint"):
        bits = int(tp[4:])
        v = rng.randint(0, (1 << bits) - 1)
        if rng.random() < 0.15:
            v = rng.choice([0, 1, (1 << bits) - 1, (1 << bits) - 2])
        return hex(v) if rng.random() < 0.3 else str(v)
    if tp.startswith("int"):
        bits = int(tp[3:])
        lo, hi = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
        v = rng.randint(lo, hi)
        if rng.random() < 0.15:
            v = rng.choice([0, 1, -1, lo, hi, lo + 1, hi - 1])
        if v >= 0 and rng.random() < 0.3:
            return hex(v)
        return str(v)
    if tp == "address":
        h = _rand_bytes(rng, 20).hex()
        r = rng.random()
        if r < 0.2:
            from eth_utils import to_checksum_address
            return to_checksum_address("0x" + h)
        if r < 0.3:
            return "0X" + h.upper()
        return "0x" + h
    if tp == "bool":
        return rng.choice(["true", "false", "1", "0"])
    if tp == "bytes":
        return "0x" + _rand_bytes(rng, rng.randint(0, 48)).hex()
    if tp.startswith("bytes"):
        return "0x" + _rand_bytes(rng, int(tp[5:])).hex()
    if tp == "string":
        s = "".join(rng.choice("abcdefghijklmnopqrstuvwxyz0123456789 ") for _ in range(rng.randint(0, 40)))
        return s.strip()
    raise AssertionError(tp)


def _fmt_arg(a: str) -> str:
    if any(c.isspace() or c == "," for c in a):
        return '"' + a + '"'
    return a if a != "" else '""'


def _corrupt_encode_arg(rng: random.Random, tp: str, arg: str) -> str:
    """Single-fault corruption with a deterministic firmware error."""
    if tp.startswith("uint"):
        bits = int(tp[4:])
        return rng.choice([str(1 << bits), str((1 << bits) + 5), "-1", "0x" + "ff" * 34, "12ab"])
    if tp.startswith("int"):
        bits = int(tp[3:])
        return rng.choice([str(1 << (bits - 1)), str(-(1 << (bits - 1)) - 1), "0x" + "ff" * 33])
    if tp == "address":
        return rng.choice(["0x1234", "0x" + "ab" * 21, "0x" + "ZZ" + "ab" * 19])
    if tp == "bool":
        return rng.choice(["maybe", "2", "tru", "True", "FALSE"])
    if tp.startswith("bytes") and tp not in ("bytes", "string"):
        n = int(tp[5:])
        # short values pad (valid), so faults are too-long, odd, or non-hex
        return rng.choice(["0x" + "ab" * (n + 1), "0x" + "ab" * n + "c", "0x" + "ZZ" + "ab" * (n - 1)])
    if tp == "bytes":
        return rng.choice(["0x123", "0xZZ", "0xab cd"])
    if tp == "string":
        return arg  # strings never fail; fall through to valid
    return arg


def encode_cases(n: int, rng: random.Random):
    for _ in range(n):
        nt = rng.randint(1, 4)
        types = [_rand_abi_type(rng) for _ in range(nt)]
        name = "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rng.randint(3, 8)))
        sig = f"{name}({','.join(types)})"
        args = []
        for tp in types:
            if tp.endswith("]") and not tp.endswith("[]"):
                elem = tp[:tp.rfind("[")]
                k = int(tp[tp.rfind("[") + 1:-1])
                elems = [_rand_scalar_arg(rng, elem) for _ in range(k)]
                if rng.random() < 0.12:  # wrong count (single fault)
                    elems = elems[:-1] if len(elems) > 1 and rng.random() < 0.5 else elems + [_rand_scalar_arg(rng, elem)]
                elif rng.random() < 0.12 and elems:  # one bad element
                    i = rng.randrange(len(elems))
                    elems[i] = _corrupt_encode_arg(rng, elem, elems[i])
                sep = ", " if rng.random() < 0.2 else ","
                args.append(_fmt_arg("[" + sep.join(elems) + "]"))
            else:
                a = _rand_scalar_arg(rng, tp)
                if rng.random() < 0.15:
                    a = _corrupt_encode_arg(rng, tp, a)
                args.append(_fmt_arg(a))
        cmd = f"encode {sig} " + " ".join(args)
        yield ("encode", cmd, expected(cmd))


def _dirty_slot(rng: random.Random, tp: str, slot: bytearray) -> bool:
    """Corrupt exactly one padding byte. False if the type has no padding."""
    if tp == "address":
        i = rng.randrange(0, 12)
    elif tp == "bool":
        i = rng.randrange(0, 31)
    elif tp.startswith("uint"):
        n = int(tp[4:]) // 8
        if n >= 32:
            return False
        i = rng.randrange(0, 32 - n)
    elif tp.startswith("int"):
        n = int(tp[3:]) // 8
        if n >= 32:
            return False
        i = rng.randrange(0, 32 - n)
    elif tp.startswith("bytes") and tp not in ("bytes", "string"):
        n = int(tp[5:])
        if n >= 32:
            return False
        i = rng.randrange(n, 32)
    else:
        return False
    slot[i] ^= 0x01 if slot[i] != 0x01 else 0x02
    return True


def decode_cases(n: int, rng: random.Random):
    for _ in range(n):
        nt = rng.randint(1, 3)
        types = [_rand_abi_type(rng) for _ in range(nt)]
        values = []
        for tp in types:
            if tp.endswith("]") and not tp.endswith("[]"):
                elem = tp[:tp.rfind("[")]
                k = int(tp[tp.rfind("[") + 1:-1])
                evs = []
                for _ in range(k):
                    a = _rand_scalar_arg(rng, elem)
                    v, verr = _encode_value(elem, a)
                    assert verr is None, (tp, a, verr)
                    evs.append(v)
                values.append(evs)
            else:
                a = _rand_scalar_arg(rng, tp)
                v, verr = _encode_value(tp, a)
                assert verr is None, (tp, a, verr)
                values.append(v)
        data = bytearray(_abi_encode(types, values))
        mode = rng.random()
        if mode < 0.12:
            # single dirty padding byte in a random static slot
            statics = [(i, tp) for i, tp in enumerate(types)
                       if not (tp in ("bytes", "string") or tp.endswith("[]"))]
            rng.shuffle(statics)
            for i, tp in statics:
                if tp.endswith("]"):
                    elem = tp[:tp.rfind("[")]
                    k = int(tp[tp.rfind("[") + 1:-1])
                    base = sum(_head_slots(t) for t in types[:i]) * 32
                    ei = rng.randrange(k)
                    if _dirty_slot(rng, elem, memoryview(data)[base + ei * 32:base + ei * 32 + 32]):
                        break
                else:
                    base = sum(_head_slots(t) for t in types[:i]) * 32
                    if _dirty_slot(rng, tp, memoryview(data)[base:base + 32]):
                        break
        elif mode < 0.18 and len(data) > 32:
            # truncation: drop a whole slot (too short) or one byte (ragged)
            data = data[:-32] if rng.random() < 0.5 else data[:-1]
        elif mode < 0.22 and any(tp in ("bytes", "string") for tp in types):
            # dynamic offset/length fault (single)
            dyns = [i for i, tp in enumerate(types) if tp in ("bytes", "string")]
            i = rng.choice(dyns)
            base = sum(_head_slots(t) for t in types[:i]) * 32
            if rng.random() < 0.5:
                data[base + 28:base + 32] = (len(data) + 64).to_bytes(4, "big")
            else:
                off = int.from_bytes(data[base + 28:base + 32], "big")
                data[off + 28:off + 32] = (len(data) + 64).to_bytes(4, "big")
        cmd = f"decode {','.join(types)} 0x{bytes(data).hex()}"
        yield ("decode", cmd, expected(cmd))


def _rand_addr(rng: random.Random) -> str:
    return "0x" + _rand_bytes(rng, 20).hex()


def create_cases(n: int, rng: random.Random):
    for _ in range(n):
        addr = _rand_addr(rng)
        r = rng.random()
        if r < 0.6:
            nonce = str(rng.choice([0, 1, 2, 7, 127, 128, 255, 256, 1000, 1 << 32]))
        elif r < 0.7:
            nonce = hex(rng.randint(0, 1 << 40))
        elif r < 0.8:
            nonce = "0" + oct(rng.randint(0, 0o7777))[2:]  # leading zeros, still decimal
        elif r < 0.9:
            nonce = rng.choice([str((1 << 64) - 1), str(1 << 64), "10ZZ", "-1", "+42"])
        else:
            nonce = str(rng.randint(0, 10**6))
        sep = rng.choice([" ", ",", " , "])
        cmd = f"create {addr}{sep}{nonce}"
        yield ("create", cmd, expected(cmd))


def create2_cases(n: int, rng: random.Random):
    for _ in range(n):
        dep = _rand_addr(rng)
        salt = "0x" + _rand_bytes(rng, 32).hex()
        ih = "0x" + _rand_bytes(rng, 32).hex()
        if rng.random() < 0.12:  # single bad hex
            which = rng.randrange(3)
            bad = rng.choice(["0x1234", "0x" + "ab" * 19, "0x" + "ZZ" + "ab" * 19, "deadbeef"])
            if which == 0:
                dep = bad if rng.random() < 0.5 else "0x" + "ab" * 19 + "ZZ"
            elif which == 1:
                salt = bad
            else:
                ih = bad
        sep = rng.choice([" ", ","])
        cmd = f"create2 {sep.join([dep, salt, ih])}"
        yield ("create2", cmd, expected(cmd))


# The bridge's capture budget: 80 rows of one token each, 16 bytes per row.
CAP_TOKENS, CAP_ROW_BYTES = 80, 16


def cases(n: int, seed: int = 0, only: list[str] | None = None, stats: dict | None = None, window: bool = True):
    """n ACCEPTED cases per op, interleaved round-robin.

    Every draw is sized with the Qwen tokenizer; draws over the capture window
    are rejected the way the bridge firmware would (it prints "error (too long)")
    and counted in stats[op] = [over_tokens, over_rowbytes, drawn]. window=False runs them
    too, up to what eth_in holds: a spilled command reaches the firmware without the window.
    """
    rng = random.Random(seed)
    want = only or OPS
    gens = []
    if "keccak" in want:
        gens.append(("keccak", keccak_cases))
    if "u256" in want:
        gens.append(("u256", u256_cases))
    if "selector" in want:
        gens.append(("selector", selector_cases))
    if "encode" in want:
        gens.append(("encode", encode_cases))
    if "decode" in want:
        gens.append(("decode", decode_cases))
    if "create" in want:
        gens.append(("create", create_cases))
    if "create2" in want:
        gens.append(("create2", create2_cases))
    per_op: dict[str, list] = {}
    for op, gen in gens:
        acc: list[tuple[str, str, str]] = []
        drawn = over_tok = over_row = 0
        # Draw cap: some ops (full create2, wide decodes) rarely fit the
        # 80-token window; report short with the reject counts, never hang.
        max_drawn = max(500, 20 * n)
        while len(acc) < n and drawn < max_drawn:
            for cand_op, cmd, oracle in gen(n, rng):
                drawn += 1
                ntok, wide = captured_size(cmd)
                if ntok > CAP_TOKENS:
                    over_tok += 1
                    if window or len(cmd.encode()) >= ETH_IN_SIZE:
                        continue
                elif wide > CAP_ROW_BYTES:
                    over_row += 1
                    if window or len(cmd.encode()) >= ETH_IN_SIZE:
                        continue
                acc.append((cand_op, cmd, oracle))
                if len(acc) >= n:
                    break
        per_op[op] = acc
        if stats is not None:
            stats[op] = [over_tok, over_row, drawn]
    # interleave
    for i in range(n):
        for op, _ in gens:
            if i < len(per_op[op]):
                yield per_op[op][i]


def boundary_cases():
    """Every intN and uintN at min, max, max+1, min-1, in decimal and in hex; every bytesN at N and N+1 bytes.

    Hex ints are 256-bit two's complement, so a negative is sign-extended: int8's min is 0xff..80 and
    min-1 is 0xff..7f. int256 fills all 64 digits, so its max+1 is its min and min-1 its max; its
    out-of-range forms are 65 digits.

    Out of range must be an error, asserted and not compared. In range is compared with eth_abi
    directly, not with expected(). Never sized against the window: spill reaches past it."""
    from eth_abi import encode as abi_encode

    def ok(tp, v):
        return "0x" + _keccak_hex(f"f({tp})".encode())[:8] + abi_encode([tp], [v]).hex() + "\n"

    for bits in range(8, 257, 8):
        for tp, lo, hi in ((f"uint{bits}", 0, 2**bits - 1), (f"int{bits}", -(2 ** (bits - 1)), 2 ** (bits - 1) - 1)):
            yield f"encode f({tp}) {lo}", ok(tp, lo)
            yield f"encode f({tp}) {hi}", ok(tp, hi)
            yield f"encode f({tp}) {hi + 1}", REJECT
            yield f"encode f({tp}) {lo - 1}", REJECT
    for bits in range(8, 257, 8):
        tp, hi = f"uint{bits}", 2**bits - 1
        yield f"encode f({tp}) 0x0", ok(tp, 0)
        yield f"encode f({tp}) {hi:#x}", ok(tp, hi)
        yield f"encode f({tp}) {hi + 1:#x}", REJECT
        yield f"encode f({tp}) -0x1", REJECT
        tp, hi, lo = f"int{bits}", 2 ** (bits - 1) - 1, -(2 ** (bits - 1))
        yield f"encode f({tp}) {hi:#x}", ok(tp, hi)
        yield f"encode f({tp}) {2**256 + lo:#x}", ok(tp, lo)
        if bits == 256:
            yield f"encode f({tp}) {2**256:#x}", REJECT
            yield f"encode f({tp}) {2**256 + 2**255:#x}", REJECT
        else:
            yield f"encode f({tp}) {hi + 1:#x}", REJECT
            yield f"encode f({tp}) {2**256 + lo - 1:#x}", REJECT
    for n in range(1, 33):
        tp = f"bytes{n}"
        yield f"encode f({tp}) 0x{'ab' * n}", ok(tp, b"\xab" * n)
        yield f"encode f({tp}) 0x{'ab' * (n + 1)}", REJECT


def unary_cases():
    """Unary -(E) and +(E) after every binary operator, nested, and at the u256 boundaries, in decimal and hex.

    Expected values are Python ints with the grouping written out here, not expected(): the oracle's tokenizer
    is a copy of the firmware's and shared its bare `0-E` desugar, which computed `5 - -(3)` as (5-0)-3."""
    M = 1 << 256

    def ap(op, a, b):
        if op in ("/", "%") and b == 0:
            return None
        return {"+": lambda: a + b, "-": lambda: a - b, "*": lambda: a * b, "/": lambda: a // b, "%": lambda: a % b,
                "**": lambda: pow(a, b, M), "&": lambda: a & b, "|": lambda: a | b, "^": lambda: a ^ b,
                "<<": lambda: a << b if b < 256 else 0, ">>": lambda: a >> b if b < 256 else 0}[op]() % M

    def show(v):
        return "error (div by zero)\n" if v is None else f"{v}\n"

    ops = ["+", "-", "*", "/", "%", "**", "&", "|", "^", "<<", ">>"]
    vals = [0, 1, 2, 3, 255, 2**128, 2**255, M - 2, M - 1]
    forms = [str, hex]
    for op in ops:
        for x in vals:
            for y in vals:
                for fa, fb in ((str, str), (hex, hex), (str, hex)):
                    yield f"u256 {fa(x)} {op} -({fb(y)})", show(ap(op, x, -y % M))
                    yield f"u256 {fa(x)} {op} +({fb(y)})", show(ap(op, x, y))
    # A following operator: -(E) is an atom, so it groups by the outer operators' precedence alone.
    prec = _U256_PREC
    for op in ops:
        for op2 in ("+", "*", "|"):
            for x, y, z in ((5, 3, 2), (2**255, 1, M - 1), (M - 1, 2, 3), (7, 0, 1)):
                ny = -y % M
                if prec[op] >= prec[op2]:
                    inner = ap(op, x, ny)
                    want = None if inner is None else ap(op2, inner, z)
                else:
                    inner = ap(op2, ny, z)
                    want = ap(op, x, inner)
                for f in forms:
                    yield f"u256 {f(x)} {op} -({f(y)}) {op2} {f(z)}", show(want)
    for f in forms:
        for x, y, z in ((4, 1, 2), (0, M - 1, 1), (2**255, 2**255, 3)):
            yield f"u256 -(-({f(x)}))", show(x)
            yield f"u256 -({f(x)})", show(-x % M)
            yield f"u256 -(-(-({f(x)})))", show(-x % M)
            yield f"u256 {f(x)} - -({f(y)} - -({f(z)}))", show((x - (-(y - (-z % M)) % M)) % M)
            yield f"u256 -({f(x)} * -({f(y)}))", show(-(x * (-y % M)) % M)
            yield f"u256 ({f(x)} - -({f(y)})) * -({f(z)})", show(((x + y) % M) * (-z % M) % M)
            yield f"u256 {f(x)} ** -(-({f(z)}))", show(pow(x, z, M))


def wrap_cases():
    """decode of bytes and string with offsets and lengths near 2^32, where 32-bit `pos + l` wraps (the Solana lane's
    finding). Out of range must be an error, asserted and not compared. The in-range neighbours are compared with
    eth_abi directly, not with expected(). Data: head slot (offset), length slot, one 32-byte payload slot."""
    from eth_abi import decode as abi_decode

    def slot(n):
        return f"{n:064x}"
    payload = "41" * 32
    for ty in ("bytes", "string"):
        for off in (0xffffffff, 0xfffffffe, 0xffffffe1, 0xffffffe0, 0xffffffdf, 0xfffffff0, 2**32 - 64, 0xffffffc0, 97, 65, 96):
            yield f"decode {ty} 0x{slot(off)}{slot(3)}{payload}", REJECT
        for blen in (0xffffffff, 0xfffffffe, 0xffffffe0, 0xffffffdf, 2**32 - 33, 2**32 - 64, 33, 64):
            yield f"decode {ty} 0x{slot(32)}{slot(blen)}{payload}", REJECT
        for blen in (0, 3, 31, 32):
            data = bytes.fromhex(slot(32) + slot(blen) + "41" * blen + "00" * (32 - blen))
            val = abi_decode([ty], data)[0]
            yield f"decode {ty} 0x{data.hex()}", ("0x" + val.hex() if ty == "bytes" else val) + "\n"


# ---------------------------------------------------------------------------
# Runner

def run_one(cmd: str):
    m = machine(cmd)
    toks = m.run(3 * 10**6)
    assert m.halted and m.fault is None, (cmd, m.fault, m.out[:200])
    out = m.out.decode()
    st = ref.trace_stats(toks)
    return out, st, m.out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--only", type=str, default="keccak,u256", help="comma-separated ops from keccak,u256,selector,encode,decode,create,create2,run")
    ap.add_argument("--expr", type=str, default=None, help="single cmd, e.g. 'keccak 0x6161' or 'u256 2**255 - 1'")
    ap.add_argument("--cases-per-op", type=int, default=None)
    ap.add_argument("--no-window", action="store_true", help="also run cases over the 80-token window, as eth_in allows")
    ap.add_argument("--elf", type=str, default=None, help="firmware image to test instead of tests/data/rv/eth.elf")
    args = ap.parse_args()
    if args.elf:
        global ELF
        ELF = Path(args.elf)

    # --expr: single shot with stats (sized, but run regardless of the window)
    if args.expr:
        cmd = args.expr
        val, st, raw = run_one(cmd)
        oracle = expected(cmd)
        ntok, wide = captured_size(cmd)
        win = "in-window" if ntok <= CAP_TOKENS and wide <= CAP_ROW_BYTES else "OVER-WINDOW"
        print(repr(val), "ok" if agrees(val, oracle) else "MISMATCH", st, f"({ntok} tok, {wide}B max, {win})")
        if not agrees(val, oracle):
            print(" expected:", repr(oracle))
            print(" raw out:", repr(raw.decode()[:2000]))
        return

    only = [x.strip() for x in args.only.split(",") if x.strip()] if args.only else None
    n = args.cases
    if n <= 0:
        ap.error("--cases N required (or --expr)")
    t0 = time.time()
    per: dict[str, list[int]] = {op: [0, 0, 0] for op in (only or OPS)}  # count, instr, tokens
    bad = rejected = 0
    bad_samples: list[tuple[str, str, str]] = []
    # n accepted cases per op (spec "2000 per cmd"); over-window draws are rejected
    # by the tokenizer sizer and counted in stats, never run.
    stats: dict[str, list[int]] = {}
    gen = cases(n, args.seed, only, stats, window=not args.no_window)

    total_expected = 0
    for op, cmd, oracle in gen:
        total_expected += 1
        if total_expected % 250 == 0:
            print(f"  ... {total_expected} cases, {bad} mismatches ({time.time()-t0:.0f} s)", flush=True)
        val, st, raw = run_one(cmd)
        per[op][0] += 1
        per[op][1] += st["instructions"]
        per[op][2] += st["tokens"]
        rejected += oracle == REJECT
        if not agrees(val, oracle):
            bad += 1
            if len(bad_samples) < 10:
                bad_samples.append((cmd, repr(val), repr(oracle)))
            print(f"MISMATCH {op} {cmd!r} got={val!r} want={oracle!r}")
            if bad <= 3:
                print(" raw:", repr(raw.decode()[:2000]))

    if "u256" in (only or OPS):
        nu = bu = 0
        for cmd, want in unary_cases():
            nu += 1
            total_expected += 1
            val, _, _ = run_one(cmd)
            if val != want or expected(cmd) != want:
                bad += 1
                bu += 1
                if len(bad_samples) < 10:
                    bad_samples.append((cmd, repr(val), repr(want)))
                print(f"MISMATCH unary {cmd!r} got={val!r} oracle={expected(cmd)!r} want={want!r}")
        print(f"unary slice: {nu} cases, {bu} mismatches")

    if "decode" in (only or OPS):
        nw = bw = 0
        for cmd, want in wrap_cases():
            nw += 1
            total_expected += 1
            val, _, _ = run_one(cmd)
            if not agrees(val, want):
                bad += 1
                bw += 1
                if len(bad_samples) < 10:
                    bad_samples.append((cmd, repr(val), repr(want)))
                print(f"MISMATCH wrap {cmd[:60]!r} got={val!r} want={want!r}")
        print(f"wraparound slice: {nw} cases, {bw} mismatches")

    if "encode" in (only or OPS):
        nb = bb = 0
        for cmd, want in boundary_cases():
            nb += 1
            total_expected += 1
            val, _, _ = run_one(cmd)
            if not agrees(val, want):
                bad += 1
                bb += 1
                if len(bad_samples) < 10:
                    bad_samples.append((cmd, repr(val), repr(want)))
                print(f"MISMATCH boundary {cmd!r} got={val!r} want={want!r}")
        print(f"boundary slice: {nb} cases, {bb} mismatches")

    elapsed = time.time() - t0
    print(f"{total_expected} cases ({', '.join(f'{op} {per[op][0]}' for op in per)}), {bad} mismatches ({elapsed:.1f} s), {rejected} rejected by design, asserted as errors")
    for op in per:
        cnt, instr, toks = per[op]
        if cnt:
            print(f"  {op:10s} {cnt:5d} cases  {instr/cnt:8.0f} instructions  {toks/cnt:8.0f} tokens  ({toks/max(instr,1):.2f} tok/instr)")
    for op in (only or OPS):
        if op in stats:
            ot, orw, drawn = stats[op]
            print(f"  {op:10s} window: {drawn} drawn, {ot} over-80-tokens, {orw} over-16B-row, {drawn-ot-orw} run")
    # Machine-readable summary line for docs
    if total_expected and bad == 0:
        print("GATE 1 PASS (ref machine == Python on all cases)")
    elif total_expected:
        print(f"GATE 1 FAIL: {bad}/{total_expected} mismatches")
        for cmd, got, want in bad_samples[:5]:
            print(f"  e.g. {cmd!r} got {got} want {want}")
        sys.exit(1)

if __name__ == "__main__":
    main()
