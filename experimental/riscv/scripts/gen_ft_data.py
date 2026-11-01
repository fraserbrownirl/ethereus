#!/usr/bin/env python3
"""Fine-tune data, docs/finetune-spec-v1.md section 3. No GPU.

Every tool response comes from running the reference machine and the reference store, never typed.
Each example is a full chat plus token segments: `train` is true only for tokens the model generates,
so the circuit's output carries no loss. Calls over the 80-token window are rejected, not truncated.

    python scripts/gen_ft_data.py [--scale 1.0] [--workers N] [--out data/ft1]

Family B (verified traces from gate and eval runs) is ingested separately once those runs exist.
"""
import argparse
import hashlib
import json
import multiprocessing as mp
import random
import re
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

import sys

RV = Path(__file__).parents[1]
sys.path.insert(0, str(RV))
from chipformer.rv import bridge as BR  # noqa: E402
from chipformer.rv import elf, ref, spill  # noqa: E402
from chipformer.rv import store as ST  # noqa: E402
from chipformer.rv.tools import ETH_TOOL  # noqa: E402

FW = RV / "tests/data/rv/eth-bridge.elf"
MAN = RV / "build/ethskills.manifest.json"
SELECTORS = RV / "chipformer/rv/env/eth/selectors.txt"
SMOKE = RV / "scripts/smoke_questions.json"
BUILTINS = json.loads((RV / "tests/data/rv/eth.builtins.json").read_text())
A, B, S = (BUILTINS[k] for k in ("alice", "bob", "spender"))
SEED = 20261101
MAX_LEN = 4096
SYSTEM = json.loads(SMOKE.read_text())["system_line"]
COUNTS = {"abi": 800, "u256": 400, "create": 200, "run": 400, "files": 600, "negative": 100, "knowledge": 500}
TOOLS = [ETH_TOOL, *ST.TOOLS]


def sel(sig):
    from eth_hash.auto import keccak

    return keccak(sig.encode())[:4].hex()


def addr(r, simple=0.5):
    if r.random() < simple:
        return r.choice([A, B, S])
    return "0x" + r.randbytes(20).hex()


def word(n):
    return f"{n:064x}"


def strip_addr(a):
    return "0" * 24 + a[2:].lower()


# --- tool families: a user turn, one or more commands, and a final answer built from the responses --------

SIGS = [ln.strip() for ln in SELECTORS.read_text().splitlines() if ln.strip() and not ln.startswith("#")]


def abi(r):
    k = r.choice(["selector"] * 5 + ["encode"] * 6 + ["decode"] * 3 + ["keccak"] * 3)
    if k == "selector":
        s = r.choice(SIGS)
        q = r.choice([f"What is the 4-byte selector of {s}?", f"I need the function selector for {s}.",
                      f"Give me the selector for `{s}`."])
        return q, [f"selector {s}"], lambda o: f"The selector of `{s}` is `{o[0]}`."
    if k == "encode":
        fn, ts = r.choice([("transfer", ["a", "n"]), ("approve", ["a", "n"]), ("transferFrom", ["a", "a", "n"]),
                           ("balanceOf", ["a"]), ("allowance", ["a", "a"])])
        vals = [addr(r) if t == "a" else str(r.randint(1, 10 ** r.randint(1, 9))) for t in ts]
        sig = f"{fn}({','.join('address' if t == 'a' else 'uint256' for t in ts)})"
        q = r.choice([f"Encode a call to {sig} with arguments {', '.join(vals)}.",
                      f"What is the exact calldata for {fn}({', '.join(vals)})?",
                      f"Build the calldata: {sig}, args {', '.join(vals)}."])
        return q, [f"encode {sig} {' '.join(vals)}"], lambda o: f"The calldata is:\n\n`{o[0]}`"
    if k == "decode":
        n = r.randint(1, 10 ** r.randint(1, 12))
        hx = "0x" + word(n)
        q = r.choice([f"Decode this uint256 return value: {hx}", f"What number is the ABI word {hx}?"])
        return q, [f"decode uint256 {hx}"], lambda o: f"The value is `{o[0]}`."
    if r.random() < 0.5:
        s = r.choice(["hello", "ethereus", "transfer", "Permit", "owner", "vault", "EIP712Domain"]) + str(r.randint(0, 99))
        q = r.choice([f"What is the keccak256 hash of the text '{s}'?", f"Hash the string '{s}' with keccak256."])
        return q, [f"keccak '{s}'"], lambda o: f"The keccak256 of '{s}' is `{o[0]}`."
    hx = "0x" + r.randbytes(r.randint(2, 16)).hex()
    q = r.choice([f"What is the keccak256 of the bytes {hx}?", f"Compute keccak256({hx})."])
    return q, [f"keccak {hx}"], lambda o: f"The keccak256 of `{hx}` is `{o[0]}`."


