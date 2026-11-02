#!/usr/bin/env python3
"""Write docs/status.html: one public page. Unknown values print "unknown", never a guess.

    python3 scripts/status_page.py "What I am doing and waiting on." [--push]

Sources: docs/dispatcher-tasks.md, docs/dispatcher-log.md, eth-bench.md, and the eval machine over ssh.
Nothing new is measured. No secret or host address goes on the page.
"""
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SSH = ["ssh", "-i", os.path.expanduser("~/.ssh/id_ed25519_podminer"), "-o", "IdentitiesOnly=yes",
       "-o", "ConnectTimeout=15", "-o", "BatchMode=yes", "-p", "24138", "root@121.158.120.138"]
U = "unknown"

ARTIFACTS = {"eth-bridge.elf": "tests/data/rv/eth-bridge.elf", "eth.elf": "tests/data/rv/eth.elf",
             "calc-bridge.elf": "tests/data/rv/calc-bridge.elf", "ethskills.fs.npz": "build/ethskills.fs.npz"}
REMOTE = "ARTIFACTS = " + repr(ARTIFACTS) + r'''
import glob, json, os, re, subprocess
def sh(c):
    try: return subprocess.run(c, shell=True, capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception: return ""
out = {"versions": {}, "gates": {}, "arms": {}, "test": {}}
for line in open("/workspace/venv.lock.txt") if os.path.exists("/workspace/venv.lock.txt") else []:
    m = re.match(r"(torch|transformers)==(.+)", line.strip())
    if m: out["versions"][m[1]] = m[2]
def gates(D):
    g = {}
    f = D + "/rv-text.json"
    if os.path.exists(f):
        d = json.load(open(f)); g["1"] = {"ratio": d["kl_merged_mean"] / d["kl_noise_floor_mean"], "greedy": f"{sum(d['greedy_identical'])}/{len(d['greedy_identical'])}", "file": f}
    f = D + "/gpucheck.log"
    if os.path.exists(f):
        s = open(f, errors="replace").read()
        g["2"] = {"exact": len(re.findall(" exact on ", s)), "bad": len(re.findall("mismatch|not exact|Traceback", s)), "fla": "kernels: fla" in s, "file": f}
    f = D + "/gate4.log"
    if os.path.exists(f):
        rows = [json.loads(x) for x in open(f, errors="replace") if x.startswith('{"prompt"')]
        g["4"] = {"ok": sum(r["ok"] for r in rows), "n": len(rows), "wrong": sum(r["replay"].get("wrong_predictions", 0) for r in rows), "file": f}
    f = D + "/smoke.log"
    if os.path.exists(f):
        g["smoke"] = {"totals": re.findall(r"\[(\w+)\] TOTAL (\d+/\d+)", open(f, errors="replace").read()), "file": f}
    f = D + "/verify.log"
    if os.path.exists(f):
        m = re.search(r"^verify: .*", open(f, errors="replace").read(), re.M)
        g["verify"] = {"line": m[0][:60] if m else U_, "file": f}
    return g
U_ = "unknown"
out["gates"]["Ethereus-9B (untrained), q9-rv2"] = gates("/workspace/ethereus/dist/q9-rv2")
out["gates"]["Ethereus-9B (untrained), with files"] = gates("/workspace/dist-store/q9-store-rv")
out["gates"]["Ethereus-9B-v0.1"] = gates("/workspace/dist-store/q9-store-rv-ft1b")
GROUPS = {"base": ["internet-nokey-base", "internet-nokey-base-capped", "internet-rest-base", "internet-fork-base"],
          "ethereus": ["internet-nokey-rv2", "internet-rest-rv2", "internet-fork-rv2"]}
for arm, names in GROUPS.items():
    tasks, trials, passed, errors = {}, 0, 0, 0
    for n in names:
        for r in glob.glob(f"/workspace/results/{n}/jobs/*/*__*/result.json"):
            try: d = json.load(open(r))
            except Exception: continue
            v = (d.get("verifier_result") or {}).get("rewards", {}).get("reward")
            e = d.get("exception_info")
            if v is None and not e: continue
            t = d.get("task_name", "").split("/")[-1]; rec = tasks.setdefault(t, [])
            if v is None: errors += 1
            else: trials += 1; passed += v == 1; rec.append(v)
    scored = {k: v for k, v in tasks.items() if v}
    out["arms"][arm] = {"tasks_scored": len(scored), "tasks_passed": sum(any(x == 1 for x in v) for v in scored.values()), "trials": trials, "trials_passed": passed, "errors": errors, "file": "/workspace/results/" + names[0] + " and siblings"}
out["per_task"] = {}
for arm, names in GROUPS.items():
    for n in names:
        for r in glob.glob(f"/workspace/results/{n}/jobs/*/*__*/result.json"):
            try: d = json.load(open(r))
            except Exception: continue
            t = d.get("task_name", "").split("/")[-1]
            v = (d.get("verifier_result") or {}).get("rewards", {}).get("reward"); e = (d.get("exception_info") or {}).get("exception_type")
            if v is None and not e: continue
            out["per_task"].setdefault(t, {}).setdefault(arm, []).append("pass" if v == 1 else "fail" if v == 0 else "error")
            if v == 0 and e: out["per_task"][t][arm][-1] = "timeout" if "Timeout" in e else "fail"
out["trial"] = sh("docker ps --format '{{.Names}}|{{.Status}}' | grep env-main | head -1")
# Step 3: ETH Evals under the board's settings, one folder per arm and mode (results/step3-*), our trials only
out["step3"] = {}
for d in sorted(glob.glob("/workspace/results/step3-*")):
    n = {"passed": 0, "failed": 0, "errors": 0, "tasks": set()}
    for r in glob.glob(d + "/jobs/*/*__*/result.json"):
        try: x = json.load(open(r))
        except Exception: continue
        v = ((x.get("verifier_result") or {}).get("rewards") or {}).get("reward"); e = x.get("exception_info")
        n["tasks"].add(x.get("task_name", ""))
        if v == 1: n["passed"] += 1
        elif v is not None: n["failed"] += 1
        elif e: n["errors"] += 1
    n["tasks"] = len(n["tasks"]); out["step3"][os.path.basename(d)] = n
out["watchdog_stops"] = sum(1 for _ in open("/workspace/step3/watchdog.log")) if os.path.exists("/workspace/step3/watchdog.log") else 0
# Ethereus test summaries: ALL rows (called, tool right, final exact) per arm
out["etest"] = {}
for tag in ("after", "ft1", "ft1-saved"):
    f = f"/workspace/results/ethereus-test/{tag}/summary.txt"
    if not os.path.exists(f): continue
    for line in open(f):
        m = re.match(r"(\S+) ALL (\d+) (\d+) (\d+) (\d+)", line)
        if m: out["etest"][tag + "/" + m[1]] = {"n": int(m[2]), "called": int(m[3]), "tool": int(m[4]), "final": int(m[5])}
def h(p):
    return sh(f"sha256sum {p} 2>/dev/null").split(" ")[0] or None
R = "experimental/riscv/"
out["hashes"] = {"route-a checkout": {n: h(f"/workspace/ethereus/{R}{p}") for n, p in ARTIFACTS.items()},
                 "main checkout": {n: h(f"/workspace/ethereus-main/{R}{p}") for n, p in ARTIFACTS.items()}}
def _js(p):
    try: return json.load(open(p))
    except Exception: return None
out["env_pin"] = _js("/workspace/ethereus/dist/q9-rv2/env-pin.json")
out["canary"] = _js("/workspace/canary.json")
out["gate_times"] = {}
_g = "/workspace/ethereus/dist/q9-rv2/gate4-keep1"
if os.path.exists(_g + "/gate4.log"):
    for line in open(_g + "/gate4.log", errors="replace"):
        if not line.startswith('{"prompt"'): continue
        r = json.loads(line)
        chat = open(f"{_g}/chat-{r['prompt']}.log", errors="replace").read()
        m = re.search(r"<parameter=cmd>\n(\S+)(?: (\S+) (\S+))?", chat)
        if not m: continue
        name = m[1]
        if name == "run" and m[3] and m[3].startswith("0x70a08231"): name = "run balanceOf(alice)"
        out["gate_times"].setdefault(name, {"seconds": r["seconds"], "chip_seconds": r["chip_seconds"]})
out["gpu"] = sh("nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader")
print(json.dumps(out))
'''


