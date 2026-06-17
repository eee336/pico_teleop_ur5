#!/usr/bin/env bash
set -euo pipefail

DATASET_REPO_ID="${DATASET_REPO_ID:-${1:-}}"
if [[ -z "$DATASET_REPO_ID" ]]; then
  echo "Set DATASET_REPO_ID or pass it as the first argument." >&2
  exit 2
fi

OUTPUT_DIR="${OUTPUT_DIR:-outputs/train/ur5e_smolvla}"
JOB_NAME="${JOB_NAME:-ur5e_smolvla}"
BATCH_SIZE="${BATCH_SIZE:-64}"
STEPS="${STEPS:-20000}"
DEVICE="${DEVICE:-cuda}"
WANDB_ENABLE="${WANDB_ENABLE:-false}"

ROOT_ARGS=()
if [[ -n "${DATASET_ROOT:-}" ]]; then
  ROOT_ARGS+=("--dataset.root=${DATASET_ROOT}")
fi

lerobot-train \
  --policy.path=lerobot/smolvla_base \
  --dataset.repo_id="${DATASET_REPO_ID}" \
  "${ROOT_ARGS[@]}" \
  --batch_size="${BATCH_SIZE}" \
  --steps="${STEPS}" \
  --output_dir="${OUTPUT_DIR}" \
  --job_name="${JOB_NAME}" \
  --policy.device="${DEVICE}" \
  --wandb.enable="${WANDB_ENABLE}"

