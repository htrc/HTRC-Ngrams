#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

: "${SHARD_MANIFEST:?SHARD_MANIFEST must point to the language-shard manifest}"

RUN_ROOT="${RUN_ROOT:-outputs/current_campaign}"
WAVE_SIZE="${WAVE_SIZE:-900}"
MAX_CONCURRENT_TASKS="${MAX_CONCURRENT_TASKS:-200}"
CAMPAIGN_FRESH_RUN="${FRESH_RUN:-1}"
CAMPAIGN_NAME="${CAMPAIGN_NAME:-initial}"
PROVENANCE_RUN_ID="${PROVENANCE_RUN_ID:-campaign_${CAMPAIGN_NAME}}"

if [[ ! "$CAMPAIGN_NAME" =~ ^[A-Za-z0-9._-]+$ ]]; then
    echo "CAMPAIGN_NAME may contain only letters, numbers, dot, underscore, and hyphen" >&2
    exit 1
fi

if [ "$CAMPAIGN_FRESH_RUN" = "1" ] && [ -e "$RUN_ROOT" ]; then
    echo "Fresh campaign output already exists: $RUN_ROOT" >&2
    exit 1
fi
if [ ! -f "$SHARD_MANIFEST" ]; then
    echo "Shard manifest not found: $SHARD_MANIFEST" >&2
    exit 1
fi
if [ "$MAX_CONCURRENT_TASKS" -le 0 ]; then
    echo "MAX_CONCURRENT_TASKS must be greater than zero" >&2
    exit 1
fi

CAMPAIGN_CONTROL_DIR="$RUN_ROOT/control/$CAMPAIGN_NAME"
CAMPAIGN_PENDING_DIR="$RUN_ROOT/pending/campaigns/$CAMPAIGN_NAME"
CAMPAIGN_STATUS_DIR="$RUN_ROOT/status/campaigns/$CAMPAIGN_NAME"
mkdir -p "$CAMPAIGN_CONTROL_DIR" "$RUN_ROOT/logs" \
    "$CAMPAIGN_PENDING_DIR/waves" "$CAMPAIGN_STATUS_DIR/waves" \
    "$RUN_ROOT/metadata"

WAVE_DIR="$CAMPAIGN_CONTROL_DIR/waves"
python3 scripts/create_wave_manifests.py \
    --shard-manifest "$SHARD_MANIFEST" \
    --output-dir "$WAVE_DIR" \
    --wave-size "$WAVE_SIZE"

CAMPAIGN_MANIFEST="$WAVE_DIR/campaign_manifest.json"
printf "wave_index\tarray_job_id\tfinalizer_job_id\n" \
    > "$CAMPAIGN_CONTROL_DIR/submissions.tsv"

export RUN_ROOT WAVE_DIR CAMPAIGN_MANIFEST MAX_CONCURRENT_TASKS
export CAMPAIGN_FRESH_RUN CAMPAIGN_NAME CAMPAIGN_CONTROL_DIR
export CAMPAIGN_PENDING_DIR CAMPAIGN_STATUS_DIR
export PROVENANCE_RUN_ID

bash submit_wave.sh 0
