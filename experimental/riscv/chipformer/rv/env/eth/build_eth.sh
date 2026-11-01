#!/bin/bash
# Build the eth firmware:  build_eth.sh OUT [NAME] [--bridge]
# OUT/eth.elf (default): non-bridge gate-1 image; the NUL-terminated cmd line in
#   eth_in at RAM 0x100 (like calc_in in calc.c), value + "\n" on stdout.
# OUT/eth-bridge.elf (--bridge): the P3 bridge image; reads the tool call's text
#   from the TEXT buffer and prints the <tool_response>.. trailer.
# libc is picolibc rv32im/ilp32 (toolchain/picolibc-rv32im). Newlib must never
# be linked: the author's gate-1 parity was established with picolibc's
# printf/memcpy code paths, and newlib differs in code and size.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ENV=$HERE/..
OUT=$(realpath "$1"); NAME=${2:-eth}
BRIDGE=0
for a in "$@"; do if [ "$a" = "--bridge" ]; then BRIDGE=1; fi; done
if [ "$NAME" = "--bridge" ]; then NAME=eth-bridge; fi
# Durable single-target picolibc (meson -Dmultilib=false, rv32im/ilp32).
# Override with PICO=/path for a scratch rebuild; never point at newlib.
PICO=${PICO:-$(realpath "$HERE/../../../../toolchain/picolibc-rv32im")}
if [ ! -f "$PICO/lib/libc.a" ]; then
  echo "FATAL: picolibc libc.a not found at $PICO/lib/libc.a" >&2
  echo "Rebuild with: experimental/riscv/toolchain/build_picolibc.sh" >&2
  exit 1
fi
if riscv64-elf-nm "$PICO/lib/libc.a" 2>/dev/null | grep -qi newlib; then
  echo "FATAL: $PICO/lib/libc.a contains newlib symbols; refusing to build" >&2
  exit 1
fi
GCC=riscv64-elf-gcc
if ! command -v $GCC >/dev/null 2>&1; then GCC=riscv64-unknown-elf-gcc; fi
MARCH=rv32im_zba
LIBGCC=$($GCC -march=rv32im -mabi=ilp32 -print-libgcc-file-name 2>/dev/null || echo "")
B=$(mktemp -d)
python3 "$HERE/../calc/gen_sled.py" "$B/sled.S"
# gen_selectors needs eth_hash: invoke with PYTHON set to the project venv
# (e.g. PYTHON=.venv/bin/python build_eth.sh ...), else any python3 with deps.
PYBIN=${PYTHON:-python3}
"$PYBIN" -c "import eth_hash" 2>/dev/null || { echo "FATAL: $PYBIN lacks eth_hash; set PYTHON=.venv/bin/python" >&2; exit 1; }
"$PYBIN" "$HERE/gen_selectors.py" "$HERE/selectors.txt" "$B/selectors.h"
"$PYBIN" -c "import solcx" 2>/dev/null || { echo "FATAL: $PYBIN lacks py-solc-x; set PYTHON=.venv/bin/python" >&2; exit 1; }
"$PYBIN" "$HERE/gen_builtins.py" "$HERE/contracts" "$B" || exit 1
if [ "$BRIDGE" = 1 ]; then DEFS="-DBRIDGE"; else DEFS=""; fi
# eth_in first in RAM (0x100), like .calcin in build_calc.sh; file_buf right
# after (0x900), both host-poked inputs outside the token window. Same layout
# in both images; the bridge build reads TEXT and leaves eth_in unused.
sed 's/  .data : {/  .ethin : { *(.ethin) } > RAM\n  .filebuf : { *(.filebuf) } > RAM\n  .data : {/' "$ENV/rom.ld" > "$B/eth.ld"
LD="$B/eth.ld"
# shellcheck disable=SC2086
$GCC -march=$MARCH -mabi=ilp32 -O2 ${EXTRA_CFLAGS:-} $DEFS -isystem "$PICO/include" -I"$ENV" -I"$HERE" -I"$HERE/xkcp" -I"$B" \
  -nostdlib -nostartfiles -static -T"$LD" -o "$OUT/$NAME.elf" \
  "$ENV/crt0.S" "$HERE/eth.c" "$HERE/u256.c" "$HERE/keccak.c" "$HERE/xkcp/KeccakP-1600-inplace32BI.c" "$HERE/abi.c" "$HERE/evm.c" "$HERE/builtins.c" "$ENV/sys.c" "$B/sled.S" \
  -Wl,--start-group "$PICO/lib/libc.a" "$PICO/lib/libsemihost.a" "$PICO/lib/libnosys.a" ${LIBGCC:+"$LIBGCC"} -Wl,--end-group
NM=${GCC%-gcc}-nm; command -v "$NM" >/dev/null 2>&1 || NM=riscv64-unknown-elf-nm
if "$NM" "$OUT/$NAME.elf" 2>/dev/null | grep -qi newlib; then
  echo "FATAL: $OUT/$NAME.elf contains newlib symbols" >&2
  exit 1
fi
"$NM" "$OUT/$NAME.elf" 2>/dev/null | grep -E ' eth_in$' || { echo "FATAL: eth_in missing"; exit 1; }
"$NM" "$OUT/$NAME.elf" 2>/dev/null | grep -E ' file_buf$' || { echo "FATAL: file_buf missing"; exit 1; }
SIZE=${GCC%-gcc}-size; command -v "$SIZE" >/dev/null 2>&1 || SIZE=riscv64-unknown-elf-size
"$SIZE" -A "$OUT/$NAME.elf" 2>/dev/null | grep -E '^\.(text|rodata|ethin|filebuf|data|bss)' || echo "size check skipped"
cp "$B/builtins.gen.json" "$OUT/$NAME.builtins.json"  # oracle's exact-input record
rm -rf "$B"
