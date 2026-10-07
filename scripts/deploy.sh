#!/usr/bin/env bash
# Builds the site and uploads it to the bucket created by infra/site.
#
#   AWS_PROFILE=<profile> ./scripts/deploy.sh
#
# Layout in the bucket:
#   /index.html                  home page, never cached
#   /spike/                      the browser inference test page
#   /models/<set>/<repo>/<file>  model files, gzip-compressed, cached forever
#   /runtime/ort-<version>/      ONNX Runtime WASM files, gzip-compressed, cached forever
#
# <set> is derived from the SHA-256 of every model file deployed, so a change
# to any model gets a new path and nothing stale is served from a cache.
# CloudFront does not compress objects larger than 10 MB, which covers every
# model file, so they are stored compressed with Content-Encoding: gzip.
set -euo pipefail

cd "$(dirname "$0")/.."
root=$(pwd)

tf() { terraform -chdir=infra/site "$@"; }
bucket=$(tf output -raw bucket)
distribution=$(tf output -raw distribution_id)

# Every precision in the lock file, so the test page at /spike/ can run any
# of them. The app itself uses CLIP at q4f16 and MiniLM at q8; a browser only
# downloads the files it asks for.
models_filter='.'

stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT

echo "==> models"
(cd spike && ./scripts/fetch-models.sh "$models_filter")
model_set=$(jq -r --arg re "$models_filter" \
  '[.[] | select(.file | test($re)) | .sha256] | join("\n")' spike/models.lock.json |
  sha256sum | cut -c1-12)
jq -r --arg re "$models_filter" '.[] | select(.file | test($re)) | "\(.repo)/\(.file)"' spike/models.lock.json |
  while read -r path; do
    mkdir -p "$stage/models/$model_set/$(dirname "$path")"
    gzip -9 -n -c "spike/public/models/$path" > "$stage/models/$model_set/$path"
  done

echo "==> runtime"
ort_version=$(node -p "require('./spike/node_modules/onnxruntime-web/package.json').version")
runtime="runtime/ort-$ort_version"
mkdir -p "$stage/$runtime"
for f in ort-wasm-simd-threaded.asyncify.mjs ort-wasm-simd-threaded.asyncify.wasm \
  ort-wasm-simd-threaded.mjs ort-wasm-simd-threaded.wasm; do
  gzip -9 -n -c "spike/node_modules/onnxruntime-web/dist/$f" > "$stage/$runtime/$f"
done

echo "==> spike page"
(cd spike && SPIKE_BASE=/spike/ VITE_MODEL_BASE="/models/$model_set/" VITE_ORT_BASE="/$runtime/" \
  npx vite build --outDir "$stage/site/spike" --emptyOutDir --logLevel warn)
# Vite also emits the ONNX Runtime WASM file it finds in the bundle; the page
# loads the copy under /runtime/ instead, so this one is never requested.
rm -f "$stage"/site/spike/assets/*.wasm

echo "==> home page"
cp web/index.html "$stage/site/index.html"

immutable="public, max-age=31536000, immutable"

echo "==> upload models and runtime"
aws s3 sync "$stage/models" "s3://$bucket/models" --only-show-errors \
  --content-encoding gzip --cache-control "$immutable" \
  --exclude '*' --include '*.onnx' --content-type application/octet-stream
aws s3 sync "$stage/models" "s3://$bucket/models" --only-show-errors \
  --content-encoding gzip --cache-control "$immutable" \
  --exclude '*' --include '*.json' --content-type application/json
aws s3 sync "$stage/runtime" "s3://$bucket/runtime" --only-show-errors \
  --content-encoding gzip --cache-control "$immutable" \
  --exclude '*' --include '*.wasm' --content-type application/wasm
aws s3 sync "$stage/runtime" "s3://$bucket/runtime" --only-show-errors \
  --content-encoding gzip --cache-control "$immutable" \
  --exclude '*' --include '*.mjs' --content-type text/javascript

echo "==> upload site"
# Hashed build output first, so a page never references assets that are not
# there yet, then the pages, which are never cached. Old assets are left in
# place for anyone still holding the previous page.
aws s3 sync "$stage/site" "s3://$bucket" --only-show-errors \
  --exclude '*' --include '*/assets/*' --cache-control "$immutable"
aws s3 sync "$stage/site" "s3://$bucket" --only-show-errors --delete \
  --exclude 'models/*' --exclude 'runtime/*' \
  --exclude '*/assets/*' --cache-control "no-cache"

echo "==> remove model sets and runtimes the pages no longer use"
aws s3 rm "s3://$bucket/models/" --recursive --only-show-errors --exclude "$model_set/*"
aws s3 rm "s3://$bucket/runtime/" --recursive --only-show-errors --exclude "ort-$ort_version/*"

echo "==> invalidate pages"
aws cloudfront create-invalidation --distribution-id "$distribution" \
  --paths "/" "/index.html" "/spike/" "/spike/index.html" "/spike/fixtures/*" \
  --query 'Invalidation.Id' --output text

echo "deployed to $(tf output -raw url) (models $model_set, runtime ort-$ort_version) from $root"
