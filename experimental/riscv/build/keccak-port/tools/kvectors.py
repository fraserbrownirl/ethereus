"""Keccak-256 test vectors through the firmware's `keccak` command on the ref machine."""
import sys
from chipformer.rv import eth
from eth_hash.auto import keccak
ELF = sys.argv[1]
EMPTY = "0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
fails = 0
def chk(cmd, want, label, files=None):
    global fails
    got, _ = eth.run_with_files(cmd, files, ELF) if files else eth.run(cmd, ELF)
    ok = got.strip() == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'} {label}: {got.strip()}" + ("" if ok else f" want {want}"))
chk("keccak ''", EMPTY, "empty (quoted '')")
chk('keccak ""', EMPTY, "empty (quoted \"\")")
# known published vector: keccak256("abc")
chk("keccak abc", "0x4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45", "'abc' (published vector)")
for n in [1, 31, 32, 33, 135, 136, 137, 271, 272, 273, 544, 1000]:
    d = bytes((i * 197 + 3) & 0xFF for i in range(n))
    chk("keccak 0x" + d.hex(), "0x" + keccak(d).hex(), f"{n} bytes ({(n // 136) + 1} blocks)")
d = bytes((i * 197 + 3) & 0xFF for i in range(1100))
chk("keccak @f", "0x" + keccak(d).hex(), "1100 bytes via @file (9 blocks, firmware max)", {"f": "0x" + d.hex()})
import chipformer; print("chipformer from", chipformer.__file__)
print("FAILS", fails)
sys.exit(1 if fails else 0)
