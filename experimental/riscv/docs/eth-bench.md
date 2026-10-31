# eth tool — instruction budget table

Per-call cost on the reference machine (`chipformer/rv/eth.py` on the non-bridge
`eth.elf`, response phase only). Wall seconds assume ~3,700 instr/s at batch 1
on an L4. A tool that doubles its count without a reason is a regression.

Note on tok/instr: 1.8 versus the author's 1.2–1.4 is EXPECTED for code full of
32-bit random values (each takes a 3-token record), not a regression to chase.

Note on the accepted set: the 80-token window rejects most natural draws (below),
so accepted gate-1 cases skew toward short inputs. Full-width exactness is
covered via hex literals (radix-independent limbs) plus over-window capability
vectors in `test_rv_eth.py` (non-bridge runs anything in `eth_in`).

## Toolchain (pin this: a future parity failure may be the compiler)

- `riscv64-elf-gcc 16.2.0` (Homebrew) — the author used 13.2. Same
  `-march=rv32im_zba -mabi=ilp32 -O2`. If gate 1 ever breaks without a firmware
  change, suspect codegen drift first.
- libc: picolibc 1.8.12 @ `b5d6050`, single-target `rv32im/ilp32`
  (`-Dmultilib=false`), in `toolchain/picolibc-rv32im`. Newlib is never linked;
  `build_eth.sh` refuses to build if newlib symbols appear (0 in both images).
  Rebuild recipe: `toolchain/build_picolibc.sh`.
- Keccak: XKCP `KeccakP-1600-inplace32BI.c` (CC0) @ `5585d021`, vendored in
  `env/eth/xkcp/` with provenance README. No `uint64_t` lanes anywhere.
- Gate-1 qemu half: `qemu-riscv32` (linux-user) is not shipped by Homebrew qemu
  on macOS, so the qemu cross-check runs on Linux or against recorded vectors
  only. Ref-machine-vs-Python (the exactness half) runs anywhere.
- Sized with the real Qwen3.5 tokenizer (`tokenizers==0.23.3`, dev dep).

## Gate 1 — keccak + u256 (2026-10-31, PASS)

`eth.py --cases 2000 --only keccak,u256`: 4000 accepted cases, 0 mismatches
(203 s referee time, was 747 s).

| op | cases | instr/call | tokens/call | tok/instr | ~wall |
| --- | --- | --- | --- | --- | --- |
| keccak | 2000 | 24,153 | 51,917 | 2.15 | 7 s |
| u256 | 2000 | 12,023 | 17,475 | 1.45 | 3 s |

Capture-window rejects (drawn from the natural distribution, sized with the
Qwen tokenizer, rejected the way the bridge firmware would):

| op | drawn | over-80-tokens | over-16B-row | run |
| --- | --- | --- | --- | --- |
| keccak | 5037 | 3037 (60%) | 0 | 2000 |
| u256 | 12134 | 10134 (84%) | 0 | 2000 |

Previous run (pre-XKCP, pre-dispatch-fix): keccak 109,821 / u256 31,872 instr
(keccak rejects 3037/5037, u256 rejects 12017/14017). The u256 generator now
emits values > 10⁴⁰ as hex, cutting draws from 14,017 to 12,134.

## Gate 1 — ABI: selector/encode/decode/create/create2 (2026-10-31, PASS)

`eth.py --cases 2000 --seed 7 --only selector,encode,decode,create,create2`:
6920 accepted cases, 0 mismatches (400 s referee time). Strict grammar: the
firmware accepts exactly eth_abi's canonical types (bare `uint`/`int` = 256
included); tuples, fixed-point, and dynamic arrays are well-formed but out
of v1 scope (`error (unsupported type)`); everything else is
`error (bad type)` / `error (bad …)`. Nothing truncates: oversize input is
an error. Every value-level rejection is cross-checked against eth_abi
itself by the oracle (xchecks).

| op | cases | instr/call | tokens/call | tok/instr | ~wall |
| --- | --- | --- | --- | --- | --- |
| selector | 2000 | 13,119 | 27,816 | 2.12 | 4 s |
| encode | 2000 | 30,287 | 60,111 | 1.98 | 8 s |
| decode | 920 | 8,677 | 11,759 | 1.36 | 2 s |
| create | 2000 | 24,254 | 51,560 | 2.13 | 7 s |
| create2 | 0 | — | — | — | — |

Capture-window rejects:

| op | drawn | over-80-tokens | over-16B-row | run |
| --- | --- | --- | --- | --- |
| selector | 2000 | 0 | 0 | 2000 |
| encode | 4815 | 2815 (58%) | 0 | 2000 |
| decode | 40000 | 39080 (98%) | 0 | 920 |
| create | 2049 | 49 (2%) | 0 | 2000 |
| create2 | 40000 | 40000 (100%) | 0 | 0 |

decode and create2 are window-starved by construction (a 32-byte word is
~20–30 tokens; a full create2 call is ~150): their exactness is covered
unfiltered in `test_tiny_fuzz_abi` (non-bridge runs anything in `eth_in`),
and the draw cap (20×cases) keeps the gate run from hanging on them.

create2 is the first production command the 80-token window kills: 42-char
deployer + 66-char salt + 66-char hash is ~150 tokens, and no harness work
fixes that. It is the concrete case for the two-level counter (spec open
questions). Resolution: create2 takes salt as a short decimal (most salts
are small ints) and initcodehash by file name once Phase 2 lands; inline
hashes stay unsupported. (Not yet implemented — the machine path still
takes inline hashes, as gated above.)