def esc(x):
    return html.escape(str(x))


def vm():
    try:
        r = subprocess.run(SSH + ["python3 -"], input=REMOTE, capture_output=True, text=True, timeout=120)
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        return None




def tasks():
    p = REPO / "docs/dispatcher-tasks.md"
    rows = []
    for line in p.read_text().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")] if line.startswith("|") else []
        if len(cells) == 7 and cells[0] not in ("Task", "") and not set(cells[0]) <= set("-: "):
            rows.append(cells)
    head = "<tr><th>Task</th><th>State</th><th>Blocker</th><th>Crossing artefact</th><th>Started</th><th>Finished</th><th>Result</th></tr>"

    def tr(r):
        return "<tr>" + "".join(f"<td>{esc(c) if c else '&mdash;'}</td>" for c in r) + "</tr>"

    live = [r for r in rows if r[1] != "done"]
    done = [r for r in rows if r[1] == "done"]
    return (f"<table>{head}{''.join(tr(r) for r in live)}</table>"
            f"<details><summary>Done ({len(done)})</summary><table>{head}{''.join(tr(r) for r in done)}</table></details>")


def results(data):
    if not data:
        return f"<p>{U}: the eval VM did not answer this tick.</p>"
    out = ["<table><tr><th>Backbone</th><th>Gate 1</th><th>Gate 2</th><th>Gates 3, 4</th><th>Verify</th><th>Smoke</th><th>Files</th></tr>"]
    for name, g in data["gates"].items():
        g1 = g.get("1"); g2 = g.get("2"); g4 = g.get("4"); sm = g.get("smoke"); ve = g.get("verify")
        out.append("<tr><td>" + esc(name) + "</td>"
                   + (f"<td>does not apply: the fine-tune changes text on purpose; its gate is KL to stock 0.0078 (cap 0.05)</td>" if name == "Ethereus-9B-v0.1"
                      else f"<td>{f'{g1['ratio']:.2f}x floor, greedy {g1['greedy']}' if g1 else U}</td>")
                   + f"<td>{f'{g2['exact']} exact, {g2['bad']} bad, ' + ('fla' if g2['fla'] else 'not fla') if g2 else U}</td>"
                   + f"<td>{f'{g4['ok']}/{g4['n']} clean, {g4['wrong']} wrong on replay' if g4 else U}</td>"
                   + f"<td>{esc(ve['line']) if ve else U}</td>"
                   + f"<td>{esc(', '.join(a + ' ' + b for a, b in sm['totals'])) if sm else U}</td>"
                   + "<td class='f'>" + "<br>".join(esc(x["file"].replace("/workspace/ethereus/", "").replace("/workspace/", "")) for x in (g1, g2, g4, sm, ve) if x) + "</td></tr>")
    out.append("</table>")
    pin = data.get("env_pin")
    if pin:
        ok = not pin["drift"]
        vs = ", ".join(f"{k} {v}" for k, v in pin["versions"].items())
        out.append(f"<p style='{'' if ok else 'background:#7a5a00;color:#fff;padding:4px'}'><b>Environment:</b> "
                   + ("matches the pinned venv and driver. " if ok else "differs from the pin: " + esc("; ".join(pin["drift"][:6])) + ". ")
                   + esc(f"{vs}, driver {pin['driver']}.") + "</p>")
    else:
        out.append(f"<p><b>Environment:</b> {U}.</p>")
    c = data.get("canary")
    out.append(f"<p><b>Canary</b> (three-call conversation, replayed, after every firmware or environment change): {esc(c['result'] + ' at ' + c['at'])} UTC</p>" if c else "<p><b>Canary:</b> no run yet; it needs the GPU free.</p>")
    out.append("<h3>Ethereus test (ours, not ETH Evals)</h3><p>15 questions with exact answers: ABI calldata, ERC-20 state after a transfer, storage slots. Final answer must match exactly.</p>"
               "<table><tr><th>Model</th><th>Called the tool</th><th>Tool answered right</th><th>Final answer exact</th></tr>")
    et = data.get("etest", {})
    for key, label in (("after/base", "Qwen3.5-9B, no tool"), ("after/ceiling", "Qwen3.5-9B, tool given from outside"),
                       ("after/rv2", "Ethereus-9B (untrained)"), ("ft1/rv2", "Ethereus-9B-v0.1"),
                       ("ft1-saved/rv2", "First fine-tune (ft1), not released: drifted too far from stock")):
        r = et.get(key)
        out.append(f"<tr><td>{esc(label)}</td>" + (f"<td>{r['called']} of {r['n']}</td><td>{r['tool']} of {r['n']}</td><td class='big'>{r['final']} of {r['n']}</td>" if r else f"<td colspan=3>{U}</td>") + "</tr>")
    out.append("</table>")
    out.append("<h3>ETH Evals: earlier runs, superseded by Step 3</h3><p class='f'>These ran before the fine-tune, on older firmware (b320d31b), with our own 45-minute cap and without web search, so they do not match the board's settings. Kept for the record.</p>"
               "<table><tr><th>Model</th><th>Tasks scored</th><th>Tasks passed</th><th>Trials passed</th><th>Errors</th></tr>")
    for arm, a in data["arms"].items():
        label = {"base": "Qwen3.5-9B", "ethereus": "Ethereus-9B (untrained)"}.get(arm, arm)
        out.append(f"<tr><td>{label}</td><td>{a['tasks_scored']}</td><td>{a['tasks_passed']}</td>"
                   f"<td>{a['trials_passed']}/{a['trials']}</td><td>{a['errors']}</td></tr>")
    out.append("</table>")
    return "".join(out)


