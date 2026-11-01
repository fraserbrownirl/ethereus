"""merge.output_head: an untied base keeps its own output head on Qwen's rows and dims."""

import torch

from chipformer.rv.merge import output_head


def _emb():
    torch.manual_seed(0)
    return torch.randn(8, 6)  # 8 rows (5 Qwen, 3 chip), 6 dims (4 Qwen, 2 chip)


def test_tied_base_stays_tied():
    emb = _emb()
    assert output_head(emb, None, 5, 4) is None
    assert output_head(emb, emb[:5, :4].clone(), 5, 4) is None


def test_untied_base_keeps_its_head():
    emb, base = _emb(), torch.ones(5, 4)
    head = output_head(emb, base, 5, 4)
    assert torch.equal(head[:5, :4], base)
    assert torch.equal(head[:, 4:], emb[:, 4:])  # output code and tau columns
    assert torch.equal(head[5:], emb[5:])  # RV32 token rows
    assert not torch.equal(emb[:5, :4], base)  # the embedding itself is untouched
