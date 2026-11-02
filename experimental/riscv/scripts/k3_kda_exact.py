"""Can K3's KDA kernels do an exact one-hot RAM write? alpha = 1 (no decay) and beta = 1 (full overwrite) from raw
gate inputs, through FLA's own chunk_kda and fused_recurrent_kda with K3's settings: in-kernel gate with
lower_bound = -5 and safe_gate, in-kernel sigmoid beta, in-kernel q/k L2 norm, head dim 128, bf16 q/k/v."""
import torch

from fla.ops.kda import chunk_kda, fused_recurrent_kda

torch.manual_seed(0)
dev, B, T, H, D = "cuda", 1, 200, 2, 128
rows = torch.randint(0, D, (T, H))
vals = torch.randint(-256, 257, (T, H, D)).float()        # integers up to 256: 8 significant bits, exact in bf16
write = torch.rand(T, H) < 0.6                              # some tokens write, the rest only read

k = torch.zeros(B, T, H, D); q = torch.zeros(B, T, H, D)
for t in range(T):
    for h in range(H):
        k[0, t, h, rows[t, h]] = 1.0
        q[0, t, h, rows[(t * 7) % T, h]] = 1.0              # read some row written earlier (or not)
v = vals.unsqueeze(0)
u_raw = torch.full((B, T, H, D), -200.0)                    # sigmoid(-200) underflows to 0: log-decay 0, alpha 1
b_raw = torch.where(write, 40.0, -200.0).unsqueeze(0)       # beta 1 on writes, 0 on reads

# Exact reference in float64: S[h] is D x D, row r holds the value written to key r.
S = torch.zeros(H, D, D, dtype=torch.float64); ref = torch.zeros(B, T, H, D, dtype=torch.float64)
for t in range(T):
    for h in range(H):
        kk = k[0, t, h].double(); beta = 1.0 if write[t, h] else 0.0
        S[h] = S[h] + beta * torch.outer(kk, v[0, t, h].double() - S[h].T @ kk)
        ref[0, t, h] = S[h].T @ q[0, t, h].double()

A_log = torch.zeros(H, device=dev); dt_bias = torch.zeros(H * D, device=dev)
args = dict(q=q.to(dev, torch.bfloat16), k=k.to(dev, torch.bfloat16), v=v.to(dev, torch.bfloat16),
            g=u_raw.to(dev, torch.bfloat16), beta=b_raw.to(dev, torch.bfloat16), A_log=A_log, dt_bias=dt_bias,
            use_qk_l2norm_in_kernel=True, use_gate_in_kernel=True, use_beta_sigmoid_in_kernel=True,
            lower_bound=-5.0, scale=1.0)
for name, fn, extra in (("chunk_kda", chunk_kda, dict(safe_gate=True, transpose_state_layout=True)),
                        ("fused_recurrent_kda", fused_recurrent_kda, {})):
    try:
        o, _ = fn(**args, **extra)
        o = o.double().cpu()
        exact = torch.equal(o, ref)
        err = (o - ref).abs().max().item()
        print(f"{name}: output dtype {o.dtype}, exact {exact}, max abs error {err:.3g}, nonzero reads {(ref != 0).sum().item()}")
    except TypeError as e:
        print(f"{name}: signature: {e}")
# alpha and beta themselves, as the kernel's gate math computes them in fp32
u = torch.tensor([-200.0, -104.0, -90.0, -88.0, -20.0], device=dev)
print("alpha = exp(-5*sigmoid(u)) at u", u.tolist(), "->", torch.exp(-5 * torch.sigmoid(u)).tolist())
b = torch.tensor([16.0, 17.0, 20.0, 40.0], device=dev)
print("beta = sigmoid(b) at b", b.tolist(), "->", torch.sigmoid(b).tolist(), [x == 1.0 for x in torch.sigmoid(b).tolist()])
print("--- detail")
for name, fn, extra in (("chunk_kda", chunk_kda, dict(safe_gate=True, transpose_state_layout=True)),
                        ("fused_recurrent_kda", fused_recurrent_kda, {})):
    o, _ = fn(**args, **extra)
    print(name, "kernel output dtype", o.dtype)
    o = o.double().cpu()
    bad = (o != ref); rbad = (o.round() != ref)
    print(f"  reads {ref.numel()}, not bit-exact {bad.sum().item()}, wrong after rounding to integers {rbad.sum().item()}")
    if bad.any():
        i = bad.nonzero()[0].tolist(); print("  first mismatch", i, "got", o[tuple(i)].item(), "want", ref[tuple(i)].item())
# same test without the in-kernel L2 norm: keys and queries are already unit one-hots
for name, fn, extra in (("chunk_kda no-l2norm", chunk_kda, dict(safe_gate=True, transpose_state_layout=True)),
                        ("fused_recurrent_kda no-l2norm", fused_recurrent_kda, {})):
    a2 = {**args, "use_qk_l2norm_in_kernel": False}
    o, _ = fn(**a2, **extra); o = o.double().cpu()
    print(f"{name}: not bit-exact {(o != ref).sum().item()} of {ref.numel()}, max err {(o - ref).abs().max().item():.3g}")
# Isolate the chunk error: K3's lower-bound gate (exp2 shift by 5/ln2) against the plain softplus gate.
a3 = {**args, "use_qk_l2norm_in_kernel": False}; a3.pop("lower_bound")
o, _ = chunk_kda(**a3, transpose_state_layout=True); o = o.double().cpu()
print(f"chunk_kda softplus gate (no lower bound): not bit-exact {(o != ref).sum().item()} of {ref.numel()}, max err {(o - ref).abs().max().item():.3g}")
a4 = {**args, "use_qk_l2norm_in_kernel": False, "v": (v / 64).to(dev, torch.bfloat16)}
o, _ = chunk_kda(**a4, safe_gate=True, transpose_state_layout=True); o = o.double().cpu() * 64
print(f"chunk_kda lower bound, values scaled to |v|<=4: not bit-exact {(o != ref).sum().item()} of {ref.numel()}, max err {(o - ref).abs().max().item():.3g}")
# Hypothesis: the overwrite stores v_new - v_old, which needs one more significant bit than bf16 keeps.
for lo, hi, label in ((0, 127, "values 0..127 (7 bits, differences fit 8)"), (0, 255, "values 0..255 (8 bits)")):
    vv = torch.randint(lo, hi + 1, (T, H, D)).float().unsqueeze(0)
    S2 = torch.zeros(H, D, D, dtype=torch.float64); ref2 = torch.zeros(B, T, H, D, dtype=torch.float64)
    for t in range(T):
        for h in range(H):
            kk = k[0, t, h].double(); bt = 1.0 if write[t, h] else 0.0
            S2[h] = S2[h] + bt * torch.outer(kk, vv[0, t, h].double() - S2[h].T @ kk)
            ref2[0, t, h] = S2[h].T @ q[0, t, h].double()
    a5 = {**args, "use_qk_l2norm_in_kernel": True, "v": vv.to(dev, torch.bfloat16)}
    for name, fn, extra in (("chunk", chunk_kda, dict(safe_gate=True, transpose_state_layout=True)), ("fused", fused_recurrent_kda, {})):
        o, _ = fn(**a5, **extra); o = o.double().cpu()
        print(f"{label} {name} (K3 settings incl. L2 norm): bit-exact {(o == ref2).sum().item()} of {ref2.numel()}, wrong after rounding {(o.round() != ref2).sum().item()}")
