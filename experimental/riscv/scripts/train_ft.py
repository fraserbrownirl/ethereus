#!/usr/bin/env python3
"""Fine-tune spec v1: teach Qwen to reach for its tools, with the circuit untouched.

    train_ft.py train --base models/Qwen3.5-9B --data data/ft1 --out dist/ft1-lora
    train_ft.py apply --merged dist-store/q9-store-rv --lora dist/ft1-lora/lora.safetensors --dst dist-store/q9-store-rv-ft1

Training runs on the stock 9B, not the merged checkpoint. The merge holds Qwen's weights as an exact block of
each wider tensor (merge.py: IN_COLS, OUT_ROWS), and the text path already matches base (gate 1). So the learned
change is added to that block alone: the chip layers, embeddings, head and norm are never read for training, and
apply checks their hashes anyway. No peft, so the pinned venv stays pinned.

Loss is only on what the model generates, per the data's train flags; circuit output carries none.
"""

import argparse
import hashlib
import json
import math
import random
import re
import shutil
import time
from pathlib import Path

import torch
from torch import nn

TARGET = re.compile(r"\.(q_proj|k_proj|v_proj|o_proj|in_proj_qkv|in_proj_z|out_proj|gate_proj|up_proj|down_proj)$")
RANK, ALPHA = 16, 32
MERGED_LAYER = re.compile(r"^model\.layers\.(\d+)\.")


class Lora(nn.Module):
    def __init__(self, base: nn.Linear, rank: int = RANK) -> None:
        super().__init__()
        self.base = base
        self.A = nn.Parameter(torch.empty(rank, base.in_features, device=base.weight.device))
        self.B = nn.Parameter(torch.zeros(base.out_features, rank, device=base.weight.device))
        nn.init.kaiming_uniform_(self.A, a=math.sqrt(5))
        self.scale = ALPHA / rank

    def forward(self, x):
        d = (x.float() @ self.A.T) @ self.B.T * self.scale
        return self.base(x) + d.to(x.dtype)

    def delta(self):
        return (self.B @ self.A * self.scale).detach()


def wrap(text_model: nn.Module) -> dict[str, Lora]:
    """Freeze everything, then LoRA on each projection. Returns {module name: Lora}."""
    for p in text_model.parameters():
        p.requires_grad_(False)
    out = {}
    for name, mod in list(text_model.named_modules()):
        if isinstance(mod, nn.Linear) and TARGET.search(name):
            parent = text_model.get_submodule(name.rsplit(".", 1)[0])
            lora = Lora(mod)
            setattr(parent, name.rsplit(".", 1)[1], lora)
            out[name] = lora
    return out


def sequence(segments) -> tuple[list[int], list[int]]:
    ids, labels = [], []
    for s in segments:
        ids += s["ids"]
        labels += s["ids"] if s["train"] else [-100] * len(s["ids"])
    return ids, labels


def examples(rows) -> list[tuple[list[int], list[int]]]:
    """A spilled row is two sequences, as in serve.py: the full call, and the answer on the rewritten call."""
    out = []
    for r in rows:
        out.append(sequence(r["segments"]))
        if r.get("call_segments"):
            out.append(sequence(r["call_segments"]))
    return out


def load_rows(data: Path):
    rows = [json.loads(ln) for p in sorted(data.glob("*.jsonl")) if p.name != "phrasings.jsonl" for ln in p.read_text().splitlines()]
    return [r for r in rows if r.get("split") == "train"], [r for r in rows if r.get("split") == "heldout"]


def groups(ex, batch: int, rng: random.Random):
    idx = list(range(len(ex)))
    rng.shuffle(idx)
    return [idx[i:i + batch] for i in range(0, len(idx), batch)]


def loss_sum(text_model, head, ex, group, micro_tokens: int, pad: int, dev, backward: float | None):
    """Summed over the group's labelled tokens; micro-batched so one long sequence does not set the batch's memory."""
    group = sorted(group, key=lambda i: len(ex[i][0]))
    total, cur = 0.0, []

    def flush(cur):
        n = max(len(ex[i][0]) for i in cur)
        ids = torch.full((len(cur), n), pad, device=dev)
        lab = torch.full((len(cur), n), -100, device=dev)
        mask = torch.zeros((len(cur), n), dtype=torch.long, device=dev)
        for j, i in enumerate(cur):
            a, b = ex[i]
            ids[j, :len(a)] = torch.tensor(a, device=dev)
            lab[j, :len(b)] = torch.tensor(b, device=dev)
            mask[j, :len(a)] = 1
        h = text_model(input_ids=ids, attention_mask=mask, use_cache=False).last_hidden_state[:, :-1]
        tgt = lab[:, 1:]
        pick = tgt != -100
        logits = head(h[pick])  # only where there is a loss: the head is wide
        loss = nn.functional.cross_entropy(logits.float(), tgt[pick], reduction="sum")
        if backward is not None:
            (loss / backward).backward()
        return loss.item()

    for i in group:
        if cur and max(len(ex[j][0]) for j in [*cur, i]) * (len(cur) + 1) > micro_tokens:
            total += flush(cur)
            cur = []
        cur.append(i)
    return total + flush(cur)


