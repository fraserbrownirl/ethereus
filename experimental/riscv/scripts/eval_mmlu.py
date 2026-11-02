#!/usr/bin/env python3
"""MMLU, a fixed 500-question sample, zero-shot, the answer letter's logit after the chat prompt (thinking off).

    eval_mmlu.py MODEL --out FILE [--n 500] [--seed 0]

MODEL is a stock Qwen3.5 checkpoint or a merged one (it has chipformer.json). Fine-tune gate (Fraser, 2026-11-02):
the tuned checkpoint within 1 point of stock on the same 500.
"""
import argparse
import json
import random
from pathlib import Path

import torch

LETTERS = "ABCD"


FIXED = Path(__file__).with_name("mmlu500.json")  # the 500 as a file, so the pinned venv needs no parquet reader


def questions(n: int, seed: int):
    if n == 500 and seed == 0 and FIXED.exists():
        return json.loads(FIXED.read_text())["questions"]
    import pyarrow.parquet as pq
    from huggingface_hub import hf_hub_download

    t = pq.read_table(hf_hub_download("cais/mmlu", "all/test-00000-of-00001.parquet", repo_type="dataset")).to_pylist()
    random.Random(seed).shuffle(t)
    return t[:n]


def prompt(q) -> str:
    opts = "\n".join(f"{LETTERS[i]}. {c}" for i, c in enumerate(q["choices"]))
    return f"{q['question']}\n\n{opts}\n\nAnswer with the letter only."


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    from transformers import AutoModelForImageTextToText, AutoTokenizer, Qwen3_5ForCausalLM

    merged = (Path(a.model) / "chipformer.json").exists()
    cls = Qwen3_5ForCausalLM if merged else AutoModelForImageTextToText
    m = cls.from_pretrained(a.model, dtype=torch.bfloat16).to("cuda").eval()
    tok = AutoTokenizer.from_pretrained(a.model)
    ids = [tok.encode(x, add_special_tokens=False) for x in LETTERS]
    assert all(len(x) == 1 for x in ids), ids
    ids = [x[0] for x in ids]
    rows, right = [], 0
    with torch.no_grad():
        for q in questions(a.n, a.seed):
            p = tok.apply_chat_template([{"role": "user", "content": prompt(q)}], add_generation_prompt=True, tokenize=True, enable_thinking=False)
            p = list(p if isinstance(p, list) else p["input_ids"])
            lg = m(input_ids=torch.tensor([p], device="cuda")).logits[0, -1]
            pick = int(torch.argmax(lg[ids]))
            ok = pick == int(q["answer"])
            right += ok
            rows.append({"subject": q["subject"], "pick": LETTERS[pick], "answer": LETTERS[int(q["answer"])], "ok": ok})
    out = {"model": a.model, "n": len(rows), "accuracy": 100 * right / len(rows), "seed": a.seed, "rows": rows}
    Path(a.out).write_text(json.dumps(out))
    print("MMLU", a.model, f"{out['accuracy']:.1f}", f"{right}/{len(rows)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
