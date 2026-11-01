#!/bin/bash
# Internet with the 45-minute agent limit, on a task copy that differs only in that limit.
# Base: only tasks without two finished trials. Circuit: all selected tasks. Then EVAL2_DONE.
W=/workspace; G=$W/ethereus/route-a/gates; cd $W/ethevals
uv run --frozen python - <<'PY'
import glob, json, os, re, shutil, tomllib
out = "tasks-capped"; shutil.rmtree(out, ignore_errors=True)
done = {}
for r in glob.glob("/workspace/results/internet-nokey-base/jobs/*/*__*/result.json"):
    d = json.load(open(r)); n = d.get("task_name", "").split("/")[-1]
    if d.get("verifier_result"): done[n] = done.get(n, 0) + 1
allt, base = [], []
for f in sorted(glob.glob("tasks/*/*/task.toml")):
    t = tomllib.load(open(f, "rb")); src = os.path.dirname(f); dst = src.replace("tasks", out, 1)
    if "ANTHROPIC_API_KEY" in (t.get("verifier", {}).get("env") or {}): continue
    if any("MAINNET_RPC_URL" in open(os.path.join(a, x), errors="replace").read() for a, _, fs in os.walk(src) for x in fs if x.endswith((".toml", ".sh", ".json"))) : pass
    shutil.copytree(src, dst)
    s = open(dst + "/task.toml").read()
    if "timeout_sec" not in (m.group(0) if (m := re.search(r"^\[agent\][^\[]*", s, re.M)) else ""):
        s = re.sub(r"^\[agent\]\n", "[agent]\ntimeout_sec = 2700.0\n", s, 1, re.M) if m else s.replace("[verifier]", "[agent]\ntimeout_sec = 2700.0\n\n[verifier]", 1)
    tomllib.loads(s); open(dst + "/task.toml", "w").write(s)
    allt.append(dst)
    if done.get(os.path.basename(src), 0) < 2: base.append(dst)
open("/tmp/tasks-all", "w").write("\n".join(allt)); open("/tmp/tasks-base", "w").write("\n".join(base))
print("all", len(allt), "base remaining", len(base), base)
PY
mapfile -t ALL < /tmp/tasks-all; mapfile -t BASE < /tmp/tasks-base
if [ ${#BASE[@]} -gt 0 ]; then bash $G/serve_arm.sh base; bash $G/run_trials.sh base internet-nokey-base-capped 2 "${BASE[@]}"; fi
echo "BASE_ARM_DONE $(date -u)"
bash $G/serve_arm.sh rv2; bash $G/run_trials.sh rv2 internet-nokey-rv2 2 "${ALL[@]}"
echo EVAL2_DONE
