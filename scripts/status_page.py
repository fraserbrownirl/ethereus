#!/usr/bin/env python3
"""Write docs/status.html: one page, five panels. Unknown values print "unknown", never a guess.

    python3 scripts/status_page.py "What I am doing and waiting on." [--push]

Sources: docs/dispatcher-tasks.md, docs/dispatcher-log.md, the eval VM over ssh, the Vast API.
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
INSTANCES = [58623317, 58640793, 58642243]
CAP = 150
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
out = {"procs": [], "versions": {}, "last_log": "", "gates": {}, "arms": {}, "test": {}}
for line in sh("nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader,nounits").splitlines():
    pid, mem = [x.strip() for x in line.split(",")]
    name = " ".join(sh(f"ps -o args= -p {pid}").split()[1:5])
    out["procs"].append({"name": name[:70], "vram_mib": mem})
for line in open("/workspace/venv.lock.txt") if os.path.exists("/workspace/venv.lock.txt") else []:
    m = re.match(r"(torch|transformers)==(.+)", line.strip())
    if m: out["versions"][m[1]] = m[2]
logs = sorted(glob.glob("/workspace/*.log"), key=os.path.getmtime)
if logs:
    for raw in reversed(open(logs[-1], errors="replace").read().splitlines()):
        t = re.sub(r"[^ -~]", "", raw).strip()
        if t:
            out["last_log"] = os.path.basename(logs[-1]) + ": " + t[:150]; break
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
def spend(x):
    if isinstance(x, dict):
        c = x.get("usage", {}).get("cost_usd", 0) if isinstance(x.get("usage"), dict) else 0
        return (c or 0) + sum(spend(v) for v in x.values())
    if isinstance(x, list): return sum(spend(v) for v in x)
    return 0
j = 0.0
for f in glob.glob("/workspace/results/internet-*/jobs/*/*__*/verifier/reward-details.json"):
    try: j += spend(json.load(open(f)))
    except Exception: pass
out["judge_usd"] = round(j, 3)
out["trial"] = sh("docker ps --format '{{.Names}}|{{.Status}}' | grep env-main | head -1")
def tail_marks(p, pat):
    if not os.path.exists(p): return []
    return [re.sub(r"[^ -~]", "", x)[:110] for x in open(p, errors="replace").read().splitlines() if re.search(pat, x)][-4:]
out["chain"] = {"Judged batch": tail_marks("/workspace/eval3.log", "RUN_EXIT|EVAL3_DONE|SERVER_FAILED|recheck|serve .* up"),
                "Gate 4 recheck": ["passed" if os.path.exists("/workspace/gate4-keep1.ok") else "not passed yet"] + tail_marks("/workspace/ethereus/dist/q9-rv2/gate4-keep1/gate4.log", "^GATE4"),
                "Fork batch": tail_marks("/workspace/eval4.log", "selected|RUN_EXIT|EVAL4_DONE|SERVER_FAILED|serve .* up"),
                "Ethereus test, before": tail_marks("/workspace/etest-before.log", r"ARM_DONE|TEST_DONE| tool (True|False)"),
                "Three-call serve rerun": tail_marks("/workspace/serve-recheck.log", "RESULT|EXIT|PASS|FAIL")}
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
out["gpu"] = sh("nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu --format=csv,noheader")
for tag in ("before", "after"):
    f = f"/workspace/results/ethereus-test/{tag}/summary.txt"
    if os.path.exists(f): out["test"][tag] = {"text": open(f).read()[:1500], "file": f}
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


def vast():
    """Rows per instance, or None if the API or key is unavailable."""
    try:
        key = None
        for line in open(os.path.expanduser("~/Dev/podminer/.env")):
            if line.startswith("VAST_API_KEY="):
                key = line.split("=", 1)[1].strip().strip("\"'")
        rows = []
        for iid in INSTANCES:
            req = urllib.request.Request(f"https://console.vast.ai/api/v0/instances/{iid}/",
                                         headers={"Authorization": "Bearer " + key})
            i = json.load(urllib.request.urlopen(req, timeout=30)).get("instances") or {}
            rows.append({"id": iid, "gpu": i.get("gpu_name") or U, "dph": i.get("dph_total"),
                         "state": i.get("intended_status") or i.get("actual_status") or U,
                         "start": i.get("start_date")})
        return rows
    except Exception:
        return None


def boxes(v, data):
    rows = v or [{"id": i, "gpu": U, "dph": None, "state": U, "start": None} for i in INSTANCES]
    out = ["<table><tr><th>id</th><th>GPU</th><th>$/h</th><th>hours so far</th><th>state</th><th>processes (VRAM)</th><th>venv</th><th>last log line</th></tr>"]
    spend, known = 0.0, v is not None
    for r in rows:
        running = r["state"] == "running"
        hours = (time.time() - r["start"]) / 3600 if running and r["start"] else None
        if hours is not None and r["dph"]:
            spend += hours * r["dph"]
        else:
            known = False
        live = running and r["id"] == 58642243 and data
        procs = "<br>".join(f"{esc(p['name'])} ({esc(p['vram_mib'])} MiB)" for p in data["procs"]) if live else U
        ver = esc(", ".join(f"{k} {w}" for k, w in data["versions"].items())) if live and data["versions"] else U
        last = esc(data["last_log"]) if live and data["last_log"] else U
        out.append(f"<tr class='{'' if running else 'off'}'><td>{r['id']}</td><td>{esc(r['gpu'])}</td>"
                   f"<td>{f'{r['dph']:.3f}' if r['dph'] else U}</td><td>{f'{hours:.1f}' if hours is not None else U}</td>"
                   f"<td>{esc(r['state'])}</td><td>{procs if procs else 'none'}</td><td>{ver}</td><td>{last}</td></tr>")
    out.append("</table>")
    out.append(f"<p>Weekly spend vs cap: {f'${spend:.2f}' if known else U} of ${CAP}."
               + ("" if known else " Stopped instances' hours are not in what I read, so the total is unknown.")
               + (f" Running instances so far: ${spend:.2f}." if not known and v else "") + "</p>")
    return "".join(out)


def tasks():
    p = REPO / "docs/dispatcher-tasks.md"
    rows = []
    for line in p.read_text().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")] if line.startswith("|") else []
        if len(cells) == 7 and cells[0] not in ("Task", "") and not set(cells[0]) <= set("-: "):
            rows.append(cells)
    head = "<tr><th>Task</th><th>State</th><th>Blocker</th><th>Crossing artefact</th><th>Started</th><th>Finished</th><th>Result</th></tr>"

    def tr(r):
        return "<tr>" + "".join(f"<td>{esc(c) if c else U}</td>" for c in r) + "</tr>"

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
                   + f"<td>{f'{g1['ratio']:.2f}x floor, greedy {g1['greedy']}' if g1 else U}</td>"
                   + f"<td>{f'{g2['exact']} exact, {g2['bad']} bad, ' + ('fla' if g2['fla'] else 'not fla') if g2 else U}</td>"
                   + f"<td>{f'{g4['ok']}/{g4['n']} clean, {g4['wrong']} wrong on replay' if g4 else U}</td>"
                   + f"<td>{esc(ve['line']) if ve else U}</td>"
                   + f"<td>{esc(', '.join(a + ' ' + b for a, b in sm['totals'])) if sm else U}</td>"
                   + "<td class='f'>" + "<br>".join(esc(x["file"]) for x in (g1, g2, g4, sm, ve) if x) + "</td></tr>")
    out.append("</table>")
    pin = data.get("env_pin")
    if pin:
        ok = not pin["drift"]
        vs = ", ".join(f"{k} {v}" for k, v in pin["versions"].items())
        out.append(f"<p style='{'' if ok else 'background:#7a5a00;color:#fff;padding:4px'}'><b>Environment:</b> "
                   + ("matches the pinned venv and driver. " if ok else "differs from the pin: " + esc("; ".join(pin["drift"][:6])) + ". ")
                   + esc(f"{vs}, driver {pin['driver']}.") + " <span class='f'>dist/q9-rv2/env-pin.json</span></p>")
    else:
        out.append(f"<p><b>Environment:</b> {U}.</p>")
    c = data.get("canary")
    out.append(f"<p><b>Canary:</b> {esc(c['result'] + ' at ' + c['at'])} <span class='f'>{esc(c['log'])}</span></p>" if c else "<p><b>Canary:</b> no run yet; it needs the GPU free.</p>")
    out.append("<h3>ETH Evals, Internet, 9B</h3><table><tr><th>Arm</th><th>Tasks scored</th><th>Tasks passed</th><th>Trials passed</th><th>Errors</th><th>File</th></tr>")
    for arm, a in data["arms"].items():
        out.append(f"<tr><td>{arm}</td><td>{a['tasks_scored']}</td><td>{a['tasks_passed']}</td>"
                   f"<td>{a['trials_passed']}/{a['trials']}</td><td>{a['errors']}</td><td class='f'>{esc(a['file'])}</td></tr>")
    out.append("</table><h3>Ethereus test (ours, not ETH Evals)</h3>")
    if data["test"]:
        for tag, t in data["test"].items():
            out.append(f"<p>{tag}: <span class='f'>{esc(t['file'])}</span></p><pre>{esc(t['text'])}</pre>")
    else:
        out.append(f"<p>{U}: no run has finished.</p>")
    return "".join(out)


def activity(data):
    if not data:
        return f"<p>{U}: the eval VM did not answer this tick.</p>"
    t = data.get("trial") or ""
    name, _, up = t.partition("|")
    rows = [f"<p><b>Running trial:</b> {esc(name.split('__')[0]) if name else 'none'}"
            + (f" ({esc(up)})" if up else "") + f". <b>GPU:</b> {esc(data.get('gpu') or U)}. "
            f"<b>Judge spend so far:</b> ${data.get('judge_usd', U)} (each usage entry in the reward files).</p>"
            "<table><tr><th>Automatic chain, in order</th><th>Latest</th></tr>"]
    for k, v in data.get("chain", {}).items():
        rows.append(f"<tr><td>{esc(k)}</td><td>{'<br>'.join(esc(x) for x in v) if v else 'not started'}</td></tr>")
    rows.append("</table>")
    ft = REPO / "experimental/riscv/data/ft1/manifest.json"
    if ft.exists():
        m = json.loads(ft.read_text())
        rows.append("<h3>Fine-tune data</h3><table><tr><th>Family</th><th>Wanted</th><th>Written</th><th>Held out</th><th>Rejected</th></tr>")
        for f, d in m["families"].items():
            rows.append(f"<tr><td>{esc(f)}</td><td>{d['wanted']}</td><td>{d['written']}</td><td>{d['heldout']}</td><td>{esc(d['rejected'])}</td></tr>")
        rows.append("</table>")
    return "".join(rows)


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
            gpu = "over-window calls error on GPU"
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
    cols = ["origin/main", "origin/route-a", "box route-a checkout", "box main checkout"]
    head = "<table><tr><th>Artifact</th>" + "".join(f"<th>{c}</th>" for c in cols) + "<th>Check</th></tr>"
    rows = []
    box = (data or {}).get("hashes", {})
    def add(name, values, need=None):
        known = {v for v in values if v}
        bad = len(known) > 1 or (need and need not in known)
        cells = "".join(f"<td class='f'>{(v[:12] if v else 'n/a' if data else U)}</td>" for v in values)
        rows.append(f"<tr class='{'red' if bad else ''}'><td>{esc(name)}</td>{cells}<td>{'MISMATCH' if bad else 'match' if known else U}</td></tr>")
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
    rows = ["<details open><summary>Per task, each attempt</summary><table><tr><th>Task</th><th>Base</th><th>Ethereus</th></tr>"]
    for t in sorted(data["per_task"]):
        cells = "".join("<td>" + (" ".join(f"<span class='{cls.get(x, '')}'>{x}</span>" for x in data["per_task"][t].get(a, [])) or "not run") + "</td>" for a in arms)
        rows.append(f"<tr><td>{esc(t)}</td>{cells}</tr>")
    rows.append("</table></details>")
    return "".join(rows)


def timeline():
    events = []
    for line in (REPO / "docs/dispatcher-log.md").read_text().splitlines():
        m = re.match(r"- (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) (.*)", line)
        if m:
            events.append((m[1], m[2]))
    events.sort(key=lambda e: e[0], reverse=True)
    items = "".join(f"<li><b>{esc(t)} UTC</b> {esc(x)}</li>" for t, x in events)
    return f"<ul>{items}</ul>" if items else f"<p>{U}</p>"


def main():
    args = [a for a in sys.argv[1:] if a != "--push"]
    now = args[0] if args else U
    data, v = vm(), vast()
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>Ethereus status</title>
<meta http-equiv="refresh" content="300"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{{background:#111;color:#ddd;font:14px/1.45 system-ui,sans-serif;margin:16px;max-width:1200px}}
h2{{color:#fff;border-bottom:1px solid #333;padding-bottom:4px;margin-top:28px}}table{{border-collapse:collapse;width:100%;margin:8px 0}}
th,td{{border:1px solid #333;padding:4px 7px;text-align:left;vertical-align:top}}th{{background:#1c1c1c}}
.off{{color:#666}}.f{{color:#888;font-size:12px;word-break:break-all}}pre{{background:#1a1a1a;padding:8px;overflow:auto}}
summary{{cursor:pointer;color:#9bd}}.ok{{color:#6c6}}.bad{{color:#d77}}.warn{{color:#db6}}.red td{{background:#4a1616;color:#fbb}}</style></head><body>
<h1>Ethereus status</h1>
<h2>Now</h2><p><b>{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}</b>. {esc(now)}</p>
<h2>Activity</h2>{activity(data)}
<h2>Boxes</h2>{boxes(v, data)}
<h2>Tasks</h2>{tasks()}
<h2>Results</h2>{results(data)}{per_task(data)}
<h2>Firmware</h2>{firmware(data)}
<h2>Timeline</h2>{timeline()}
</body></html>
"""
    out = REPO / "docs/status.html"
    out.write_text(page)
    print("wrote", out, "vm", bool(data), "vast", bool(v))
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
