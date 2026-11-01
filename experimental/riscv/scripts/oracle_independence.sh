#!/usr/bin/env bash
# Rule 1, docs/dispatcher-rules.md: one flipped keccak round-constant bit must fail gate 1; the same build unflipped must pass.
# Works on a scratch copy; the tracked firmware is never touched. Run from experimental/riscv.
set -u
RV=$PWD
W=$(mktemp -d)
trap 'rm -rf "$W"' EXIT
export PICO=$RV/toolchain/picolibc-rv32im PYTHON=$RV/.venv/bin/python
cp -R chipformer/rv/env "$W/env"
SRC=$W/env/eth/xkcp/KeccakP-1600-inplace32BI.c
grep -q '0x00000000UL,    0x00000089UL,' "$SRC" || { echo "ORACLE FAIL: constant not found"; exit 2; }
bash chipformer/rv/env/eth/build_eth.sh "$W" control >/dev/null 2>&1 || { echo "ORACLE FAIL: control build"; exit 2; }
sed -i.bak 's/0x00000000UL,    0x00000089UL,/0x00000000UL,    0x00000088UL,/' "$SRC"
cmp -s "$SRC" "$SRC.bak" && { echo "ORACLE FAIL: flip did not apply"; exit 2; }
bash "$W/env/eth/build_eth.sh" "$W" flipped >/dev/null 2>&1 || { echo "ORACLE FAIL: flipped build"; exit 2; }
shasum -a 256 "$W/control.elf" "$W/flipped.elf" | awk '{print $1}' | uniq | wc -l | grep -q 2 || { echo "ORACLE FAIL: images identical"; exit 2; }
for op in keccak selector create; do
  "$PYTHON" -m chipformer.rv.eth --cases 40 --seed 11 --only $op --elf "$W/control.elf" 2>/dev/null | grep -E '^GATE 1' | cut -c1-60 | sed "s/^/control $op: /"
  [ "${PIPESTATUS[0]}" = 0 ] || { echo "ORACLE FAIL: control $op failed"; exit 1; }
  "$PYTHON" -m chipformer.rv.eth --cases 40 --seed 11 --only $op --elf "$W/flipped.elf" 2>/dev/null | grep -E '^GATE 1' | cut -c1-60 | sed "s/^/flipped $op: /"
  [ "${PIPESTATUS[0]}" = 1 ] || { echo "ORACLE FAIL: flipped $op was not caught"; exit 1; }
done
echo "ORACLE PASS"
