#!/usr/bin/env python3
"""Print per-arm, per-kind counts from the run.py outputs given on the command line."""
import collections
import json
import sys

c = collections.defaultdict(lambda: [0, 0, 0, 0, 0])
for p in sys.argv[1:]:
    for line in open(p):
        r = json.loads(line)
        for key in ((r["arm"], r["kind"]), (r["arm"], "ALL")):
            s = c[key]
            s[0] += 1; s[1] += r["tool_ok"]; s[2] += r["final_ok"]; s[3] += r["final_ok_despaced"]; s[4] += bool(r["calls"])
print("arm kind n called tool_ok final_ok final_ok_despaced")
for (a, k), s in sorted(c.items()):
    print(a, k, s[0], s[4], s[1], s[2], s[3])
