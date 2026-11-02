"""A device booted from a pack answers a get with the file, so replay can check a loaded snapshot."""

import json
from pathlib import Path

import pytest

from chipformer.rv import bridge as BR
from chipformer.rv import spill
from chipformer.rv import store as ST

RV = Path(__file__).parents[1]


def test_device_from_manifest_serves_the_files():
    try:
        T = BR.Text(BR.default_tokenizer())
    except Exception:
        pytest.skip("no Qwen3.5 tokenizer in the HF cache")
    man = json.loads((RV / "build/ethskills.manifest.json").read_text())
    fw = RV / "tests/data/rv/eth-bridge.elf"
    dev = ST.device_from_manifest(T, man, ST.Config(), spill.parked(fw))
    s = ST.StoreSession(T, spill.parked(fw), ST.Config(), dev)
    s.qwen(T.encode("<|im_start|>assistant\nHi.\n"))
    n = len(s.toks)
    s.call("get", T.encode("es/blocks.md"))
    text = T.tok.decode([t - BR.text_token(0) for t in s.toks[n:] if t >= BR.text_token(0)], skip_special_tokens=False)
    assert "# Verify DeFi Integrations" in text
