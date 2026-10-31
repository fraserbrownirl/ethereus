# eth tool — instruction budget table

Per-call cost on the reference machine (`chipformer/rv/eth.py`, response phase
only: parked snapshot → GO → next HALT). Park is one-time setup (~40 instr, not
counted). A tool that doubles its count without a reason is a regression.

Wall seconds assume ~3,700 instr/s, ~1.3 trace tokens/instr at batch 1 on an L4
(the author's measured rate; observed tok/instr here is 1.4–1.8).

## Toolchain (pin this: a future parity failure may be the compiler)

- `riscv64-elf-gcc 16.2.0` (Homebrew) — the author used 13.2. Same `-march=rv32im_zba
  -mabi=ilp32 -O2`. If gate 1 ever breaks without a firmware change, suspect codegen drift first.
- libc: picolibc 1.8.12 @ `b5d6050`, single-target `rv32im/ilp32`
  (`-Dmultilib=false`), in `toolchain/picolibc-rv32im`. Newlib is never linked;
  `build_eth.sh` refuses to build if newlib symbols appear. Rebuild recipe:
  `toolchain/build_picolibc.sh`.
- Gate-1 qemu half: `qemu-riscv32` (linux-user) is not shipped by Homebrew qemu
  on macOS, so the qemu cross-check runs on Linux or against recorded vectors
  only. Ref-machine-vs-Python (the exactness half) runs anywhere.

## Gate 1 — keccak + u256 (PRELIMINARY: single probes + 100-case smoke)

Final `eth.py --cases 2000` numbers replace this table when the run lands.

| call | instr | tokens | tok/instr | ~wall |
| --- | --- | --- | --- | --- |
| unknown cmd (gather+extract+dispatch baseline) | 18,749 | — | — | 5 s |
| keccak, 0 bytes (1 perm) | 111,305 | 203,191 | 1.82 | 30 s |
| keccak, 136 bytes (2 perm) | 205,770 | 368,489 | 1.79 | 56 s |
| keccak, 137 bytes (2 perm) | 205,875 | 368,630 | 1.79 | 56 s |
| ⇒ per keccak-f[1600] permutation | ~95,000 | — | — | ~26 s |
| u256 `1+1` | 26,185 | — | — | 7 s |
| u256 bare 78-digit literal (parse+print only) | 230,205 | — | — | 62 s |
| u256 `2**255-1` | 143,321 | 215,059 | 1.50 | 39 s |
| smoke avg, keccak (50 cases) | 228,007 | 409,734 | 1.80 | 62 s |
| smoke avg, u256 (50 cases) | 212,745 | 295,253 | 1.39 | 57 s |

## Known budget gaps (exactness unaffected)

Spec targets: ≤12k instr/permutation, u256 mul ≤300 instr, keccak-100B ≈15k,
ERC-20 `transfer` ≈40–80k. Actuals are ~10–15× over:

1. `keccak.c` uses `uint64_t` lanes. RV32 has no 64-bit ops, so every
   rotate/add goes through libgcc software emulation (~95k/permutation). The
   spec calls for 50× `uint32` bit-interleaved lanes (the keccak-compact trick)
   so rotates stay 32-bit. Expected fix gain: ~8×.
2. ~19k fixed overhead per call (gather + 2× `snprintf` + `strstr` chain in
   `extract_param`). Suspect picolibc `snprintf`/format init; needs profiling,
   then likely hand-rolled tag matching.
3. u256 decimal print/parse dominates every result: a bare 78-digit literal
   costs 230k with zero arithmetic. Suspect repeated-divmod print; print via a
   faster base-10⁹ path.

## Firmware sizes (eth-bridge.elf)

`.text` 78,196 / 262,144 · `.rodata` 5,272 / 131,072 · `.data` 24 · `.bss` 1,304.
