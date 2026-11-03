"""Retired-instruction counts for `keccak 0x<N bytes>` on the ref machine (chipformer.rv.ref).
Reports: whole command (trace_stats instructions), inside keccak256() (entry->return, incl. callees),
and inside the permutation function only. Usage: kmeasure.py ELF [label]"""
import subprocess, sys, hashlib
import chipformer; print("chipformer from", chipformer.__file__)
from chipformer.rv import eth, ref
ELF = sys.argv[1]
syms = {}
for line in subprocess.run(["riscv64-elf-nm", ELF], capture_output=True, text=True).stdout.splitlines():
    p = line.split()
    if len(p) == 3: syms[p[2]] = int(p[0], 16)
K = syms["keccak256"]
PERM = syms.get("keccakf1600") or syms["KeccakP1600_Permute_24rounds"]
data_gen = lambda n: bytes((i * 131 + 7) & 0xFF for i in range(n))
print("elf", ELF, "sha256", hashlib.sha256(open(ELF, "rb").read()).hexdigest()[:16])
print(f"{'bytes':>6} {'cmd_instr':>10} {'keccak256':>10} {'perm_total':>10} {'perms':>5} {'perm_each':>9} ok")
for n in [0, 32, 64, 136, 1000]:
    d = data_gen(n)
    cmd = "keccak 0x" + d.hex() if n else "keccak ''"
    m = eth.machine(cmd, ELF)
    toks = []
    in_k = None; k_ret = None; k_instr = 0
    in_p = None; p_ret = None; p_instr = 0; perms = 0
    while not m.halted:
        pc = m.pc
        if in_k is None and pc == K: in_k = m.steps; k_ret = m.x[1]
        elif in_k is not None and k_ret is not None and pc == k_ret: k_instr += m.steps - in_k; in_k = None; k_ret = None
        if in_p is None and pc == PERM: in_p = m.steps; p_ret = m.x[1]; perms += 1
        elif in_p is not None and pc == p_ret: p_instr += m.steps - in_p; in_p = None
        toks += m.record()
        assert len(toks) < 5 * 10**6
    assert m.fault is None, m.fault
    st = ref.trace_stats([eth.S.tok("GO")] + toks) if hasattr(eth, "S") else ref.trace_stats(toks); assert st["instructions"] == m.steps, (st, m.steps)
    out = m.out.decode()
    want = eth._py_keccak(d) + "\n"
    print(f"{n:>6} {st['instructions']:>10} {k_instr:>10} {p_instr:>10} {perms:>5} {p_instr//max(perms,1):>9} {'ok' if out==want else 'MISMATCH '+out}")
