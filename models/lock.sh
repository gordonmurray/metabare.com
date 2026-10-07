#!/usr/bin/env bash
# Writes models.lock.json: the exact revision, size and SHA-256 of every model
# file the spike uses, read from the Hugging Face API. Run once per model
# change; fetch.sh downloads and verifies against the result.
set -euo pipefail

cd "$(dirname "$0")"

# SHA-256 of standard input, on Linux (sha256sum) or macOS (shasum).
sha256() { if command -v sha256sum > /dev/null; then sha256sum; else shasum -a 256; fi; }

# repo, revision, then the files wanted from it.
declare -A REVISION=(
  [Xenova/clip-vit-base-patch32]=d15189d7028b43f1d3e65039190477f6af591c2a
  [Xenova/all-MiniLM-L6-v2]=751bff37182d3f1213fa05d7196b954e230abad9
)
declare -A FILES=(
  [Xenova/clip-vit-base-patch32]="config.json preprocessor_config.json tokenizer.json tokenizer_config.json special_tokens_map.json onnx/vision_model.onnx onnx/vision_model_fp16.onnx onnx/vision_model_quantized.onnx onnx/vision_model_q4f16.onnx onnx/text_model.onnx onnx/text_model_fp16.onnx onnx/text_model_quantized.onnx onnx/text_model_q4f16.onnx"
  [Xenova/all-MiniLM-L6-v2]="config.json tokenizer.json tokenizer_config.json special_tokens_map.json onnx/model.onnx onnx/model_fp16.onnx onnx/model_quantized.onnx onnx/model_q4f16.onnx"
)

entries=()
for repo in "${!REVISION[@]}"; do
  rev=${REVISION[$repo]}
  api=$(curl -fsSL "https://huggingface.co/api/models/${repo}/revision/${rev}?blobs=true")
  for f in ${FILES[$repo]}; do
    entries+=("$(jq -c --arg repo "$repo" --arg rev "$rev" --arg f "$f" '
      .siblings[] | select(.rfilename == $f)
      | {repo: $repo, revision: $rev, file: $f, size: .size,
         sha256: (.lfs.sha256 // null)}' <<<"$api")")
    # Small files are plain git blobs with no SHA-256 in the API, so hash
    # them here to pin their content too.
    if [[ $(jq -r '.sha256' <<<"${entries[-1]}") == null ]]; then
      sum=$(curl -fsSL "https://huggingface.co/${repo}/resolve/${rev}/${f}" | sha256 | cut -d' ' -f1)
      entries[-1]=$(jq -c --arg s "$sum" '.sha256 = $s' <<<"${entries[-1]}")
    fi
  done
done

printf '%s\n' "${entries[@]}" | jq -s 'sort_by(.repo, .file)' > models.lock.json
echo "wrote models.lock.json with ${#entries[@]} files"
