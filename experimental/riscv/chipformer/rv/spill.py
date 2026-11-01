"""Large tool arguments: the host moves them out of the 80-token window (api-spec-clean, Spill).

A call over the window has each 0x-hex argument over ARG_TOKENS tokens replaced by ``@spill/<n>``
and its bytes written to the firmware's file buffer. The firmware's ``file_arg`` resolves the name.
The rewritten command itself goes to ``eth_in``: generation restarts from a snapshot, which clears
the circuit's capture, so the firmware reads the command from RAM and not from the captured text.
One implementation, used standalone and in the worker. Knows nothing about HTTP.
"""

import re
import struct
import tempfile
from pathlib import Path

from chipformer.rv import bridge as BR
from chipformer.rv import elf, ref

WINDOW = 80  # S.CAP_MAX: tokens the bridge captures between <tool_call> and </tool_call>
ARG_TOKENS = 32
FILE_BUF, FILE_BUF_SIZE = 0x900, 4096  # .filebuf in eth-bridge.elf (env/eth/eth.c)
ETH_IN, ETH_IN_SIZE = 0x100, 2048  # eth_in: a command here wins over the captured call, once
CMD = re.compile(r"<function=eth>\s*<parameter=cmd>\n?(.*?)\n?</parameter>\s*</function>", re.DOTALL)
HEX = re.compile(r"0x[0-9a-fA-F]+\Z")


def call_text(cmd: str) -> str:
    """What the model writes between <tool_call> and </tool_call>, newlines included."""
    return f"\n<function=eth>\n<parameter=cmd>\n{cmd}\n</parameter>\n</function>\n"


def parse_cmd(text: str) -> str | None:
    m = CMD.search(text)
    return m.group(1) if m else None


def rewrite(cmd: str, count) -> tuple[str, dict[str, str]]:
    """(cmd with long hex arguments as @spill/<n>, {name: hex}). ``count(s)`` = Qwen tokens in s.

    Non-hex arguments stay in the call, so the firmware answers ``error (too long)`` for them
    rather than the host guessing an encoding."""
    files: dict[str, str] = {}
    out = []
    for arg in cmd.split(" "):
        if HEX.match(arg) and count(arg) > ARG_TOKENS:
            name = f"spill/{len(files)}"
            files[name] = arg
            arg = "@" + name
        out.append(arg)
    return " ".join(out), files


def file_blob(files: dict[str, str]) -> bytes:
    """name\\0hex\\0 pairs, double-NUL terminated. ValueError if over the buffer."""
    blob = b"".join(k.encode() + b"\0" + v.encode() + b"\0" for k, v in files.items()) + b"\0"
    if len(blob) > FILE_BUF_SIZE:
        raise ValueError(f"spill: {len(blob)} bytes over the {FILE_BUF_SIZE} byte file buffer")
    return blob


def firmware_with_files(firmware, files: dict[str, str], cmd: str | None = None) -> Path:
    """A temporary copy of the ELF with ``files`` in .filebuf and ``cmd`` in eth_in, so generate,
    the lockstep reference and replay all load the same machine through the ordinary loader.
    ``firmware`` itself when there is nothing to write."""
    if not files and cmd is None:
        return Path(firmware)
    data = bytearray(Path(firmware).read_bytes())
    assert data[:5] == b"\x7fELF\x01", "RV32 ELF expected"
    if files:
        _write(data, firmware, FILE_BUF, FILE_BUF_SIZE, file_blob(files))
    if cmd is not None:
        b = cmd.encode() + b"\0"
        if len(b) > ETH_IN_SIZE:
            raise ValueError(f"spill: {len(b)} byte command over the {ETH_IN_SIZE} byte eth_in")
        _write(data, firmware, ETH_IN, ETH_IN_SIZE, b)
    f = tempfile.NamedTemporaryFile(prefix="eth-spill-", suffix=".elf", delete=False)
    f.write(data)
    f.close()
    return Path(f.name)


def _write(data: bytearray, firmware, addr: int, size: int, blob: bytes) -> None:
    phoff, phentsize, phnum = (struct.unpack_from("<I", data, 28)[0],
                               struct.unpack_from("<H", data, 42)[0],
                               struct.unpack_from("<H", data, 44)[0])
    for i in range(phnum):
        kind, off, vaddr, _, filesz = struct.unpack_from("<5I", data, phoff + i * phentsize)
        if kind == 1 and vaddr <= addr and addr + size <= vaddr + filesz:
            at = off + addr - vaddr
            data[at:at + len(blob)] = blob
            return
    raise ValueError(f"{firmware} has no loaded buffer at {addr:#x}")


def parked(firmware) -> "ref.Machine":
    m = ref.Machine.from_program(elf.program(str(firmware)))
    BR.park(m)
    return m