STEP3 = [("step3-v01-skills", "Ethereus-9B-v0.1", "Internet + Skills"), ("step3-v01-internet", "Ethereus-9B-v0.1", "Internet"),
         ("step3-untrained-internet", "Ethereus-9B (untrained)", "Internet"), ("step3-stock-internet", "Qwen3.5-9B", "Internet"),
         ("step3-v01-modelonly", "Ethereus-9B-v0.1", "Model only"), ("step3-untrained-modelonly", "Ethereus-9B (untrained)", "Model only"),
         ("step3-stock-modelonly", "Qwen3.5-9B", "Model only")]


def now_panel(data, now):
    rows = [f"<p class='now'><b>{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}</b> &middot; {esc(now)}</p>"]
    if not data:
        return "".join(rows) + f"<p>{U}: the eval machine did not answer this refresh.</p>"
    s3 = data.get("step3", {})
    rows.append("<p>Step 3 runs ETH Evals' own runner on its 37 tasks with the board's settings: OpenCode 1.18.33, two trials per task, "
                "web search, the harness's own time limits, and real mainnet RPC for the fork tasks. One arm at a time, on one GPU. "
                "Each arm is reported when it finishes; scores below count only finished trials.</p>"
                "<table><tr><th>Model</th><th>Mode</th><th>Tasks started</th><th>Trials passed</th><th>Failed</th><th>Errors (not scored)</th></tr>")
    for key, model, mode in STEP3:
        n = s3.get(key)
        cells = (f"<td>{n['tasks']} of 37</td><td>{n['passed']}</td><td>{n['failed']}</td><td>{n['errors']}</td>" if n
                 else "<td colspan=4 class='off'>not started</td>")
        rows.append(f"<tr><td>{esc(model)}</td><td>{esc(mode)}</td>{cells}</tr>")
    smoke = {k: v for k, v in s3.items() if "smoke" in k or "probe" in k}
    rows.append("</table>")
    if smoke:
        rows.append("<p class='f'>Set-up checks before the arms (not scores): " + esc(", ".join(f"{k.replace('step3-', '')} {v['passed'] + v['failed']} scored, {v['errors']} errors" for k, v in smoke.items())) + ".</p>")
    t = data.get("trial") or ""
    name = t.partition("|")[0].split("__")[0]
    rows.append(f"<p><b>Trial running now:</b> {esc(name) if name else 'none'}. <b>GPU busy:</b> {esc(data.get('gpu') or U)}."
                + (f" <b>Trials stopped at 190 minutes</b> (as ETH Evals' CI does; counted as failures): {data['watchdog_stops']}." if data.get('watchdog_stops') else "") + "</p>")
    return "".join(rows)


