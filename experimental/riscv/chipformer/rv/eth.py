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
                if c in "[(":
                    depth += 1
                elif c in "])" and depth > 0:
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
#   1. whitespace inside the type: bad (eth_abi rejects it too).
#   2. fixed-point token anywhere: well-formed but out of scope.
#   3. recursive parse: bad type / too deep / array too long.
#   4. eth_abi's own registry (oracle only): canonical-only safety net;
#      anything it rejects is bad. A hit here means the firmware parser
#      drifted from the grammar.
# Values are normalized to Python natives (whitespace trim, 0x prefix,
# decimal vs hex) and encoded/decoded by eth_abi itself.

_WS = " \t\n\r\v\f"
_ABI_MAX_DEPTH = 16
_ABI_ARR_CAP = 64
_ABI_POOL = 96
_FIXED_WORD_RE = re.compile(r"u?fixed([1-9][0-9]*x[1-9][0-9]*)?")


class _AbiNode:
    """Mirror of abi.c's tyn_t. kind in uint/int/addr/bool/bytesN/bytes/
    str/arr/tup; num is bits, N, or array length (-1 dynamic)."""
    __slots__ = ("kind", "num", "dyn", "elem", "kids")

    def __init__(self, kind, num=0):
        self.kind = kind
        self.num = num
        self.dyn = kind in ("bytes", "str")
        self.elem = None
        self.kids = ()


def _has_fixed(tp: str) -> bool:
    """Any fixed-point token (mirror has_fixed): a letter-word exactly
    u?fixed with optional canonical MxN."""
    for m in re.finditer(r"[A-Za-z]+", tp):
        if _FIXED_WORD_RE.fullmatch(m.group(0)):
            return True
    return False


def _canon_num(s: str):
    """Canonical digits, saturating at 1e9 (mirror canon_num). None if bad."""
    if not s or s[0] == "0":
        return None
    v = 0
    for c in s:
        if c < "0" or c > "9":
            return None
        v = 10**9 if v > 10**9 else v * 10 + (ord(c) - 48)
    return v


def _classify_scalar(t: str):
    """(kind, num) or None (mirror classify_scalar)."""
    if t == "address":
        return ("addr", 0)
    if t == "bool":
        return ("bool", 0)
    if t == "bytes":
        return ("bytes", 0)
    if t == "string":
        return ("str", 0)
    if t == "uint":
        return ("uint", 256)
    if t == "int":
        return ("int", 256)
    v = _canon_num(t[4:]) if t.startswith("uint") else None
    if v is not None and 8 <= v <= 256 and v % 8 == 0:
        return ("uint", v)
    v = _canon_num(t[3:]) if t.startswith("int") else None
    if v is not None and 8 <= v <= 256 and v % 8 == 0:
        return ("int", v)
    v = _canon_num(t[5:]) if t.startswith("bytes") else None
    if v is not None and 1 <= v <= 32:
        return ("bytesN", v)
    return None


def _parse_abi_type(tp: str):
    """Mirror parse_top_type. Returns (node, None) or (None, reason) with
    reason in bad type / unsupported type / too deep / array too long."""
    if any(c in _WS for c in tp):
        return None, "bad type"
    if _has_fixed(tp):
        return None, "unsupported type"
    nn = [0]
    nk = [0]

    def alloc():
        if nn[0] >= _ABI_POOL:
            return None
        nn[0] += 1
        return nn[0] - 1

    def parse_inner(s: str, depth: int):
        if depth > _ABI_MAX_DEPTH:
            return None, "too deep"
        if len(s) == 0 or len(s) >= 128:
            return None, "bad type"
        if s[0] == "(":
            d = 0
            mc = None
            for i, c in enumerate(s):
                if c == "(":
                    d += 1
                elif c == ")":
                    d -= 1
                    if d < 0:
                        return None, "bad type"
                    if d == 0:
                        mc = i
                        break
            if mc is None:
                return None, "bad type"
            comps = []
            cs = 1
            dd = 0
            for i in range(1, mc):
                c = s[i]
                if c in "([":
                    dd += 1
                elif c in ")]":
                    dd -= 1
                    if dd < 0:
                        return None, "bad type"
                elif c == "," and dd == 0:
                    if len(comps) >= 32:
                        return None, "bad type"
                    comps.append(s[cs:i])
                    cs = i + 1
            if dd != 0:
                return None, "bad type"
            if len(comps) >= 32:
                return None, "bad type"
            comps.append(s[cs:mc])
            if alloc() is None:
                return None, "bad type"
            if nk[0] + len(comps) > _ABI_POOL:
                return None, "bad type"
            nk[0] += len(comps)  # reserve (mirror)
            node = _AbiNode("tup")
            kids = []
            for c in comps:
                if len(c) <= 0:
                    return None, "bad type"
                cn, r = parse_inner(c, depth + 1)
                if r:
                    return None, r
                kids.append(cn)
            node.kids = tuple(kids)
            node.dyn = any(k.dyn for k in kids)
            base, rest = node, mc + 1
        else:
            be = 0
            while be < len(s) and s[be] not in "[]()":
                be += 1
            if be == 0 or be >= 128:
                return None, "bad type"
            sc = _classify_scalar(s[:be])
            if sc is None:
                return None, "bad type"
            if alloc() is None:
                return None, "bad type"
            base = _AbiNode(*sc)
            rest = be
        lvl = depth
        p = rest
        while p < len(s):
            if s[p] != "[":
                return None, "bad type"
            q = s.find("]", p + 1)
            if q < 0:
                return None, "bad type"
            dim = s[p + 1:q]
            if dim == "":
                alen = -1
            else:
                if len(dim) >= 32:
                    return None, "bad type"
                nv = _canon_num(dim)
                if nv is None:
                    return None, "bad type"
                if nv > _ABI_ARR_CAP:
                    return None, "array too long"
                alen = nv
            lvl += 1
            if lvl > _ABI_MAX_DEPTH:
                return None, "too deep"
            if alloc() is None:
                return None, "bad type"
            node = _AbiNode("arr", alen)
            node.elem = base
            node.dyn = True if alen < 0 else base.dyn
            base = node
            p = q + 1
        return base, None

    return parse_inner(tp, 0)


def _gate_type(tp: str, for_encode: bool):
    """Judge one type. Returns None if usable, else a reason string."""
    _, reason = _parse_abi_type(tp)
    if reason:
        return reason
    try:
        _registry.get_encoder(tp) if for_encode else _registry.get_decoder(tp)
    except Exception:
        return "bad type"
    return None


def _head_slots_node(nd: _AbiNode) -> int:
    if nd.dyn:
        return 1
    if nd.kind == "tup":
        return sum(_head_slots_node(k) for k in nd.kids)
    if nd.kind == "arr":
        return nd.num * _head_slots_node(nd.elem)
    return 1


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