def u256(r):
    k = r.choice(["mul", "mul", "add", "mod", "pow", "wei", "ether", "sub", "overflow"])
    a, b = r.randint(10 ** 6, 10 ** 15), r.randint(10 ** 6, 10 ** 15)
    if k == "mul":
        return f"What is {a} * {b}, exactly?", [f"u256 {a}*{b}"], lambda o: f"{a} * {b} = `{o[0]}`."
    if k == "add":
        return f"Add {a} and {b}.", [f"u256 {a}+{b}"], lambda o: f"The sum is `{o[0]}`."
    if k == "sub":
        hi, lo = max(a, b), min(a, b)
        return f"What is {hi} - {lo}?", [f"u256 {hi}-{lo}"], lambda o: f"The difference is `{o[0]}`."
    if k == "mod":
        return f"What is {a} * {b} modulo {a // 3 + 1}?", [f"u256 {a}*{b}%{a // 3 + 1}"], lambda o: f"The result is `{o[0]}`."
    if k == "pow":
        e = r.randint(70, 255)
        return f"What is 2 to the power {e}, minus 1, as a uint256?", [f"u256 2**{e}-1"], lambda o: f"2^{e} - 1 = `{o[0]}`."
    if k == "wei":
        n = r.randint(1, 5000)
        return f"How many wei is {n} ether?", [f"u256 {n}*10**18"], lambda o: f"{n} ether is `{o[0]}` wei."
    if k == "ether":
        w = r.randint(1, 5000) * 10 ** 18
        return f"How many whole ether is {w} wei?", [f"u256 {w}/10**18"], lambda o: f"That is `{o[0]}` ether."
    e = r.randint(128, 200)
    return (f"Does (2**{e})*(2**{e}) overflow a uint256? Check it.", [f"u256 2**{e}*2**{e}!"],
            lambda o: f"The tool's checked result is: `{o[0]}`.")


def create(r):
    d = addr(r)
    if r.random() < 0.7:
        n = r.randint(0, 5000)
        q = r.choice([f"What address does {d} deploy to at nonce {n}?",
                      f"Compute the CREATE address for deployer {d}, nonce {n}."])
        return q, [f"create {d} {n}"], lambda o: f"The contract address is `{o[0]}`."
    salt, h = r.randint(0, 9999), "0x" + r.randbytes(32).hex()
    q = f"Compute the CREATE2 address for deployer {d}, salt {salt}, init code hash {h}."
    return q, [f"create2 {d} {salt} {h}"], lambda o: f"The CREATE2 address is `{o[0]}`."


def run(r):
    """Calls on the built-in contracts' fixed state. Random addresses are valid: they hold 0 there."""
    who = addr(r, 0.4)
    k = r.choice(["balance"] * 4 + ["allowance"] * 3 + ["transfer"] + ["approve"] + ["supply", "decimals", "amm"])

    def ret(o):
        return o[-1].splitlines()[0] if o[-1].startswith("0x") else o[-1]

    def number(o):
        h = ret(o)
        return int(h, 16) if re.fullmatch(r"0x[0-9a-f]+", h) else None

    def says(fn):
        return lambda o: f"`{fn}` returned `{ret(o)}`, which is {number(o)}."

    if k == "balance":
        cd = "0x" + sel("balanceOf(address)") + strip_addr(who)
        q = r.choice([f"On the built-in erc20 contract, what is the balance of {who}?",
                      f"Run balanceOf({who}) on the built-in erc20. What does it return?",
                      f"What would the built-in erc20's balanceOf return for {who}?",
                      f"How many tokens does {who} hold on the built-in erc20?"])
        return q, [f"run erc20 {cd}"], says("balanceOf")
    if k == "allowance":
        other = addr(r, 0.6)
        cd = "0x" + sel("allowance(address,address)") + strip_addr(who) + strip_addr(other)
        q = r.choice([f"On the built-in erc20, what is allowance({who}, {other})?",
                      f"What allowance has {who} given {other} on the built-in erc20?"])
        return q, [f"run erc20 {cd}"], says("allowance")
    if k in ("transfer", "approve"):
        n = r.randint(1, 900)
        sig = "transfer(address,uint256)" if k == "transfer" else "approve(address,uint256)"
        to = addr(r, 0.5)
        cd = "0x" + sel(sig) + strip_addr(to) + word(n)
        return (f"If alice calls {k}({to}, {n}) on the built-in erc20, what does the call return?",
                [f"run erc20 {cd}"], says(k))
    if k == "supply":
        cd = "0x" + sel("totalSupply()")
        q = r.choice(["What is the totalSupply of the built-in erc20 contract?", "Run totalSupply() on the built-in erc20.",
                      "How many tokens exist on the built-in erc20?", "What does the built-in erc20 return for totalSupply?"])
        return q, [f"run erc20 {cd}"], says("totalSupply")
    if k == "decimals":
        cd = "0x" + sel("decimals()")
        q = r.choice(["How many decimals does the built-in erc20 contract report?", "Run decimals() on the built-in erc20.",
                      "What does the built-in erc20 return for decimals?"])
        return q, [f"run erc20 {cd}"], says("decimals")
    a, x, y = r.randint(1, 99), r.randint(100, 9999), r.randint(100, 9999)
    cd = "0x" + sel("getAmountOut(uint256,uint256,uint256)") + word(a) + word(x) + word(y)
    return (f"On the built-in amm contract, what does getAmountOut({a}, {x}, {y}) return?", [f"run amm {cd}"], says("getAmountOut"))


