#!/bin/bash
# Rebuild the single-target picolibc used by env/eth/build_eth.sh.
# Recipe (matches the build it replaces; ~10 min):
#   picolibc 1.8.12 @ b5d6050414d682effd3f2ecf7f536073e37e72a3
#   meson setup -Dmultilib=false -Dtests=false --cross-file=toolchain/picolibc-cross-rv32im.txt
#   cross gcc: riscv64-elf-gcc -march=rv32im -mabi=ilp32
# Usage: toolchain/build_picolibc.sh [PREFIX=toolchain/picolibc-rv32im]
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
PREFIX=${1:-$HERE/picolibc-rv32im}
SRC=${PICOLIBC_SRC:-/tmp/picolibc-src}
if [ ! -d "$SRC" ]; then
  git clone --depth 1 https://github.com/picolibc/picolibc.git "$SRC"
  git -C "$SRC" fetch --depth 1 origin b5d6050414d682effd3f2ecf7f536073e37e72a3 2>/dev/null || true
fi
B=$(mktemp -d)
meson setup "$SRC" "$B" -Dmultilib=false -Dtests=false \
  --cross-file="$HERE/picolibc-cross-rv32im.txt" --prefix="$PREFIX"
ninja -C "$B"
ninja -C "$B" install
riscv64-elf-objdump -a "$PREFIX/lib/libc.a" | head -5
riscv64-elf-readelf -A "$PREFIX/lib/crt0.o" | grep -m1 Tag_RISCV_arch
rm -rf "$B"
echo "picolibc installed to $PREFIX"