def hero():
    return """<header><h1>Ethereus</h1>
<p class='lede'>Most models call tools outside themselves. <b>Ethereus has the tool in its weights.</b> A RISC-V computer is compiled
into the layers of Qwen3.5-9B and runs Ethereum firmware: keccak, ABI encode and decode, CREATE and CREATE2 addresses,
u256 arithmetic, small EVM programs, and a read-only store of Ethereum reference notes. Its answers are exact, replayed
token by token against a reference machine. The fine-tune teaches the model to decide when to use it.</p></header>"""


# Every number here was read from a run; the source is named beside it.
ACHIEVEMENTS = [
    ("Ethereus test: exact final answer (15 calldata, ERC-20 and storage-slot questions)",
     "13 of 15", "Qwen3.5-9B 4; Ethereus-9B (untrained) 3; Qwen with the tool given from outside 5",
     "results/ethereus-test; an earlier fine-tune (ft1) scored 15 but drifted too far from stock and was not released"),
    ("Right tool call, 48 held-out tool questions", "45 of 48", "Ethereus-9B (untrained) 19",
     "32 short-answer questions plus 16 file and knowledge questions rerun with an 8,000-token limit"),
    ("Right final answer, same 48", "45 of 48", "Ethereus-9B (untrained) 10", "target 95% not yet met (93.75%)"),
    ("Tool calls on 50 unrelated questions (should be 0)", "0 of 50", "the first fine-tune made 6", ""),
    ("Knowledge questions answered from the in-weights file store", "11 of 20", "untrained 6; stock Qwen without files 3", "target 12"),
    ("Drift from stock Qwen3.5-9B on generic prompts (KL)", "0.0078", "gate: at most 0.05", ""),
    ("MMLU-500", "78.6", "stock Qwen3.5-9B 78.0", "fixed 500 questions, seed 0"),
    ("Compiled layers untouched by the fine-tune", "395 of 395 tensors bit-identical", "", "hashes recorded with the checkpoint (lora/ft1-hashes.json)"),
    ("Circuit on GPU (gate 4), firmware 7c4175e6", "6 of 6, 0 wrong predictions on replay", "", "canary three-call conversation: pass"),
    ("Firmware gate 1, u256", "7,652 cases, 0 mismatches", "", "includes 5,652 unary-minus cases checked against independently computed Python integers"),
    ("Long tool calls through the file buffer (spill)", "7 cases pass, 0 wrong on replay", "", "decode of ten values and an ERC-20 balance exact after spilling"),
    ("Machine-checked Solidity build tasks (Kind 1)", "50 proven", "", "each passes ETH Evals' lint and free-check: reference solution 1, nothing done 0; 8 held out, never trained on"),
]