def _node_type_str(nd: _AbiNode) -> str:
    """Canonical type string for a scalar node (feeds _encode_value)."""
    if nd.kind == "uint":
        return f"uint{nd.num}"
    if nd.kind == "int":
        return f"int{nd.num}"
    if nd.kind == "addr":
        return "address"
    if nd.kind == "bool":
        return "bool"
    if nd.kind == "bytesN":
        return f"bytes{nd.num}"
    if nd.kind == "bytes":
        return "bytes"
    return "string"


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
        if len(t) != 2 * n:
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
        # eth_abi right-pads SHORT bytesN (firmware is stricter by design),
        # so only the too-long case is probed.
        t = arg.strip(_WS)
        if t[:2] in ("0x", "0X"):
            t = t[2:]
        try:
            v = bytes.fromhex(t)
        except ValueError:
            return _SKIP
        n = _scalar_bits(tp)
        if n is not None and len(v) <= n:
            return _SKIP
        return v
    return _SKIP


def _tsp_spans(s: str):
    """Mirror tsp_next: split on top-level commas (bracket/paren depth,
    quote+escape aware). Yields raw spans; never fails."""
    n = len(s)
    pos = 0
    while pos < n:
        start = pos
        depth = 0
        quote = ""
        i = pos
        done = False
        while i < n:
            c = s[i]
            if quote:
                if c == "\\":
                    i += 2
                    continue
                if c == quote:
                    quote = ""
                i += 1
                continue
            if c == "\\":
                i += 2
                continue
            if c in "\"'":
                quote = c
                i += 1
                continue
            if c in "[(":
                depth += 1
                i += 1
                continue
            if c in "])":
                if depth > 0:
                    depth -= 1
                i += 1
                continue
            if c == "," and depth == 0:
                yield s[start:i]
                pos = i + 1
                done = True
                break
            i += 1
        if not done:
            yield s[start:n]
            return


def _count_spans_interior(s: str) -> int:
    """Mirror count_spans: pre-trimmed interior; empty is zero spans."""
    if s == "":
        return 0
    n = 0
    for _ in _tsp_spans(s):
        n += 1
        if n > _ABI_ARR_CAP + 2:
            return _ABI_ARR_CAP + 2
    return n


def _strip1(s: str) -> str:
    if len(s) >= 2 and s[0] in "\"'" and s[-1] == s[0]:
        return s[1:-1]
    return s


def _unescape(s: str) -> str:
    """Mirror unescape_span. Total: never fails."""
    out: list[str] = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n:
            d = s[i + 1]
            if d == "n":
                out.append("\n")
                i += 2
            elif d == "r":
                out.append("\r")
                i += 2
            elif d == "t":
                out.append("\t")
                i += 2
            elif d == "x" and i + 3 < n and all(k in _HEXCHARS for k in s[i + 2:i + 4]):
                out.append(chr(int(s[i + 2:i + 4], 16)))
                i += 4
            elif d in "\"'\\":
                out.append(d)
                i += 2
            else:
                out.append("\\")
                i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


class _Reason(Exception):
    """A firmware error with optional eth_abi xcheck context."""

    def __init__(self, reason, x_tp=None, x_arg=_SKIP):
        super().__init__(reason)
        self.reason = reason
        self.x_tp = x_tp
        self.x_arg = x_arg


_NUL_LEXICAL = {"uint": "bad uint", "int": "bad int", "addr": "bad address",
                "bool": "bad bool", "bytesN": "bad hex"}


def _proc_scalar_span(span: str, parent_is_tup: bool):
    """Mirror proc_scalar_span: trim, strip one quote layer, length cap.
    Returns the stripped (still escaped) text."""
    t = span.strip(_WS)
    t = _strip1(t)
    if len(t) >= 512:
        raise _Reason("tuple elem too long" if parent_is_tup else "array elem too long")
    return t


def _norm_scalar_node(nd: _AbiNode, span: str, parent_is_tup: bool):
    """Normalize one nested scalar span to a Python native. Raises _Reason."""
    t = _proc_scalar_span(span, parent_is_tup)
    v = _unescape(t)
    if "\x00" in v and nd.kind in _NUL_LEXICAL:
        raise _Reason(_NUL_LEXICAL[nd.kind])
    tp = _node_type_str(nd)
    val, verr = _encode_value(tp, v)
    if verr:
        raise _Reason(verr, tp, _xvalue(tp, v, verr))
    return val


def _norm_composite(nd: _AbiNode, text: str, strip: bool):
    """Normalize one array/tuple value to nested natives (lists for arrays,
    tuples for tuples). strip: remove one quote layer (nested only).
    Raises _Reason."""
    is_arr = nd.kind == "arr"
    t = text.strip(_WS)
    if strip:
        t = _strip1(t)
    if len(t) < 2 or t[0] != ("[" if is_arr else "(") or t[-1] != ("]" if is_arr else ")"):
        raise _Reason("bad array" if is_arr else "bad tuple")
    inner = t[1:-1].strip(_WS)
    nparts = _count_spans_interior(inner)
    if is_arr:
        if nparts > _ABI_ARR_CAP:
            raise _Reason("array too long")
        if nd.num >= 0 and nparts != nd.num:
            _xcheck_encode(_node_sub_str(nd), [0] * nparts)
            raise _Reason("array len")
    else:
        if nparts != len(nd.kids):
            _xcheck_encode(_node_sub_str(nd), (0,) * nparts)
            raise _Reason("tuple len")
    spans = list(_tsp_spans(inner)) if inner else []
    out = []
    for k, sp in enumerate(spans):
        kid = nd.kids[k] if not is_arr else nd.elem
        if kid.kind in ("arr", "tup"):
            out.append(_norm_composite(kid, sp, True))
        else:
            out.append(_norm_scalar_node(kid, sp, is_arr is False))
    return out if is_arr else tuple(out)


def _node_sub_str(nd: _AbiNode) -> str:
    """Canonical type string for any node (feeds xchecks)."""
    if nd.kind == "arr":
        sub = _node_sub_str(nd.elem)
        return f"{sub}[]" if nd.num < 0 else f"{sub}[{nd.num}]"
    if nd.kind == "tup":
        return "(" + ",".join(_node_sub_str(k) for k in nd.kids) + ")"
    return _node_type_str(nd)