def labelled(ex, group) -> int:
    return sum(sum(x != -100 for x in ex[i][1][1:]) for i in group)


def save_lora(loras: dict[str, Lora], path: Path, prefix: str = "") -> None:
    from safetensors.torch import save_file

    sd = {}
    for name, m in loras.items():
        sd[f"{prefix}{name}.A"] = m.A.detach().cpu().float()
        sd[f"{prefix}{name}.B"] = m.B.detach().cpu().float()
    save_file(sd, str(path), metadata={"alpha": str(ALPHA), "rank": str(RANK)})


def run_training(text_model, head, tr, ho, a, dev, pad, log) -> dict[str, Lora]:
    loras = wrap(text_model)
    text_model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    text_model.enable_input_require_grads()
    params = [p for m in loras.values() for p in (m.A, m.B)]
    opt = torch.optim.AdamW(params, lr=a.lr, weight_decay=0.0)
    rng = random.Random(a.seed)
    n = math.ceil(len(tr) / a.batch) * a.epochs
    step, t0 = 0, time.time()

    def heldout():
        text_model.eval()
        with torch.no_grad():
            tot = lab = 0.0
            for g in groups(ho, a.batch, random.Random(0)):
                tot += loss_sum(text_model, head, ho, g, a.micro_tokens, pad, dev, backward=None)
                lab += labelled(ho, g)
        return tot / max(lab, 1)

    log({"before_training_heldout_loss": heldout()})
    for epoch in range(a.epochs):
        text_model.train()
        for g in groups(tr, a.batch, rng):
            lr = a.lr * min(1.0, (step + 1) / 30) * (0.1 + 0.9 * 0.5 * (1 + math.cos(math.pi * step / n)))
            for pg in opt.param_groups:
                pg["lr"] = lr
            d = max(1, labelled(tr, g))
            s = loss_sum(text_model, head, tr, g, a.micro_tokens, pad, dev, backward=d)
            nn.utils.clip_grad_norm_(params, 1.0)
            opt.step()
            opt.zero_grad(set_to_none=True)
            step += 1
            if step % a.log_every == 0 or step == n:
                mem = torch.cuda.max_memory_allocated() / 2**30 if torch.cuda.is_available() else 0
                log({"step": step, "of": n, "epoch": epoch, "loss": s / d, "lr": lr, "minutes": (time.time() - t0) / 60, "peak_gib": round(mem, 1)})
            if step % a.save_every == 0:
                Path(a.out).mkdir(parents=True, exist_ok=True)
                save_lora(loras, Path(a.out) / "lora-latest.safetensors")
        log({"epoch_done": epoch, "heldout_loss": heldout()})
        Path(a.out).mkdir(parents=True, exist_ok=True)
        save_lora(loras, Path(a.out) / f"lora-epoch{epoch + 1}.safetensors")
    save_lora(loras, Path(a.out) / "lora.safetensors")
    return loras


