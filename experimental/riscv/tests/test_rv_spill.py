"""spill.py on the bridge image and the reference machine: over-window calls answer exactly."""

from pathlib import Path

import pytest
from eth_abi import decode as abi_decode
from eth_hash.auto import keccak

from chipformer.rv import bridge as BR
from chipformer.rv import spill

ELF = Path(__file__).parent / "data/rv/eth-bridge.elf"


@pytest.fixture(scope="module")
def T():
    try:
        return BR.Text(BR.default_tokenizer())
    except Exception:
        pytest.skip("no Qwen3.5 tokenizer in the HF cache")


def count(T, s):
    return len(T.tok.encode(s, add_special_tokens=False).ids)


def ask(T, cmd, files=None):
    fw = spill.firmware_with_files(ELF, files or {})
    s = BR.Session(T, spill.parked(fw))
    s.qwen([T.TCO] + T.tok.encode(spill.call_text(cmd), add_special_tokens=False).ids + [T.TCC])
    return T.tok.decode(s.runs[-1], skip_special_tokens=False).split("<tool_response>\n")[1]


def test_short_call_is_unchanged(T):
    cmd = "selector transfer(address,uint256)"
    assert spill.rewrite(cmd, lambda s: count(T, s)) == (cmd, {})
    assert spill.firmware_with_files(ELF, {}) == ELF
    assert spill.firmware_with_files(ELF, {}, None) == ELF


def run_calls(T, fw, *calls):
    """Each call as the circuit sees it: captured tokens or nothing, then a run."""
    s = BR.Session(T, spill.parked(fw))
    out = []
    for c in calls:
        s.qwen([T.TCO] + (T.tok.encode(spill.call_text(c), add_special_tokens=False).ids if c else []) + [T.TCC])
        out.append(T.tok.decode(s.runs[-1], skip_special_tokens=False).split("<tool_response>\n")[1].split("\n</tool_response>")[0])
    return out


def test_eth_in_answers_when_nothing_was_captured(T):
    """The GPU case: a snapshot restart clears the capture, so the circuit sees an empty window."""
    fw = spill.firmware_with_files(ELF, {}, "selector transfer(address,uint256)")
    assert run_calls(T, fw, "") == ["0xa9059cbb"]


def test_eth_in_wins_over_the_capture_and_is_used_once(T):
    fw = spill.firmware_with_files(ELF, {}, "selector transfer(address,uint256)")
    assert run_calls(T, fw, "selector approve(address,uint256)", "selector approve(address,uint256)") == ["0xa9059cbb", "0x095ea7b3"]


def test_spilled_command_and_files_answer_on_empty_capture(T):
    data = bytes(range(256)) * 3
    new, files = spill.rewrite("keccak 0x" + data.hex(), lambda s: count(T, s))
    fw = spill.firmware_with_files(ELF, files, new)
    assert run_calls(T, fw, "") == ["0x" + keccak(data).hex()]


def test_no_eth_in_over_window_still_answers_too_long(T):
    assert "error (too long)" in run_calls(T, ELF, "keccak " + "x" * 2000)[0]


def test_over_eth_in_is_refused():
    with pytest.raises(ValueError):
        spill.firmware_with_files(ELF, {}, "k" * 2048)


def test_parse_cmd_round_trip():
    assert spill.parse_cmd(spill.call_text("keccak 0x6161")) == "keccak 0x6161"
    assert spill.parse_cmd("<function=bash>\n<parameter=cmd>\nls\n</parameter>\n</function>") is None


def test_long_keccak_spills_and_answers_exactly(T):
    data = bytes(range(256)) * 3
    cmd = "keccak 0x" + data.hex()
    assert count(T, spill.call_text(cmd)) > spill.WINDOW
    new, files = spill.rewrite(cmd, lambda s: count(T, s))
    assert new == "keccak @spill/0" and count(T, spill.call_text(new)) <= spill.WINDOW
    assert ask(T, new, files).startswith("0x" + keccak(data).hex())


def test_decode_ten_words_spills(T):
    """The API spec's spill test: eth decode with 10 words of calldata."""
    vals = [i * 7 + 1 for i in range(10)]
    hexs = "0x" + "".join(f"{v:064x}" for v in vals)
    types = ",".join(["uint256"] * 10)
    new, files = spill.rewrite(f"decode {types} {hexs}", lambda s: count(T, s))
    assert new == f"decode {types} @spill/0"
    out = ask(T, new, files).split("\n</tool_response>")[0].split("\n")
    assert [int(x, 0) for x in out] == list(abi_decode(["uint256"] * 10, bytes.fromhex(hexs[2:])))


def test_over_buffer_is_refused():
    with pytest.raises(ValueError):
        spill.file_blob({"a": "0" * 5000})


def test_non_hex_stays_in_the_call(T):
    long = "x" * 2000
    assert spill.rewrite(f"keccak {long}", lambda s: count(T, s)) == (f"keccak {long}", {})
