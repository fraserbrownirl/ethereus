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
import time
from pathlib import Path

from chipformer.rv import bridge as BR
from chipformer.rv import elf as elfmod
from chipformer.rv import ref

ELF = Path(__file__).parents[2] / "tests" / "data" / "rv" / "eth.elf"
BRIDGE_ELF = Path(__file__).parents[2] / "tests" / "data" / "rv" / "eth-bridge.elf"
ETH_IN, ETH_IN_SIZE = 0x100, 2048

OPS = ["keccak", "u256"]
ALL_OPS = ["keccak", "u256", "selector", "encode", "decode", "create", "create2", "run"]

def machine(cmd: str, path=ELF) -> ref.Machine:
    """The non-bridge firmware's snapshot with the cmd line in eth_in (cf calc.py)."""
    m = ref.Machine.from_program(elfmod.program(path))
    b = cmd.encode() + b"\0"
    assert len(b) <= ETH_IN_SIZE, f"cmd too long for eth_in: {len(b)}"
    for i, x in enumerate(b):
        a = ETH_IN + i
        w = a >> 2
        m.ram[w] = m.ram[w] & ~(0xFF << 8 * (a & 3)) | x << 8 * (a & 3)
    return m


def run(cmd: str, path=ELF) -> tuple[str, list[int]]:
    m = machine(cmd, path)
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

def expected_keccak(arg: str) -> str:
    # Mirror handle_keccak exactly: trim, hex? (odd/invalid -> bad hex), quoted?
    # (len>=2), else raw; the 1100 cap applies to DECODED data length.
    a = arg.strip()
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
            return int(tok) & MASK256
        except ValueError:
            return None
    return None

# Shunting-yard in Python matching firmware's prec table exactly
_U256_PREC = {"**": 6, "*": 5, "/": 5, "%": 5, "+": 4, "-": 4, "<<": 3, ">>": 3, "&": 2, "^": 1, "|": 0}
_U256_RIGHT = {"**"}

def _u256_tokenize(s: str):
    toks = []
    i = 0
    expect_operand = True
    while i < len(s):
        if s[i].isspace():
            i += 1
            continue
        if expect_operand:
            if s[i] == "(":
                toks.append("("); i += 1; expect_operand = True; continue
            if s[i] in "+-":
                j = i + 1
                while j < len(s) and s[j].isspace():
                    j += 1
                if j < len(s) and s[j] == "(":
                    toks.append(("num", 0)); toks.append(s[i]); i += 1; expect_operand = True; continue
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
                toks.append(")"); i += 1; expect_operand = False; continue
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
# Command dispatch

def expected(cmd: str) -> str:
    """Python oracle for one eth cmd line. Returns what the firmware's TEXT section prints."""
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
        # abi/evm/create not in gate-1 target; return stub so they don't crash suite
        return f"error (unknown cmd)\n"
    if head == "keccak":
        return expected_keccak(arg)
    if head == "u256":
        return expected_u256(arg)
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
    if rng.random() < 0.55:
        # decimal
        v = rng.randint(0, MASK256)
        # occasionally produce small / boundary values
        if rng.random() < 0.2:
            v = rng.choice([0, 1, 2, 255, 256, (1 << 128), MASK256, MASK256 - 1])
        return str(v)
    else:
        v = rng.randint(0, MASK256)
        if rng.random() < 0.2:
            v = rng.choice([0, 1, 0xFF, 0x100, (1 << 128), MASK256])
        h = hex(v)
        # randomly uppercase
        if rng.random() < 0.3:
            h = h.upper().replace("X", "x")
        return h

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


# The bridge's capture budget: 80 rows of one token each, 16 bytes per row.
CAP_TOKENS, CAP_ROW_BYTES = 80, 16


def cases(n: int, seed: int = 0, only: list[str] | None = None, stats: dict | None = None):
    """n ACCEPTED cases per op, interleaved round-robin.

    Every draw is sized with the Qwen tokenizer; draws over the capture window
    are rejected the way the bridge firmware would (it prints "error (too long)")
    and counted in stats[op] = [over_tokens, over_rowbytes, drawn].
    """
    rng = random.Random(seed)
    want = only or OPS
    gens = []
    if "keccak" in want:
        gens.append(("keccak", keccak_cases))
    if "u256" in want:
        gens.append(("u256", u256_cases))
    per_op: dict[str, list] = {}
    for op, gen in gens:
        acc: list[tuple[str, str, str]] = []
        drawn = over_tok = over_row = 0
        while len(acc) < n:
            for cand_op, cmd, oracle in gen(n, rng):
                drawn += 1
                ntok, wide = captured_size(cmd)
                if ntok > CAP_TOKENS:
                    over_tok += 1
                    continue
                if wide > CAP_ROW_BYTES:
                    over_row += 1
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
    args = ap.parse_args()

    # --expr: single shot with stats (sized, but run regardless of the window)
    if args.expr:
        cmd = args.expr
        val, st, raw = run_one(cmd)
        oracle = expected(cmd)
        ntok, wide = captured_size(cmd)
        win = "in-window" if ntok <= CAP_TOKENS and wide <= CAP_ROW_BYTES else "OVER-WINDOW"
        print(repr(val), "ok" if val == oracle else "MISMATCH", st, f"({ntok} tok, {wide}B max, {win})")
        if val != oracle:
            print(" expected:", repr(oracle))
            print(" raw out:", repr(raw.decode()[:2000]))
        return

    only = [x.strip() for x in args.only.split(",") if x.strip()] if args.only else None
    n = args.cases
    if n <= 0:
        ap.error("--cases N required (or --expr)")
    t0 = time.time()
    per: dict[str, list[int]] = {op: [0, 0, 0] for op in (only or OPS)}  # count, instr, tokens
    bad = 0
    bad_samples: list[tuple[str, str, str]] = []
    # n accepted cases per op (spec "2000 per cmd"); over-window draws are rejected
    # by the tokenizer sizer and counted in stats, never run.
    stats: dict[str, list[int]] = {}
    gen = cases(n, args.seed, only, stats)

    total_expected = 0
    for op, cmd, oracle in gen:
        total_expected += 1
        if total_expected % 250 == 0:
            print(f"  ... {total_expected} cases, {bad} mismatches ({time.time()-t0:.0f} s)", flush=True)
        val, st, raw = run_one(cmd)
        per[op][0] += 1
        per[op][1] += st["instructions"]
        per[op][2] += st["tokens"]
        if val != oracle:
            bad += 1
            if len(bad_samples) < 10:
                bad_samples.append((cmd, repr(val), repr(oracle)))
            print(f"MISMATCH {op} {cmd!r} got={val!r} want={oracle!r}")
            if bad <= 3:
                print(" raw:", repr(raw.decode()[:2000]))

    elapsed = time.time() - t0
    print(f"{total_expected} cases ({', '.join(f'{op} {per[op][0]}' for op in per)}), {bad} mismatches ({elapsed:.1f} s)")
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

if __name__ == "__main__":
    main()
