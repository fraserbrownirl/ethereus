# Line test, per condition: passes, eth and files calls, OpenCode tool calls; and for each trial that called eth or
# files (in-weights, written as <function=...> in the model's text), whether it kept working: how many OpenCode
# tool calls it made after its first in-weights call. Usage: linetest_trials.py ARM [ARM ...]
import json, glob, os, sys, collections
INW = {"eth", "list", "get"}
for arm in sys.argv[1:]:
    rows = {}
    for l in open(f"/workspace/results/{arm}/rows.jsonl"):
        r = json.loads(l)
        if r.get("model") == "ethereus/ethereus-9b-untrained":
            rows[(r["task_id"].split("/")[-1], r.get("trial"))] = r
    passed = sum(r["status"] == "passed" for r in rows.values())
    tot = collections.Counter(); per = []
    for f in sorted(glob.glob(f"/workspace/results/{arm}/jobs/*/*/agent/opencode.txt")):
        trial = os.path.basename(os.path.dirname(os.path.dirname(f)))
        seq = []  # ordered events: ("inw", name) or ("oc", name)
        for l in open(f):
            try: e = json.loads(l)
            except Exception: continue
            p = e.get("part") or {}
            if p.get("type") == "tool": seq.append(("oc", str(p.get("tool"))))
            elif p.get("type") == "text":
                for x in p.get("text", "").split("<function=")[1:]: seq.append(("inw", x.split(">")[0].strip()))
        for k, n in seq:
            tot["opencode" if k == "oc" else ("eth" if n == "eth" else ("files" if n in ("list", "get") else "stray"))] += 1
        first = next((i for i, (k, n) in enumerate(seq) if k == "inw" and n in INW), None)
        if first is not None:
            after = [n for k, n in seq[first + 1:] if k == "oc"]
            calls = [n for k, n in seq if k == "inw" and n in INW]
            per.append(f"  {trial}: in-weights {dict(collections.Counter(calls))}; OpenCode calls after the first: {len(after)} "
                       f"({dict(collections.Counter(after))}) -> {'kept working' if after else 'stopped'}")
    print(f"{arm}: passed {passed} of {len(rows)}; eth {tot['eth']}, files {tot['files']}, OpenCode {tot['opencode']}, "
          f"agent-tool names in the in-weights channel {tot['stray']}; trials calling eth or files: {len(per)}")
    print("\n".join(per) or "  (none)")
