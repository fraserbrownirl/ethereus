"""The fine-tune trainer on tiny models: what gets loss, what can change, and that apply touches only Qwen's block."""

import json
import sys
from argparse import Namespace
from pathlib import Path

import pytest
import torch
from safetensors.torch import load_file, save_file
from torch import nn

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import train_ft as F  # noqa: E402


def seg(role, train, ids):
    return {"role": role, "train": train, "ids": ids}


def test_loss_only_on_generated_tokens():
    ids, lab = F.sequence([seg("prompt", False, [1, 2]), seg("assistant_call", True, [3]), seg("tool_output", False, [4, 5]), seg("assistant_final", True, [6])])
    assert ids == [1, 2, 3, 4, 5, 6]
    assert lab == [-100, -100, 3, -100, -100, 6]


def test_spilled_row_is_two_sequences():
    row = {"segments": [seg("prompt", False, [1]), seg("spill_prefill", False, [2]), seg("assistant_call", True, [3])],
           "call_segments": [seg("prompt", False, [1]), seg("assistant_call", True, [9, 9, 3])]}
    ex = F.examples([row, {"segments": row["segments"]}])
    assert len(ex) == 3 and ex[1] == ([1, 9, 9, 3], [-100, 9, 9, 3])


def test_lora_starts_at_identity_and_its_delta_equals_the_wrapped_forward():
    base = nn.Linear(6, 4, bias=False)
    m = F.Lora(base, rank=2)
    x = torch.randn(3, 6)
    assert torch.equal(m(x), base(x))
    nn.init.normal_(m.B)
    want = base.weight.detach() + m.delta()
    assert torch.allclose(m(x), x @ want.T, atol=1e-5)


def test_wrap_freezes_all_but_the_lora():
    class Block(nn.Module):
        def __init__(self):
            super().__init__()
            self.up_proj, self.norm, self.other = nn.Linear(4, 4), nn.LayerNorm(4), nn.Linear(4, 4)

    b = nn.Sequential(Block())
    loras = F.wrap(b)
    assert set(loras) == {"0.up_proj"} and isinstance(b[0].up_proj, F.Lora) and isinstance(b[0].other, nn.Linear)
    assert {n for n, p in b.named_parameters() if p.requires_grad} == {"0.up_proj.A", "0.up_proj.B"}


@pytest.fixture
def ckpt(tmp_path):
    """A wide merged checkpoint: 2 chip layers, one Qwen layer holding a 4x6 tensor in a wider 6x8 block."""
    d = tmp_path / "merged"
    d.mkdir()
    (d / "chipformer.json").write_text(json.dumps({"chip_layers": 2}))
    (d / "config.json").write_text("{}")
    sd = {"model.layers.0.mlp.up_proj.weight": torch.rand(6, 8), "model.layers.1.mlp.up_proj.weight": torch.rand(6, 8),
          "model.layers.2.mlp.up_proj.weight": torch.rand(6, 8), "model.layers.2.mlp.gate_proj.weight": torch.rand(6, 8),
          "model.embed_tokens.weight": torch.rand(9, 8), "lm_head.weight": torch.rand(9, 8), "model.norm.weight": torch.rand(8)}
    sd = {k: v.to(torch.bfloat16) for k, v in sd.items()}
    save_file(sd, str(d / "model-00001-of-00001.safetensors"), metadata={"format": "pt"})
    return d, sd


def lora_file(path, name="layers.0.mlp.up_proj"):
    a, b = torch.rand(2, 6), torch.rand(4, 2)
    save_file({f"{name}.A": a, f"{name}.B": b}, str(path), metadata={"alpha": "4", "rank": "2"})
    return b @ a * 2


def test_apply_changes_only_the_qwen_block(ckpt, tmp_path):
    d, sd = ckpt
    delta = lora_file(tmp_path / "l.safetensors")
    out = F.apply(d, tmp_path / "l.safetensors", tmp_path / "ft")
    got = load_file(str(tmp_path / "ft/model-00001-of-00001.safetensors"))
    k = "model.layers.2.mlp.up_proj.weight"
    want = sd[k].float()
    want[:4, :6] += delta
    assert torch.allclose(got[k].float(), want.to(torch.bfloat16).float())
    assert torch.equal(got[k][4:].float(), sd[k][4:].float()) and torch.equal(got[k][:, 6:].float(), sd[k][:, 6:].float())
    for x in sd:
        if x != k:
            assert torch.equal(got[x].float(), sd[x].float()), x
    assert out["frozen_tensors_changed"] == [] and out["chip_tensors"] == 2 and out["changed_tensors"] == 1
    assert json.loads((tmp_path / "ft/ft1-hashes.json").read_text())["chip_sha256"]


def test_apply_refuses_a_delta_wider_than_the_tensor(ckpt, tmp_path):
    d, _ = ckpt
    save_file({"layers.0.mlp.up_proj.A": torch.rand(2, 99), "layers.0.mlp.up_proj.B": torch.rand(4, 2)}, str(tmp_path / "l.safetensors"), metadata={"alpha": "4", "rank": "2"})
    with pytest.raises(AssertionError):
        F.apply(d, tmp_path / "l.safetensors", tmp_path / "ft")


def test_training_on_a_tiny_real_qwen_lowers_loss_and_never_moves_the_base(tmp_path):
    from transformers import Qwen3_5ForCausalLM, Qwen3_5TextConfig

    cfg = Qwen3_5TextConfig(vocab_size=64, hidden_size=32, intermediate_size=64, num_hidden_layers=2, num_attention_heads=2, num_key_value_heads=2,
                            linear_num_key_heads=2, linear_num_value_heads=2, layer_types=["linear_attention", "full_attention"], tie_word_embeddings=False)
    torch.manual_seed(0)
    m = Qwen3_5ForCausalLM(cfg).float()
    before = {k: v.clone() for k, v in m.state_dict().items()}
    seq = list(range(5, 25))
    ex = [(seq, [-100] * 10 + seq[10:])] * 4
    logged = []
    a = Namespace(lr=3e-3, batch=2, epochs=8, micro_tokens=1000, seed=0, log_every=1, out=str(tmp_path / "o"))
    loras = F.run_training(m.model, m.lm_head, ex, ex, a, "cpu", 0, logged.append)
    h = [x["heldout_loss"] for x in logged if "heldout_loss" in x]
    assert h[-1] < h[0]
    assert (tmp_path / "o/lora.safetensors").exists() and loras
    for name, lo in loras.items():
        assert torch.equal(lo.base.weight, before[f"model.{name}.weight"])
    assert torch.equal(m.lm_head.weight, before["lm_head.weight"])
