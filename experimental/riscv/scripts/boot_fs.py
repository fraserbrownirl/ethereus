#!/usr/bin/env python3
"""Boot a store snapshot from a pack_skills.py manifest.

  --mode check (default): reference StoreSession only. Fast, CI-able.
    Proves the manifest is valid (names fit, pieces fit, budget fits).
  --mode snap: reference boot, then the exact boot stream through the
    circuit's reference evaluator (ir.evaluate, the path rv-tiny boots
    from): owned-span predictions are checked, the persistent heads'
    final DeltaNet rows are extracted from the carry, and the snapshot
    is saved in chat.save_store_file format with a manifest (file
    list, token count, firmware hash, sha256).
  --mode verify: torch exactly once — load the snapshot into fresh
    inits, run one files get through the real kernels, assert the body
    equals the reference.

Serving the snapshot is the author's chat.py --memory path.

Usage:
  python scripts/boot_fs.py --manifest build/ethskills.manifest.json
  python scripts/boot_fs.py --manifest ... --mode snap --out build/ethskills.fs.npz
  python scripts/boot_fs.py --manifest ... --mode verify --snapshot build/ethskills.fs.npz
"""
import argparse
import json
import sys
from pathlib import Path

RV = Path(__file__).parents[1]
sys.path.insert(0, str(RV))

from chipformer.rv import bridge as BR  # noqa: E402
from chipformer.rv import core, elf, hf, ref  # noqa: E402
from chipformer.rv import sealedstore as SEAL  # noqa: E402
from chipformer.rv import store as ST  # noqa: E402
# NOTE: no top-level chat import; save/load_store_file live in the store-circuit
# merge (route-A) that main does not have yet. snap/verify import them lazily
# and fail clearly without the merge; check runs on main.

CALC = RV / "tests/data/rv/calc-bridge.elf"
CFGS = {
    "tiny": ST.Config(slot_bits=4, n_chunks=8, n_names=4, hash_bits=1),
    "small": ST.Config(slot_bits=4, n_chunks=16, n_names=8, hash_bits=2),
    "full": ST.Config(),
}


def fresh_machine():
    m = ref.Machine.from_program(elf.program(str(CALC)))
    BR.park(m)
    return m


def build_calls(T, man, max_pieces):
    """[(op, name_ids, text_ids)] for the manifest (put first piece, append rest)."""
    calls, n = [], 0
    for f in man["files"]:
        name_ids = T.encode(f["name"])
        for i, piece in enumerate(f["pieces"]):
            if n >= max_pieces:
                return calls
            calls.append(("put" if i == 0 else "append", name_ids, piece))
            n += 1
    return calls


def check_mode(T, man):
    ses = SEAL.SealedSession(T, fresh_machine(), ST.Config(), sealed=())
    ses.qwen(T.encode("<|im_start|>assistant\nBooting.\n"))
    for op, name_ids, text_ids in build_calls(T, man, 10**9):
        ses.call(op, name_ids, text_ids)
    assert ses.answers and all(a[0] in ("put", "append") for a in ses.answers)
    fails = [a for a in ses.answers if a[2]]
    assert not fails, f"store full during boot: {fails}"
    ses.seal()
    # Every file reads back qid-identical (device-level, no decode). Each
    # put/append stores its piece plus the call template's newline.
    dev = ses.dev
    for f in man["files"]:
        want = [ST.stored(t) for p in f["pieces"] for t in [*p, ST.Q_NL]]
        ses.call("get", T.encode(f["name"]))
        got = dev.text(ses.scan.name_key())
        assert got == want, (f["name"], len(got or []), len(want))
    print(f"check: {len(man['files'])} files, "
          f"{sum(len(f['pieces']) for f in man['files'])} pieces, all round-trip")


def reference_boot(T, man, cfg):
    """The boot stream and its session (reference call tokens, no sampling)."""
    ses = ST.StoreSession(T, fresh_machine(), cfg)
    ses.qwen(T.encode("<|im_start|>assistant\nBooting.\n"))
    for op, name_ids, text_ids in build_calls(T, man, 10**9):
        ses.call(op, name_ids, text_ids)
    return ses


