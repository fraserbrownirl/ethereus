#!/usr/bin/env python3
"""Pack ethskills + audit checklists into a store manifest.

Sources (cloned shallow if absent):
  ethskills:      austintgriffith/ethskills  (concept files)
  evm-audit-skills: austintgriffith/evm-audit-skills (master index + checklists)

Pack rule (documented, deterministic, budget-enforced): the ethskills
concept files ship FIRST (index + why, protocol, gas, l2s, standards,
tools, wallets, indexing, building-blocks); audit checklists fill ONLY
the remaining budget in rank order (master index first, then checklist
item count desc, name asc for ties). A candidate that does not fit is
excluded and the fill continues with the next. If the eth set alone
exceeds --budget the script FAILS (exit 1). The manifest records the
per-file token table (included and excluded) so the split is visible.

Names obey the hardware: every store name is asserted <= NMAX tokens
(NMAX=6; "ethskills/..." does not fit, so packed files live under
"es/"). Text splits into <=100-token pieces (CMAX=104).

Output: manifest JSON {files: [{name, pieces, tokens}], table: [...],
total_tokens, budget, sources}. Boot (scripts/boot_fs.py) puts/appends
pieces in order.

Usage:
  python scripts/pack_skills.py --out build/ethskills.manifest.json [--budget 40000]
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

RV = Path(__file__).parents[1]
sys.path.insert(0, str(RV))

from chipformer.rv import bridge as BR  # noqa: E402
from chipformer.rv.store import CMAX, NMAX  # noqa: E402

ETHSKILLS_URL = "https://github.com/austintgriffith/ethskills"
AUDITS_URL = "https://github.com/austintgriffith/evm-audit-skills"
FILL = 0.8  # a piece under this share of PIECE takes the start of the next line
PIECE = 102  # tokens per put/append (CMAX=104: the store's newline and the closer take the other two)

# (store name, relpath). Ships first; FAILs the pack if over budget.
ETH_FIXED = [
    ("es/index.md", "SKILL.md"),
    ("es/why.md", "why/SKILL.md"),
    ("es/protocol.md", "protocol/SKILL.md"),
    ("es/gas.md", "gas/SKILL.md"),
    ("es/l2s.md", "l2s/SKILL.md"),
    ("es/standards.md", "standards/SKILL.md"),
    ("es/tools.md", "tools/SKILL.md"),
    ("es/wallets.md", "wallets/SKILL.md"),
    ("es/indexing.md", "indexing/SKILL.md"),
    ("es/blocks.md", "building-blocks/SKILL.md"),
]

# (store name, relpath, rank). Master first, then item count desc.
AUDIT_RANKED = [
    ("es/amaster.md", "evm-audit-master/SKILL.md", -1),
    ("es/general.md", "evm-audit-general/references/checklist.md", 80),
    ("es/lending.md", "evm-audit-defi-lending/references/checklist.md", 80),
    ("es/amm.md", "evm-audit-defi-amm/references/checklist.md", 63),
    ("es/oracles.md", "evm-audit-oracles/references/checklist.md", 58),
    ("es/vaults.md", "evm-audit-erc4626/references/checklist.md", 54),
    ("es/staking.md", "evm-audit-defi-staking/references/checklist.md", 53),
    ("es/bridges.md", "evm-audit-bridges/references/checklist.md", 45),
    ("es/gov.md", "evm-audit-governance/references/checklist.md", 44),
    ("es/chains.md", "evm-audit-chain-specific/references/checklist.md", 42),
    ("es/asm.md", "evm-audit-assembly/references/checklist.md", 41),
    ("es/math.md", "evm-audit-precision-math/references/checklist.md", 36),
    ("es/fungible.md", "evm-audit-erc20/references/checklist.md", 36),
    ("es/sigs.md", "evm-audit-signatures/references/checklist.md", 32),
    ("es/proxies.md", "evm-audit-proxies/references/checklist.md", 32),
    ("es/nft.md", "evm-audit-erc721/references/checklist.md", 21),
    ("es/aa.md", "evm-audit-erc4337/references/checklist.md", 18),
    ("es/dos.md", "evm-audit-dos/references/checklist.md", 17),
    ("es/access.md", "evm-audit-access-control/references/checklist.md", 15),
    ("es/flash.md", "evm-audit-flashloans/references/checklist.md", 13),
]


def ensure_clone(url: str, path: Path) -> str:
    if not (path / ".git").exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--depth", "1", url, str(path)],
                       check=True, capture_output=True)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path,
                         capture_output=True, text=True, check=True)
    return sha.stdout.strip()


def trim(md: str) -> str:
    # Front matter + whitespace only; content is never summarized.
    md = re.sub(r"\A---\n.*?\n---\n", "", md, flags=re.DOTALL)
    md = re.sub(r"[ \t]+$", "", md, flags=re.MULTILINE)
    md = re.sub(r"\n{4,}", "\n\n\n", md)
    return md.strip("\n") + "\n"


def split_at_space(line: str, encode, room: int) -> tuple[str, str]:
    """(head, tail) of a line, head at most ``room`` tokens, broken where a space was: after a sentence if that keeps
    most of the room, else at a word. The store's newline takes the place of the space. ("", line) if nothing fits."""
    best = ""
    for m in re.finditer(r" ", line):
        if len(encode(line[: m.start()])) > room:
            break
        best = line[: m.start()]
    if not best:
        return "", line
    at = max(best.rfind(x) for x in (". ", "; ", ": "))
    if at > len(best) * 3 // 4:
        best = best[: at + 1]
    return best, line[len(best) + 1 :]


