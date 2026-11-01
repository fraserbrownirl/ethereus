"""Harness-level read-only prefixes for the object store (v1).

The snapshot ships files under ``es/``; at runtime nothing may
write there. Enforcement lives in the harness, not the circuit:

- SealedSession (a StoreSession): raises SealedWrite on a
  put/append/delete under a sealed prefix. In lockstep (chat.py lock)
  this halts generation at the offending call.
- audit_stream: scans a token stream (e.g. a --memory session, which has
  no lock reference) for sealed writes after the fact.

Circuit-level enforcement is deferred until the store is writable by
untrusted users. Boot flows (scripts/boot_fs.py) write with sealing
disabled, then seal before snapshotting.
"""
from chipformer.rv import bridge as BR
from chipformer.rv import store as ST

# NMAX=6 keeps six name tokens; "ethskills/..." (7+) truncates and collides,
# so packed files live under "es/" (all names measured <= 6 tokens).
SEALED = ("es/",)
WRITE_OPS = ("put", "append", "delete")


class SealedWrite(Exception):
    pass


def decode_name(T: BR.Text, name_qids) -> str:
    # The capture keeps the parameter's trailing newline and, when the name
    # is shorter than NMAX, the "</" closer; strip both (prefix matching is
    # unaffected either way).
    s = T.decode_bytes([q for q in name_qids if q]).decode("utf-8", "replace")
    return s.split("<")[0].rstrip("\n")


def is_sealed(name: str, sealed=SEALED) -> bool:
    return name.startswith(sealed)


class SealedSession(ST.StoreSession):
    """StoreSession that refuses writes under sealed prefixes."""

    def __init__(self, text: BR.Text, machine, cfg: ST.Config | None = None,
                 dev: ST.Device | None = None, sealed=SEALED) -> None:
        self.sealed = tuple(sealed)
        super().__init__(text, machine, cfg, dev)

    def seal(self, prefixes=SEALED) -> None:
        """Seal after a trusted boot that wrote the sealed files."""
        self.sealed = tuple(prefixes)

    def qwen(self, qids: list[int], max_records: int = 10**6, run: bool = True) -> None:
        for q in qids:
            if q == self.T.TCC and self.scan.is_store_call():
                if run and self.scan.op in WRITE_OPS:
                    name = decode_name(self.T, self.scan.name)
                    if is_sealed(name, self.sealed):
                        raise SealedWrite(f"write under sealed prefix: {name!r}")
            super().qwen([q], max_records, run)


def audit_stream(T: BR.Text, toks: list[int], cfg: ST.Config | None = None,
                 sealed=SEALED) -> list[tuple[str, str]]:
    """(op, name) of every sealed write in a token stream. For sessions
    without a lock reference (--memory): the driver calls this after each
    generation and reports violations."""
    dev = ST.Device()
    scan = ST.StoreScan(cfg or ST.Config(), dev)
    scan.nl = {q for q in range(T.n) if b"\n" in T.bytes[q]}
    out = []
    for t in toks:
        scan.feed(t)
        q = ST.qid_of(t)
        if q == ST.Q_TCC and scan.is_store_call() and scan.op in WRITE_OPS:
            name = decode_name(T, scan.name)
            if is_sealed(name, sealed):
                out.append((scan.op, name))
    return out
