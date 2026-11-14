#!/bin/bash -l
#SBATCH --account=p201509
#SBATCH --partition=gpu
#SBATCH --qos=short
#SBATCH --time=00:30:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --gpus-per-node=1
#SBATCH --job-name=ea-demo
# usage: sbatch scripts/assistant_gpu.sbatch <out_dir>
# Runs the four-account executive-assistant demo with the open-weight model; responses are cached in <out_dir>/llm_cache.jsonl.
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"
export HF_HOME=/project/home/p201509/models/hf HF_HUB_OFFLINE=1 TOKENIZERS_PARALLELISM=false MPLCONFIGDIR=/project/home/p201509/cache/mpl
PY=/project/home/p201509/envs/upwork-rag/bin/python
MODEL=/project/home/p201509/models/hf/hub/models--Qwen--Qwen2.5-7B-Instruct/snapshots/a09a35458c702b33eeacc393d103063234e8bc28
nvidia-smi --query-gpu=name,memory.total --format=csv
$PY -m assistant.demo --llm hf --model "$MODEL" --out "$1"