NEGATIVE = [
    ("What does the `view` keyword mean on a Solidity function?", "It declares that the function reads state but does not modify it."),
    ("What does the `pure` keyword mean in Solidity?", "A pure function neither reads nor modifies contract state."),
    ("What is the difference between `memory` and `storage` in Solidity?", "`storage` persists on chain between calls, while `memory` lasts only for the current call."),
    ("What is a Solidity modifier used for?", "A modifier wraps a function with reusable checks or logic, such as an access check."),
    ("What does `msg.sender` hold?", "It holds the address that made the current call."),
    ("What is the difference between `require` and `assert` in Solidity?", "`require` validates inputs and conditions, while `assert` checks invariants that should never fail."),
    ("What does `payable` mean on a Solidity function?", "It allows the function to receive ether with the call."),
    ("What is an event in Solidity?", "An event writes an entry to the transaction logs, which off-chain code can read."),
    ("What does the `immutable` keyword do in Solidity?", "It lets a variable be set once during construction and then stay fixed in the deployed code."),
    ("What is a reentrancy attack, in one sentence?", "A contract calls out before updating its state, and the callee calls back in to exploit the stale state."),
    ("What does the checks-effects-interactions pattern recommend?", "Validate first, update state next, and make external calls last."),
    ("Who proposed Ethereum in a 2013 whitepaper?", "Vitalik Buterin."),
    ("Which programming language is most commonly used for Ethereum smart contracts?", "Solidity."),
    ("What does EVM stand for?", "Ethereum Virtual Machine."),
    ("What is an ERC-20 token, in one sentence?", "It is a fungible token following a standard interface for balances and transfers."),
    ("What is an NFT, in one sentence?", "It is a unique token, usually following a standard such as ERC-721."),
    ("What does a proxy contract do?", "It forwards calls to a separate implementation contract, which allows upgrades while keeping storage and address."),
    ("What is the capital of France?", "Paris."),
    ("What does HTTP stand for?", "Hypertext Transfer Protocol."),
    ("What is a binary search?", "It finds a value in a sorted list by repeatedly halving the search range."),
    ("Write a one-line Python expression that reverses a list `xs`.", "`xs[::-1]`"),
    ("What does a hash map do?", "It stores key-value pairs and looks values up by hashing the key."),
    ("What is the boiling point of water at sea level in Celsius?", "100 degrees Celsius."),
    ("Who wrote Pride and Prejudice?", "Jane Austen."),
    ("What is recursion?", "A function calling itself on a smaller version of the problem until it reaches a base case."),
]
PARAPHRASE = ["{}", "Quick question: {}", "{} Keep it short.", "Please answer briefly: {}"]


# --- knowledge: sentences from the store's own response, so an answer always appears in its file -----------

SENT = re.compile(r"^[A-Z`*][^|#\n]{50,220}[.]$")


def facts_of(body):
    """(heading, sentence) from a file's text. Tables, code and short lines are skipped."""
    heading, out, code = None, [], False
    for raw in body.splitlines():
        s = raw.strip()
        if s.startswith("```"):
            code = not code
        if code or not s:
            continue
        if s.startswith("#"):
            heading = s.lstrip("# ").strip()
            continue
        s = re.sub(r"^[-*]\s+", "", s).replace("**", "")
        if heading and SENT.match(s):
            out.append((heading, s))
    return out


