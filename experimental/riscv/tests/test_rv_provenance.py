"""The provenance check has to catch what it claims to: a changed byte, a changed or untracked file."""

import json
import shutil
import sys
from pathlib import Path

RV = Path(__file__).parents[1]
sys.path.insert(0, str(RV / "scripts"))
import provenance as P  # noqa: E402


def test_same_image_has_no_differences():
    assert P.loaded_differences(P.DATA / "eth.elf", P.DATA / "eth.elf") == []


def test_one_flipped_loaded_byte_is_caught(tmp_path):
    bad = tmp_path / "eth.elf"
    shutil.copy(P.DATA / "eth.elf", bad)
    _, segs = P.elfmod.segments(bad)
    raw = bytearray(bad.read_bytes())
    i = raw.find(segs[-1][1][:64]) + 32
    raw[i] ^= 1
    bad.write_bytes(raw)
    assert P.loaded_differences(P.DATA / "eth.elf", bad)


def test_different_images_differ():
    assert P.loaded_differences(P.DATA / "eth.elf", P.DATA / "eth-bridge.elf")


def test_hash_list_catches_change_and_absence(tmp_path, monkeypatch):
    m = tmp_path / "PROVENANCE.json"
    good = json.loads(P.MANIFEST.read_text())["author"]
    first = sorted(good)[0]
    changed = dict(good, **{first: "0" * 64})
    m.write_text(json.dumps({"author": changed}))
    monkeypatch.setattr(P, "MANIFEST", m)
    assert any(first in b for b in P.check_hashes())
    missing = dict(good)
    missing.pop(first)
    m.write_text(json.dumps({"author": missing}))
    assert any("no recorded hash" in b for b in P.check_hashes())
