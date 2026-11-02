#!/usr/bin/env python3
"""20-question knowledge smoke test: base Qwenputer vs Qwenputer + ethskills.fs.

Questions come from the ethskills "what LLMs get wrong" table (es/why.md),
with expected answers in scripts/smoke_questions.json. Each question runs
twice through RvChat (greedy): base (no files) and fs (one system line +
the snapshot). Grading is exact-match or keyword-groups per question.

Usage (on the GPU box):
  python scripts/smoke_knowledge.py --model <merged-qwenputer> --fs build/ethskills.fs.npz
  python scripts/smoke_knowledge.py --dry-run   # validate the question file, no model
"""
import argparse
import json
import sys
import time
from pathlib import Path

RV = Path(__file__).parents[1]
sys.path.insert(0, str(RV))

DEFAULT_QUESTIONS = Path(__file__).parent / "smoke_questions.json"


def load_questions(path):
    d = json.loads(Path(path).read_text())
    assert isinstance(d.get("system_line"), str) and d["system_line"].count("\n") == 0, \
        "one system line"
    qs = d["questions"]
    assert len(qs) == 20, f"want 20 questions, got {len(qs)}"
    assert len({q["id"] for q in qs}) == 20, "duplicate ids"
    for q in qs:
        assert set(q) == {"id", "q", "answer", "grade", "src"}, q.get("id")
        assert set(q["grade"]) in ({"exact"}, {"keywords"}), q["id"]
        g = q["grade"]
        if "exact" in g:
            assert g["exact"] and all(isinstance(s, str) for s in g["exact"]), q["id"]
        else:
            assert g["keywords"] and all(
                grp and all(isinstance(s, str) for s in grp)
                for grp in g["keywords"]), q["id"]
    return d["system_line"], qs


def norm(s):
    return " ".join(s.lower().split())


def grade(text, spec):
    t = norm(text)
    if "exact" in spec:
        return any(norm(e) in t for e in spec["exact"])
    return all(any(norm(a) in t for a in grp) for grp in spec["keywords"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None)
    ap.add_argument("--fs", default=None, help="ethskills.fs.npz snapshot")
    ap.add_argument("--firmware", default="tests/data/rv/calc-bridge.elf")
    ap.add_argument("--questions", default=str(DEFAULT_QUESTIONS))
    ap.add_argument("--out", default="build/smoke_knowledge.json")
    ap.add_argument("--max-new", type=int, default=8000)  # the largest file is over 6,000 tokens; 400 cut answers off
    ap.add_argument("--configs", default="base,fs", help="which halves to run, in order")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    system_line, qs = load_questions(args.questions)
    n_exact = sum(1 for q in qs if "exact" in q["grade"])
    print(f"questions: {len(qs)} ({n_exact} exact, "
          f"{len(qs) - n_exact} keyword), system line {len(system_line)} chars")
    if args.dry_run:
        print("dry-run OK")
        return
    if not args.model or not args.fs:
        ap.error("--model and --fs are required (or --dry-run)")

    import torch

    from chipformer.rv import bridge as BR
    from chipformer.rv import elf, ref
    from chipformer.rv import store as ST
    from chipformer.rv.chat import CALC_TOOL, RvChat, load_store_file

    chat = RvChat(args.model)
    snap = load_store_file(args.fs)
    firm = elf.program(args.firmware)
    results = {}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    for cfg in args.configs.split(","):
        rows = []
        results[cfg] = {"score": None, "total": len(qs), "rows": rows}
        for i, q in enumerate(qs):
            m = ref.Machine.from_program(firm)
            BR.park(m)
            if cfg == "fs":
                msgs = [{"role": "system", "content": system_line},
                        {"role": "user", "content": q["q"]}]
                tools = (CALC_TOOL, *ST.TOOLS)
                chat.store_snap = snap
            else:
                msgs = [{"role": "user", "content": q["q"]}]
                tools = (CALC_TOOL,)
                chat.store_snap = None
            ids = chat.prompt_ids(msgs, tools)
            with torch.no_grad():
                r = chat.generate(ids, m, args.max_new)
            text = chat.text_of(r["tokens"])
            ok = grade(text, q["grade"])
            calls = [f for f in ("<function=list>", "<function=get>")
                     if f in text]
            rows.append({"id": q["id"], "pass": ok,
                         "text_tokens": r["text_tokens"],
                         "seconds": round(r["seconds"], 1), "calls": calls,
                         "answer": text[:600], "answer_end": text[-600:]})
            out.write_text(json.dumps(results, indent=1))  # as it goes: a timeout keeps what ran
            print(f"[{cfg}] {i + 1:2d}/20 {'PASS' if ok else 'fail'} "
                  f"{q['id']:16s} {r['text_tokens']:4d}tok "
                  f"{r['seconds']:5.1f}s "
                  f"{','.join(calls) if calls else '-'}", flush=True)
        score = sum(1 for x in rows if x["pass"])
        results[cfg] = {"score": score, "total": len(rows), "rows": rows}
        print(f"[{cfg}] TOTAL {score}/{len(rows)}")
    out.write_text(json.dumps(results, indent=1))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
