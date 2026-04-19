#!/usr/bin/env bash
# Parity check: query both backends with the same text and compare
# the top-k results. Assumes the stack is up and ingest has run.
#
# Usage:
#   ./scripts/parity-check.sh
#   QUERY="a red bus" K=5 ./scripts/parity-check.sh

set -Eeuo pipefail

QUERY="${QUERY:-a photo of a cat}"
K="${K:-3}"
SEARCH_URL="${SEARCH_URL:-http://localhost:8081}"

encoded=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$QUERY")

printf '\n==> Query: %s (k=%s)\n' "$QUERY" "$K"

for backend in lance firn; do
    printf '\n--- backend=%s ---\n' "$backend"
    curl -fsS "$SEARCH_URL/search?text=$encoded&backend=$backend&k=$K" \
        | jq -c '.results[] | {filename, score}'
done

printf '\n--- overlap ---\n'
lance=$(curl -fsS "$SEARCH_URL/search?text=$encoded&backend=lance&k=$K" \
    | jq -r '.results[].filename' | sort)
firn=$(curl -fsS "$SEARCH_URL/search?text=$encoded&backend=firn&k=$K" \
    | jq -r '.results[].filename' | sort)
overlap=$(comm -12 <(echo "$lance") <(echo "$firn") | grep -c . || true)
printf 'overlap=%s/%s (filenames appearing in both top-%s lists)\n' "$overlap" "$K" "$K"
