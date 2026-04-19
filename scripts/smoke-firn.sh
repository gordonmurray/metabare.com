#!/usr/bin/env bash
# Smoke test for the local Firn stack.
#
# Upserts one deterministic 512-dim vector into namespace "images",
# queries it back, then probes /list (Firn v0.3.0+). The retry loop
# doubles as a readiness wait. Exits non-zero on any HTTP failure.
#
# Prereqs:
#   docker compose up -d
#   jq, python3
#
# Env overrides:
#   FIRN_URL=http://localhost:3000
#   FIRN_SMOKE_NAMESPACE=images

set -Eeuo pipefail

FIRN_URL="${FIRN_URL:-http://localhost:3000}"
NS="${FIRN_SMOKE_NAMESPACE:-images}"

step() { printf '\n==> %s\n' "$*"; }

step "Build a deterministic 512-dim unit vector"
VEC=$(python3 -c "import json; v=[0.0]*512; v[0]=1.0; print(json.dumps(v))")

step "Upsert one row into namespace '$NS' on $FIRN_URL (retries on cold start)"
for i in $(seq 1 30); do
    if out=$(curl -fsS -X POST "$FIRN_URL/ns/$NS/upsert" \
        -H 'Content-Type: application/json' \
        -d "{\"rows\":[{\"id\":1,\"vector\":$VEC,\"text\":\"smoke-test-row\"}]}" 2>/dev/null); then
        echo "$out" | jq .
        break
    fi
    if [[ $i -eq 30 ]]; then
        echo "Firn never accepted the upsert on $FIRN_URL" >&2
        exit 1
    fi
    sleep 1
done

step "Query with the same vector, k=3, expect id=1 as top hit"
curl -fsS -X POST "$FIRN_URL/ns/$NS/query" \
    -H 'Content-Type: application/json' \
    -d "{\"vector\":$VEC,\"k\":3}" \
    | jq .

step "List namespace '$NS' via /list (Firn v0.3.0+), expect id=1 in newest-first order"
curl -fsS "$FIRN_URL/ns/$NS/list?order_by=_ingested_at&order=desc&limit=5" \
    | jq .

step "Done. Confirm id=1 appears at the top of both outputs (score is L2 distance, so 0.0 is a perfect match)."
