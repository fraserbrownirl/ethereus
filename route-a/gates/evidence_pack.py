#!/usr/bin/env python3
"""Step 3 evidence pack for the ETH Evals maintainers (Step 4). Run on the eval VM after the arms:

    /workspace/venv/bin/python route-a/gates/evidence_pack.py OUT_DIR [ARM ...]

Writes OUT_DIR/table.md (per arm: passed/scored per pillar, errors and timeouts apart, tool calls, judge spend),
OUT_DIR/config.md (harness commit, job file, server per arm, firmware and snapshot hashes the server recorded) and
OUT_DIR/SHA256SUMS over each arm's rows.jsonl and trajectories. Our rows only; rows.jsonl also mirrors the board.
Timeouts = watchdog stops (option C: a failure, no retry) plus harness limit hits; errors = every other error.
Model only in-weights calls come from the server traces: serve.py keeps our calls out of the answer when the client
offers no tools, so the vanilla trajectory never shows them. Nothing here is posted anywhere.
"""
import json, glob, collections, os, re, sys, hashlib, subprocess, datetime
from tokenizers import Tokenizer
W = "/workspace"
pil = {l.strip().split("/")[-1]: l.strip().split("/")[1] for l in open(f"{W}/lists/step3/all37")}
# A watchdog stop names its trial's container (task__suffix__env-main-1); it belongs to the arm whose jobs hold that trial.
stops = [l.split()[1].rsplit("__env-main-1", 1)[0].lower() for l in open(f"{W}/step3/watchdog.log") if l.startswith("STOPPED")]
stop_arm = {}
for st in stops:
    for d in glob.glob(f"{W}/results/step3-*/jobs/*/*__*"):
        if os.path.basename(d).lower() == st:
            stop_arm.setdefault(d.split("/")[3], set()).add(st.split("__")[0])
ALL = [("step3-v01-skills", "ethereus/ethereus-9b-v0.1", "Ethereus-9B-v0.1", "Internet + Skills"),
        ("step3-v01-internet", "ethereus/ethereus-9b-v0.1", "Ethereus-9B-v0.1", "Internet"),
        ("step3-v01-modelonly", "openai/ethereus-9b-v0.1", "Ethereus-9B-v0.1", "Model only"),
        ("step3-untrained-internet", "ethereus/ethereus-9b-untrained", "Ethereus-9B (untrained)", "Internet"),
        ("step3-untrained-modelonly", "openai/ethereus-9b-untrained", "Ethereus-9B (untrained)", "Model only"),
        ("step3-stock-internet", "ethereus/qwen3.5-9b", "Qwen3.5-9B (stock)", "Internet"),
        ("step3-stock-modelonly", "openai/qwen3.5-9b", "Qwen3.5-9B (stock)", "Model only")]
