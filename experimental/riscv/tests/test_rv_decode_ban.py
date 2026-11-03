"""Qwen never picks <|im_start|> or <|endoftext|>, as the Qwenputer author's generation_config.json bans them, and
<|im_end|> is the only end of turn (Fraser, 2026-11-03: added after the Step 3 arms, so every arm ran one server)."""
from types import SimpleNamespace

import torch

from chipformer.rv.chat import RvChat

IM_START, ENDOFTEXT, IM_END = 248045, 248044, 248046


def test_banned_tokens_are_never_picked():
    me = SimpleNamespace(v0=248320, banned=(IM_START, ENDOFTEXT))
    for tok in (IM_START, ENDOFTEXT):
        logits = torch.zeros(248320 + 10)
        logits[tok] = 100.0
        logits[IM_END] = 1.0
        assert RvChat._qwen_pick(me, logits) == IM_END


def test_pick_ignores_chip_vocabulary_and_does_not_touch_the_logits():
    me = SimpleNamespace(v0=248320, banned=(IM_START, ENDOFTEXT))
    logits = torch.zeros(248320 + 10)
    logits[248320 + 3] = 50.0   # a chip token: never Qwen's pick
    logits[42] = 2.0
    before = logits.clone()
    assert RvChat._qwen_pick(me, logits) == 42
    assert torch.equal(logits, before)
