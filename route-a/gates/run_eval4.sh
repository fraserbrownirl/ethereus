#!/bin/bash
# After run_eval3: the 7 mainnet-fork tasks on both arms, same 45-minute limit.
# PLAN_ONLY=1 prints the plan's cost reserve and exits. The RPC is only named mainnet_rpc from here on.
W=/workspace; G=$W/ethereus/route-a/gates; H=/root/.harness; cd $W/ethevals
uv run --frozen python - <<'PY'
import glob, os, re, shutil, tomllib
out = "tasks-capped4"; shutil.rmtree(out, ignore_errors=True); sel = []
for f in sorted(glob.glob("tasks/*/*/task.toml")):
    src = os.path.dirname(f)
    txt = "".join(open(os.path.join(a, x), errors="replace").read() for a, _, fs in os.walk(src) for x in fs if x.endswith((".toml", ".sh", ".yaml", ".yml")))
    if not re.search("FORK_BLOCK_NUMBER|MAINNET_RPC_URL", txt): continue
    dst = src.replace("tasks", out, 1); shutil.copytree(src, dst)
    s = open(dst + "/task.toml").read(); m = re.search(r"^\[agent\][^\[]*", s, re.M)
    if not (m and "timeout_sec" in m.group(0)):
        s = re.sub(r"^\[agent\]\n", "[agent]\ntimeout_sec = 2700.0\n", s, count=1, flags=re.M) if m else s.replace("[verifier]", "[agent]\ntimeout_sec = 2700.0\n\n[verifier]", 1)
    tomllib.loads(s); open(dst + "/task.toml", "w").write(s); sel.append(dst)
open("/tmp/tasks4", "w").write("\n".join(sel)); print("selected", len(sel), [os.path.basename(x) for x in sel])
PY
mapfile -t T < /tmp/tasks4
if [ -n "${PLAN_ONLY:-}" ]; then
  export ETHEREUS_API_KEY=x MAINNET_RPC_URL=$(< $H/mainnet_rpc.alchemy) ANTHROPIC_API_KEY=$(< $H/anthropic.key)
  uv run --frozen ethevals plan --config $W/ethereus/route-a/gates/job-ethereus.yaml --agents opencode-q9-base opencode-q9-rv2 --modes internet --n-attempts 2 --tasks "${T[@]}" 2>&1 | grep -aE '^  "(worst_case_usd|missing_trials|deferred)"|rror' | cut -c1-120
  exit 0
fi
while ! grep -aq EVAL3_DONE $W/eval3.log; do sleep 30; done
mv $H/mainnet_rpc.alchemy $H/mainnet_rpc
for ARM in base rv2; do
  bash $G/serve_arm.sh $ARM || { echo "SERVER_FAILED $ARM"; exit 1; }
  BUDGET=${BUDGET:?} bash $G/run_trials.sh $ARM internet-fork-$ARM 2 "${T[@]}"
done
echo EVAL4_DONE
