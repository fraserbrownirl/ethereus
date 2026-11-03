"""Instruction counts for the test_rv_evm cases (+ selector miss, create) on base vs new eth.elf."""
import sys
sys.path.insert(0, sys.argv[3])  # tests/ for evm_oracle + CASES
from chipformer.rv import eth, ref
import test_rv_evm as T
base, new = sys.argv[1], sys.argv[2]
def cnt(cmd, elf):
    m = eth.machine(cmd, elf); m.run(3 * 10**6); assert m.halted and m.fault is None
    return m.steps, m.out.decode()
rows = [(n, f"run {c} {cd.hex()} {v} {s}") for n, c, s, cd, v in T.CASES]
rows += [("selector-miss", "selector fooBarBaz(uint256,address)"),
         ("selector-hit", "selector transfer(address,uint256)"),
         ("create", "create 0x000000000000000000000000000000000000dEaD 5")]
print(f"{'case':24s} {'before':>8s} {'after':>8s} {'delta':>7s} same_out")
for n, cmd in rows:
    a, oa = cnt(cmd, base); b, ob = cnt(cmd, new)
    print(f"{n:24s} {a:8d} {b:8d} {100*(b-a)/a:6.1f}% {oa == ob}")
