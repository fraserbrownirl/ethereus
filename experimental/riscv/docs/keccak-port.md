# Keccak-f[1600] port: Ko-/riscvcrypto into eth firmware (assessment, branch `keccak-port`)

Status: working prototype, gate 1 passes, not merged. The tracked images in
`tests/data/rv/` are unchanged on this branch (`eth-bridge.elf` 7c4175e6, the frozen
eval ELF; `eth.elf` 7dcd8933). The new images live in `build/keccak-port/`.

Short version: one permutation drops from 19,469 to 12,736 retired instructions (-34.6%).
A short `keccak` call drops 28.5%. Gate 1 has 0 mismatches over 17,148 cases. `.text`
grows by 8.8 KB.

## ISA found

The core is **RV32IM + Zba**, plus a custom bignum ("digit") coprocessor. There is no Zbb, Zbkb
or other rotate instruction.
- `chipformer/rv/ref.py` line 1: "Reference RV32IM + Zba", with `ZBA = {SH1ADD, SH2ADD, SH3ADD}`.
- `chipformer/rv/core.py` line 1: "The RV32IM + Zba core as a q35 circuit".
- `README.md`: rv32ui / rv32um / rv32uzba riscv-tests pass.
- `env/eth/build_eth.sh`: `-march=rv32im_zba -mabi=ilp32 -O2`, riscv64-elf-gcc 16.2.0, picolibc rv32im.

So the right variant is riscvcrypto's **plain RV32I** `keccakf1600_asm.S`, which is the only
Keccak in that repo. It uses only lw/sw/xor/and/or/not/slli/srli/addi/bge/la and builds each
rotate from srli+slli+xor. It needs no bit-manipulation extension.

## Licence and upstream

- Repo: https://github.com/Ko-/riscvcrypto @ `0d99785a8cfdf12e7bccdcf8998034f0e66113c1`
  (2019-07-02, "Initial upload", the only commit).
- Licence: **CC0 1.0 Universal** (root `LICENSE`, copied verbatim to `env/eth/riscvcrypto/LICENSE`).
  This is allowed. The file also credits XKCP's ARMv7-M in-place 32bi code (XKCP is CC0 too).
- Vendored: `keccakf1600/keccakf1600_asm.S` → `env/eth/riscvcrypto/keccakf1600_asm.S`, with
  **one change**: line 429 `.data` → `.section .rodata`. This puts the 24-entry round-constant table in
  the DROM, not the 32 KB RAM. The code itself is verbatim. Provenance is in `env/eth/riscvcrypto/README`.

## What changed (all on `keccak-port`)

| file | change |
| --- | --- |
| `env/eth/riscvcrypto/{keccakf1600_asm.S,LICENSE,README}` | new, vendored as above |
| `env/eth/keccak.c` | `PERMUTE()` calls `keccakf1600(state.A)` on `__riscv`. Host builds (`tests/test_eth_host.py`, ASan) keep XKCP's C `KeccakP1600_Permute_24rounds` |
| `env/eth/build_eth.sh` | adds `riscvcrypto/keccakf1600_asm.S` to the link |
| `scripts/oracle_independence.sh` | the bit-flip now targets the asm's RC table (XKCP's table is dead code in the firmware now). `PYTHON` can be overridden |
| `keccak.h` | unchanged (`void keccak256(const uint8_t*, size_t, uint8_t[32])`) |

The state layout is the same, so XKCP's `AddBytes`/`ExtractBytes`/`Initialize` are reused
unchanged. That layout is 50 × u32, with lane i at words 2i (even bits) and 2i+1 (odd bits).
The asm complements the lanes on entry and undoes it on exit, so the state it takes and
returns is the plain interleaved one.

## Instruction counts (reference machine, `eth.elf`, `keccak 0x<N bytes>`)

Command (run from `experimental/riscv` in the worktree, `PY=/Users/fraserbrown/Dev/ethereus-phase2/experimental/riscv/.venv/bin/python`):

    PYTHONPATH=. $PY build/keccak-port/tools/kmeasure.py <ELF>

