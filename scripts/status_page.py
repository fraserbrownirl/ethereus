#!/usr/bin/env python3
"""Write docs/status.html: one page, five panels. Unknown values print "unknown", never a guess.

    python3 scripts/status_page.py "What I am doing and waiting on." [--push]

Sources: docs/dispatcher-tasks.md, docs/dispatcher-log.md, the eval VM over ssh, the Vast API.
Nothing new is measured. No secret or host address goes on the page.
"""
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

REMOTE = r'''
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
out["gates"]["q9-rv2"] = gates("/workspace/ethereus/dist/q9-rv2")
out["gates"]["q9-store-rv"] = gates("/workspace/dist-store/q9-store-rv")
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
        if len(cells) == 6 and cells[0] not in ("Task", "") and not set(cells[0]) <= set("-: "):
            rows.append(cells)
    head = "<tr><th>Task</th><th>State</th><th>Blocker</th><th>Started</th><th>Finished</th><th>Result</th></tr>"

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
    out.append("</table><h3>ETH Evals, Internet, 9B</h3><table><tr><th>Arm</th><th>Tasks scored</th><th>Tasks passed</th><th>Trials passed</th><th>Errors</th><th>File</th></tr>")
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
summary{{cursor:pointer;color:#9bd}}</style></head><body>
<h1>Ethereus status</h1>
<h2>Now</h2><p><b>{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}</b>. {esc(now)}</p>
<h2>Boxes</h2>{boxes(v, data)}
<h2>Tasks</h2>{tasks()}
<h2>Results</h2>{results(data)}
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


main()
