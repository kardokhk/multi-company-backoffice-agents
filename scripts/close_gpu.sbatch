#!/bin/bash -l
#SBATCH --account=p201509
#SBATCH --partition=gpu
#SBATCH --qos=short
#SBATCH --time=01:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=128
#SBATCH --gpus-per-node=4
#SBATCH --job-name=bo-close
# usage: sbatch scripts/close_gpu.sbatch <var_dir> <company ids...>
# Runs one process per GPU, companies split round-robin. Responses are cached per company in <var_dir>/llm_cache.
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"
export HF_HOME=/project/home/p201509/models/hf HF_HUB_OFFLINE=1 TOKENIZERS_PARALLELISM=false MPLCONFIGDIR=/project/home/p201509/cache/mpl
PY=/project/home/p201509/envs/upwork-rag/bin/python
MODEL=/project/home/p201509/models/hf/hub/models--Qwen--Qwen2.5-7B-Instruct/snapshots/a09a35458c702b33eeacc393d103063234e8bc28
VAR=$1; shift
mkdir -p "$VAR"
nvidia-smi --query-gpu=name,memory.total --format=csv
ids=("$@"); pids=()
for g in 0 1 2 3; do
  sel=(); for ((i=g; i<${#ids[@]}; i+=4)); do sel+=("data/companies/${ids[$i]}"); done
  [ ${#sel[@]} -eq 0 ] && continue
  CUDA_VISIBLE_DEVICES=$g $PY -m backoffice.cli close --companies "${sel[@]}" --llm hf --model "$MODEL" --var "$VAR" \
     --cache-dir "$VAR/llm_cache" --llm-for-all > "$VAR/gpu$g.log" 2>&1 &
  pids+=($!)
done
rc=0; for p in "${pids[@]}"; do wait $p || rc=1; done
tail -n 3 "$VAR"/gpu*.log
exit $rc