OUT = sys.argv[1]; want = sys.argv[2:] or [a[0] for a in ALL]
ARMS = [a for a in ALL if a[0] in want and os.path.exists(f"{W}/results/{a[0]}/rows.jsonl")]
os.makedirs(OUT, exist_ok=True)
tok = Tokenizer.from_file("/workspace/models/Qwen3.5-9B/tokenizer.json")
ts = lambda s: datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
traces = [json.load(open(f)) for f in glob.glob(f"{W}/serve-data/*/traces/*.json")]
table = ["| Model | Mode | Concepts | Build | Act | Overall | Errors | Timeouts | eth | files | OpenCode's tools | stray in-weights | Judge (trials) | Trials |", "|" + "---|" * 14]
detail = []; sums = []; fw = collections.Counter()
for arm, model, name, mode in ARMS:
    latest = {}
    for l in open(f"{W}/results/{arm}/rows.jsonl"):
        r = json.loads(l)
        if r.get("model") == model:
            latest[(r["task_id"], r.get("trial"), r.get("mode"))] = r
    cell = collections.defaultdict(lambda: collections.Counter()); errs = []
    for r in latest.values():
        t = r["task_id"].split("/")[-1]; p = pil.get(t, "?"); c = cell[p]
        wd = r["status"] == "error" and r.get("error_kind") == "NonZeroAgentExitCodeError" and t in stop_arm.get(arm, set())
        if wd or r.get("limit"):
            c["timeout"] += 1; c["scored"] += 1
        elif r["status"] == "error":
            c["error"] += 1; errs.append(f"{t} {r.get('error_kind')}")
        else:
            c["scored"] += 1; c["passed"] += r["status"] == "passed"
    calls = collections.Counter()
    for f in glob.glob(f"{W}/results/{arm}/jobs/*/*/agent/opencode.txt"):
        for l in open(f):
            try: e = json.loads(l)
            except Exception: continue
            part = e.get("part") or {}
            if part.get("type") == "tool": calls["opencode:" + str(part.get("tool"))] += 1
            if part.get("type") == "text":
                for x in part.get("text", "").split("<function=")[1:]: calls["inw:" + x.split(">")[0]] += 1
    rs = [r for r in latest.values() if r.get("completed_at")]
    lo = min(ts(r["completed_at"]) - (r.get("total_seconds") or 0) for r in rs) - 5; hi = max(ts(r["completed_at"]) for r in rs) + 5
    for tr in traces:
        if lo <= tr["timestamp"] <= hi:
            fw[(arm, (tr.get("firmware_sha256") or "none")[:8], (tr.get("snapshot_sha256") or "")[:8])] += 1
            if mode == "Model only":
                for seg in tr.get("segments") or [{"gen_ids": tr.get("gen_ids", [])}]:
                    for x in tok.decode(seg.get("gen_ids", []), skip_special_tokens=False).split("<function=")[1:]:
                        calls["inw:" + x.split(">")[0].strip()] += 1
    for f in sorted([f"{W}/results/{arm}/rows.jsonl"] + glob.glob(f"{W}/results/{arm}/jobs/*/*/agent/*")):
        if os.path.isfile(f): sums.append(f"{hashlib.sha256(open(f, 'rb').read()).hexdigest()}  {f[len(W) + 1:]}")
    judge = sum((json.loads(l).get("judge_cost_usd") or 0) for l in open(f"{W}/results/{arm}/rows.jsonl") if json.loads(l).get("model") == model)
    nj = sum(1 for l in open(f"{W}/results/{arm}/rows.jsonl") if json.loads(l).get("model") == model and json.loads(l).get("judge_cost_usd"))
    tot = sum(cell.values(), collections.Counter())
    eth = calls["inw:eth"]; files = calls["inw:list"] + calls["inw:get"]
    stray = sum(v for k, v in calls.items() if k.startswith("inw:") and k not in ("inw:eth", "inw:list", "inw:get"))
    oc = sum(v for k, v in calls.items() if k.startswith("opencode:"))
    ocd = ", ".join(f"{k[9:]} {v}" for k, v in calls.most_common() if k.startswith("opencode:"))
    f = lambda p: f"{cell[p]['passed']}/{cell[p]['scored']}" if cell[p]["scored"] or cell[p]["error"] else "–"
    table.append(f"| {name} | {mode} | {f('concepts')} | {f('build')} | {f('act')} | {tot['passed']}/{tot['scored']} | {tot['error']} | {tot['timeout']} | {eth} | {files} | {oc if mode != 'Model only' else '–'} | {stray} | ${judge:.2f} ({nj}) | {len(latest)} |")
    detail.append(f"- `{arm}` ({model}): errors {errs or 'none'}; OpenCode {ocd or 'none'}; in-weights {dict((k[4:], v) for k, v in calls.items() if k.startswith('inw:')) or 'none'}")
sh = lambda c: subprocess.run(c, shell=True, capture_output=True, text=True).stdout.strip()
open(f"{OUT}/table.md", "w").write("\n".join(table + [""] + detail) + "\n")
cfg = [f"- Harness: BuidlGuidl/ethevals {sh('git -C /workspace/ethevals rev-parse HEAD')}",
       f"- Job file: route-a/gates/job-step3.yaml, sha256 {sh('sha256sum /workspace/ethereus/route-a/gates/job-step3.yaml').split()[0]}",
       f"- Core repo on the VM: {sh('git -C /workspace/ethereus rev-parse HEAD')}",
       "- Firmware / snapshot the server recorded, per arm (traces in the arm's window):"]
cfg += [f"  - {a}: firmware {f_}, snapshot {s_ or 'none'}: {n} traces" for (a, f_, s_), n in sorted(fw.items())]
open(f"{OUT}/config.md", "w").write("\n".join(cfg) + "\n")
open(f"{OUT}/SHA256SUMS", "w").write("\n".join(sums) + "\n")
print(open(f"{OUT}/table.md").read()); print(open(f"{OUT}/config.md").read()); print(len(sums), "files hashed")
