#!/bin/bash
# After run_eval2: the unscored task, the 4 RPC tasks, and the 9 judged tasks, on both arms, with the
# same 45-minute limit. PLAN_ONLY=1 prints the harness's plan and cost reserve and exits.
W=/workspace; G=$W/ethereus/route-a/gates; cd $W/ethevals
uv run --frozen python - <<'PY'
import glob, os, re, shutil, tomllib
out = "tasks-capped3"; shutil.rmtree(out, ignore_errors=True); sel = []
RERUN = {"signatures-permit2-witness-string"}
for f in sorted(glob.glob("tasks/*/*/task.toml")):
    t = tomllib.load(open(f, "rb")); src = os.path.dirname(f); n = os.path.basename(src)
    txt = "".join(open(os.path.join(a, x), errors="replace").read() for a, _, fs in os.walk(src) for x in fs if x.endswith((".toml", ".sh")))
    judged = "ANTHROPIC_API_KEY" in (t.get("verifier", {}).get("env") or {})
    if not (judged or "MAINNET_RPC_URL" in txt or n in RERUN): continue
    dst = src.replace("tasks", out, 1); shutil.copytree(src, dst)
    s = open(dst + "/task.toml").read(); m = re.search(r"^\[agent\][^\[]*", s, re.M)
    if not (m and "timeout_sec" in m.group(0)):
        s = re.sub(r"^\[agent\]\n", "[agent]\ntimeout_sec = 2700.0\n", s, count=1, flags=re.M) if m else s.replace("[verifier]", "[agent]\ntimeout_sec = 2700.0\n\n[verifier]", 1)
    tomllib.loads(s); open(dst + "/task.toml", "w").write(s); sel.append(dst)
open("/tmp/tasks3", "w").write("\n".join(sel)); print("selected", len(sel))
PY
mapfile -t T < /tmp/tasks3
if [ -n "${PLAN_ONLY:-}" ]; then
  export ETHEREUS_API_KEY=x MAINNET_RPC_URL=$(< /root/.harness/mainnet_rpc) ANTHROPIC_API_KEY=$(< /root/.harness/anthropic.key)
  uv run --frozen ethevals plan --config $W/ethereus/route-a/gates/job-ethereus.yaml --agents opencode-q9-base opencode-q9-rv2 --modes internet --n-attempts 2 --tasks "${T[@]}" 2>&1 | grep -aE '"(worst_case_usd|missing_trials|within_budget|budget_usd)"|deferred|rror'
  exit 0
fi
while ! grep -aq EVAL2_DONE $W/eval2-rv2.log; do sleep 30; done
for ARM in base rv2; do
  bash $G/serve_arm.sh $ARM || { echo "SERVER_FAILED $ARM"; exit 1; }
  BUDGET=${BUDGET:?} bash $G/run_trials.sh $ARM internet-rest-$ARM 2 "${T[@]}"
done
echo EVAL3_DONE
