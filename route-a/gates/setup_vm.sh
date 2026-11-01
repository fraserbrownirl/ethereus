#!/bin/bash
# One box: pinned venv, repo, harness, base model, merge. Log: /workspace/setup.log
set -euo pipefail
W=/workspace; mkdir -p $W; cd $W
export PATH=/usr/local/bin:$PATH
apt-get -qq update >/dev/null; apt-get -qq install -y python3-venv git time >/dev/null
[ -x /opt/uvenv/bin/uv ] || { python3 -m venv /opt/uvenv; /opt/uvenv/bin/pip -q install uv; ln -sf /opt/uvenv/bin/uv /usr/local/bin/uv; }
uv --version
[ -d ethereus ] || git clone -q -b route-a https://github.com/fraserbrownirl/ethereus.git
git -C ethereus pull -q --ff-only origin route-a; git -C ethereus log --format='route-a %h %s' -1
[ -d ethevals ] || git clone -q https://github.com/BuidlGuidl/ethevals.git
git -C ethevals checkout -q cc3daaf; (cd ethevals && uv sync --frozen -q); echo "ethevals $(git -C ethevals rev-parse --short HEAD)"
V=$W/venv
uv venv --clear -q --python 3.12 $V
uv pip install -q --python $V/bin/python --index-url https://download.pytorch.org/whl/cu132 --extra-index-url https://pypi.org/simple --index-strategy unsafe-best-match "torch==2.15.0+cu132"
uv pip install -q --python $V/bin/python "transformers==5.22.1" "flash-linear-attention==0.6.0" "triton==3.9.0" "tokenizers" fastapi uvicorn httpx safetensors numpy accelerate huggingface_hub sentencepiece jinja2 pillow
$V/bin/python - <<'PY'
import torch, transformers, fla, triton
assert torch.cuda.is_available(), "CUDA unavailable"
x = torch.ones(4, 4, device="cuda", dtype=torch.bfloat16); (x @ x).sum().item()
print("torch", torch.__version__, "cuda", torch.version.cuda, "gpu", torch.cuda.get_device_name(), "tf", transformers.__version__, "fla", fla.__version__, "triton", triton.__version__)
PY
uv pip freeze --python $V/bin/python > $W/venv.lock.txt; wc -l < $W/venv.lock.txt
sha256sum ethereus/experimental/riscv/tests/data/rv/eth-bridge.elf
M=$W/models/Qwen3.5-9B
[ -f $M/config.json ] || $V/bin/python -c "from huggingface_hub import snapshot_download as s; s('Qwen/Qwen3.5-9B', local_dir='$M')"
du -sh $M
D=$W/ethereus/dist/q9-rv2; mkdir -p $D
cd $W; export PYTHONPATH=ethereus/experimental/riscv:ethereus
/usr/bin/time -v $V/bin/python -m chipformer.rv.merge $M --out $D > $D/build.log 2>&1
grep -E "Elapsed|Maximum resident|Exit status" $D/build.log
(cd $D && sha256sum model-*.safetensors config.json > sha256sums.txt)
echo SETUP_DONE
