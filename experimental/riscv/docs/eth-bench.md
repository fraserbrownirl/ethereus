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
- EVM oracle: py-evm 0.12.1b1 (Cancun), contracts compiled with solc
  0.8.38 pinned in `env/eth/contracts/solc-version.txt` (py-solc-x
  2.1.0). All declared in the `dev` dependency group.

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

## Gate 1 — EVM: built-in ERC-20 + AMM (2026-11-01, PASS)

`tests/test_rv_evm.py`: 12 cases through the RV firmware vs py-evm
`apply_computation` on the same runtime bytecode with the same seeded
storage — return data, gross gas, revert status, logs, and storage diff
all equal. Contracts are real solc 0.8.38 `--optimize --metadata-hash
none` runtime bytecode (`env/eth/contracts/MiniERC20.sol`, `Amm.sol`;
MIT, <60 lines each), embedded by `gen_builtins.py`, which also emits
the fixture both sides read (`totalSupply=1000`, `balances[alice]=1000`).
No deploy: the firmware executes runtime code against the fixture, and
the oracle does `set_code` + `set_storage` + `apply_computation`.

Gas compared is computation gas, pre-refund (gross), on both sides —
refunds live at the transaction level, which neither side runs, so v1
tracks no refunds. Warm/cold (EIP-2929) is required and implemented: a
transfer's 29,636 gas is mostly cold SLOAD (2,100) + warm SSTORE-reset
(2,900) + cold SLOAD (2,100) + warm SSTORE-set (20,000).

| call | EVM gas | instr/call | tokens/call | tok/instr | ~wall |
| --- | --- | --- | --- | --- | --- |
| totalSupply() | 2,317 | 29,840 | 38,222 | 1.28 | 12 s |
| balanceOf(alice) | 2,541 | 69,596 | 110,336 | 1.59 | 35 s |
| transfer(bob,100) | 29,636 | 127,734 | 205,864 | 1.61 | 64 s |
| approve(spender,50) | 24,486 | 114,799 | 190,601 | 1.66 | 60 s |
| transferFrom (revert) | 2,827 | 117,531 | 194,389 | 1.65 | 61 s |
| getAmountOut(1000,50000,60000) | 813 | 95,143 | 119,310 | 1.25 | 37 s |

