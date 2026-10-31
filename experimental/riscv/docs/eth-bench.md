# eth tool — instruction budget table

Per-call cost on the reference machine (`chipformer/rv/eth.py` on the non-bridge
`eth.elf`, response phase only). Wall seconds assume ~3,700 instr/s at batch 1
on an L4. A tool that doubles its count without a reason is a regression.

## Toolchain (pin this: a future parity failure may be the compiler)

- `riscv64-elf-gcc 16.2.0` (Homebrew) — the author used 13.2. Same
  `-march=rv32im_zba -mabi=ilp32 -O2`. If gate 1 ever breaks without a firmware
  change, suspect codegen drift first.
- libc: picolibc 1.8.12 @ `b5d6050`, single-target `rv32im/ilp32`
  (`-Dmultilib=false`), in `toolchain/picolibc-rv32im`. Newlib is never linked;
  `build_eth.sh` refuses to build if newlib symbols appear (0 in both images).
  Rebuild recipe: `toolchain/build_picolibc.sh`.
- Gate-1 qemu half: `qemu-riscv32` (linux-user) is not shipped by Homebrew qemu
  on macOS, so the qemu cross-check runs on Linux or against recorded vectors
  only. Ref-machine-vs-Python (the exactness half) runs anywhere.
- Sized with the real Qwen3.5 tokenizer (`tokenizers==0.23.3`, dev dep).

## Gate 1 — keccak + u256 (2026-10-31, PASS)

`eth.py --cases 2000 --only keccak,u256`: 4000 accepted cases, 0 mismatches.

| op | cases | instr/call | tokens/call | tok/instr | ~wall |
| --- | --- | --- | --- | --- | --- |
| keccak | 2000 | 109,821 | 201,453 | 1.83 | 30 s |
| u256 | 2000 | 31,872 | 56,473 | 1.77 | 9 s |

Capture-window rejects (drawn from the natural distribution, sized with the
Qwen tokenizer, rejected the way the bridge firmware would):

| op | drawn | over-80-tokens | over-16B-row | run |
| --- | --- | --- | --- | --- |
| keccak | 5037 | 3037 (60%) | 0 | 2000 |
| u256 | 14017 | 12017 (86%) | 0 | 2000 |

Decimals tokenize ~1 char/token, hex ~2 chars/token, framing ~29 tokens — so a
78-digit decimal pair (184 tokens) never fits, while `2**255-1` (34) and one
32-byte hex word + op (63) do. Accepted u256 cases skew small-decimal and hex;
full-width exactness is covered via hex literals (radix-independent limbs).

## Probe table (non-bridge, exact counts)

| call | instr | notes |
| --- | --- | --- |
| `foo` (dispatch baseline) | 16,920 | handle_cmd + strcmp chain, no eval |
| `u256 0` | 19,527 | + tokenize/eval/print of 1 digit |
| `u256 <78-digit max>` | 33,727 | parse + print, no math |
| `u256 0x<64f>` | 51,575 | hex in, decimal out |
| `u256 0x<64f> hex` | 42,307 | hex in, hex out |
| `u256 0x<64f> * 0x<64f> hex` | 67,501 | max multiply, hex io |
| `u256 1+1` | 21,343 | small-expr total |
| `keccak` 32 bytes | 111,272 | 1 permutation |
| `keccak` 136 bytes | 198,621 | 2 permutations |
| ⇒ per keccak-f[1600] permutation | ~90,000 | ~24 s (target ≤12,000) |

## u256 split: parse / math / print (probe-derived, ±10%)

| component (max width) | instr | before (per-digit) |
| --- | --- | --- |
| dispatch + eval scaffolding | ~19,000 | same |
| parse, 78-digit decimal | ~4,000 | part of ~207,000 conversion |
| parse, 64-nibble hex | ~22,000 | same (not yet fixed) |
| print, 78-digit decimal | ~11,000 | part of ~207,000 conversion |
| print, hex (`0x`+trim) | ~1,500 | — (new suffix) |
| mul, 256×256 (`u256_mul512` + scan) | ~2,000 | same |

Conversion total (parse+print, 78 digits): 226,869 → 33,727 whole-call, i.e.
~207,000 → ~15,000 for conversion alone (~14×). The 10⁹ fix is confirmed.

Read off the probes: print_dec(78) − print_hex = 51,575 − 42,307 = 9,268;
`u256 0x1 hex` bounds print_hex ≈ 1.5k (full 64-char expansion always runs);
the rest is subtraction against the 16,920 dispatch baseline.

## Hex-in/hex-out variant (cheaper iff parse_hex is fixed)

Hex-out saves ~9k vs decimal-out on full-width values (1.5k vs 11k). But hex-IN
currently costs ~18k MORE than decimal-in (22k vs 4k): `u256_parse_hex` shifts
the whole value per nibble. Net today: hex-in/hex-out is ~9k WORSE than
dec-in/dec-out at max width (42,307 vs 33,727). The fix is the same standard
trick as decimal: 8 nibbles per step into a `uint32`, then one word-shift+OR
(~0.5k). After that, hex-in/hex-out should total ~21k — the cheapest full-width
path, worth telling Qwen to use.

## Known budget gaps (exactness unaffected)

Spec targets: ≤12k instr/permutation, u256 mul ≤300 instr, keccak-100B ≈15k.
Actuals:

1. `keccak.c` uses `uint64_t` lanes → ~90k/permutation (~8× over). Fix per spec:
   50× `uint32` bit-interleaved lanes (keccak-compact). Biggest remaining item:
   a 100-byte keccak costs ~200k (~54 s) vs the 15k target.
2. ~17k dispatch + ~2k eval scaffolding per u256 call (handle_cmd/strcpy-free
   paths, RPN arrays). Needs profiling; likely the strcmp chain + 512B strncpy.
3. `u256_parse_hex` nibble-at-a-time (~22k for 64 nibbles). Fix: 8 nibbles/step.
4. DONE 2026-10-31: decimal parse/print 10⁹-per-step (~207k → ~15k).
5. DONE 2026-10-31: picolibc `strcpy` uses word LW/SW and the machine faults on
   misalignment — replaced the two unaligned call sites with byte loops
   (`u256_to_hex_trim`, encode arg copy). Other string fns are byte-wise.

## Firmware sizes

`eth.elf`: .text 74,432 / 262,144 · .rodata 5,232 / 131,072 · .ethin 2,048 ·
.data 24 · .bss 24.
`eth-bridge.elf`: .text 78,560 / 262,144 · .rodata 5,312 / 131,072 · .data 24 ·
.bss 1,304.