def snap_boot(T, man, cfg, out):
    import hashlib

    import numpy as np

    from chipformer.q35 import ir
    from chipformer.rv.chat import save_store_file

    ses = reference_boot(T, man, cfg)
    stream = ses.toks
    print(f"boot stream: {len(stream)} tokens, "
          f"{sum(ses.own)} owned", flush=True)
    # The exact boot stream through the reference evaluator; every
    # owned position must predict the stream's next token.
    c = core.build(text=T, store=cfg)
    core.snapshot(c, fresh_machine())
    carry: dict = {}
    mats = []
    for a in range(0, len(stream), 2048):
        mats.append(ir.evaluate(c, stream[a:a + 2048], carry=carry))
        print(f"  evaluated {min(a + 2048, len(stream))}/{len(stream)}",
              flush=True)
    mat = np.concatenate(mats)
    pred = core.predictions(c, mat)
    bad = [x for x in range(len(stream) - 1)
           if ses.own[x] and pred[x] != stream[x + 1]]
    assert not bad, f"reference mispredicts at {bad[:5]}"
    print(f"predictions: {sum(ses.own)} owned positions all exact",
          flush=True)
    # Persistent heads' final DeltaNet rows, straight from the carry.
    idx = {id(n): i for i, n in enumerate(c.nodes)}
    snap = {}
    for name, n in c.store.persistent.items():
        ni = idx[id(n)]
        vs = []
        for x_ in range(len(n.vheads())):
            st = carry[ni, "S", x_]
            vs.append({int(r): np.asarray(row, dtype=np.float64)
                       for r, row in st.items()})
        snap[name] = vs
    save_store_file(out, snap)
    fw = CALC.read_bytes()
    digest = hashlib.sha256(Path(out).read_bytes()).hexdigest()
    manifest = {
        "snapshot": Path(out).name,
        "sha256": digest,
        "files": [f["name"] for f in man["files"]],
        "token_count": man["total_tokens"],
        "firmware": CALC.name,
        "firmware_sha256": hashlib.sha256(fw).hexdigest(),
        "store_cfg": {"slot_bits": cfg.slot_bits, "n_chunks": cfg.n_chunks,
                      "n_names": cfg.n_names, "hash_bits": cfg.hash_bits},
        "sources": man.get("sources", {}),
    }
    mpath = Path(out).with_suffix(".manifest.json")
    mpath.write_text(json.dumps(manifest, indent=2))
    nrows = sum(len(vs[x]) for vs in snap.values() for x in range(len(vs)))
    print(f"snapshot: {out} ({Path(out).stat().st_size} bytes, "
          f"{len(snap)} heads, {nrows} rows)")
    print(f"manifest: {mpath}")


def verify_snapshot(T, man, cfg, snap_path, get_name=None):
    """Torch exactly once: fresh inits + one files get == reference body."""
    import numpy as np
    import torch

    from chipformer.rv.chat import load_store_file

    snap = load_store_file(snap_path)
    c = core.build(text=T, store=cfg)
    core.snapshot(c, fresh_machine())
    for name, n in c.store.persistent.items():
        n.init = snap.get(name)
    if get_name is None:  # smallest file: cheapest full-body generation
        get_name = min(man["files"], key=lambda f: f["tokens"])["name"]
    gs = ST.StoreSession(T, fresh_machine(), cfg)
    gs.qwen(T.encode("<|im_start|>assistant\nHi.\n"))
    gs.call("get", T.encode(get_name))
    gown = [i for i, o_ in enumerate(gs.own) if o_]
    exp = gs.toks[gown[0] + 1:]
    print(f"get {get_name}: {len(exp)} body tokens to generate", flush=True)
    model, lay, ids = hf.build_model(c, gs.toks)
    model = model.eval()
    dims = list(range(1, len(c.dims)))
    cols = [lay.dim(d) for d in dims]
    nl = c.n_layers()
    with torch.no_grad():
        o = model(input_ids=torch.tensor([[ids[None]]]), use_cache=True)
        cache = o.past_key_values
        hf.inject(c, cache)
        for t in gs.toks[: gown[0] + 1]:  # call + its TCC only
            o = model(input_ids=torch.tensor([[ids[t]]]),
                      past_key_values=cache, use_cache=True,
                      output_hidden_states=True)
            cache = o.past_key_values
        gen = []
        for j in range(len(exp)):
            h = o.hidden_states[nl][0].double().cpu().numpy()
            mat = np.zeros((1, len(c.dims)))
            mat[0, dims] = h[0, cols]
            nxt = core.predictions(c, mat)[0]
            gen.append(nxt)
            if j % 100 == 0:
                print(f"  generated {j}/{len(exp)}", flush=True)
            o = model(input_ids=torch.tensor([[ids[nxt]]]),
                      past_key_values=cache, use_cache=True,
                      output_hidden_states=True)
            cache = o.past_key_values
    assert gen == exp, (gen[:6], exp[:6])
    body = T.decode_bytes([t - BR.text_token(0) for t in gen
                           if t >= BR.text_token(0)]).decode()
    print(f"verify: get {get_name} -> {len(gen)} tokens, "
          f"{body[:60]!r} OK")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--mode", choices=["check", "snap", "verify"],
                    default="check")
    ap.add_argument("--cfg", choices=["tiny", "small", "full"],
                    default="full")
    ap.add_argument("--out", default="build/ethskills.fs.npz")
    ap.add_argument("--snapshot", default=None,
                    help="verify mode: the .npz (default: --out)")
    ap.add_argument("--get", default=None,
                    help="verify mode: file to get (default: smallest)")
    args = ap.parse_args()
    man = json.loads(Path(args.manifest).read_text())
    T = BR.Text(BR.default_tokenizer())
    if args.mode == "check":
        check_mode(T, man)
    elif args.mode == "snap":
        snap_boot(T, man, CFGS[args.cfg], args.out)
    else:
        verify_snapshot(T, man, CFGS[args.cfg],
                        args.snapshot or args.out, args.get)


if __name__ == "__main__":
    main()