def smoke_tests():
    d = json.loads(SMOKE.read_text())["questions"]
    return [(q["q"].lower(), q["answer"].lower(),
             q["grade"] if isinstance(q["grade"], dict) else eval(q["grade"], {"__builtins__": {}})) for q in d]  # noqa: S307


def leaks_smoke(text, smoke):
    t = text.lower()
    for q, ans, grade in smoke:
        if ans in t or SequenceMatcher(None, q, t).ratio() > 0.6:
            return True
        groups = grade.get("keywords") or []
        if groups and all(any(k.lower() in t for k in g) for g in groups):
            return True
        if grade.get("exact") and str(grade["exact"]).lower() in t:
            return True
    return False


# --- the reference: sessions, token segments --------------------------------------------------------------

G = {}


def init():
    from huggingface_hub import hf_hub_download
    from transformers import AutoTokenizer

    tok_path = BR.default_tokenizer()
    G["template"] = Path(hf_hub_download("Qwen/Qwen3.5-9B", "chat_template.jinja")).read_text()
    G["T"] = BR.Text(tok_path)
    G["hf"] = AutoTokenizer.from_pretrained(str(tok_path.parent))
    G["man"] = json.loads(MAN.read_text())
    G["cfg"] = ST.Config()
    s = ST.StoreSession(G["T"], spill.parked(FW), G["cfg"])
    s.qwen(G["T"].encode("<|im_start|>assistant\nBooting.\n"))
    for f in G["man"]["files"]:
        name = G["T"].encode(f["name"])
        for i, piece in enumerate(f["pieces"]):
            s.call("put" if i == 0 else "append", name, piece)
    G["dev"] = s.dev


def session():
    return ST.StoreSession(G["T"], spill.parked(FW), G["cfg"], G["dev"])


def enc(text):
    return G["T"].tok.encode(text, add_special_tokens=False).ids


def do_call(ses, call_ids):
    """Run the call through the reference. Returns the tokens the machine wrote back."""
    n = len(ses.qwen_text())
    ses.qwen(call_ids)
    return ses.qwen_text()[n + len(call_ids):]


def tool_text(ids):
    s = G["T"].tok.decode(ids, skip_special_tokens=False)
    m = re.search(r"<tool_response>\n?(.*?)\n?</tool_response>", s, re.S)
    return (m.group(1) if m else s).strip()


def prompt_ids(user):
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
    out = G["hf"].apply_chat_template(msgs, tools=TOOLS, chat_template=G["template"], add_generation_prompt=True,
                                       enable_thinking=False, tokenize=True)
    return list(out if isinstance(out, list) else out["input_ids"]), msgs


IM_END = 248046


def finish(meta, user, steps, answer):
    """steps: [(openai call, call ids, response ids)]. Builds the record with per-token loss flags."""
    ids, msgs = prompt_ids(user)
    segs = [{"role": "prompt", "train": False, "ids": ids}]
    for call, cids, resp in steps:
        segs.append({"role": "assistant_call", "train": True, "ids": cids})
        segs.append({"role": "tool_output", "train": False, "ids": resp})
        msgs.append({"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": call}]})
        msgs.append({"role": "tool", "content": tool_text(resp)})
    segs.append({"role": "assistant_final", "train": True, "ids": enc(answer) + [IM_END]})
    msgs.append({"role": "assistant", "content": answer})
    total = sum(len(s["ids"]) for s in segs)
    if total > MAX_LEN:
        return None, "over_max_len"
    return {**meta, "messages": msgs, "tools": [t["function"]["name"] for t in TOOLS], "n_tokens": total, "segments": segs}, None


