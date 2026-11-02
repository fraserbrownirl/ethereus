#!/usr/bin/env python3
"""Fine-tune spec section 5, the checks that need the model to generate: held-out tool calls and red flags.

    eval_ft.py --model dist-store/q9-store-rv-ft1 --firmware tests/data/rv/eth-bridge.elf --fs build/ethskills.fs.npz --out DIR

Passes: correct tool and arguments on at least 90% of held-out tool rows, final answer holding the tool's result
byte-exact on at least 95% (a knowledge answer is a restatement: it must name the file and keep the numbers and
identifiers), and no tool call on any of the 50 non-Ethereum prompts. Run it on the base checkpoint
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
    """The decisive call the row teaches: its last one (a `get` of the right file, or the `eth` command). For a spilled
    row the full command, from call_segments, when a tokenizer is given; otherwise the rewritten one."""
    fn = [m for m in row["messages"] if m.get("tool_calls")][-1]["tool_calls"][0]["function"]
    if row.get("call_segments") and decode is not None:
        text = decode(row["call_segments"][-1]["ids"])
        got = calls_in(text if "<tool_call>" in text else "<tool_call>" + text)
        if got:
            return got[-1]
    return fn["name"], fn["arguments"]


def rewritten_call(row) -> tuple[str, dict]:
    fn = [m for m in row["messages"] if m.get("tool_calls")][-1]["tool_calls"][0]["function"]
    return fn["name"], fn["arguments"]


def key_terms_kept(row, final: str) -> bool:
    """Finals are restatements, so wording is free: it must name the file and keep the target's numbers and identifiers."""
    want = row["final"]
    terms = [t for t in re.findall(r"`([^`]+)`", want) if t != row["source"]] + re.findall(r"\d[\d,.]*\d|\d", want.replace(row["source"], ""))
    return row["source"] in final and all(t in final for t in terms)


def score_tool_row(row, text: str, want: tuple[str, dict], full_cmd=None) -> dict:
    """text: what the model and circuit wrote. full_cmd: the command before any spill rewrite, from the trace."""
    got = calls_in(text)
    # Any of the model's calls may be the decisive one: listing files before the right `get` is not a miss, and going
    # straight to it is not one either. A spilled call is compared on its full command when the trace gives it.
    seen = [(n, {**a, "cmd": full_cmd} if (full_cmd is not None and "cmd" in a and "@spill/" in a["cmd"]) else a) for n, a in got]
    call_ok = want in seen
    out = (row.get("tool_outputs") or [""])[-1].splitlines()
    head = out[0] if out else ""
    final_ok = bool(got) and bool(head) and head in after_last_response(text) and head in text.split("</tool_call>", 1)[-1]
    if row["family"] in ("files", "knowledge"):
        final_ok = bool(got) and key_terms_kept(row, after_last_response(text))
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


def rescore(out: Path, data: Path, decode) -> dict:
    """The summary again from saved rows, with this scorer: every model is judged by the same rules. A spilled row
    saved without its full command is compared on the rewritten command, and reported as such."""
    rows = {json.loads(ln)["id"]: json.loads(ln) for f in TOOL_FAMILIES for ln in (data / f"{f}.jsonl").read_text().splitlines()}
    res, approx, finals_kept = [], 0, 0
    for ln in (out / "heldout.jsonl").read_text().splitlines():
        x = json.loads(ln)
        r = rows[x["id"]]
        want = expected_call(r, decode)
        full = x.get("full_cmd")
        if x.get("spilled") and full is None:
            want, approx = rewritten_call(r), approx + 1
        y = score_tool_row(r, x["text"], want, full)
        if "final_text" not in x and len(x["text"]) >= 2000:
            y["final_ok"], finals_kept = x["final_ok"], finals_kept + 1  # text cut before the answer: keep the run's own verdict
        res.append(y)
    flags = [json.loads(ln) for ln in (out / "redflag.jsonl").read_text().splitlines()]
    s = summarize(res, flags)
    s["spilled_rows_compared_on_rewritten_command"] = approx
    s["finals_kept_from_the_run_because_text_was_cut"] = finals_kept
    return s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--firmware", required=True)
    ap.add_argument("--fs", default=None)
    ap.add_argument("--data", default=str(RV / "data/ft1"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-new", type=int, default=1500)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--rescore", action="store_true", help="recompute --out's summary from its saved rows")
    ap.add_argument("--per-family", type=int, default=0, help="the first N held-out rows of each family, the same N for every model")
    a = ap.parse_args()
    if a.rescore:
        from tokenizers import Tokenizer

        tok = Tokenizer.from_file(str(Path(a.model) / "tokenizer.json"))
        s = rescore(Path(a.out), Path(a.data), lambda ids: tok.decode(ids, skip_special_tokens=False))
        (Path(a.out) / "summary-rescored.json").write_text(json.dumps(s, indent=1))
        print("EVAL_FT_RESCORED", "PASS" if s["pass"] else "FAIL", json.dumps(s), flush=True)
        return 0 if s["pass"] else 1
    from chipformer.rv import serve, spill

    eng = serve.Engine(a.model, a.firmware, snapshot=a.fs)
    decode = lambda ids: eng.chat.T.tok.decode(ids, skip_special_tokens=False)  # noqa: E731
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(ln) for f in TOOL_FAMILIES for ln in (Path(a.data) / f"{f}.jsonl").read_text().splitlines()]
    rows = [r for r in rows if r["split"] == "heldout"]
    if a.per_family:
        seen = {}
        rows = [r for r in rows if seen.setdefault(r["family"], []).append(r) or len(seen[r["family"]]) <= a.per_family]
    rows = rows[: a.limit or None]
    res = []
    with open(out / "heldout.jsonl", "w") as fh:
        for r in rows:
            turn = eng.run(r["messages"][:2], a.max_new)
            first = turn.segments[0].get("gen_ids", [])
            full = spill.parse_cmd(decode(eng._visible(first))) if turn.spilled else None
            x = score_tool_row(r, turn.text, expected_call(r, decode), full)
            x |= {"text": turn.text[:2000], "final_text": after_last_response(turn.text)[-2000:], "spilled": turn.spilled, "full_cmd": full, "lock_error": str(turn.lock_error) if turn.lock_error else None}
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
