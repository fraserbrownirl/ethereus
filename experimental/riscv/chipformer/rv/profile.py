"""PC histogram by ELF symbol for one firmware call (dispatch/keccak profiling).

Runs the non-bridge ELF on ref.Machine one record() at a time, attributing each
executed instruction (the machine's PC before the record) to its ELF symbol via
`riscv64-elf-nm`. No instrumentation in the firmware; no guesses.

Usage (from experimental/riscv):
  .venv/bin/python -m chipformer.rv.profile foo
  .venv/bin/python -m chipformer.rv.profile "keccak 0xab12" --top 15
"""

import argparse
import subprocess
from pathlib import Path

from chipformer.rv import elf as elfmod
from chipformer.rv import eth
from chipformer.rv import ref as refmod
from chipformer.rv import spec as S

NM = "riscv64-elf-nm"


def symbols(path: Path) -> list[tuple[int, str]]:
    out = subprocess.run([NM, str(path)], capture_output=True, text=True, check=True).stdout
    syms = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3:
            addr, _, name = parts
            syms.append((int(addr, 16), name))
    syms.sort()
    return syms


def histogram(cmd: str, path=eth.ELF, limit: int = 3 * 10**6):
    syms = symbols(path)
    addrs = [a for a, _ in syms]
    import bisect

    def sym_at(pc: int) -> str:
        i = bisect.bisect_right(addrs, pc) - 1
        return syms[i][1] if i >= 0 else f"@{pc:#x}"

    m = eth.machine(cmd, path)
    counts: dict[str, int] = {}
    pcs: dict[str, list[int]] = {}
    n = 0
    while not m.halted and n < limit:
        pc = m.pc
        s = sym_at(pc)
        counts[s] = counts.get(s, 0) + 1
        m.record()
        n += 1
    assert m.halted and m.fault is None, (cmd, m.fault)
    return counts, m.out.decode(), m.steps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--elf", default=None)
    args = ap.parse_args()
    path = Path(args.elf) if args.elf else eth.ELF
    counts, out, steps = histogram(args.cmd, path)
    total = sum(counts.values())
    print(f"{args.cmd!r} -> {out[:60]!r} ({steps} steps)")
    for s, c in sorted(counts.items(), key=lambda kv: -kv[1])[: args.top]:
        print(f"  {c:8d}  {100*c/total:5.1f}%  {s}")
    print(f"  {total:8d}  total ({len(counts)} symbols)")


if __name__ == "__main__":
    main()