The script steps `chipformer.rv.ref.Machine` record by record and asserts that `trace_stats`
instructions equal `Machine.steps`. It counts retired instructions over three spans: the whole
command, `keccak256()` from entry to return (including callees), and the permutation function
only. Every output is checked against `eth_hash`. Logs: `docs/logs/keccak-port/measure_base.log`
(before: the baseline rebuilt from main's sources, loaded bytes identical to tracked `eth.elf`;
`measure_before_committed.log` on tracked `eth.elf` gives the same numbers) and
`docs/logs/keccak-port/measure_new.log` (after).

| input | perms | whole cmd before | after | Δ | keccak256() before | after | Δ |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 B | 1 | 23,595 | 16,862 | -28.5% | 22,661 | 15,928 | -29.7% |
| 32 B | 1 | 25,842 | 19,109 | -26.1% | 22,861 | 16,128 | -29.5% |
| 64 B | 1 | 28,086 | 21,353 | -24.0% | 23,053 | 16,320 | -29.2% |
| 136 B | 2 | 53,250 | 39,784 | -25.3% | 43,586 | 30,120 | -30.9% |
| 1,000 B | 8 | 234,732 | 180,868 | -22.9% | 169,444 | 115,580 | -31.8% |

The permutation alone goes from **19,469 to 12,736 instructions** (-6,733, -34.6%). The
eth-bench target was ≤12,000, so this is close but does not reach it. The rest of `keccak256()` is
about 3.2k per call (absorb interleaving, memset/memcpy of the pad block, extract) and is
unchanged. That is the next lever if more is needed.

Gate-1 averages over the same 2,000-case draws (seed 0 / seed 7, logs `gate1_{base,new}_*.log`):

| op | instr/call before | after | tokens/call before | after |
| --- | --- | --- | --- | --- |
| keccak | 24,044 | 17,344 (-27.9%) | 51,672 | 38,122 (-26.2%) |
| selector (mostly cache misses) | 13,245 | 9,905 (-25.2%) | 27,774 | 20,947 |
| encode | 30,574 | 23,841 (-22.0%) | 60,637 | 46,858 |
| create | 24,254 | 17,949 (-26.0%) | 51,560 | 38,634 |
| u256, decode | unchanged | unchanged | | |

EVM (`test_rv_evm` cases, `PYTHONPATH=. $PY build/keccak-port/tools/evmcount.py <base eth.elf> <new eth.elf> tests`,
log `evm_counts.log`): balanceOf 69,627 → 62,894 (-9.7%), transfer 127,765 → 114,299 (-10.5%),
approve 114,830 → 101,364 (-11.7%), allowance 107,987 → 94,521 (-12.5%). totalSupply, decimals and
the AMM calls do not hash, so their counts are unchanged. Outputs are identical in every case.
The bridge image (`eth-bridge.elf`) links the same objects, so its per-permutation count is the
same by construction. It was **not measured** directly; the bridge path was only run through the
pytest suite.

## Code size (`riscv64-elf-size -A`, `riscv64-elf-nm -S`; logs `build_*.log`, `codesize.log`)

| | before | after | Δ |
| --- | --- | --- | --- |
| `eth.elf` .text | 107,036 | 115,824 | +8,788 |
| `eth.elf` .rodata | 23,200 | 23,392 | +192 |
| `eth-bridge.elf` .text | 112,524 | 121,312 | +8,788 |
| `eth-bridge.elf` .rodata | 23,264 | 23,464 | +200 |
| RAM (.data/.bss) | — | — | 0 |

- `keccakf1600` is 8,788 B (0x2254), with 4 unrolled rounds × 6 loop iterations. `keccakf1600_rc24`
  is 232 B of rodata.
- XKCP `KeccakP1600_Permute_Nrounds` (15,308 B) and its 196 B RC table are now **dead code** but
  are still linked, because the build does not use `--gc-sections`. Dropping them would make
  .text about 6.5 KB *smaller* than before. That needs `-ffunction-sections -Wl,--gc-sections`,
  or splitting the XKCP file, and was left out of the prototype to keep the rest of the image
  layout unchanged. ROM use is 115.8 KB of 256 KB.

## Exactness