def make(job):
    family, i = job
    r = random.Random(f"{SEED}:{family}:{i}")
    meta = {"id": f"{family}-{i:05d}", "family": family}
    T = G["T"]
    try:
        if family == "negative":
            q, a = NEGATIVE[i % len(NEGATIVE)]
            user = PARAPHRASE[(i // len(NEGATIVE)) % len(PARAPHRASE)].format(q)
            return finish(meta, user, [], a)
        if family in ("files", "knowledge"):
            return make_knowledge(meta, family, r)
        q, cmds, answer = {"abi": abi, "u256": u256, "create": create, "run": run}[family](r)
        ses, steps, outs = session(), [], []
        for cmd in cmds:
            cids = [T.TCO] + enc(spill.call_text(cmd)) + [T.TCC]
            if len(cids) - 2 > spill.WINDOW:
                return None, "over_window"
            resp = do_call(ses, cids)
            out = tool_text(resp)
            if out.startswith("error") and "overflow" not in out:
                return None, "tool_error:" + out[:30]
            outs.append(out)
            steps.append(({"name": "eth", "arguments": {"cmd": cmd}}, cids, resp))
        return finish({**meta, "tool_outputs": outs}, q, steps, answer(outs))
    except Exception as e:  # noqa: BLE001 - a bad draw is counted, not fatal
        return None, f"exception:{type(e).__name__}"


FITS = 3400  # a get response must leave room for the prompt inside MAX_LEN


def make_knowledge(meta, family, r):
    T = G["T"]
    files = [f for f in G["man"]["files"] if f["tokens"] <= FITS]
    f = r.choice(files)
    name = enc(f["name"])
    ses = session()
    steps = []
    if r.random() < 0.3:
        cids = ST.StoreSession.call_tokens("list")
        steps.append(({"name": "list", "arguments": {}}, cids, do_call(ses, cids)))
    cids = ST.StoreSession.call_tokens("get", name)
    resp = do_call(ses, cids)
    steps.append(({"name": "get", "arguments": {"name": f["name"]}}, cids, resp))
    body = tool_text(resp)
    facts = [x for x in facts_of(body) if not leaks_smoke(x[0] + " " + x[1], G.setdefault("smoke", smoke_tests()))]
    if not facts:
        return None, "no_fact"
    head, sent = r.choice(facts)
    if sent not in body:
        return None, "answer_not_in_file"
    if family == "files":
        user = r.choice([f"What should I know about {head}?", f"On Ethereum, what's the current guidance on {head}?",
                         f"Tell me about {head} in Ethereum development."])
        ans = f"{sent}\n\n(Source: `{f['name']}`)"
    else:
        words = sent.split()
        cut = r.randint(4, max(5, len(words) // 2))
        user = f"In the ethskills reference, how does this statement continue? \"{' '.join(words[:cut])} ...\""
        ans = f"{sent}\n\n(Source: `{f['name']}`)"
    if leaks_smoke(user, G["smoke"]):
        return None, "smoke_leak"
    return finish({**meta, "source": f["name"], "answer": sent}, user, steps, ans)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--workers", type=int, default=max(1, mp.cpu_count() - 2))
    ap.add_argument("--out", default=str(RV / "data/ft1"))
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    fw_sha = hashlib.sha256(FW.read_bytes()).hexdigest()
    from huggingface_hub import hf_hub_download

    template_sha = hashlib.sha256(Path(hf_hub_download("Qwen/Qwen3.5-9B", "chat_template.jinja")).read_bytes()).hexdigest()
    man = {"spec": "docs/finetune-spec-v1.md", "seed": SEED, "firmware": FW.name, "firmware_sha256": fw_sha,
           "chat_template_sha256": template_sha,
           "system_line": SYSTEM, "tools": [t["function"]["name"] for t in TOOLS], "max_len": MAX_LEN,
           "families": {}}
    with mp.Pool(a.workers, initializer=init) as pool:
        for fam, want in COUNTS.items():
            want = max(1, round(want * a.scale))
            rows, rejects, seen, i = [], Counter(), set(), 0
            while len(rows) < want and i < want * 8:
                batch = [(fam, j) for j in range(i, i + max(want, a.workers * 4))]
                i += len(batch)
                for row, why in pool.imap(make, batch):
                    if row is None:
                        rejects[why] += 1
                        continue
                    key = json.dumps(row["messages"][1:2])
                    if key in seen:
                        rejects["duplicate"] += 1
                        continue
                    seen.add(key)
                    if len(rows) < want:
                        rows.append(row)
            # hold out 10% by family, chosen by a stable hash so reruns agree
            rows.sort(key=lambda x: hashlib.sha256(x["id"].encode()).hexdigest())
            n_hold = max(1, len(rows) // 10) if len(rows) > 1 else 0
            for k, row in enumerate(rows):
                row["split"] = "heldout" if k < n_hold else "train"
            rows.sort(key=lambda x: x["id"])
            with open(out / f"{fam}.jsonl", "w") as f:
                for row in rows:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
            man["families"][fam] = {"wanted": want, "written": len(rows), "heldout": n_hold, "rejected": dict(rejects),
                                    "max_tokens": max((x["n_tokens"] for x in rows), default=0)}
            print(fam, man["families"][fam], flush=True)
    (out / "manifest.json").write_text(json.dumps(man, indent=1) + "\n")
    print("GEN_DONE")


if __name__ == "__main__":
    main()