def achievements():
    rows = ["<table><tr><th>Measure</th><th>Ethereus-9B-v0.1</th><th>Compared with</th><th>Notes</th></tr>"]
    for m, v, c, n in ACHIEVEMENTS:
        rows.append(f"<tr><td>{esc(m)}</td><td class='big'>{esc(v)}</td><td>{esc(c)}</td><td class='f'>{esc(n)}</td></tr>")
    rows.append("</table>")
    ft = REPO / "experimental/riscv/data/ft1/manifest.json"
    if ft.exists():
        m = json.loads(ft.read_text())
        rows.append("<details><summary>Fine-tune training data, by family</summary><table><tr><th>Family</th><th>Wanted</th><th>Written</th><th>Held out</th></tr>")
        for f, d in m["families"].items():
            rows.append(f"<tr><td>{esc(f)}</td><td>{d['wanted']}</td><td>{d['written']}</td><td>{d['heldout']}</td></tr>")
        rows.append("</table></details>")
    return "".join(rows)


ROADMAP = [
    ("Now", "ETH Evals, Step 3", "Ethereus-9B-v0.1 in Internet and Internet + Skills, Ethereus-9B (untrained) and Qwen3.5-9B, all 37 tasks under the board's settings; Model only for all three."),
    ("Next", "Submission", "An evidence pack and a proposed job.yaml entry for the ETH Evals maintainers. The board runs every model itself, so a self-hosted model needs their agreement on how their CI reaches it."),
    ("Next", "Training the model to build", "Machine-checked Solidity tasks from real MIT-licensed code: Kind 1 (write a function to pass the project's own tests) has 50 proven; Kind 2 (find and fix a planted flaw, checked by the project's own tests) is generating; Kind 3 (reach an outcome on a mainnet fork) follows."),
    ("Then", "A bigger body: Kimi K3", "The same compiled layers in a 2.78T mixture-of-experts model with the same delta-rule memory. Feasibility mapped; four research items (no exact zero decay, a saturating activation, attention residuals, a new tokenizer). Small-scale stages first; the full merge needs an 8-GPU machine."),
    ("Later", "API and open call", "Once there is a K3-based model builders would choose."),
]


def roadmap():
    return "<table class='road'>" + "".join(f"<tr><td class='when'>{w}</td><td><b>{esc(t)}</b><br>{esc(d)}</td></tr>" for w, t, d in ROADMAP) + "</table>"


BENCH = REPO / "experimental/riscv/docs/eth-bench.md"
def bench_tables():
    """[(section heading, column names, rows, section text)] from eth-bench.md, in file order."""
    out, head, text, cols, rows = [], "", "", None, []
    def close():
        nonlocal cols, rows
        if cols:
            out.append((head, cols, rows))
        cols, rows = None, []
    sections = {}
    for line in BENCH.read_text().splitlines():
        if line.startswith("## "):
            close()
            head = line[3:]
            sections[head] = ""
            continue
        if head:
            sections[head] += line + "\n"
        if line.startswith("|"):
            cells = [c.strip().strip("`") for c in line.strip().strip("|").split("|")]
            if cols is None:
                cols = cells
            elif not set("".join(cells)) <= set("-: "):
                rows.append(dict(zip(cols, cells)))
        else:
            close()
    close()
    return out, sections


