#!/usr/bin/env bash
# Smoke test for the local Firn multivector path.
#
# Upserts one deterministic bag of (MV_BAG_SIZE x MV_SUB_DIM) sub-
# vectors into namespace "images-mv", queries with the same bag,
# then probes /list. Mirror of smoke-firn.sh for the multivector
# kind. Exits non-zero on any HTTP failure.
#
# Prereqs:
#   docker compose up -d
#   jq, python3
#
# Env overrides:
#   FIRN_URL=http://localhost:3000
#   FIRN_SMOKE_MV_NAMESPACE=images-mv
#   MV_BAG_SIZE=16
#   MV_SUB_DIM=32

set -Eeuo pipefail

FIRN_URL="${FIRN_URL:-http://localhost:3000}"
# Default to a smoke-only namespace so this script does not collide with the
# real demo namespace (whose sub-dim is fixed by the encoder service at first
# upsert; smoke uses a synthetic 16x32 bag that would be rejected against the
# demo namespace).
NS="${FIRN_SMOKE_MV_NAMESPACE:-images-mv-smoke}"
BAG_SIZE="${MV_BAG_SIZE:-16}"
SUB_DIM="${MV_SUB_DIM:-32}"

step() { printf '\n==> %s\n' "$*"; }

step "Build a deterministic bag of $BAG_SIZE x $SUB_DIM sub-vectors"
BAG=$(python3 -c "
import json, sys
bag_size = int(sys.argv[1])
sub_dim = int(sys.argv[2])
bag = []
for i in range(bag_size):
    row = [0.0] * sub_dim
    row[i % sub_dim] = 1.0
    bag.append(row)
print(json.dumps(bag))
" "$BAG_SIZE" "$SUB_DIM")

step "Upsert one row into namespace '$NS' on $FIRN_URL (retries on cold start)"
upsert_out=""
for i in $(seq 1 30); do
    if upsert_out=$(curl -fsS -X POST "$FIRN_URL/ns/$NS/upsert" \
        -H 'Content-Type: application/json' \
        -d "{\"rows\":[{\"id\":1,\"vectors\":$BAG,\"text\":\"smoke-mv-row\"}]}" 2>/dev/null); then
        echo "$upsert_out" | jq .
        break
    fi
    if [[ $i -eq 30 ]]; then
        echo "Firn never accepted the multivector upsert on $FIRN_URL" >&2
        exit 1
    fi
    sleep 1
done
echo "$upsert_out" | jq -e '.upserted == 1' >/dev/null \
    || { echo "FAIL: upsert did not report upserted == 1" >&2; exit 1; }

step "Query with the same bag, k=3, expect id=1 as top hit"
query_out=$(curl -fsS -X POST "$FIRN_URL/ns/$NS/query" \
    -H 'Content-Type: application/json' \
    -d "{\"vectors\":$BAG,\"k\":3}")
echo "$query_out" | jq '.results[] | {id, score, text}'
echo "$query_out" | jq -e '.results[0].id == 1' >/dev/null \
    || { echo "FAIL: top query hit was not id == 1" >&2; exit 1; }

step "List namespace '$NS' via /list, expect id=1 present in newest-first order"
list_out=$(curl -fsS "$FIRN_URL/ns/$NS/list?order_by=_ingested_at&order=desc&limit=5")
echo "$list_out" | jq '.rows[] | {id, text, ingested_at_micros}'
echo "$list_out" | jq -e 'any(.rows[]; .id == 1)' >/dev/null \
    || { echo "FAIL: list did not contain id == 1" >&2; exit 1; }

step "Done. Multivector wire contract round-trips: upsert, query, list. All assertions passed."
