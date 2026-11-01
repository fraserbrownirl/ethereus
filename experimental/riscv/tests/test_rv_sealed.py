"""Harness-level read-only es/ prefix (v1)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from chipformer.rv import bridge as BR  # noqa: E402
from chipformer.rv import elf, ref  # noqa: E402
from chipformer.rv import sealedstore as SEAL  # noqa: E402
from chipformer.rv import store as ST  # noqa: E402

CALC = Path(__file__).parents[1] / "tests" / "data" / "rv" / "calc-bridge.elf"
CFG = ST.Config(slot_bits=4, n_chunks=16, n_names=8, hash_bits=2)


def fresh_session(**kw):
    m = ref.Machine.from_program(elf.program(str(CALC)))
    BR.park(m)
    T = BR.Text(BR.default_tokenizer())
    ses = SEAL.SealedSession(T, m, CFG, **kw)
    ses.qwen(T.encode("<|im_start|>assistant\nHi."))
    return ses


def test_sealed_writes_raise():
    ses = fresh_session()
    T = ses.T
    with pytest.raises(SEAL.SealedWrite):
        ses.call("put", T.encode("es/audit.md"), T.encode("x"))
    with pytest.raises(SEAL.SealedWrite):
        ses.call("append", T.encode("es/audit.md"), T.encode("x"))
    with pytest.raises(SEAL.SealedWrite):
        ses.call("delete", T.encode("es/audit.md"))


def test_unsealed_writes_and_sealed_reads_pass():
    ses = fresh_session(sealed=())
    T = ses.T
    ses.call("put", T.encode("es/audit.md"), T.encode("Use checks."))
    ses.call("put", T.encode("notes"), T.encode("Buy milk."))
    ses.seal()
    ses.call("get", T.encode("es/audit.md"))
    assert ses.printed()[-1] == "Use checks.\n"
    ses.call("get", T.encode("notes"))
    assert ses.printed()[-1] == "Buy milk.\n"
    with pytest.raises(SEAL.SealedWrite):
        ses.call("put", T.encode("es/other.md"), T.encode("y"))
    # Similar names outside the prefix are unaffected.
    ses.call("put", T.encode("ethskills-backup"), T.encode("z"))
    assert ses.printed()[-1] == "ok\n"


def test_audit_stream_finds_sealed_writes():
    ses = fresh_session(sealed=())
    T = ses.T
    ses.call("put", T.encode("es/audit.md"), T.encode("Use checks."))
    ses.call("put", T.encode("notes"), T.encode("Buy milk."))
    hits = SEAL.audit_stream(T, ses.toks, CFG)
    assert hits == [("put", "es/audit.md")], hits