EVM wall is from tokens at ~3,200 tok/s (batch 1, L4): the model emits
tokens, so tokens drive demo time. (The older sections use ~3,700
instr/s; at these rows' ~1.5 tok/instr the two rules differ ~2×.)

Opcode-level exactness (`tests/test_eth_host.py`, host ASan build):
DIV/SDIV/MOD/SMOD/ADDMOD/MULMOD by zero all return 0 (EVM, not RISC-V);
SHL/SHR/SAR with shift ≥256 saturate (SAR keeps the sign); SIGNEXTEND
with x≥31 is identity — each vs py-evm on output and gas, plus
stack-underflow agreement (ADDMOD at 2-deep faults on both sides).

Profile of `balanceOf` (70k instr): keccak perm 20k (28%, the
mapping-slot hash — stays), evm_run 17k (25%), memset 8k (12%, small
fills through picolibc), evm_init 7k (10%, the jumpmap build below),
stack_req 7k (10%), handle_run 2k (3%). No fixture copy: the fixture
stays DROM-pointed (`evm_slot_orig` is noise), and calldata
parse/response print are ~3% — the cost is interpretation, not
scaffolding.

Two linear costs removed, both found by profiling (not guessed):
`evm_init` no longer bulk-zeroes (stack is sp-guarded, memory is
zeroed lazily on expansion, storage uses an occupancy count) — fixed
−52k/call. JUMP/JUMPI no longer rescan the code for push-data (~3.2k
per jump); `evm_init` builds a 1-bit-per-byte jumpmap once (~7k) and
jumps validate in O(1) (~130 each) — another −21 to −40k on jumping
calls (transfer 164k→128k). totalSupply nets +5k (one 7k scan replaces
two cheap early-code scans) — accepted. Per-opcode top three on
`transfer` are now all flat: PUSH1, CALLDATALOAD, PUSH2 (~140 each;
CALLDATALOAD's byte loop at ~980 is the next micro-target, ~2k).

RAM (32 KB total, ~4 KB C stack): `evm_t` is ~13 KB in BSS (stack
128×u256, 4 KB memory, 32 storage slots; the ERC-20 touches ≤4
slots/call), jumpmap 1.5 KB (12 KB code cap). Image sizes:
`.text 105,836 / .rodata 23,152 / .bss 14,568`. Revised 2026-11-01
from 256 / 8 KB / 128 slots (~26 KB), which did not fit.

Bugs the diff found (all fixed): PUSH aliased `u256_shl` (which zeroes
its output first) and loaded only the last immediate byte; fresh
storage slots read 0 instead of the fixture value; SUB was charged 5
instead of G_verylow 3; the ADDMOD/MULMOD depth row named 0x0C/0x0D
(the real opcodes are 0x08/0x09) and sat shadowed behind the 0x01–0x0B
row, admitting a 2-deep stack for a 3-pop op.

## Gate 1 — full-width @file: decode/create2/run (2026-11-01, PASS)

Large arguments never go through the token window on any path: the
bridge resolves `@name` and the host writes the content into
`file_buf`, a 4 KB firmware section at RAM 0x900, outside the capture.
The firmware's `file_arg()` resolves `@name` over `name\0hex\0` pairs
(wired into keccak/encode/decode/create2/run); a missing name answers
`error (no file)`, and a quoted `"@b"` stays a literal string. This is
the same mechanism as the API gateway's spill.

`tests/test_rv_filebuf.py`: 15/15. Three bulk gates of 200
seeded full-width cases each vs the same oracles as the inline gate
(eth_abi, eth_hash, py-evm); every case asserts its inline form
exceeds the 80-token window, so the file path is load-bearing, not
convenience. Plus 12 singles: 1 KB keccak, bytes-400 encode,
quoted-`@` literal, 4 missing-file errors, 16-case decode/create2
fuzz.

| op (via @file) | cases | oracle | instr avg |
| --- | --- | --- | --- |
| decode (uint256,bytes≤800,address,bytes,bool) | 200 | eth_abi | 57,408 |
| create2 (full 32 B salt + hash) | 200 | eth_hash | 29,027 |
| run erc20 (transfer/transferFrom/approve + 64–400 B pad) | 200 | py-evm | 117,148 |

Run cases vary sender (alice/spender, so transferFrom covers both the
allowance path and the revert path) and pad length; return data, gross
gas, revert status, and logs all equal py-evm.

Honest limit for the site's table: in-model file-to-tool relay is ~2
words — Qwen can repeat a tiny `files get` answer into the next call,
but anything bigger must go through the bridge/gateway spill. The
model never sees full file bytes in-window except through the spill.

## API spec spill test: not met — deferred (2026-11-01)

The spec's test (`eth decode` with 10 words and `eth run erc20` with real calldata, both `spilled: true`, through the worker) is not met. GPU spill is off (`serve.SPILL_ON_GPU`): the first run returned a lock error right after `GO`, because `core.snapshot` resets the circuit's capture state, so a re-prefilled call is not captured. Over-window calls get the firmware's exact `error (too long)`. The reference-machine path passes (`tests/test_rv_filebuf.py`, `tests/test_rv_spill.py`).

Agreed plan (Fraser): no ELF change until both eval configurations have finished on `b320d31b`. Then the host writes the rewritten command into `eth_in` and the firmware prefers it, followed by its own gate 4 and a rerun of this test.

## Oracle per command

What gate 1 compares each command against, from `chipformer/rv/eth.py`.

| command | oracle | source |
| --- | --- | --- |
| keccak | eth_hash | `expected_keccak` |
| u256 | Python integers, mirroring the firmware's grammar | `expected_u256` |
| selector | eth_hash | `expected_selector` |
| encode | eth_abi | `expected_encode` |
| decode | eth_abi | `expected_decode` |
| create | rlp + eth_hash | `expected_create` |
| create2 | eth_hash | `expected_create2` |
| run | py-evm | `tests/evm_oracle.py` |

## Run calls and the 80-token window (measured 2026-11-01)

Qwen spends one token per digit, so zero-padded words and all-digit addresses overflow the window. Only calls with no address argument fit. Reproduce with `scripts/bench_window.py`.

| call | tokens | fits 80 |
| --- | --- | --- |
| run totalSupply() | 36 | yes |
| run decimals() | 37 | yes |
| run balanceOf(alice) | 102 | no |
| run allowance(alice,spender) | 163 | no |
| run transfer(bob,100) | 164 | no |
| run approve(spender,50) | 165 | no |
| run transferFrom (revert) | 229 | no |
| run getAmountOut(1000,50000,60000) | 226 | no |

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

Tracked `eth-bridge.elf` (`c0853b4`): sha256
`b320d31b4fd722ac0adfdbcf1313892967ea1bf8e826bd318138cb167c06c685`, with `.filebuf`
at 0x900 and `file_buf` in the symbol table. Source is `880e37d`. The whole-file hash
changes per build because of debug paths, so compare loaded sections: a rebuild from
source matches `.text`, `.rodata`, `.ethin`, `.filebuf`, `.data` and the entry point byte
for byte. The sizes below predate this image: it has `.text 111,652 / .rodata 23,248 /
.ethin 2,048 / .filebuf 4,096 / .data 20`.

`eth.elf`: .text 97,348 / 262,144 · .rodata 20,792 / 131,072 · .ethin 2,048 ·
.data 20 · .bss 12.
`eth-bridge.elf`: .text 102,404 / 262,144 · .rodata 20,864 / 131,072 ·
.data 20 · .bss 1,292.
(.rodata grew ~15 KB with the 225-entry selector DROM table.)

## Oracle independence and malformed input

One flipped bit in a keccak round constant, built from a scratch copy, must fail gate 1 while the unflipped build passes. `scripts/oracle_independence.sh` does this; log `docs/logs/oracle-independence.log`. Over 40 cases per command the flipped build fails keccak 40, create 37 and selector 15. The cases it does not fail do not exercise the hash: 25 selectors come from the precomputed table, and 3 creates are bad-nonce errors. The table is therefore checked only by this gate's comparison with the Python oracle.

`tests/test_rv_errors.py` sends malformed input to every command, log `docs/logs/malformed-input.log`. No input crashes, hangs or prints non-text. Six forms answer instead of giving an error, and the oracle agrees with each: bare `keccak`, a `u256` literal above 2**256 which wraps silently, `selector` without a full signature (three forms), and a short `bytes32` in `encode`. They are strict expected failures until the next firmware change; the ELF stays frozen through the evals.

`chipformer/rv/eth.py` now exits 1 on a mismatch and takes `--elf`. Before this it exited 0 on `GATE 1 FAIL`, so read the printed verdict on any earlier log.