| check | command | result | log |
| --- | --- | --- | --- |
| Gate 1 keccak + u256 (+ unary slice 5,652) | `$PY -m chipformer.rv.eth --cases 2000 --only keccak,u256 --elf <new eth.elf>` | **PASS, 9,652 cases, 0 mismatches** | `docs/logs/keccak-port/gate1_new_keccak_u256.log` |
| Gate 1 ABI (+ boundary slice 576) | `$PY -m chipformer.rv.eth --cases 2000 --seed 7 --only selector,encode,decode,create,create2 --elf <new eth.elf>` | **PASS, 7,496 cases, 0 mismatches** | `docs/logs/keccak-port/gate1_new_abi.log` |
| Same two gates on the rebuilt baseline | same commands, `--elf <base eth.elf>` | PASS / PASS | `gate1_base_*.log` |
| Keccak vectors | `PYTHONPATH=. $PY build/keccak-port/tools/kvectors.py <ELF>` | **0 failures**: empty = `c5d24601…5d85a470` (both quoting forms), "abc" = `4e03657a…a12d6c45` (published), 1/31/32/33/135/136/137/271/272/273/544/1000 B vs eth_hash, and 1,100 B (9 blocks, firmware max) via `@file` | `vectors_new.log` |
| Oracle independence | `PYTHON=$PY bash scripts/oracle_independence.sh` | **ORACLE PASS**: control passes. A flipped RC bit fails keccak 40/40, selector 15/40 and create 37/40, the same as the XKCP-era record | `oracle_independence_new.log` |
| Firmware pytest suites on new ELFs | `NEW_ELF_DIR=build/keccak-port PYTHONPATH=build/keccak-port/tools:. $PY -m pytest -p newelf_plugin tests/test_rv_eth.py tests/test_rv_evm.py tests/test_rv_errors.py tests/test_rv_filebuf.py tests/test_rv_spill.py tests/test_rv_eth_alias.py tests/test_eth_host.py` | **341 passed** (EVM vs py-evm, bridge smoke, malformed input, spill, filebuf, host ASan) | `pytest_new.log` |

The gates ran on a scratch build. Its loaded bytes are identical to `build/keccak-port/*.elf`:
the loaded-image digests are `eth.elf` 46746544…, `eth-bridge.elf` f4a7b47a… (`loaded_digests.log`).
File sha256 changes on every build because of debug paths. The committed files are
`eth.elf` ec819d2f…, `eth-bridge.elf` c097f335….
qemu cross-check was **not run** (no `qemu-riscv32` linux-user on macOS, same as before).

## What else this would touch if adopted

Nothing below was done. All of it is required before the new firmware could serve:

1. **ELF hashes change.** `eth-bridge.elf` would replace 7c4175e6 and `eth.elf` would replace
   7dcd8933. `scripts/provenance.py` on this branch reports PROVENANCE FAIL, because the
   rebuild no longer matches the tracked images (`provenance_branch.log`). Adoption means
   committing new `tests/data/rv/eth*.elf` and recording them in `docs/eth-bench.md`.
2. **File-store snapshot.** `build/ethskills.fs.manifest.json` pins `firmware_sha256` 7c4175e6
   (snapshot c2f24eca). It has to be rebuilt with `scripts/boot_fs.py --mode snap` against the
   new bridge ELF and checked with `--mode verify` (GPU). The ROM/DROM contents differ, so the
   snapshot will not stay byte-identical the way it did after the unary fix.
3. **Fine-tune data and checkpoints.** `data/ft1/manifest.json` records the firmware. The FT
   traces were generated with the old image (`scripts/gen_ft_data.py` uses `eth-bridge.elf`),
   so keccak traces now have different lengths and contents. Whether the trained model still
   drives the new firmware has to be proven by gate 4 / canary on GPU.
4. **Gates to rerun:** gate 1 on the tracked images (done here on the build/ copies), gate 4,
   canary, snapshot verify, the spill test, and any ETH Evals arm that should use the faster
   firmware. Running evals stay on 7c4175e6.
5. `oracle_independence.sh` must change along with the port. On main's version the flip hits
   XKCP's now-dead table, so the flipped build would still pass and the script would report
   ORACLE FAIL.
