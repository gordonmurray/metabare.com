#!/usr/bin/env bash
# Downloads the files in models.lock.json into models/files/<repo>/ and
# verifies each one's size and SHA-256. An optional argument is an extended
# regular expression matched against file names, so CI can fetch only the
# precisions it runs, for example: models/fetch.sh '_q4f16|_quantized|json$'.
set -euo pipefail

cd "$(dirname "$0")"
dest=files
only=${1:-.}

jq -c '.[]' models.lock.json | while read -r entry; do
  repo=$(jq -r .repo <<<"$entry")
  rev=$(jq -r .revision <<<"$entry")
  file=$(jq -r .file <<<"$entry")
  size=$(jq -r .size <<<"$entry")
  want=$(jq -r .sha256 <<<"$entry")
  out="$dest/$repo/$file"
  [[ "$file" =~ $only ]] || continue

  if [[ ! -f "$out" || $(stat -c %s "$out") != "$size" ]]; then
    mkdir -p "$(dirname "$out")"
    echo "fetch $repo/$file"
    curl -fsSL -o "$out.part" "https://huggingface.co/${repo}/resolve/${rev}/${file}"
    mv "$out.part" "$out"
  fi

  got_size=$(stat -c %s "$out")
  if [[ "$got_size" != "$size" ]]; then
    echo "size mismatch for $out: $got_size != $size" >&2
    exit 1
  fi
  got=$(sha256sum "$out" | cut -d' ' -f1)
  if [[ "$got" != "$want" ]]; then
    echo "sha256 mismatch for $out" >&2
    exit 1
  fi
done
echo "models verified in $dest"