def split_pieces(text: str, encode, limit: int = PIECE, fill: float = FILL) -> list[list[int]]:
    """Pieces end at the file's own line ends where they can. The store writes a newline after every value it keeps
    (the call's format, store.py), so a piece leaves its last newline for the store to add. A piece far from full takes
    the start of the next line, wrapping where a space was, so the store keeps room for what the model writes.
    Cutting at a fixed token count put a newline inside words and numbers: ``depegged to 0\\n.93 ETH``."""
    assert text.endswith("\n")
    queue = text[:-1].split("\n")
    pieces, cur = [], None
    while queue:
        line = queue.pop(0)
        joined = line if cur is None else cur + "\n" + line
        n = len(encode(joined))
        if n <= limit:
            cur = joined
            continue
        have = 0 if cur is None else len(encode(cur)) + 1
        if have < fill * limit and " " in line:  # wrap this line to fill the piece
            head, tail = split_at_space(line, encode, limit - have)
            if head:
                pieces.append(encode(head if cur is None else cur + "\n" + head))
                cur = None
                queue.insert(0, tail)
                continue
        pieces.append(encode(cur))  # cur is not None: a lone line over the limit always has a head above
        cur = None
        queue.insert(0, line)
    pieces.append(encode(cur if cur is not None else ""))
    return pieces


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--budget", type=int, default=40000)
    ap.add_argument("--src", default="build/skills-src")
    args = ap.parse_args()

    src = Path(args.src)
    eth_sha = ensure_clone(ETHSKILLS_URL, src / "ethskills")
    aud_sha = ensure_clone(AUDITS_URL, src / "evm-audit-skills")
    T = BR.Text(BR.default_tokenizer())

    def pack_one(name, root, rel):
        text = trim((root / rel).read_text())
        nids = T.encode(name + "\n")
        assert len(nids) <= NMAX, f"name over NMAX: {name} ({len(nids)})"
        pieces = split_pieces(text, T.encode)
        assert all(len(p) <= CMAX for p in pieces)
        return {"name": name, "pieces": pieces, "tokens": sum(len(p) + 1 for p in pieces)}

    eth_root = src / "ethskills"
    aud_root = src / "evm-audit-skills"
    files, table = [], []
    for name, rel in ETH_FIXED:
        f = pack_one(name, eth_root, rel)
        files.append(f)
        table.append({"name": name, "repo": "ethskills", "rel": rel,
                      "tokens": f["tokens"], "pieces": len(f["pieces"]),
                      "included": True, "reason": "concept"})
    eth_total = sum(f["tokens"] for f in files)
    if eth_total > args.budget:
        raise SystemExit(
            f"FAIL: eth concept set is {eth_total} tokens, budget is "
            f"{args.budget} (over by {eth_total - args.budget})")

    total = eth_total
    for name, rel, rank in AUDIT_RANKED:
        f = pack_one(name, aud_root, rel)
        if total + f["tokens"] <= args.budget:
            files.append(f)
            total += f["tokens"]
            table.append({"name": name, "repo": "evm-audit-skills",
                          "rel": rel, "tokens": f["tokens"],
                          "pieces": len(f["pieces"]), "included": True,
                          "reason": f"rank {rank}"})
        else:
            table.append({"name": name, "repo": "evm-audit-skills",
                          "rel": rel, "tokens": f["tokens"],
                          "pieces": len(f["pieces"]), "included": False,
                          "reason": "over budget"})

    n_chunks = sum(len(f["pieces"]) for f in files)
    assert len(files) <= 512 and n_chunks <= 512, (len(files), n_chunks)

    man = {"files": files, "table": table, "total_tokens": total,
           "budget": args.budget,
           "sources": {"ethskills": eth_sha, "evm-audit-skills": aud_sha}}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(man))
    n_eth = sum(1 for t in table if t["included"] and t["repo"] == "ethskills")
    n_aud = sum(1 for t in table
                if t["included"] and t["repo"] == "evm-audit-skills")
    print(f"pack: {len(files)} files ({n_eth} eth + {n_aud} audit), "
          f"{n_chunks} chunks, {total} tokens (budget {args.budget})")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
