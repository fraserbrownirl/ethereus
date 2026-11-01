#!/usr/bin/env python3
"""Fine-tune spec section 5, the checks that need the model to generate: held-out tool calls and red flags.

    eval_ft.py --model dist-store/q9-store-rv-ft1 --firmware tests/data/rv/eth-bridge.elf --fs build/ethskills.fs.npz --out DIR

Passes: correct tool and arguments on at least 90% of held-out tool rows, final answer holding the tool's result
byte-exact on at least 95%, and no tool call on any of the 50 non-Ethereum prompts. Run it on the base checkpoint
too, so the difference is one glance. Exit 0 only on all three.
"""

import argparse
import json
import re
from pathlib import Path

RV = Path(__file__).parents[1]
CALL = re.compile(r"<tool_call>\s*<function=([\w.-]+)>(.*?)</function>\s*</tool_call>", re.S)
PARAM = re.compile(r"<parameter=([\w.-]+)>\n?(.*?)\n?</parameter>", re.S)
TOOL_FAMILIES = ("abi", "u256", "create", "run", "files", "knowledge")
MIN_CALLS, MIN_FINALS = 0.90, 0.95


def calls_in(text: str) -> list[tuple[str, dict]]:
    return [(m[1], dict(PARAM.findall(m[2]))) for m in CALL.finditer(text)]


def after_last_response(text: str) -> str:
    return text.rsplit("</tool_response>", 1)[-1]


def expected_call(row, decode=None) -> tuple[str, dict]:
    """The first call the row teaches. A spilled row's cmd holds @spill names; its full call is in call_segments."""
    if row.get("call_segments") and decode is not None:
        got = calls_in("<tool_call>" + decode(row["call_segments"][-1]["ids"]).split("<tool_call>", 1)[-1])
        if got:
            return got[0]
    fn = next(m for m in row["messages"] if m.get("tool_calls"))["tool_calls"][0]["function"]
    return fn["name"], fn["arguments"]


def score_tool_row(row, text: str, want: tuple[str, dict], full_cmd=None) -> dict:
    """text: what the model and circuit wrote. full_cmd: the command before any spill rewrite, from the trace."""
    got = calls_in(text)
    call_ok = False
    if got:
        name, args = got[0]
        if full_cmd is not None and "cmd" in args:
            args = {**args, "cmd": full_cmd}
        call_ok = (name, args) == want
    out = (row.get("tool_outputs") or [""])[-1].splitlines()
    head = out[0] if out else ""
    final_ok = bool(got) and bool(head) and head in after_last_response(text) and head in text.split("</tool_call>", 1)[-1]
    if row["family"] in ("files", "knowledge"):
        final_ok = bool(got) and row["answer"] in after_last_response(text).replace("**", "")
    return {"id": row["id"], "family": row["family"], "call_ok": call_ok, "final_ok": final_ok}


def summarize(rows: list[dict], flags: list[dict]) -> dict:
    n = max(len(rows), 1)
    calls, finals = sum(r["call_ok"] for r in rows) / n, sum(r["final_ok"] for r in rows) / n
    spurious = sum(bool(f["calls"]) for f in flags)
    per = {}
    for r in rows:
        p = per.setdefault(r["family"], [0, 0, 0])
        p[0] += 1
        p[1] += r["call_ok"]
        p[2] += r["final_ok"]
    return {"rows": len(rows), "call_rate": calls, "final_rate": finals, "red_flag_prompts": len(flags), "spurious_calls": spurious,
            "per_family": {k: {"rows": v[0], "calls": v[1], "finals": v[2]} for k, v in per.items()},
            "pass": calls >= MIN_CALLS and finals >= MIN_FINALS and spurious == 0 and bool(flags)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--firmware", required=True)
    ap.add_argument("--fs", default=None)
    ap.add_argument("--data", default=str(RV / "data/ft1"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-new", type=int, default=1500)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    from chipformer.rv import serve, spill

    eng = serve.Engine(a.model, a.firmware, snapshot=a.fs)
    decode = lambda ids: eng.chat.T.tok.decode(ids, skip_special_tokens=False)  # noqa: E731
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(ln) for f in TOOL_FAMILIES for ln in (Path(a.data) / f"{f}.jsonl").read_text().splitlines()]
    rows = [r for r in rows if r["split"] == "heldout"][: a.limit or None]
    res = []
    with open(out / "heldout.jsonl", "w") as fh:
        for r in rows:
            turn = eng.run(r["messages"][:2], a.max_new)
            first = turn.segments[0].get("gen_ids", [])
            full = spill.parse_cmd(decode(eng._visible(first))) if turn.spilled else None
            x = score_tool_row(r, turn.text, expected_call(r, decode), full)
            x |= {"text": turn.text[:2000], "spilled": turn.spilled, "lock_error": str(turn.lock_error) if turn.lock_error else None}
            res.append(x)
            fh.write(json.dumps(x) + "\n")
            fh.flush()
    flags = []
    prompts = json.loads((RV / "scripts/redflag_prompts.json").read_text())
    with open(out / "redflag.jsonl", "w") as fh:
        for p in prompts[: a.limit or None]:
            turn = eng.run([{"role": "user", "content": p}], a.max_new)
            f = {"prompt": p, "calls": [c[0] for c in calls_in(turn.text)], "text": turn.text[:800]}
            flags.append(f)
            fh.write(json.dumps(f) + "\n")
            fh.flush()
    s = summarize(res, flags)
    (out / "summary.json").write_text(json.dumps(s, indent=1))
    print("EVAL_FT", "PASS" if s["pass"] else "FAIL", json.dumps(s), flush=True)
    return 0 if s["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