def expected_encode(arg: str) -> str:
    s = arg[:2047].lstrip(_WS)
    lp = s.find("(")
    rp = -1
    if lp >= 0:  # depth-matched close (mirror handle_encode)
        dd = 0
        for i in range(lp, len(s)):
            if s[i] == "(":
                dd += 1
            elif s[i] == ")":
                dd -= 1
                if dd == 0:
                    rp = i
                    break
    if lp < 0 or rp < 0:
        return "error (bad encode)\n"
    sig, rest = s[:rp + 1], s[rp + 1:].lstrip(_WS)
    if len(sig) > 511:
        return "error (bad sig)\n"
    name, types = _split_sig(sig)
    if name is None:
        # handle_encode discards abi_parse_sig's reason and prints bad sig.
        return "error (bad sig)\n"
    args, err = _split_encode_args(rest, len(types))
    if err == "arg too long":
        return "error (arg too long)\n"
    if err == "arg count" or args is None:
        return "error (arg count)\n"
    # Types first (in order), then values: statics in order, dynamic values
    # deferred to the tail pass, head capacity before any value.
    nodes = []
    for tp in types:
        nd, reason = _parse_abi_type(tp)
        if reason:
            return f"error ({reason})\n"
        try:
            _registry.get_encoder(tp)
        except Exception:
            return "error (bad type)\n"
        nodes.append(nd)
    head = sum(_head_slots_node(nd) for nd in nodes)
    head_end = 4 + head * 32
    if head_end > 4096:
        return "error (out too small)\n"
    values: list = [None] * len(types)
    for i, (nd, tp, av) in enumerate(zip(nodes, types, args)):
        if nd.dyn:
            continue
        try:
            if nd.kind in ("arr", "tup"):
                values[i] = _norm_composite(nd, av, False)
            else:
                v, verr = _encode_value(tp, av)
                if verr:
                    raise _Reason(verr, tp, _xvalue(tp, av, verr))
                values[i] = v
        except _Reason as r:
            if r.x_tp is not None:
                _xcheck_encode(r.x_tp, r.x_arg)
            return f"error ({r.reason})\n"
    tail_off = head_end
    for i, (nd, tp, av) in enumerate(zip(nodes, types, args)):
        if not nd.dyn:
            continue
        try:
            if nd.kind in ("arr", "tup"):
                values[i] = _norm_composite(nd, av, False)
            else:
                v, verr = _encode_value(tp, av)
                if verr:
                    raise _Reason(verr, tp, _xvalue(tp, av, verr))
                values[i] = v
        except _Reason as r:
            if r.x_tp is not None:
                _xcheck_encode(r.x_tp, r.x_arg)
            return f"error ({r.reason})\n"
        tail_off += len(_abi_encode([tp], [values[i]]))
        if tail_off > 4096:
            return "error (out too small)\n"
    try:
        data = _abi_encode(types, values)
    except Exception as e:  # accepted by firmware but not eth_abi: oracle bug
        raise AssertionError(f"eth_abi rejected firmware-accepted encode: {types} {e}")
    return "0x" + _keccak_hex(re.sub(r"[ \t\n\r\v\f]+", "", sig).encode())[:8] + data.hex() + "\n"


def _head_slots(tp: str) -> int:
    """Head slots for a gated type string (recursive)."""
    nd, r = _parse_abi_type(tp)
    assert r is None, (tp, r)
    return _head_slots_node(nd)


def _split_decode_types(raw: str):
    """Mirror abi_decode's splitter: depth-0 commas, per-token trim.
    Returns (types, None) or (None, reason)."""
    if raw.strip(_WS) == "":
        return [], None
    types: list[str] = []
    depth = 0
    seg = 0
    n = len(raw)
    for i in range(n + 1):
        ch = raw[i] if i < n else ""
        if ch in "([":
            depth += 1
        elif ch in ")]":
            if depth > 0:
                depth -= 1
        if (ch == "," and depth == 0) or i == n:
            t = raw[seg:i].strip(_WS)
            if t == "":
                return None, "bad type"
            if len(types) >= 32:
                return None, "too many types"
            if len(t) >= 64:
                return None, "bad type"
            types.append(t)
            seg = i + 1
    return types, None


def _u32_full(slot: bytes):
    """Full-width u32 slot read (mirror read_u32_full). None if high set."""
    if any(slot[:28]):
        return None
    return int.from_bytes(slot[28:32], "big")