Error fuzzing is single-fault only: multi-fault error precedence between
the firmware and the oracle is not asserted. Acceptable — it only matters
if error strings ever become load-bearing, and they shouldn't.

Encode on a cached selector (the demo path): `transfer(address,uint256)` =
9,693 instr, `approve(address,uint256)` = 9,594, `balanceOf(address)` =
6,766 — vs ~30k average with keccak misses. The ~10k row is the number the
demo shows.

Selector DROM cache (225 entries, verified 225/225 against 4byte.directory;
see `env/eth/selectors.verify.log`): demo prompts hit 25/25 at ~1.9k instr;
a miss costs ~24.8k (one keccak). `encode transfer(address,uint256) …` =
9,693 instr on a cache hit vs ~27k on a miss.

## Probe table (non-bridge, exact counts)

| call | instr | before | notes |
| --- | --- | --- | --- |
| `foo` (dispatch) | 417 | 16,920 | strncpy-padding fix (40×) |
| `u256 0` | — | 19,527 | |
| `u256 1+1` | 2,826 | 21,343 | |
| `u256 <78-digit max>` | 15,811 | 33,727 | dec-in/dec-out |
| `u256 0x<64f>` | 17,737 | 51,575 | hex in, decimal out |
| `u256 0x<64f> hex` | 8,499 | 42,307 | hex in, hex out: cheapest full-width |
| `u256 0x<64f> * 0x<64f> hex` | 18,418 | 67,501 | max multiply |
| `keccak` 32 bytes | 25,984 | 111,272 | 1 perm (4.3×) |
| `keccak` 136 bytes | 53,885 | 198,621 | 2 perms (3.7×) |
| ⇒ per keccak-f[1600] permutation | 19,769 | ~90,000 | ~5.3 s (target ≤12,000) |

## u256 split: parse / math / print (probe-derived, ±10%)

| component (max width) | instr | before |
| --- | --- | --- |
| dispatch + eval scaffolding | ~500 | ~19,000 |
| parse, 78-digit decimal | ~4,000 | same (10⁹ already in) |
| parse, 64-nibble hex | ~2,000 | ~22,000 (8-nibble fix) |
| print, 78-digit decimal | ~11,000 | same (10⁹ already in) |
| print, hex (`0x`+trim) | ~1,500 | same |
| mul, 256×256 | ~2,000 | same |

## Dispatch profile (PC histogram by symbol, `foo`; profiler: `rv/profile.py`)

| symbol | instr | share |
| --- | --- | --- |
| put_str | 144 | 34.5% |
| handle_cmd | 108 | 25.9% |
| cpy_cap | 74 | 17.7% |
| total | 417 | |

Top entry before the fix: `memset` 16,478 (97.4%), called from `strncpy` —
every handler copied its input with `strncpy(tmp,…,sizeof-1)`, zero-padding
kilobytes per call. All 19 sites now use `cpy_cap` (copy to NUL, always
terminates; `env/eth/util.h`). Matches calc.c's ~200–250 dispatch scale plus
output printing.

## Keccak per-permutation: 19,769 instr (XKCP port)

Symbol profile of 1-perm `keccak 0x`: Permute_Nrounds 19,769 (82.7%),
AddLanes 1,443, ExtractLanes 323, memset 1,350 (pad block), rest dispatch.
A 2-perm minus 1-perm profile confirms the 2nd permutation costs exactly
19,769 — the ~30k ladder delta conflates absorb + hex parse of the extra block.

19.8k is 1.6× over the ≤12k target. Mechanism: the port keeps the 50 half-lanes
memory-resident, ~824 instr/round over 24 rounds, and gcc must reload lanes
after every store through the same base pointer (can't prove disjointness).
~500/round needs register-cached rounds — a rewrite, not a port. Recommended:
accept 19.8k (keccak-32B ≈ 26k ≈ 7 s); revisit only if EVM/verify budgets miss.

## Hex as the primary u256 input form

Decimals run ~1 char/token: a 78-digit literal (184 tokens with framing) never
fits the window. 64 hex chars run ~20–30 tokens, so full-width values go hex —
the spec and the harness generator now say so (values > 10⁴⁰ emit hex; small
values stay mixed to fuzz both parse paths). With 8-nibbles-per-step parse,
hex-in/hex-out totals 8,499 at max width — 1.9× cheaper than dec-in/dec-out
(15,811) and the path Qwen should be told to use.

## Remaining budget gaps (exactness unaffected)

1. Keccak ~20k/perm vs ≤12k target (see above; accept or rewrite rounds).
2. DONE: dispatch 16,920 → 417 (strncpy padding → `cpy_cap`).
3. DONE: hex parse 64 nibbles ~22k → ~2k (8 nibbles/step).
4. DONE: decimal parse/print ~207k → ~15k (10⁹-per-step).
5. DONE: picolibc `strcpy` word-LW/SW misaligned faults → byte loops at the two
   unaligned call sites. Other string fns are byte-wise.

## Firmware sizes

`eth.elf`: .text 97,348 / 262,144 · .rodata 20,792 / 131,072 · .ethin 2,048 ·
.data 20 · .bss 12.
`eth-bridge.elf`: .text 102,404 / 262,144 · .rodata 20,864 / 131,072 ·
.data 20 · .bss 1,292.
(.rodata grew ~15 KB with the 225-entry selector DROM table.)
