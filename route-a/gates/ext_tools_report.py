#!/usr/bin/env python3
"""eth-evals tool track (33 keccak tasks) per category and model, with eth calls (Fraser, 2026-11-04).

    /workspace/venv/bin/python route-a/gates/ext_tools_report.py ARM [ARM ...]     (arms: stock untrained untrained-line v01)

Passes come from eth-evals' own result file (results/ext-<arm>-tools.json). eth calls come from the arm's server traces
(/workspace/serve-data/<arm>/traces): each trace is matched to its task by the task's prompt text in the decoded
prompt, and `<function=eth>` is counted in every generated segment, so a call that ran in the circuit counts even
though the server keeps it out of the answer. Categories: the task id's prefix, named as Fraser listed them.
"""
import glob, json, re, sys, collections
from tokenizers import Tokenizer

X = "/workspace/eth-evals-ext"
NAMES = {"calldata-sel": "selectors", "indexing-topic": "event topics", "wallets-eip55": "EIP-55 checksums",
         "derivations-create": "CREATE/CREATE2 addresses", "derivations-create2": "CREATE/CREATE2 addresses",
         "derivations-slot": "mapping slots", "calldata-enc": "ABI encode", "calldata-dec": "ABI decode"}
ORDER = ["selectors", "event topics", "EIP-55 checksums", "CREATE/CREATE2 addresses", "mapping slots",
         "ABI encode", "ABI decode"]
tok = Tokenizer.from_file("/workspace/models/Qwen3.5-9B/tokenizer.json")
tasks = [json.loads(l) for f in sorted(glob.glob(f"{X}/tasks-tools/*.jsonl")) for l in open(f)]
cat = {t["id"]: NAMES[re.sub(r"-\d+$", "", t["id"])] for t in tasks}
probe = {t["id"]: t["prompt"].strip() for t in tasks}   # the full prompt: two mapping-slot prompts share 120 characters

table = collections.OrderedDict(); per_task = {}
for arm in sys.argv[1:]:
    res = json.load(open(next(iter(glob.glob(f"{X}/results*/ext-{arm}-tools.json")))))
    passed = {r["id"]: r["pass"] for r in res["tasks"]}
    eth = collections.Counter(); seen = set(); resp = collections.defaultdict(list)
    for f in glob.glob(f"/workspace/serve-data/{arm}/traces/*.json"):
        t = json.load(open(f))
        text = tok.decode(t.get("prompt_ids", []), skip_special_tokens=False)
        tid = next((i for i, p in probe.items() if p in text), None)
        if tid is None:
            continue
        seen.add(tid)
        for seg in t.get("segments") or [{"gen_ids": t.get("gen_ids", [])}]:
            g = tok.decode(seg.get("gen_ids", []), skip_special_tokens=False)
            eth[tid] += g.count("<function=eth>")
            for call in g.split("<function=eth>")[1:]:   # the circuit's reply follows its call, as <tool_response>
                m = re.search(r"</tool_call>\s*<tool_response>\n?(.*?)\n?</tool_response>", call, re.S)
                resp[tid].append(m.group(1).strip() if m else None)
    table[arm] = (passed, eth, seen, res["benchmark"])
    expect = {t["id"]: str(t["grader"].get("expect", "")) for t in tasks}
    def klass(i):
        if passed.get(i): return "pass"
        r = resp[i]
        if not r: return "no call"
        if all(x is None for x in r): return "call, no response"
        got = [x for x in r if x is not None]
        if any(x.lower().startswith("error") for x in got) and not any(x.lower() == expect[i].lower() for x in got):
            return "firmware error"
        return "right value, wrong final" if any(x.lower() == expect[i].lower() for x in got) else "wrong value"
    per_task[arm] = {i: (klass(i), eth[i], len([x for x in resp[i] if x is not None])) for i in cat}

print("| Category | Tasks | " + " | ".join(f"{a}: passed, eth calls (tasks with any)" for a in table) + " |")
print("|---|---|" + "---|" * len(table))
for c in ORDER + ["Total"]:
    ids = [i for i in cat if c == "Total" or cat[i] == c]
    cells = []
    for a, (passed, eth, seen, _) in table.items():
        cells.append(f"{sum(passed.get(i, False) for i in ids)}/{len(ids)}, {sum(eth[i] for i in ids)} "
                     f"({sum(eth[i] > 0 for i in ids)})")
    print(f"| {c} | {len(ids)} | " + " | ".join(cells) + " |")
for a, (passed, eth, seen, bench) in table.items():
    print(f"- {a}: {len(passed)} results, traces matched for {len(seen)} of {len(tasks)} tasks; manifest "
          f"{bench['manifest_sha256'][:16]}…, harness {bench['harness_commit']}")

KL = ["pass", "no call", "call, no response", "firmware error", "right value, wrong final", "wrong value"]
print()
print("| Outcome | " + " | ".join(per_task) + " |"); print("|---|" + "---|" * len(per_task))
for k in KL:
    print(f"| {k} | " + " | ".join(str(sum(v[0] == k for v in pt.values())) for pt in per_task.values()) + " |")
print()
print("| Task | Category | " + " | ".join(f"{a}: outcome (eth calls, responses)" for a in per_task) + " |")
print("|---|---|" + "---|" * len(per_task))
for i in sorted(cat, key=lambda i: (ORDER.index(cat[i]), i)):
    print(f"| {i} | {cat[i]} | " + " | ".join(f"{pt[i][0]} ({pt[i][1]}, {pt[i][2]})" for pt in per_task.values()) + " |")
