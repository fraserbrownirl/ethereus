#!/usr/bin/env python3
"""Where each tracked firmware binary came from.

Ours are rebuilt with the pinned toolchain and must match in every loaded byte; the file hash may differ
because of debug paths. The author's are not ours to rebuild and are compared with a recorded hash.

    provenance.py            check everything; exit 1 on any difference
    provenance.py --hashes   check only what needs no toolchain (for CI without riscv gcc)
    provenance.py --record   rewrite the author's hash list; only after reading the diff
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

RV = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RV))
from chipformer.rv import elf as elfmod  # noqa: E402

DATA = RV / "tests/data/rv"
MANIFEST = DATA / "PROVENANCE.json"
OURS = {"eth.elf": ("eth", []), "eth-bridge.elf": ("eth-bridge", ["--bridge"])}
OURS_SIDECAR = {"eth.builtins.json": "eth.builtins.json"}
GCC_PIN = "16.2.0"
LIBC = RV / "toolchain/picolibc-rv32im/lib/libc.a"


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def tracked():
    out = subprocess.run(["git", "ls-files", str(DATA.relative_to(RV))], cwd=RV, capture_output=True, text=True, check=True).stdout
    return [Path(x).name for x in out.splitlines()]


def loaded_differences(a, b):
    """Every loaded byte: entry point, segment addresses, contents. Empty means the same image."""
    ea, sa = elfmod.segments(a)
    eb, sb = elfmod.segments(b)
    bad = []
    if ea != eb:
        bad.append(f"entry {ea:#x} vs {eb:#x}")
    if [x[0] for x in sa] != [x[0] for x in sb]:
        bad.append("segment addresses differ")
    for (addr, x), (_, y) in zip(sa, sb):
        if x != y:
            at = next((i for i, (p, q) in enumerate(zip(x, y)) if p != q), min(len(x), len(y)))
            bad.append(f"segment {addr:#x}: {len(x)} vs {len(y)} bytes, first difference at +{at:#x}")
    return bad


def check_hashes():
    want = json.loads(MANIFEST.read_text())["author"]
    ours = set(OURS) | set(OURS_SIDECAR) | {MANIFEST.name}
    bad = []
    names = set(tracked()) - ours
    for n in sorted(names | set(want)):
        if n not in want:
            bad.append(f"{n}: tracked, no recorded hash")
        elif n not in names:
            bad.append(f"{n}: recorded, not tracked")
        elif sha(DATA / n) != want[n]:
            bad.append(f"{n}: hash {sha(DATA / n)[:12]}, recorded {want[n][:12]}")
    print(f"author's files: {len(names)} tracked, {len(bad)} differ")
    return bad


def check_toolchain():
    v = subprocess.run(["riscv64-elf-gcc", "-dumpversion"], capture_output=True, text=True, check=True).stdout.strip()
    print(f"riscv64-elf-gcc {v}, pin {GCC_PIN}")
    return [] if v == GCC_PIN else [f"gcc {v}, pin {GCC_PIN}"]


def check_libc():
    want = json.loads(MANIFEST.read_text())["libc_sha256"]
    print(f"picolibc libc.a {sha(LIBC)[:12]}, recorded {want[:12]}")
    return [] if sha(LIBC) == want else ["libc.a differs from the recorded picolibc build"]


def check_ours():
    bad = []
    env = dict(os.environ, PICO=str(RV / "toolchain/picolibc-rv32im"), PYTHON=sys.executable)
    with tempfile.TemporaryDirectory() as t:
        for name, (stem, flags) in OURS.items():
            r = subprocess.run(["bash", str(RV / "chipformer/rv/env/eth/build_eth.sh"), t, stem, *flags], env=env, capture_output=True, text=True)
            if r.returncode:
                bad.append(f"{name}: build failed: {r.stderr[-200:]}")
                continue
            d = loaded_differences(DATA / name, Path(t) / name)
            print(f"{name}: tracked {sha(DATA / name)[:12]}, rebuilt {sha(Path(t) / name)[:12]}, loaded bytes {'same' if not d else 'DIFFER'}")
            bad += [f"{name}: {x}" for x in d]
        for name, built in OURS_SIDECAR.items():
            if sha(DATA / name) != sha(Path(t) / built):
                bad.append(f"{name}: differs from the rebuild")
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hashes", action="store_true")
    ap.add_argument("--record", action="store_true")
    a = ap.parse_args()
    if a.record:
        names = set(tracked()) - set(OURS) - set(OURS_SIDECAR) - {MANIFEST.name}
        MANIFEST.write_text(json.dumps({"libc_sha256": sha(LIBC), "author": {n: sha(DATA / n) for n in sorted(names)}}, indent=1) + "\n")
        return 0
    bad = check_hashes() + check_libc()
    if not a.hashes:
        bad += check_toolchain() + check_ours()
    for b in bad:
        print("DIFF", b)
    print("PROVENANCE", "FAIL" if bad else "PASS")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
