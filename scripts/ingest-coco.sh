#!/usr/bin/env bash
# Ingest N COCO images through the local /upload endpoint.
#
# Upload is dual-write: each success lands in both local Lance and
# Firn. After the batch, trigger a manual MinIO sync so the lance
# search path sees the rows without waiting for the cron tick.
#
# Usage:
#   COUNT=20 ./scripts/ingest-coco.sh
#   COCO_DIR=/path/to/images COUNT=50 ./scripts/ingest-coco.sh

set -Eeuo pipefail

COCO_DIR="${COCO_DIR:-/home/gordon/Downloads/coco_sample_images}"
COUNT="${COUNT:-20}"
OFFSET="${OFFSET:-0}"
URL="${URL:-http://localhost:8080/upload}"

if [[ ! -d "$COCO_DIR" ]]; then
    echo "COCO dir not found: $COCO_DIR" >&2
    exit 1
fi

printf 'Uploading %s image(s), skipping first %s, from %s to %s\n\n' "$COUNT" "$OFFSET" "$COCO_DIR" "$URL"

ok=0
fail=0
while IFS= read -r f; do
    name=$(basename "$f")
    if resp=$(curl -fsS -X POST -F "file=@$f" "$URL" 2>&1); then
        filename=$(echo "$resp" | jq -r '.filename' 2>/dev/null || echo "$resp")
        printf 'ok   %s -> %s\n' "$name" "$filename"
        ok=$((ok+1))
    else
        printf 'FAIL %s: %s\n' "$name" "$resp"
        fail=$((fail+1))
    fi
done < <(find "$COCO_DIR" -maxdepth 1 -name '*.jpg' -type f | sort | tail -n +$((OFFSET + 1)) | head -n "$COUNT")

printf '\nUploaded: %s ok, %s failed\n' "$ok" "$fail"

if [[ $ok -gt 0 ]]; then
    printf '\nTriggering lance -> MinIO sync for the lance search path...\n'
    docker compose exec -T upload python /app/sync-to-r2.py
    printf 'Sync done.\n'
fi