def cmd_train(a) -> int:
    from transformers import AutoModelForImageTextToText

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    torch.manual_seed(a.seed)
    model = AutoModelForImageTextToText.from_pretrained(a.base, dtype=torch.bfloat16).to(dev)
    text, head = model.model.language_model, model.lm_head
    tr_rows, ho_rows = load_rows(Path(a.data))
    if a.limit_rows:  # a short probe: speed, memory, the loss moving; shuffled so it spans the families
        random.Random(a.seed).shuffle(tr_rows)
        random.Random(a.seed).shuffle(ho_rows)
        tr_rows, ho_rows = tr_rows[: a.limit_rows], ho_rows[: max(1, a.limit_rows // 4)]
    tr, ho = examples(tr_rows), examples(ho_rows)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    fh = open(out / "train.log.jsonl", "w")

    def log(d):
        print(json.dumps(d), flush=True)
        fh.write(json.dumps(d) + "\n")
        fh.flush()

    man = json.loads((Path(a.data) / "manifest.json").read_text())
    log({"train_rows": len(tr_rows), "heldout_rows": len(ho_rows), "train_seq": len(tr), "heldout_seq": len(ho),
         "firmware_sha256": man.get("firmware_sha256"), "epochs": a.epochs, "lr": a.lr, "batch": a.batch})
    pad = model.config.get_text_config().eos_token_id
    run_training(text, head, tr, ho, a, dev, pad[0] if isinstance(pad, list) else pad, log)
    return 0


def tensor_sha(t: torch.Tensor) -> str:
    return hashlib.sha256(t.float().numpy().tobytes() + str(tuple(t.shape)).encode()).hexdigest()


def merged_name(name: str, n_chip: int) -> str:
    """layers.3.mlp.up_proj -> model.layers.15.mlp.up_proj.weight"""
    m = re.match(r"^layers\.(\d+)\.(.*)$", name)
    assert m, name
    return f"model.layers.{int(m[1]) + n_chip}.{m[2]}.weight"


def apply(merged: Path, lora: Path, dst: Path) -> dict:
    """Add the LoRA's change to the Qwen block of each wide tensor. Every other tensor is hashed before and after."""
    from safetensors import safe_open
    from safetensors.torch import load_file, save_file

    meta = json.loads((merged / "chipformer.json").read_text())
    n_chip = meta["chip_layers"]
    ls = load_file(str(lora))
    with safe_open(str(lora), "pt") as fh:
        scale = int(fh.metadata()["alpha"]) / int(fh.metadata()["rank"])
    deltas = {}
    for k in ls:
        if k.endswith(".A"):
            name = k[:-2]
            key = merged_name(name, n_chip)
            assert int(MERGED_LAYER.match(key)[1]) >= n_chip, f"{key} is a chip layer"
            deltas[key] = ls[name + ".B"] @ ls[k] * scale
    dst.mkdir(parents=True, exist_ok=True)
    for f in merged.iterdir():
        if f.is_file() and f.suffix != ".safetensors" and not f.name.endswith(".log"):
            shutil.copy2(f, dst / f.name)
    before, after, touched = {}, {}, 0
    for shard in sorted(merged.glob("*.safetensors")):
        sd = load_file(str(shard))
        for k in sd:
            if k in deltas:
                continue
            before[k] = tensor_sha(sd[k])
        for k, d in deltas.items():
            if k not in sd:
                continue
            t = sd[k].float()
            h, w = d.shape
            assert h <= t.shape[0] and w <= t.shape[1], (k, d.shape, t.shape)
            t[:h, :w] += d
            sd[k] = t.to(torch.bfloat16)
            touched += 1
        save_file(sd, str(dst / shard.name), metadata={"format": "pt"})
    assert touched == len(deltas), (touched, len(deltas))
    for shard in sorted(dst.glob("*.safetensors")):
        sd = load_file(str(shard))
        for k in sd:
            if k not in deltas:
                after[k] = tensor_sha(sd[k])
    bad = sorted(k for k in before if before[k] != after.get(k))
    chip = sorted(k for k in before if (m := MERGED_LAYER.match(k)) and int(m[1]) < n_chip)
    out = {"chip_layers": n_chip, "chip_tensors": len(chip), "other_frozen_tensors": len(before) - len(chip), "changed_tensors": touched,
           "frozen_tensors_changed": bad, "lora": str(lora), "chip_sha256": {k: before[k] for k in chip}}
    (dst / "ft1-hashes.json").write_text(json.dumps(out, indent=1))
    if bad:
        raise SystemExit(f"FROZEN TENSORS CHANGED: {bad[:5]}")
    print(json.dumps({k: v for k, v in out.items() if k != "chip_sha256"}))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train")
    t.add_argument("--base", required=True)
    t.add_argument("--data", default="data/ft1")
    t.add_argument("--out", required=True)
    t.add_argument("--epochs", type=int, default=2)
    t.add_argument("--lr", type=float, default=1e-4)
    t.add_argument("--batch", type=int, default=16)
    t.add_argument("--micro-tokens", type=int, default=40000)
    t.add_argument("--seed", type=int, default=0)
    t.add_argument("--log-every", type=int, default=5)
    t.add_argument("--limit-rows", type=int, default=0)
    t.add_argument("--save-every", type=int, default=50)
    p = sub.add_parser("apply")
    p.add_argument("--merged", required=True)
    p.add_argument("--lora", required=True)
    p.add_argument("--dst", required=True)
    a = ap.parse_args()
    if a.cmd == "train":
        return cmd_train(a)
    apply(Path(a.merged), Path(a.lora), Path(a.dst))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
