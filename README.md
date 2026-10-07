# MetaBare

Search your own images and notes by what is in them, with the machine learning
running in your browser. Nothing you add leaves your device.

[metabare.com](https://metabare.com)

## What it does

Choose a folder or files, or drop files onto the page: images (PNG, JPEG,
WebP) and notes (`.txt`, `.md`). Then search them in plain words: "a bar chart", "a terminal showing an error",
"terraform destroy failed". Each result shows why it matched: what the picture
shows, what the note means, or the words it contains.

A demo library of 50 synthetic images and 54 synthetic notes loads with one
click.

## How it works

Two embedding models run in the browser, in a Web Worker, through
[Transformers.js](https://huggingface.co/docs/transformers.js) and ONNX
Runtime Web:

| Model | Used for | Precision |
| --- | --- | --- |
| CLIP ViT-B/32 | Images, and the words of a search, in one shared space | q4f16 |
| all-MiniLM-L6-v2 | Notes, and the words of a search | q8 |

A search runs three rankings and fuses them with Reciprocal Rank Fusion:

1. images by CLIP similarity to the query;
2. notes by MiniLM similarity to the query;
3. every item by BM25 over note text and file names.

Each model loads the first time it is needed and is then cached by the
browser. The library (records, vectors and thumbnails) is stored in the
browser's IndexedDB and can be exported to a single file and imported into
another browser. Originals are not copied: the library keeps a thumbnail,
the file name and path, and for notes the text.

The models run on WebAssembly by default. WebGPU is a setting, used when the
browser offers it.

## Performance

Measured on a laptop with Intel Iris Xe graphics, Linux, on 2026-10-07.
Embedding and download figures come from the browser test page
(`spike/results/`), in Chrome 154. The rest come from the app
(`web/results/`), in Playwright's builds of Chromium 153 and Firefox 155.

| Measure | Result |
| --- | --- |
| Embedding an image, WebAssembly | 125 ms |
| Embedding an image, WebGPU | 232 ms |
| Embedding a search query, both models | 27 ms |
| Download, every model and the runtime, compressed | 139.5 MB, once |
| Searching 1,000 / 10,000 / 50,000 items | 1 / 10 / 46 ms in Chromium, 1 / 9 / 54 ms in Firefox |
| Peak memory, first visit adding 50 images and 30 notes | 0.69 to 0.84 GB in Chromium, 1.36 to 1.44 GB in Firefox |

On the demo library, image queries find 68% of the relevant images within as
many results as there are relevant images (recall@R 0.68), and note queries
62%. The scores are identical in Chrome, Firefox and WebKit.

## Browsers

| Browser | Works | Tested |
| --- | --- | --- |
| Chrome, Edge | Yes | Chrome 154, Chromium 153 |
| Firefox | Yes | Firefox 155 |
| Safari | Yes, through WebKit | Playwright's WebKit build |

Any current browser with WebAssembly, IndexedDB and Web Workers runs the app.
Whether WebGPU is offered depends on the browser and platform; see the
[WebGPU implementation status](https://github.com/gpuweb/gpuweb/wiki/Implementation-Status).

## Hosting

The site is static: a private S3 bucket behind CloudFront, with no server.

CloudFront adds `Cross-Origin-Opener-Policy` and `Cross-Origin-Embedder-Policy`
headers, which let WebAssembly use more than one thread. One thread is 3.4
times slower than four (417 ms against 124 ms per image).

Model files are stored gzip-compressed, because CloudFront does not compress
files over 10 MB, under paths named for their content, so they are cached
forever.

### What it costs

Prices for `eu-west-1` and CloudFront Europe, from the AWS Price List API on
2026-10-07, excluding tax and the free tier.

| Resource | Price | Monthly |
| --- | --- | --- |
| Route 53 hosted zone | $0.50 per zone | $0.50 |
| S3 storage | $0.023 per GB-month, about 1 GB stored | about $0.02 |
| CloudFront data transfer | $0.085 per GB | about $0.012 per first-time visitor |
| CloudFront HTTPS requests | $0.012 per 10,000 | negligible |
| CloudFront Function, one per page request | $0.10 per million | negligible |
| Certificate, budget | no charge | $0.00 |

The fixed cost is about $0.52 a month; the variable cost is mostly model
downloads by new visitors. CloudFront's free tier covers the first 1 TB of
transfer a month, about 7,000 first visits that load every model. An AWS
Budget emails the owner at 80% of $10 actual or 100% forecast. It filters on
the `Project` cost allocation tag, which has to be activated once in the
Billing console, or with `aws ce update-cost-allocation-tags-status`.

## Run it locally

Needs Node.js 24, Bash, `curl` and `jq`, on Linux, macOS or WSL.

```bash
models/fetch.sh '_model_q4f16|^onnx/model_quantized|json$'   # about 150 MB
cd web
ONNXRUNTIME_NODE_INSTALL=skip npm ci
npm run dev                  # http://localhost:5173
npm test                     # unit tests
npx playwright install --with-deps chromium
npx playwright test          # browser tests, Chromium by default
```

`ONNXRUNTIME_NODE_INSTALL=skip` stops a Node-only dependency of
Transformers.js downloading CUDA libraries the browser never uses.

## Deploy

Needs Terraform 1.16, the AWS CLI, Node.js 24 and a Route 53 hosted zone for
the domain.

```bash
# Once: a bucket for Terraform state, kept separate from the site.
terraform -chdir=infra/bootstrap init && terraform -chdir=infra/bootstrap apply

# Point the site's state at it, and set the domain and an address for budget alerts.
cp infra/site/backend.hcl.example infra/site/backend.hcl      # fill in the bucket
cp infra/site/example.tfvars infra/site/terraform.tfvars      # fill in both values

terraform -chdir=infra/site init -backend-config=backend.hcl
terraform -chdir=infra/site apply
(cd spike && npm ci) && (cd web && npm ci)
./scripts/deploy.sh
```

To take the site offline quickly, set `enabled = false` on the CloudFront
distribution in `infra/site/main.tf` and apply. `terraform -chdir=infra/site
destroy` removes the bucket, distribution, certificate, DNS records and
budget; the state bucket stays.