def _pad_ok(kind: str, num: int, slot: bytes) -> bool:
    """Mirror validate_slot."""
    if kind == "addr":
        return not any(slot[:12])
    if kind == "bool":
        return not any(slot[:31]) and slot[31] <= 1
    if kind == "uint":
        return not any(slot[:32 - num // 8])
    if kind == "int":
        fill = 0xFF if slot[32 - num // 8] & 0x80 else 0x00
        return all(b == fill for b in slot[:32 - num // 8])
    if kind == "bytesN":
        return not any(slot[num:])
    return False


def _decode_prepass(nodes, data: bytes):
    """Mirror dec_validate: structural + pads + utf8, depth-first in order.
    Returns a reason string or None."""
    dlen = len(data)

    def read_offset(frame_base: int, pos: int):
        if pos > dlen or dlen - pos < 32:
            return None, "bad offset"
        off = _u32_full(data[pos:pos + 32])
        if off is None:
            return None, "bad offset"
        pos += 32
        if frame_base > dlen or off > dlen - frame_base:
            return None, "bad offset"
        target = frame_base + off
        if dlen - target < 32:
            return None, "bad offset"
        return (target, pos), None

    def walk(nd: _AbiNode, frame_base: int, pos: int):
        if nd.kind not in ("arr", "tup", "bytes", "str"):
            if pos > dlen or dlen - pos < 32:
                return pos, "bad data"
            if not _pad_ok(nd.kind, nd.num, data[pos:pos + 32]):
                return pos, "bad padding"
            return pos + 32, None
        if nd.kind in ("bytes", "str"):
            r, reason = read_offset(frame_base, pos)
            if reason:
                return pos, reason
            target, pos = r
            blen = _u32_full(data[target:target + 32])
            if blen is None:
                return pos, "bad data"
            if blen > dlen - target - 32:
                return pos, "bad data"
            padded = (blen + 31) & ~31
            if padded > dlen - target - 32:
                return pos, "bad data"
            if any(data[target + 32 + blen:target + 32 + padded]):
                return pos, "bad padding"
            if nd.kind == "str":
                try:
                    data[target + 32:target + 32 + blen].decode("utf-8")
                except UnicodeDecodeError:
                    return pos, "bad string"
            return pos, None
        if nd.kind == "arr":
            if not nd.dyn:
                for _ in range(nd.num):
                    pos, reason = walk(nd.elem, frame_base, pos)
                    if reason:
                        return pos, reason
                return pos, None
            r, reason = read_offset(frame_base, pos)
            if reason:
                return pos, reason
            target, pos = r
            cbase = target
            if nd.num < 0:
                count = _u32_full(data[target:target + 32])
                if count is None:
                    return pos, "bad data"
                if count > _ABI_ARR_CAP:
                    return pos, "array too long"
                cbase = target + 32
            else:
                count = nd.num
            ei = nd.elem
            if not ei.dyn:
                esz = _head_slots_node(ei) * 32
                if count > (dlen - cbase) // esz:
                    return pos, "bad data"
                ep = cbase
                for _ in range(count):
                    ep, reason = walk(ei, cbase, ep)
                    if reason:
                        return pos, reason
                return pos, None
            if count > (dlen - cbase) // 32:
                return pos, "bad data"
            ep = cbase
            for _ in range(count):
                ep, reason = walk(ei, cbase, ep)
                if reason:
                    return pos, reason
            return pos, None
        # tup
        if not nd.dyn:
            for k in nd.kids:
                pos, reason = walk(k, frame_base, pos)
                if reason:
                    return pos, reason
            return pos, None
        r, reason = read_offset(frame_base, pos)
        if reason:
            return pos, reason
        target, pos = r
        ep = target
        for k in nd.kids:
            ep, reason = walk(k, target, ep)
            if reason:
                return pos, reason
        return pos, None

    pos = 0
    for nd in nodes:
        pos, reason = walk(nd, 0, pos)
        if reason:
            return reason
    return None


def expected_decode(arg: str) -> str:
    s = arg[:4095].strip(_WS)
    sp = max(s.rfind(c) for c in _WS)
    if sp < 0:
        return "error (bad decode)\n"
    types_raw, hexd = s[:sp], s[sp + 1:].lstrip(_WS)
    types, terr = _split_decode_types(types_raw)
    if terr:
        return f"error ({terr})\n"
    nodes = []
    for tp in types:
        nd, reason = _parse_abi_type(tp)
        if reason:
            return f"error ({reason})\n"
        try:
            _registry.get_decoder(tp)
        except Exception:
            return "error (bad type)\n"
        nodes.append(nd)
    t = hexd.strip(_WS)
    if t[:2] in ("0x", "0X"):
        t = t[2:]
    if len(t) > 4096:  # firmware cap (abi.c hex_to_bytes); unreachable via cmd/file
        return "error (hex too long)\n"
    if len(t) % 2:
        return "error (odd hex)\n"
    if any(c not in _HEXCHARS for c in t):
        return "error (bad hex)\n"
    data = bytes.fromhex(t)
    head = sum(_head_slots_node(nd) for nd in nodes)
    if len(data) % 32:
        _xcheck_decode(types, data)
        return "error (bad data)\n"
    if len(data) < head * 32:
        _xcheck_decode(types, data)
        return "error (data too short)\n"
    reason = _decode_prepass(nodes, data)
    if reason:
        if reason != "array too long":  # firmware cap: eth_abi accepts
            _xcheck_decode(types, data)
        return f"error ({reason})\n"
    try:
        vals = _abi_decode(types, data)
    except _NonEmptyPad:
        return "error (bad padding)\n"
    except UnicodeDecodeError:
        return "error (bad string)\n"
    except Exception as e:  # pre-pass clean but eth_abi rejects: oracle bug
        raise AssertionError(f"eth_abi rejected pre-pass-clean decode: {types} {e}")
    lines = []
    for nd, v in zip(nodes, vals):
        lines.extend(_fmt_node_lines(nd, v, True))
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


def _quote_str(s: str) -> str:
    """Mirror emit_quoted_str, on the decoded str (equivalent: the value is
    valid UTF-8, so ASCII bytes and chars agree and multibyte bytes pass
    through raw on both sides)."""
    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif o < 0x20 or o == 0x7F:
            out.append(f"\\x{o:02x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _fmt_node_val(nd: _AbiNode, v, is_top: bool) -> str:
    """One composite value's text (no trailing newline)."""
    if nd.kind in ("arr", "tup"):
        is_arr = nd.kind == "arr"
        kids = [nd.elem] * len(v) if is_arr else list(nd.kids)
        parts = [_fmt_node_val(k, e, False) for k, e in zip(kids, v)]
        return ("[" if is_arr else "(") + ",".join(parts) + ("]" if is_arr else ")")
    if nd.kind == "bytes":
        return "0x" + bytes(v).hex()
    if nd.kind == "str":
        s = v if isinstance(v, str) else bytes(v).decode("utf-8")
        if is_top and not any(ord(c) < 0x20 or ord(c) == 0x7F for c in s):
            return s
        return _quote_str(s)
    return _fmt_abi_value(_node_type_str(nd), v)


def _fmt_node_lines(nd: _AbiNode, v, is_top: bool) -> list:
    """Top-level value -> lines. A top-level fixed array of static scalars
    keeps the flat one-line-per-element format; everything else is one line."""
    if (nd.kind == "arr" and not nd.dyn and is_top
            and nd.elem.kind not in ("arr", "tup", "bytes", "str")):
        return [_fmt_node_val(nd.elem, e, False) for e in v]
    return [_fmt_node_val(nd, v, is_top)]


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
_ABI_BITS_ALL = list(range(8, 257, 8))
_ABI_BYTESN = [1, 4, 20, 32]
_ABI_BYTESN_ALL = list(range(1, 33))
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


def _rand_scalar_type(rng: random.Random, full_widths=False) -> str:
    bits = rng.choice(_ABI_BITS_ALL if full_widths else _ABI_BITS)
    bn = rng.choice(_ABI_BYTESN_ALL if full_widths else _ABI_BYTESN)
    c = rng.choice(["uint", "int", "uint", "int", "address", "bool",
                    "bytesN", "bytes", "string", "bare"])
    if c == "uint":
        return f"uint{bits}"
    if c == "int":
        return f"int{bits}"
    if c == "bytesN":
        return f"bytes{bn}"
    if c == "bare":
        return rng.choice(["uint", "int"])
    return c


def _rand_abi_type(rng: random.Random, depth: int = 0, full_widths=False) -> str:
    """A valid canonical type, depth <= 3 (tuples, fixed/dynamic arrays)."""
    if depth > 2:
        return _rand_scalar_type(rng, full_widths)
    r = rng.random()
    if r < 0.40:
        return _rand_scalar_type(rng, full_widths)
    if r < 0.56:
        e = (_rand_abi_type(rng, depth + 1, full_widths) if rng.random() < 0.22
             else _rand_scalar_type(rng, full_widths))
        return f"{e}[{rng.randint(1, 3)}]"
    if r < 0.70:
        e = (_rand_abi_type(rng, depth + 1, full_widths) if rng.random() < 0.22
             else _rand_scalar_type(rng, full_widths))
        return f"{e}[]"
    if r < 0.88:
        k = rng.randint(1, 3)
        return "(" + ",".join(_rand_abi_type(rng, depth + 1, full_widths) for _ in range(k)) + ")"
    return rng.choice(["bytes", "string"])


def _rand_scalar_arg(rng: random.Random, tp: str) -> str:
    """A valid cmd-line arg for a scalar type."""
    if tp in ("uint", "int"):
        tp = tp + "256"
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


_NESTED_STR_PLAIN = "abcdefghijklmnopqrstuvwxyz0123456789 "
_NESTED_STR_WILD = "abcdefghijklmnopqrstuvwxyz0123456789 ,[]()\"'\\ \n\t\x00\x7fé"


def _escape_nested(s: str) -> str:
    """Quote + escape a nested string value for the value syntax."""
    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif o < 0x20 or o == 0x7F:
            out.append(f"\\x{o:02x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _balanced_brackets(s: str) -> bool:
    """Shared-counter bracket balance (the top-level splitter's rule)."""
    d = 0
    for c in s:
        if c in "[(":
            d += 1
        elif c in "])":
            d -= 1
            if d < 0:
                return False
    return True


def _rand_nested_scalar_text(rng: random.Random, nd: _AbiNode):
    """Valid nested scalar text: (text, quoted_flag). Strings sometimes
    carry delimiters/quotes/controls (quoted + escaped); with both quote
    kinds present the text stays bracket-balanced for top-level transport."""
    if nd.kind != "str":
        return _rand_scalar_arg(rng, _node_type_str(nd)), False
    if rng.random() < 0.6:
        s = "".join(rng.choice(_NESTED_STR_PLAIN) for _ in range(rng.randint(0, 24)))
        if s == "":
            return '""', True  # bare empty renders as nothing: ("",) -> ()
        return s, False
    for _ in range(10):
        s = "".join(rng.choice(_NESTED_STR_WILD) for _ in range(rng.randint(0, 24)))
        if '"' in s and "'" in s and not _balanced_brackets(s):
            continue
        return _escape_nested(s), True
    s = "".join(rng.choice(_NESTED_STR_PLAIN) for _ in range(rng.randint(0, 24)))
    return s, False


def _fmt_arg(a: str) -> str:
    """Top-level transport quoting: quote when a delimiter or whitespace is
    present, picking the quote kind absent from the text when possible. With
    both quote kinds present the text goes unquoted, so the caller must keep
    brackets balanced (composites only; scalars never carry both kinds)."""
    if a == "":
        return '""'
    if not any(c.isspace() or c == "," for c in a):
        return a
    if '"' in a and "'" not in a:
        return "'" + a + "'"
    if '"' in a and "'" in a:
        return a
    return '"' + a + '"'


def _corrupt_encode_arg(rng: random.Random, tp: str, arg: str) -> str:
    """Single-fault corruption with a deterministic firmware error."""
    if tp in ("uint", "int"):
        tp = tp + "256"
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
        shorts = ["0x" + "ab" * (n - 1)] if n >= 1 else []
        return rng.choice(["0x" + "ab" * (n + 1), "0x" + "ab" * n + "c",
                           "0x" + "ZZ" + "ab" * (n - 1)] + shorts)
    if tp == "bytes":
        return rng.choice(["0x123", "0xZZ", "0xab cd"])
    if tp == "string":
        return arg  # strings never fail; fall through to valid
    return arg


_BAD_TYPES = ["uint7", "uint08", "bytes0", "bytes33", "foobar", "uint256[",
              "address[]x", "(uint8", "uint8)", "tuple", "(uint256,)", "(,uint256)",
              "fixed", "ufixed128x18", "(uint8,fixed)", "fixed[2]", "uint256[65]",
              "uint256[0]", "Uint256", "(uint8, bool)", "bool[2", "((uint8)"]


def _rand_value_tree(rng: random.Random, nd: _AbiNode, nested: bool):
    """A value tree: scalars are TEXT, arrays lists, tuples tuples."""
    if nd.kind == "arr":
        n = nd.num if nd.num >= 0 else rng.randint(0, 3)
        return [_rand_value_tree(rng, nd.elem, True) for _ in range(n)]
    if nd.kind == "tup":
        return tuple(_rand_value_tree(rng, k, True) for k in nd.kids)
    if nested:
        t, _ = _rand_nested_scalar_text(rng, nd)
        return t
    return _rand_scalar_arg(rng, _node_type_str(nd))


def _render_tree(rng: random.Random, nd: _AbiNode, tree) -> str:
    if nd.kind == "arr":
        sep = ", " if rng.random() < 0.2 else ","
        return "[" + sep.join(_render_tree(rng, nd.elem, e) for e in tree) + "]"
    if nd.kind == "tup":
        sep = ", " if rng.random() < 0.2 else ","
        return "(" + sep.join(_render_tree(rng, k, e) for k, e in zip(nd.kids, tree)) + ")"
    return tree


def _tree_has_leaf(nd: _AbiNode, tree) -> bool:
    if nd.kind == "arr":
        return any(_tree_has_leaf(nd.elem, e) for e in tree)
    if nd.kind == "tup":
        return any(_tree_has_leaf(k, e) for k, e in zip(nd.kids, tree))
    return True


def _corrupt_tree_leaf(rng: random.Random, nd: _AbiNode, tree):
    """Replace one random scalar leaf's text with a corrupt value."""
    if nd.kind == "arr":
        live = [i for i, e in enumerate(tree) if _tree_has_leaf(nd.elem, e)]
        i = rng.choice(live)
        new = list(tree)
        new[i] = _corrupt_tree_leaf(rng, nd.elem, tree[i])
        return new
    if nd.kind == "tup":
        live = [i for i, (k, e) in enumerate(zip(nd.kids, tree)) if _tree_has_leaf(k, e)]
        i = rng.choice(live)
        new = list(tree)
        new[i] = _corrupt_tree_leaf(rng, nd.kids[i], tree[i])
        return tuple(new)
    return _corrupt_encode_arg(rng, _node_type_str(nd), tree)


def _composite_paths(nd: _AbiNode, tree, path=()):
    if nd.kind == "arr":
        yield nd, tree, path
        for i, e in enumerate(tree):
            yield from _composite_paths(nd.elem, e, path + (i,))
    elif nd.kind == "tup":
        yield nd, tree, path
        for i, (k, e) in enumerate(zip(nd.kids, tree)):
            yield from _composite_paths(k, e, path + (i,))


def _tree_set(tree, path, sub):
    if not path:
        return sub
    i = path[0]
    new = list(tree)
    new[i] = _tree_set(tree[i], path[1:], sub)
    return new if isinstance(tree, list) else tuple(new)


def _arity_fault_tree(rng: random.Random, nd: _AbiNode, tree):
    """Add or drop one element at a random composite level."""
    paths = list(_composite_paths(nd, tree))
    cnd, ctree, path = rng.choice(paths)
    if cnd.kind == "arr":
        kids = list(ctree)
        if kids and (len(kids) == 1 or rng.random() < 0.5):
            kids = kids[:-1]
        else:
            kids = kids + [_rand_value_tree(rng, cnd.elem, True)]
        return _tree_set(tree, path, kids)
    kids = list(ctree)
    if kids and (len(kids) == 1 or rng.random() < 0.5):
        kids = kids[:-1]
    else:
        kids = kids + [_rand_value_tree(rng, rng.choice(cnd.kids), True)]
    return _tree_set(tree, path, tuple(kids))


def encode_cases(n: int, rng: random.Random):
    for _ in range(n):
        nt = rng.randint(1, 3)
        types = [_rand_abi_type(rng) for _ in range(nt)]
        if rng.random() < 0.06:
            types[rng.randrange(nt)] = rng.choice(_BAD_TYPES)
        name = "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rng.randint(3, 8)))
        sig = f"{name}({','.join(types)})"
        args = []
        for tp in types:
            nd, r = _parse_abi_type(tp)
            if nd is None or _gate_type(tp, True):
                args.append("1")  # value moot; the type error wins
                continue
            tree = _rand_value_tree(rng, nd, False)
            f = rng.random()
            if f < 0.10 and nd.kind in ("arr", "tup"):
                tree = _arity_fault_tree(rng, nd, tree)
            elif f < 0.22 and _tree_has_leaf(nd, tree):
                tree = _corrupt_tree_leaf(rng, nd, tree)
            text = _render_tree(rng, nd, tree)
            if 0.22 <= f < 0.27:
                spots = [i for i, c in enumerate(text) if c in "[](),\"'"]
                if spots:
                    i = rng.choice(spots)
                    if rng.random() < 0.5:
                        text = text[:i] + text[i + 1:]  # drop a structural char
                    else:
                        text = text[:i] + '"' + text[i:]  # stray quote
            args.append(_fmt_arg(text))
        cmd = f"encode {sig} " + " ".join(args)
        yield ("encode", cmd, expected(cmd))


def _dirty_slot(rng: random.Random, kind: str, num: int, slot: bytearray) -> bool:
    """Corrupt exactly one padding byte. False if the type has no padding."""
    if kind == "addr":
        i = rng.randrange(0, 12)
    elif kind == "bool":
        i = rng.randrange(0, 31)
    elif kind == "uint":
        n = num // 8
        if n >= 32:
            return False
        i = rng.randrange(0, 32 - n)
    elif kind == "int":
        n = num // 8
        if n >= 32:
            return False
        i = rng.randrange(0, 32 - n)
    elif kind == "bytesN":
        if num >= 32:
            return False
        i = rng.randrange(num, 32)
    else:
        return False
    slot[i] ^= 0x01 if slot[i] != 0x01 else 0x02
    return True


def _rand_native(rng: random.Random, nd: _AbiNode):
    """A random native value for a node (decode-side: no text involved)."""
    if nd.kind == "arr":
        n = nd.num if nd.num >= 0 else rng.randint(0, 3)
        return [_rand_native(rng, nd.elem) for _ in range(n)]
    if nd.kind == "tup":
        return tuple(_rand_native(rng, k) for k in nd.kids)
    tp = _node_type_str(nd)
    if nd.kind == "str" and rng.random() < 0.35:
        return "".join(rng.choice(_NESTED_STR_WILD) for _ in range(rng.randint(0, 24)))
    a = _rand_scalar_arg(rng, tp)
    v, verr = _encode_value(tp, a)
    assert verr is None, (tp, a, verr)
    return v


def _walk_spots(nd: _AbiNode, data: bytes, base: int):
    """Fault-injection spots on valid data: yields (kind, pos, extra) with
    kind in pad/tailpad/off/len/count/utf8. Mirrors the decode traversal."""
    dlen = len(data)

    def off_at(pos):
        return int.from_bytes(data[pos + 28:pos + 32], "big")

    def walk(n, frame, pos):
        if n.kind not in ("arr", "tup", "bytes", "str"):
            yield ("pad", pos, (n.kind, n.num))
            return pos + 32
        if n.kind in ("bytes", "str"):
            yield ("off", pos, None)
            target = frame + off_at(pos)
            pos += 32
            blen = int.from_bytes(data[target + 28:target + 32], "big")
            yield ("len", target, None)
            padded = (blen + 31) & ~31
            if padded > blen:
                yield ("tailpad", target + 32 + blen, padded - blen)
            if n.kind == "str" and blen > 0:
                yield ("utf8", target + 32, blen)
            return pos
        if n.kind == "arr":
            if not n.dyn:
                for _ in range(n.num):
                    for s in walk(n.elem, frame, pos):
                        yield s
                    pos = _walk_end(n.elem, data, frame, pos)
                return pos
            yield ("off", pos, None)
            target = frame + off_at(pos)
            pos += 32
            if n.num < 0:
                yield ("count", target, None)
                cbase = target + 32
                count = int.from_bytes(data[target + 28:target + 32], "big")
            else:
                cbase = target
                count = n.num
            ep = cbase
            for _ in range(count):
                for s in walk(n.elem, cbase, ep):
                    yield s
                ep = _walk_end(n.elem, data, cbase, ep)
            return pos
        if not n.dyn:
            for k in n.kids:
                for s in walk(k, frame, pos):
                    yield s
                pos = _walk_end(k, data, frame, pos)
            return pos
        yield ("off", pos, None)
        target = frame + off_at(pos)
        pos += 32
        ep = target
        for k in n.kids:
            for s in walk(k, target, ep):
                yield s
            ep = _walk_end(k, data, target, ep)
        return pos

    yield from walk(nd, 0, base)


def _walk_end(nd: _AbiNode, data: bytes, frame: int, pos: int) -> int:
    """Head bytes a node consumes (static width or one offset slot)."""
    if nd.kind in ("arr", "tup") and not nd.dyn:
        return pos + _head_slots_node(nd) * 32
    if nd.kind in ("arr", "tup", "bytes", "str"):
        return pos + 32
    return pos + 32


def _apply_spot_fault(rng: random.Random, data: bytearray, spot) -> None:
    kind, pos, extra = spot
    if kind == "pad":
        sk, num = extra
        view = memoryview(data)[pos:pos + 32]
        if sk == "bool" and rng.random() < 0.2:
            view[31] = 0x02
        elif sk == "int" and num < 256:
            n = num // 8
            fill = 0xFF if data[pos + 32 - n] & 0x80 else 0x00
            i = rng.randrange(0, 32 - n)
            view[i] = 0xFE if fill == 0xFF else 0x01
        elif not _dirty_slot(rng, sk, num, view):
            view[rng.randrange(32)] ^= 0x01
    elif kind == "tailpad":
        data[pos + rng.randrange(extra)] = 0x01
    elif kind == "off":
        if rng.random() < 0.25:
            data[pos + rng.randrange(28)] = 0x01  # high byte
        else:
            data[pos + 28:pos + 32] = (len(data) + 64).to_bytes(4, "big")
    elif kind == "len":
        if rng.random() < 0.25:
            data[pos + rng.randrange(28)] = 0x01
        else:
            data[pos + 28:pos + 32] = (len(data) + 64).to_bytes(4, "big")
    elif kind == "count":
        r = rng.random()
        if r < 0.4:
            data[pos + 28:pos + 32] = rng.randint(65, 70).to_bytes(4, "big")
        elif r < 0.7:
            data[pos + rng.randrange(28)] = 0x01
        else:  # in-cap count the short data cannot hold
            data[pos + 28:pos + 32] = (64).to_bytes(4, "big")
    elif kind == "utf8":
        data[pos + rng.randrange(extra)] = 0xFF


def decode_cases(n: int, rng: random.Random):
    yielded = 0
    attempts = 0
    while yielded < n and attempts < n * 10 + 50:
        attempts += 1
        nt = rng.randint(1, 3)
        types = [_rand_abi_type(rng) for _ in range(nt)]
        if rng.random() < 0.06:
            types[rng.randrange(nt)] = rng.choice(_BAD_TYPES)
        nodes = []
        for tp in types:
            nd, r = _parse_abi_type(tp)
            nodes.append(nd if r is None and _gate_type(tp, False) is None else None)
        if any(nd is None for nd in nodes):
            cmd = f"decode {','.join(types)} 0x{'00' * 32}"
            yield ("decode", cmd, expected(cmd))
            yielded += 1
            continue
        values = [_rand_native(rng, nd) for nd in nodes]
        data = bytearray(_abi_encode(types, values))
        mode = rng.random()
        if mode < 0.16:
            spots = []
            base = 0
            for nd in nodes:
                spots.extend(_walk_spots(nd, bytes(data), base))
                base = _walk_end(nd, bytes(data), 0, base)
            if spots:
                _apply_spot_fault(rng, data, rng.choice(spots))
        elif mode < 0.22 and len(data) > 32:
            # truncation: drop a whole slot (too short) or one byte (ragged)
            data = data[:-32] if rng.random() < 0.5 else data[:-1]
        cmd = f"decode {','.join(types)} 0x{bytes(data).hex()}"
        if len(cmd) + 1 > 2048:  # must fit eth_in unfiltered; redraw
            continue
        yield ("decode", cmd, expected(cmd))
        yielded += 1


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


# ---------------------------------------------------------------------------
# Per-type ABI audit: every scalar width plus composite groups, 200 encode +
# 200 decode oracle cases each. Caps reachable only via @file (out too small,
# hex too long, bytes too long, elem too long) are covered by committed file
# tests instead; inline args cannot reach them (255-char args, eth_in 2048).

_AUDIT_N = 200


def _audit_types():
    out = [("bool", "bool")]
    for b in _ABI_BITS_ALL:
        out.append((f"int{b}", f"int{b}"))
    for b in _ABI_BITS_ALL:
        out.append((f"uint{b}", f"uint{b}"))
    out.append(("address", "address"))
    for n in _ABI_BYTESN_ALL:
        out.append((f"bytes{n}", f"bytes{n}"))
    out += [("bytes", "bytes"), ("string", "string")]
    # Width cap: a fixed uint8[64] blob (2048 bytes) fits neither eth_in
    # (2048) nor file_buf (4096 hex chars + overhead), so its decode side
    # livelocked the generator. 16 slots still exercises multi-slot decode.
    for t in ["uint8[1]", "uint8[16]", "uint256[3]", "int16[4]", "address[2]",
              "bool[5]", "bytes1[3]", "bytes20[2]", "(uint8,bool)[3]", "string[2]"]:
        out.append((f"fixed:{t}", t))
    for t in ["uint8[]", "uint256[]", "bool[]", "address[]", "bytes[]", "string[]",
              "bytes32[]", "(uint8)[]"]:
        out.append((f"dyn:{t}", t))
    for t in ["uint8[2][3]", "uint8[][2]", "uint8[2][]", "bool[2][2]", "uint8[][]"]:
        out.append((f"multi:{t}", t))
    for t in ["(uint8)", "(uint8,bool)", "(uint256,address,bytes32)", "(string)",
              "(bytes,uint8[])", "(uint8,uint8,uint8,uint8,uint8)",
              "(string,bool,bytes)", "(address)"]:
        out.append((f"tup:{t}", t))
    for t in ["((uint8))", "((uint8,bool),string[])", "(uint8[],(bool,string))",
              "((uint8[2])[2],address)", "(uint8,(bool,(string)))", "((string))"]:
        out.append((f"nested:{t}", t))
    return out


def _audit_scalar_edges(tp: str):
    """Pinned (arg, kind) edges for a scalar type. kind: valid/fault."""
    if tp.startswith("uint"):
        bits = int(tp[4:])
        mx = (1 << bits) - 1
        valid = ["0", "1", str(mx), str(mx - 1), hex(mx), "0X" + format(mx, "X"),
                 " 42 ", "007"]
        fault = [str(1 << bits), "-1", "12ab", "0x" + "ff" * 34, ""]
        return [(a, "valid") for a in valid] + [(a, "fault") for a in fault]
    if tp.startswith("int"):
        bits = int(tp[3:])
        lo, hi = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
        valid = ["0", "1", "-1", str(lo), str(hi), str(lo + 1), str(hi - 1),
                 hex(hi), "  -42 "]
        fault = [str(hi + 1), str(lo - 1), "0x" + "ff" * 33, "12ab"]
        return [(a, "valid") for a in valid] + [(a, "fault") for a in fault]
    if tp == "bool":
        valid = ["true", "false", "1", "0", " true "]
        fault = ["maybe", "2", "tru", "True", "FALSE", ""]
        return [(a, "valid") for a in valid] + [(a, "fault") for a in fault]
    if tp == "address":
        valid = ["0x0000000000000000000000000000000000000000",
                 "0xffffffffffffffffffffffffffffffffffffffff",
                 "0xDeaDbeefdEAdbeefdEadbEEFdeadbeEFdEaDbeeF",
                 "0X" + "AB" * 20, "ab" * 20, "  0x" + "11" * 20 + "  "]
        fault = ["0x1234", "0x" + "ab" * 21, "0x" + "ZZ" + "ab" * 19, "0x", ""]
        return [(a, "valid") for a in valid] + [(a, "fault") for a in fault]
    if tp.startswith("bytes") and tp not in ("bytes", "string"):
        n = int(tp[5:])
        valid = ["0x" + "00" * n, "0x" + "ff" * n, "0X" + "AB" * n,
                 "  0x" + "11" * n + "  "]
        fault = ["0x" + "ab" * (n - 1), "0x" + "ab" * (n + 1), "0x" + "ab" * n + "c",
                 "0x" + "ZZ" + "ab" * (n - 1), ""]
        return [(a, "valid") for a in valid] + [(a, "fault") for a in fault]
    if tp == "bytes":
        valid = ["0x", "0xab", "0x" + "ab" * 31, "0x" + "ab" * 32, "0x" + "ab" * 33,
                 "0X" + "CD" * 48, "  0x12  "]
        fault = ["0x123", "0xZZ", "0xab cd"]
        return [(a, "valid") for a in valid] + [(a, "fault") for a in fault]
    if tp == "string":
        valid = ["", "a", "hello world", "x" * 200, "caf\u00e9\U0001f600",
                 "  padded  "]
        return [(a, "valid") for a in valid]
    raise AssertionError(tp)


def _transport_ok(text: str) -> bool:
    """Whether text survives the top-level splitter inline (firmware and
    oracle splitters are bracket-aware but not quote-aware, so a nested
    string with unbalanced brackets/closers breaks the arg split on both
    sides identically). Untransportable renders are fault-kind: still
    agreement-checked, but exempt from the valid-kind completeness assert."""
    _, err = _split_encode_args(_fmt_arg(text), 1)
    return err is None


def _audit_encode_cases(tag: str, tp: str, rng: random.Random):
    """200 (cmd, oracle, kind) encode cases for one type."""
    nd, r = _parse_abi_type(tp)
    assert r is None, (tp, r)
    out = []
    if nd.kind not in ("arr", "tup"):
        edges = _audit_scalar_edges(tp)
    else:
        edges = []
        # pinned composites: empties, singletons, full, wild strings
        protos = []
        if nd.kind == "arr" and nd.num < 0:
            protos.append([])
        for _ in range(24):
            protos.append(_rand_value_tree(rng, nd, False))
        for tree in protos[:24]:
            text = _render_tree(rng, nd, tree)
            if len(text) <= 250:
                edges.append((text, "valid"))
        # pinned faults: arity, leaf, shape
        for _ in range(8):
            tree = _rand_value_tree(rng, nd, False)
            tree = _arity_fault_tree(rng, nd, tree)
            text = _render_tree(rng, nd, tree)
            if len(text) <= 250:
                edges.append((text, "fault"))
        for _ in range(8):
            tree = _rand_value_tree(rng, nd, False)
            if _tree_has_leaf(nd, tree):
                tree = _corrupt_tree_leaf(rng, nd, tree)
                text = _render_tree(rng, nd, tree)
                if len(text) <= 250:
                    edges.append((text, "fault"))
        for shape in ("[1,2", "1,2]", "(1,2", "1,2)", "[[1]]", "((1))"):
            edges.append((shape, "fault"))
    for text, kind in edges:
        if kind == "valid" and not _transport_ok(text):
            kind = "fault"
        cmd = f"encode f{re.sub(r'[^A-Za-z0-9_]', '_', tag)}({tp}) " + _fmt_arg(text)
        if len(cmd) + 1 > 2048:
            continue
        out.append((cmd, expected(cmd), kind))
        if len(out) >= _AUDIT_N:
            return out
    attempts = 0
    while len(out) < _AUDIT_N:
        attempts += 1
        # Livelock guard (cf. decode_cases): a blob wider than eth_in
        # (e.g. fixed uint8[64]) used to retry forever. Fail loud.
        assert attempts <= _AUDIT_N * 50 + 500, ("audit encode livelock", tag)
        if nd.kind not in ("arr", "tup"):
            text = _rand_scalar_arg(rng, tp)
            kind = "valid"
            if rng.random() < 0.3:
                text = _corrupt_encode_arg(rng, tp, text)
                kind = "fault" if tp != "string" else "valid"
        else:
            tree = _rand_value_tree(rng, nd, False)
            f = rng.random()
            kind = "valid"
            if f < 0.12:
                tree = _arity_fault_tree(rng, nd, tree)
                kind = "fault"
            elif f < 0.30 and _tree_has_leaf(nd, tree):
                tree = _corrupt_tree_leaf(rng, nd, tree)
                kind = "fault"
            text = _render_tree(rng, nd, tree)
            if 0.30 <= f < 0.36:
                spots = [i for i, c in enumerate(text) if c in "[](),\"'"]
                if spots:
                    i = rng.choice(spots)
                    text = text[:i] + text[i + 1:]
                    kind = "fault"
            if len(text) > 250:
                continue
        if kind == "valid" and not _transport_ok(text):
            kind = "fault"
        cmd = f"encode fa({tp}) " + _fmt_arg(text)
        if len(cmd) + 1 > 2048:
            continue
        out.append((cmd, expected(cmd), kind))
    return out


def _audit_decode_cases(tag: str, tp: str, rng: random.Random):
    """200 (cmd, oracle, kind) decode cases for one type."""
    nd, r = _parse_abi_type(tp)
    assert r is None, (tp, r)
    out = []
    # pinned: zero / max natives where the kind has them
    try:
        blob = _abi_encode([tp], [_rand_native(rng, nd)]).hex()
    except Exception:
        blob = None
    if blob is not None:
        cmd = f"decode {tp} 0x{blob}"
        if len(cmd) + 1 <= 2048:
            out.append((cmd, expected(cmd), "valid"))
    attempts = 0
    while len(out) < _AUDIT_N:
        attempts += 1
        assert attempts <= _AUDIT_N * 50 + 500, ("audit decode livelock", tag)
        v = _rand_native(rng, nd)
        try:
            data = bytearray(_abi_encode([tp], [v]))
        except Exception:
            continue
        kind = "valid"
        f = rng.random()
        if f < 0.28:
            spots = list(_walk_spots(nd, bytes(data), 0))
            if spots:
                _apply_spot_fault(rng, data, rng.choice(spots))
                kind = "fault"
        elif f < 0.34 and len(data) > 32:
            data = data[:-32] if rng.random() < 0.5 else data[:-1]
            kind = "fault"
        cmd = f"decode {tp} 0x{bytes(data).hex()}"
        if len(cmd) + 1 > 2048:
            continue
        out.append((cmd, expected(cmd), kind))
    return out


def audit_cases(only_tag=None, shard=None):
    """Yield (tag, op, cmd, oracle, kind) for the whole per-type audit.
    shard=(i,n): only tags with index%n==i (parallel sharding; per-tag RNG
    uses the global index, so sharded cases are identical to unsharded)."""
    seen = set()
    for tag, tp in _audit_types():
        nm = "f" + re.sub(r"[^A-Za-z0-9_]", "_", tag)
        assert nm not in seen, ("audit name collision", tag, nm)
        seen.add(nm)
    for ti, (tag, tp) in enumerate(_audit_types()):
        if only_tag is not None and tag != only_tag:
            continue
        if shard is not None and ti % shard[1] != shard[0]:
            continue
        rng = random.Random(0xAB17 + ti)
        for cmd, oracle, kind in _audit_encode_cases(tag, tp, rng):
            yield (tag, "encode", cmd, oracle, kind)
        for cmd, oracle, kind in _audit_decode_cases(tag, tp, rng):
            yield (tag, "decode", cmd, oracle, kind)


def run_audit(elf=None, verbose=False, only_tag=None, shard=None):
    """The per-type audit on the reference machine, unfiltered. Returns
    (fails, total, per_tag). Asserts valid kinds succeed (completeness)."""
    fails, total = 0, 0
    per_tag: dict = {}
    instr = [0, 0]
    for tag, op, cmd, oracle, kind in audit_cases(only_tag, shard):
        total += 1
        if kind == "valid":
            assert not oracle.startswith("error"), (tag, cmd, oracle)
        try:
            m = machine(cmd, path=elf)
            toks = m.run(3 * 10**6)
            ok = m.halted and m.fault is None
            val = m.out.decode() if ok else ""
        except Exception as e:
            ok, val = False, f"HARNESS {e}"
        good = ok and agrees(val, oracle)
        if not good:
            fails += 1
            if verbose or fails <= 10:
                print(f"AUDIT FAIL {tag} {op} {cmd[:120]!r}")
                print(f"  got : {val[:150]!r}")
                print(f"  want: {oracle[:150]!r}")
        t = per_tag.setdefault(tag, [0, 0])
        t[0] += 1
        t[1] += 0 if good else 1
        if ok:
            instr[0] += m.steps if op == "encode" else 0
            instr[1] += m.steps if op == "decode" else 0
    return fails, total, per_tag, instr


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
    ap.add_argument("--audit", action="store_true", help="per-type ABI audit: 200 encode + 200 decode oracle cases per type, unfiltered")
    ap.add_argument("--audit-tag", type=str, default=None, help="with --audit: run one tag only (e.g. bool, uint8, 'fixed:uint8[16]')")
    ap.add_argument("--audit-shard", type=str, default=None, help="with --audit: run shard I/N only (e.g. 0/4); cases identical to unsharded")
    args = ap.parse_args()
    if args.elf:
        global ELF
        ELF = Path(args.elf)

    if args.audit:
        t0 = time.time()
        shard = None
        if args.audit_shard:
            i, n = args.audit_shard.split("/")
            shard = (int(i), int(n))
            assert 0 <= shard[0] < shard[1], args.audit_shard
        fails, total, per_tag, instr = run_audit(only_tag=args.audit_tag, shard=shard)
        for tag in sorted(per_tag):
            n, f = per_tag[tag]
            print(f"  {tag:28} {n:4} cases  {f} fail")
        print(f"AUDIT {total} cases, {fails} mismatches ({time.time()-t0:.0f} s)"
              f" [encode {instr[0]} instr, decode {instr[1]} instr]")
        print("AUDIT GATE " + ("PASS" if fails == 0 else "FAIL"))
        sys.exit(0 if fails == 0 else 1)

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
