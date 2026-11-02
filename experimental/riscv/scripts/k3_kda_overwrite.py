"""Can K3's KDA be RAM? Overwrite one cell with every value after every value: a de Bruijn sequence of order 2 over
0..255 gives 65,537 writes in which each ordered pair (a -> b, a == b included) occurs exactly once. Every token
writes its value to row r and reads row r back; the read must equal the value just written, bit for bit.

K3's settings, as Moonshot's modeling code calls FLA: lower-bound gate -5 with safe gate, in-kernel sigmoid beta,
in-kernel q/k L2 norm, head dim 128, bf16 q/k/v, scale 1. alpha = 1 (raw gate -200), beta = 1 (raw 40)."""
import sys

import torch

from fla.ops.kda import chunk_kda, fused_recurrent_kda


def de_bruijn(k: int, n: int) -> list[int]:
    a, seq = [0] * k * n, []

    def db(t, p):
        if t > n:
            if n % p == 0:
                seq.extend(a[1:p + 1])
        else:
            a[t] = a[t - p]
            db(t + 1, p)
            for j in range(a[t - p] + 1, k):
                a[t] = j
                db(t + 1, t)
    sys.setrecursionlimit(10000)
    db(1, 1)
    return seq


vals = de_bruijn(256, 2)
vals = vals + vals[:1]                                       # close the cycle: 65,537 writes, 65,536 transitions
pairs = {(vals[i], vals[i + 1]) for i in range(len(vals) - 1)}
assert len(pairs) == 256 * 256, len(pairs)
dev, B, T, H, D, ROW = "cuda", 1, len(vals), 1, 128, 5
k = torch.zeros(B, T, H, D); k[..., ROW] = 1.0
v = torch.zeros(B, T, H, D); v[0, :, 0, :] = torch.tensor(vals, dtype=torch.float32)[:, None]   # every column
want = v.clone()                                             # a read right after a write returns that value
for l2 in (True, False):
    args = dict(q=k.to(dev, torch.bfloat16), k=k.to(dev, torch.bfloat16), v=v.to(dev, torch.bfloat16),
                g=torch.full((B, T, H, D), -200.0, device=dev, dtype=torch.bfloat16),
                beta=torch.full((B, T, H), 40.0, device=dev, dtype=torch.bfloat16),
                A_log=torch.zeros(H, device=dev), dt_bias=torch.zeros(H * D, device=dev),
                use_qk_l2norm_in_kernel=l2, use_gate_in_kernel=True, use_beta_sigmoid_in_kernel=True,
                lower_bound=-5.0, scale=1.0)
    for name, fn, extra in (("chunk_kda", chunk_kda, dict(safe_gate=True, transpose_state_layout=True)),
                            ("fused_recurrent_kda", fused_recurrent_kda, {})):
        o, _ = fn(**args, **extra)
        o = o.float().cpu()
        bad = (o != want)
        tokens_bad = bad.any(-1).squeeze()
        bad_pairs = {(vals[i - 1], vals[i]) for i in tokens_bad.nonzero().flatten().tolist() if i > 0}
        rbad = (o.round() != want).any(-1).squeeze().sum().item()
        print(f"{name} l2norm={l2}: writes {T}, bit-exact reads {T - tokens_bad.sum().item()}, "
              f"wrong after rounding {rbad}, transitions not bit-exact {len(bad_pairs)} of 65,536"
              + (f", e.g. {sorted(bad_pairs)[:5]}" if bad_pairs else ""), flush=True)