def fw_commands(data):
    tables, sections = bench_tables()
    rows, over, oracle, run_fit = [], {}, {}, {}
    for head, cols, rs in tables:
        if "over-80-tokens" in cols:
            for r in rs:
                over[r["op"]] = r["over-80-tokens"]
        if "oracle" in cols and "command" in cols:
            oracle = {r["command"]: r["oracle"] for r in rs}
        if "fits 80" in cols:
            run_fit = {r["call"]: r for r in rs}
    live = (data or {}).get("gate_times", {})

    def secs(name):
        t = live.get(name)
        return f"{t['seconds']} total, {t['chip_seconds']} in circuit" if t else "not measured here"

    def run_window(name):
        r = run_fit.get(name)
        return (f"over: {r['tokens']} tokens" if r["fits 80"] == "no" else "no") if r else U
    spill_on = re.search(r"^SPILL_ON_GPU = (\w+)", (REPO / "experimental/riscv/chipformer/rv/serve.py").read_text(), re.M)
    spill_on = spill_on[1] if spill_on else U
    def mism(head):
        m = re.search(r"(\d+) accepted cases, (\d+) mismatches", sections.get(head, ""))
        if m:
            return m[2]
        return "0 (bench: all equal)" if "all equal" in sections.get(head, "") else U
    for head, cols, rs in tables:
        state = "PASS" if "PASS" in head else U
        date = (re.search(r"\d{4}-\d{2}-\d{2}", head) or [U])[0]
        if "cases" in cols and "instr/call" in cols:
            for r in rs:
                wb = over.get(r["op"], "")
                n = r["cases"]
                st = state if n != "0" else "PASS inline untested: window-starved; covered via @file"
                rows.append([r["op"], st, oracle.get(r["op"], U), n, mism(head), r["instr/call"], secs(r["op"]), wb if wb and not wb.startswith("0") else "no", date])
        elif "EVM gas" in cols:
            for r in rs:
                name = "run " + r["call"]
                rows.append([name, state, oracle.get("run", U), "12 (all calls)", mism(head), r["instr/call"], secs(name), run_window(name), date])
        elif "via @file" in " ".join(cols):
            for r in rs:
                rows.append([r[cols[0]] + " via @file", state, r["oracle"], r["cases"], mism(head), r["instr avg"], "not measured here", "bypasses it, reference only", date])
    h = ["Command", "State", "Oracle", "Cases", "Mismatches", "Instr/call", "Seconds on this VM (gate 4)", "Window-bound (over 80 tokens)", "Date"]
    o = ["<table><tr>" + "".join(f"<th>{x}</th>" for x in h) + "<th>GPU</th></tr>"]
    for r in rows:
        gpu = "ok"
        if "@file" in r[0]:
            gpu = f"GPU-off: spill {spill_on}" if spill_on != "True" else "ok"
        elif r[7] == U:
            gpu = U
        elif r[7] != "no":
            gpu = "long calls spill to the file buffer" if spill_on == "True" else "over-window calls error on GPU"
        o.append("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in r) + f"<td class='{'warn' if gpu != 'ok' else ''}'>{esc(gpu)}</td></tr>")
    o.append("</table>")
    return "".join(o), tables, sections


def fw_trend(tables, sections):
    series = {}
    prev = re.search(r"Previous run[^:]*: keccak ([\d,]+) / u256 ([\d,]+) instr", " ".join(sections.values()).replace("\n", " "))
    if prev:
        series.setdefault("keccak", []).append(("before XKCP", prev[1]))
        series.setdefault("u256", []).append(("before XKCP", prev[2]))
    for head, cols, rs in tables:
        date = (re.search(r"\d{4}-\d{2}-\d{2}", head) or [None])[0]
        if not date:
            continue
        key = "instr/call" if "instr/call" in cols else "instr avg" if "instr avg" in cols else None
        if not key:
            continue
        for r in rs:
            name = r[cols[0]] + (" via @file" if key == "instr avg" else "")
            if r[key] not in ("—", ""):
                series.setdefault(name, []).append((date, r[key]))
    rows = "".join(f"<tr><td>{esc(k)}</td><td>{' &rarr; '.join(f'{esc(v)} ({esc(d)})' for d, v in pts)}</td></tr>" for k, pts in series.items())
    return f"<table><tr><th>Tool</th><th>Instructions, by dated bench section</th></tr>{rows}</table><p class='f'>Most tools have one dated entry so far. Source: eth-bench.md.</p>"


def fw_artifacts(data):
    R = "experimental/riscv/"
    def git_sha(ref, path):
        r = subprocess.run(["git", "show", f"{ref}:{R}{path}"], cwd=REPO, capture_output=True)
        return hashlib.sha256(r.stdout).hexdigest() if r.returncode == 0 else None
    cols = ["main", "Route A branch", "eval machine, Route A", "eval machine, main (served)"]
    head = "<table><tr><th>Artifact</th>" + "".join(f"<th>{c}</th>" for c in cols) + "<th>Check</th></tr>"
    rows = []
    box = (data or {}).get("hashes", {})
    def add(name, values, need=None):
        main, ra, box_ra, box_main = values
        bad = bool(main and box_main and main != box_main)
        older = bool(main and ra and ra != main)
        cells = "".join(f"<td class='f'>{(v[:12] if v else 'n/a' if data else U)}</td>" for v in values)
        check = "MISMATCH: eval machine differs from main" if bad else ("match" + (" (Route A keeps an older build; evals serve main's)" if older else "") if main else U)
        rows.append(f"<tr class='{'red' if bad else ''}'><td>{esc(name)}</td>{cells}<td>{check}</td></tr>")
    for name, path in ARTIFACTS.items():
        add(name, [git_sha("origin/main", path), git_sha("origin/route-a", path),
                   box.get("route-a checkout", {}).get(name), box.get("main checkout", {}).get(name)])
    try:
        man = json.loads(subprocess.run(["git", "show", f"origin/main:{R}build/ethskills.fs.manifest.json"], cwd=REPO, capture_output=True, text=True).stdout)
        elf = git_sha("origin/main", ARTIFACTS["eth-bridge.elf"])
        ok = man["firmware_sha256"] == elf
        rows.append(f"<tr class='{'' if ok else 'red'}'><td>snapshot manifest's firmware hash vs eth-bridge.elf</td><td class='f'>{man['firmware_sha256'][:12]}</td><td></td><td></td><td></td><td>{'match' if ok else 'MISMATCH'}</td></tr>")
    except Exception:
        rows.append(f"<tr><td>snapshot manifest's firmware hash</td><td>{U}</td><td></td><td></td><td></td><td>{U}</td></tr>")
    return head + "".join(rows) + "</table>"


def fw_limits():
    spec = (REPO / "experimental/riscv/chipformer/rv/spec.py").read_text()
    def words(name):
        m = re.search(rf"{name}_BASE, {name}_WORDS = \S+, 1 << (\d+)", spec)
        return 4 * (1 << int(m[1])) if m else None
    cap = re.search(r"^CAP_MAX = (\d+)", spec, re.M)
    used = {}
    try:
        r = subprocess.run(["riscv64-elf-size", "-A", str(REPO / "experimental/riscv" / ARTIFACTS["eth-bridge.elf"])], capture_output=True, text=True)
        for ln in r.stdout.splitlines():
            p = ln.split()
            if len(p) == 3 and p[0].startswith("."):
                used[p[0]] = (int(p[1]), int(p[2]))
    except Exception:
        pass
    ram = used[".bss"][0] + used[".bss"][1] if ".bss" in used else None
    def row(name, lim, u, unit="bytes"):
        limit = f"{lim:,} {unit}" if lim else U
        use = f"{u:,} ({100 * u / lim:.0f}%)" if u is not None and lim else ("n/a" if u is None and unit == "tokens" else U)
        return f"<tr><td>{name}</td><td>{limit}</td><td>{use}</td></tr>"
    store = (REPO / "experimental/riscv/chipformer/rv/store.py").read_text()

    def const(pat):
        m = re.search(pat, store, re.M)
        return int(m[1]) if m else None

    chunks, names, cmax, nmax = (const(r"n_chunks: int = (\d+)"), const(r"n_names: int = (\d+)"), const(r"^CMAX = (\d+)"), const(r"^NMAX = (\d+)"))
    try:
        man = json.loads((REPO / "experimental/riscv/build/ethskills.manifest.json").read_text())
    except Exception:
        man = {}

    def srow(name, lim, u):
        limit = f"{lim:,}" if lim else U
        pct = f" ({100 * u / lim:.0f}%)" if isinstance(u, int) and lim else ""
        return f"<tr><td>{name}</td><td>{limit}</td><td>{f'{u:,}' if isinstance(u, int) else u}{pct}</td></tr>"

    return ("<table><tr><th>Fixed limit</th><th>Size</th><th>eth-bridge.elf uses</th></tr>"
            + row("Tool-call capture window", int(cap[1]) if cap else None, None, "tokens")
            + row("RAM", words("RAM"), ram) + row("ROM (code)", words("ROM"), used.get(".text", (None,))[0])
            + row("DROM (read-only data)", words("DROM"), used.get(".rodata", (None,))[0])
            + "</table><p class='f'>From spec.py and riscv64-elf-size on main's ELF. RAM use is the end of .bss; the stack also needs room below the top of RAM.</p>"
            + "<table><tr><th>Store limit</th><th>Size</th><th>ethskills snapshot uses</th></tr>"
            + srow("Files", names, len(man["files"]) if man.get("files") else U)
            + srow("Text capacity, tokens (chunks x tokens per chunk)", chunks * cmax if chunks and cmax else None, man.get("total_tokens", U))
            + srow("Pack budget, tokens", man.get("budget"), man.get("total_tokens", U))
            + srow("Tokens per put or append", cmax, max((len(p) for f in man.get("files", []) for p in f["pieces"]), default=U))
            + srow("Tokens in a name, including its newline", nmax, "names measured by pack_skills: within limit" if man else U)
            + "</table><p class='f'>From store.py and build/ethskills.manifest.json.</p>")


def firmware(data):
    try:
        cmds, tables, sections = fw_commands(data)
        return (cmds + "<h3>Artifacts</h3>" + fw_artifacts(data) + "<h3>Instruction counts over time</h3>" + fw_trend(tables, sections)
                + "<h3>Fixed limits</h3>" + fw_limits())
    except Exception as e:
        return f"<p>{U}: could not read the firmware sources ({esc(type(e).__name__)}: {esc(e)}).</p>"


def per_task(data):
    if not data or not data.get("per_task"):
        return f"<p>{U}</p>"
    arms = ["base", "ethereus"]
    cls = {"pass": "ok", "fail": "bad", "timeout": "bad", "error": "warn"}
    rows = ["<details><summary>Earlier runs, per task and attempt</summary><table><tr><th>Task</th><th>Qwen3.5-9B</th><th>Ethereus-9B (untrained)</th></tr>"]
    for t in sorted(data["per_task"]):
        cells = "".join("<td>" + (" ".join(f"<span class='{cls.get(x, '')}'>{x}</span>" for x in data["per_task"][t].get(a, [])) or "not run") + "</td>" for a in arms)
        rows.append(f"<tr><td>{esc(t)}</td>{cells}</tr>")
    rows.append("</table></details>")
    return "".join(rows)


def timeline(n=14):
    events = []
    for line in (REPO / "docs/dispatcher-log.md").read_text().splitlines():
        m = re.match(r"- (\d{4}-\d{2}-\d{2})(?: (\d{2}:\d{2}))? (.*)", line)
        if m:
            events.append((m[1], m[2] or "", m[3]))
    latest = events[-n:][::-1]  # the log is appended in order; same-day entries have no times

    def headline(x):  # the first sentence; private names stay off the public page
        x = re.sub(r"`?frsrcom/Ethereus-9b-v0\.1`?", "the private Hugging Face repo", x)
        m = re.match(r"(.{40,280}?\.)\s", x + " ")
        return m[1] if m else x[:280].rsplit(" ", 1)[0] + " ..."
    items = "".join(f"<li><b>{esc(d + (' ' + t + ' UTC' if t else ''))}</b> {esc(headline(x))}</li>" for d, t, x in latest)
    return (f"<ul class='log'>{items}</ul><p class='f'>Latest {len(latest)} of {len(events)} entries. Full record: "
            "<a href='https://github.com/fraserbrownirl/ethereus/blob/main/docs/dispatcher-log.md'>docs/dispatcher-log.md</a>.</p>") if items else f"<p>{U}</p>"


def main():
    args = [a for a in sys.argv[1:] if a != "--push"]
    now = args[0] if args else U
    data = vm()
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>Ethereus: the tool in the weights</title>
<meta http-equiv="refresh" content="300"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{{background:#111;color:#ddd;font:14px/1.5 system-ui,sans-serif;margin:16px auto;max-width:1150px;padding:0 16px}}
h1{{color:#fff;font-size:30px;margin:8px 0 4px}}.lede{{font-size:16px;color:#ccc;max-width:900px}}
h2{{color:#fff;border-bottom:1px solid #333;padding-bottom:4px;margin-top:32px}}table{{border-collapse:collapse;width:100%;margin:8px 0}}
th,td{{border:1px solid #333;padding:5px 8px;text-align:left;vertical-align:top}}th{{background:#1c1c1c}}td.big{{color:#7fd88f;font-weight:600;white-space:nowrap}}
.off{{color:#666}}.f{{color:#8a8a8a;font-size:12px;word-break:break-word}}pre{{background:#1a1a1a;padding:8px;overflow:auto}}a{{color:#9bd}}
summary{{cursor:pointer;color:#9bd;margin:6px 0}}.ok{{color:#6c6}}.bad{{color:#d77}}.warn{{color:#db6}}.red td{{background:#4a1616;color:#fbb}}
.road td.when{{width:70px;color:#9bd;font-weight:600}}.now{{font-size:15px}}ul.log li{{margin:4px 0}}</style></head><body>
{hero()}
<h2>What it has shown so far</h2>{achievements()}
<h2>Running now</h2>{now_panel(data, now)}
<h2>Where it is going</h2>{roadmap()}
<h2>Work in progress</h2>{tasks()}
<h2>Checks and results</h2>{results(data)}{per_task(data)}
<h2>Firmware</h2>{firmware(data)}
<h2>Latest log</h2>{timeline()}
<p class='f'>Every number on this page comes from a run that was read; anything not read shows as unknown. Refreshed with each work cycle.</p>
</body></html>
"""
    out = REPO / "docs/status.html"
    out.write_text(page)
    print("wrote", out, "vm", bool(data))
    if "--push" in sys.argv:
        for c in (["git", "add", "docs/status.html"],
                  ["git", "commit", "-q", "-m", "status: tick\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>", "--", "docs/status.html"],
                  ["git", "pull", "-q", "--rebase", "origin", "main"], ["git", "push", "-q", "origin", "main"]):
            subprocess.run(c, cwd=REPO, capture_output=True)
        # Fraser, 2026-11-02: the console is also at https://ethereus-console.vercel.app. Only this page is deployed,
        # from its own folder (its .vercelignore admits index.html and vercel.json); detached, so a tick never waits.
        site = Path.home() / "Dev/ethereus-console"
        if (site / ".vercel/project.json").exists():
            (site / "index.html").write_text(page)
            import glob
            import shutil

            cli = shutil.which("vercel") or next(iter(sorted(glob.glob(str(Path.home() / ".fnm/node-versions/*/installation/bin/vercel")))), None)
            if cli is None:
                print("vercel CLI not found; console not redeployed")
            else:  # its shebang needs the node beside it on PATH
                env = {**os.environ, "PATH": f"{Path(cli).parent}:{os.environ.get('PATH', '')}"}
                subprocess.Popen([cli, "deploy", "--prod", "--yes"], cwd=site, env=env, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)


main()
