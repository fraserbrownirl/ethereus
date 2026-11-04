# Overlap between eth-evals closed-book tasks and v0.1's ethskills-derived training rows (train split only).
import json, re, glob, collections, sys
X, F = sys.argv[1], sys.argv[2]
words = lambda s: re.findall(r"[a-z0-9]+", s.lower())
grams = lambda s, n=8: {tuple(w[i:i + n]) for w in [words(s)] for i in range(len(w) - n + 1)}
tasks = [json.loads(l) for f in sorted(glob.glob(f"{X}/tasks/*.jsonl")) for l in open(f)]
rows = [json.loads(l) for f in ("knowledge.jsonl", "files.jsonl") for l in open(f"{F}/{f}")]
train = [r for r in rows if r.get("split") == "train"]
q = lambda r: next((m["content"] for m in r["messages"] if m["role"] == "user"), "")
qg = set().union(*(grams(q(r)) for r in train)); ag = set().union(*(grams(r["answer"]) for r in train))
src_files = collections.Counter(r["source"] for r in train)
hit_q, hit_a = [], []
for t in tasks:
    if grams(t["prompt"]) & qg: hit_q.append(t["id"])
    if t.get("source_quote") and grams(t["source_quote"]) & ag: hit_a.append(t["id"])
print(f"training rows: {len(train)} of {len(rows)} (knowledge + files, split=train); eth-evals closed tasks: {len(tasks)}")
print(f"(1) tasks whose prompt shares an 8-word run with a training question: {len(hit_q)}  {hit_q[:8]}")
print(f"(2) tasks whose source_quote shares an 8-word run with a training answer: {len(hit_a)} "
      f"({sorted(collections.Counter(t['source'] for t in tasks if t['id'] in hit_a).items())})")
by = collections.Counter(t["source"] for t in tasks)
es = {s.split("/")[-1].removesuffix(".md").split("-")[0] for s in src_files}
print(f"(3) task sources: {dict(by)}; training source files: {len(src_files)}; task source names also a training file stem: "
      f"{sorted(s for s in by if s in es)}")
print("OVERLAP_IDS", " ".join(hit_a))
for i in hit_a[:0]:
    t = next(t for t in tasks if t["id"] == i); print("   e.g.", i, "|", t["source_quote"][:110])
