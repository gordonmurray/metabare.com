#!/usr/bin/env bash
# End-to-end check against a running MetaBare stack.
#
# Ingests a note, searches for it, and asserts the round-trip. Also asserts
# idempotency, because "it worked once" is a much weaker claim than "it worked
# twice and did not duplicate anything".
#
#   make up && make smoke

set -euo pipefail

API_URL="${API_URL:-http://localhost:8080}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-180}"

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

pass() {
    echo "  ok: $*"
}

require() {
    command -v "$1" >/dev/null 2>&1 || fail "$1 is required but not installed"
}

require curl
require jq

echo "==> Waiting for the API to become ready (up to ${TIMEOUT_SECONDS}s)"
deadline=$(( $(date +%s) + TIMEOUT_SECONDS ))
until curl -sf "${API_URL}/readyz" >/dev/null 2>&1; do
    if [ "$(date +%s)" -ge "${deadline}" ]; then
        fail "API did not become ready within ${TIMEOUT_SECONDS}s. Try: docker compose logs api"
    fi
    sleep 2
done

ready=$(curl -sf "${API_URL}/readyz")
echo "${ready}" | jq -e '.object_storage == true' >/dev/null || fail "object storage unreachable"
echo "${ready}" | jq -e '.firn == true' >/dev/null || fail "Firn unreachable"
pass "API ready, dependencies reachable"

# A distinctive body so the search assertion cannot pass by accident against
# whatever else happens to be in the index.
marker="smoke-$(date +%s)-$$"
note_body="# Terraform teardown ${marker}

Error: deleting EC2 Subnet: DependencyViolation: The subnet has dependencies
and cannot be deleted. Left over ENI from the load balancer controller.
"

echo "==> Ingesting a note"
created=$(curl -sf -X POST "${API_URL}/v1/notes" \
    -H 'content-type: application/json' \
    --data "$(jq -n --arg body "${note_body}" '{body: $body}')") \
    || fail "note ingestion returned an error"

item_id=$(echo "${created}" | jq -r '.item_id')
state=$(echo "${created}" | jq -r '.state')
chunks=$(echo "${created}" | jq -r '.chunk_count')
[ "${state}" = "complete" ] || fail "expected state 'complete', got '${state}'"
[ "${chunks}" -ge 1 ] || fail "expected at least one chunk"
pass "ingested item ${item_id:0:12}… state=${state} chunks=${chunks}"

echo "==> Re-ingesting identical content (must be idempotent)"
again=$(curl -sf -X POST "${API_URL}/v1/notes" \
    -H 'content-type: application/json' \
    --data "$(jq -n --arg body "${note_body}" '{body: $body}')")
again_id=$(echo "${again}" | jq -r '.item_id')
[ "${again_id}" = "${item_id}" ] || fail "duplicate ingestion produced a different item id"
first_records=$(echo "${created}" | jq -c '.record_ids')
again_records=$(echo "${again}" | jq -c '.record_ids')
[ "${first_records}" = "${again_records}" ] || fail "duplicate ingestion produced different row ids"
pass "duplicate ingestion converged on the same item and rows"

echo "==> Fetching item status"
status=$(curl -sf "${API_URL}/v1/items/${item_id}/status")
echo "${status}" | jq -e '.text_stage == "complete"' >/dev/null || fail "text stage not complete"
echo "${status}" | jq -e '.image_stage == "not_applicable"' >/dev/null \
    || fail "a note should have no image stage"
pass "status reports text indexed, no image stage"

echo "==> Searching"
results=$(curl -sf --get "${API_URL}/v1/search" --data-urlencode "q=subnet dependency violation")
hit_count=$(echo "${results}" | jq '.hits | length')
[ "${hit_count}" -ge 1 ] || fail "search returned no hits; response: ${results}"

found=$(echo "${results}" | jq -r --arg id "${item_id}" '[.hits[] | select(.item_id == $id)] | length')
[ "${found}" -ge 1 ] || fail "the ingested note was not in the results"

top=$(echo "${results}" | jq -r '.hits[0]')
echo "${top}" | jq -e '.score_explanation | length > 0' >/dev/null \
    || fail "a score was returned without an explanation"
echo "${top}" | jq -e '.retrieval_path | length > 0' >/dev/null \
    || fail "no retrieval path reported"
pass "search returned ${hit_count} hit(s), including the ingested note"
pass "top hit path=$(echo "${top}" | jq -r '.retrieval_path') with an explained score"

echo "==> Checking metrics"
metrics=$(curl -sf "${API_URL}/metrics")
echo "${metrics}" | grep -q 'metabare_uploads_total' || fail "upload metric missing"
echo "${metrics}" | grep -q 'metabare_searches_total' || fail "search metric missing"
pass "application metrics exposed"

echo
echo "PASS: ingest, idempotency, status, search and metrics all verified"
