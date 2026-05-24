#!/usr/bin/env bash
# Ingest the curated demo corpora (photo + arXiv pages) into both
# the single-vector and multivector Firn namespaces via the upload
# service.
#
# Walks scripts/photo-corpus/data and scripts/arxiv-corpus/data,
# posts each .jpg to /upload (single-vector dual-write to Lance +
# Firn images) and /upload-mv (multivector to Firn images-mv).
#
# Usage:
#   ./scripts/ingest-corpus.sh
#   ./scripts/ingest-corpus.sh --mv-only        # skip /upload
#   ./scripts/ingest-corpus.sh --single-only    # skip /upload-mv
#   ./scripts/ingest-corpus.sh --reset          # DELETE both ns first
#
# Env overrides:
#   UPLOAD_URL=http://localhost:8080
#   FIRN_URL=http://localhost:3000
#   PHOTO_DIR=scripts/photo-corpus/data
#   ARXIV_DIR=scripts/arxiv-corpus/data

set -Eeuo pipefail

UPLOAD_URL="${UPLOAD_URL:-http://localhost:8080}"
FIRN_URL="${FIRN_URL:-http://localhost:3000}"
PHOTO_DIR="${PHOTO_DIR:-scripts/photo-corpus/data}"
ARXIV_DIR="${ARXIV_DIR:-scripts/arxiv-corpus/data}"
NS_SINGLE="${FIRN_NAMESPACE:-images}"
NS_MV="${FIRN_MV_NAMESPACE:-images-mv}"

run_single=1
run_mv=1
reset=0
for arg in "$@"; do
    case "$arg" in
        --mv-only) run_single=0 ;;
        --single-only) run_mv=0 ;;
        --reset) reset=1 ;;
        -h|--help)
            sed -n '1,30p' "$0"
            exit 0
            ;;
        *)
            echo "Unknown flag: $arg" >&2
            exit 2
            ;;
    esac
done

step() { printf '\n==> %s\n' "$*"; }

if [[ $reset -eq 1 ]]; then
    step "Reset: DELETE namespaces $NS_SINGLE and $NS_MV"
    if [[ $run_single -eq 1 ]]; then
        curl -fsS -X DELETE "$FIRN_URL/ns/$NS_SINGLE" || true
        echo "  deleted: $NS_SINGLE"
    fi
    if [[ $run_mv -eq 1 ]]; then
        curl -fsS -X DELETE "$FIRN_URL/ns/$NS_MV" || true
        echo "  deleted: $NS_MV"
    fi
fi

step "Locate corpus images under $PHOTO_DIR and $ARXIV_DIR"
files=()
if [[ -d "$PHOTO_DIR" ]]; then
    while IFS= read -r f; do files+=("$f"); done < <(find "$PHOTO_DIR" -maxdepth 1 -name '*.jpg' -type f | sort)
else
    echo "  warn: $PHOTO_DIR not found, skipping"
fi
if [[ -d "$ARXIV_DIR" ]]; then
    while IFS= read -r f; do files+=("$f"); done < <(find "$ARXIV_DIR" -mindepth 2 -name '*.jpg' -type f | sort)
else
    echo "  warn: $ARXIV_DIR not found, skipping"
fi
echo "  found ${#files[@]} image(s)"

if [[ ${#files[@]} -eq 0 ]]; then
    echo "Nothing to ingest. Build the corpora first (see scripts/{photo,arxiv}-corpus/README.md)." >&2
    exit 1
fi

ok_single=0; fail_single=0
ok_mv=0; fail_mv=0

for f in "${files[@]}"; do
    name=$(basename "$f")
    if [[ $run_single -eq 1 ]]; then
        if resp=$(curl -fsS -X POST -F "file=@$f" "$UPLOAD_URL/upload" 2>&1); then
            printf 'ok   single  %s\n' "$name"
            ok_single=$((ok_single+1))
        else
            printf 'FAIL single  %s: %s\n' "$name" "$resp"
            fail_single=$((fail_single+1))
        fi
    fi
    if [[ $run_mv -eq 1 ]]; then
        if resp=$(curl -fsS -X POST -F "file=@$f" "$UPLOAD_URL/upload-mv" 2>&1); then
            printf 'ok   mv      %s\n' "$name"
            ok_mv=$((ok_mv+1))
        else
            printf 'FAIL mv      %s: %s\n' "$name" "$resp"
            fail_mv=$((fail_mv+1))
        fi
    fi
done

step "Summary"
[[ $run_single -eq 1 ]] && printf 'single-vector: %s ok, %s failed\n' "$ok_single" "$fail_single"
[[ $run_mv -eq 1 ]] && printf 'multi-vector:  %s ok, %s failed\n' "$ok_mv" "$fail_mv"

if [[ $run_single -eq 1 && $ok_single -gt 0 ]]; then
    step "Trigger lance -> MinIO sync for the lance search path"
    docker compose exec -T upload python /app/sync-to-r2.py || \
        echo "  warn: sync failed; the lance backend may lag until the next cron tick"
fi

if [[ $fail_single -gt 0 || $fail_mv -gt 0 ]]; then
    printf '\nFAIL: %s single-vector + %s multi-vector upload(s) failed.\n' \
        "$fail_single" "$fail_mv" >&2
    exit 1
fi
