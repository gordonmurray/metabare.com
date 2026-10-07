# MetaBare

Semantic search over your own images and notes, with the machine learning
running in your browser.

> **Status: early.** [metabare.com](https://metabare.com) serves a test page,
> [metabare.com/spike/](https://metabare.com/spike/), that runs the models in
> your browser and reports how they did. The search app itself is not built
> yet. This README describes each part as it lands.

## The idea

Searching images by what is in them needs an embedding model: something that
turns a picture, or a phrase like "a terminal showing an error", into a vector
that can be compared with others. Usually that model runs on a server, often
on a GPU, and someone pays for it whether or not anyone is searching.

MetaBare will run the models in the browser instead, on the device's GPU
through WebGPU, or on its CPU through WebAssembly where WebGPU is not
available. The server's job shrinks to storing files and vectors.

The plan:

1. **Local-only search.** Drop a folder of images or notes into the page; they
   are embedded and searched on your device, and nothing is uploaded.
2. **A saved library.** Optionally store originals in Amazon S3 and vectors in
   [Firn](https://github.com/gordonmurray/firnflow), an S3-backed search engine,
   so a library survives a cleared browser and is reachable from another
   device.

Every claim about speed, size or cost will link to the raw measurements behind
it, scoped to the device, browser and date it was measured on.

## In this repository

| Path | What |
| --- | --- |
| [`spike/`](spike/README.md) | A feasibility test: embeds images and text in the browser on WebGPU and WebAssembly and measures speed, download size, memory and whether results agree across backends |
| `web/` | The app: add images and notes, embedded and stored in the browser. Search is not built yet |
| `models/` | `models.lock.json` pins every model file by revision and SHA-256; `fetch.sh` downloads and verifies them |
| `infra/` | Terraform for the hosting: S3 and CloudFront for metabare.com |
| `scripts/deploy.sh` | Builds and uploads the site |
| `eval/technical-notes.json` | 24 synthetic technical notes and 12 queries with relevance labels, for evaluating text search |

## Run it locally

Needs Node.js 24.

```bash
models/fetch.sh '_model_q4f16|^onnx/model_quantized|json$'   # about 150 MB, the app's models
cd web
npm ci
npm run dev                  # http://localhost:5173
npm test                     # unit tests
npx playwright test          # browser tests, Chromium by default
```

Set `ONNXRUNTIME_NODE_INSTALL=skip` before `npm ci` to stop a Node-only
dependency of Transformers.js downloading CUDA libraries the browser never
uses.

## Hosting

The site is static: a private S3 bucket behind CloudFront, with no server. The
browser downloads the page, the models and the ONNX Runtime files, then does
all the work itself.

CloudFront adds `Cross-Origin-Opener-Policy` and `Cross-Origin-Embedder-Policy`
headers, which let WebAssembly use more than one thread. On the laptop the
test page has been measured on, one thread was 3.4 times slower than four
(417 ms against 124 ms per image, `spike/results/deployed-laptop-iris-xe*`).

Model files are stored gzip-compressed, because CloudFront does not compress
files over 10 MB. The test page loads all three models on a first visit, which
transferred 139.5 MB of models and runtime; the app fetches each model the
first time it needs one. Later visits load them from the browser's cache. All four precisions the test page
offers are deployed, about 1.1 GB compressed; a browser only downloads the
ones it uses.

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

So the fixed cost is about $0.52 a month, and the variable cost is mostly model
downloads by new visitors. CloudFront's free tier covers the first 1 TB of
transfer a month, about 7,000 first visits that load every model. An AWS
Budget emails the owner at 80% of $10 actual or 100% forecast. It filters on the `Project` cost allocation
tag, which has to be activated once in the Billing console, or with
`aws ce update-cost-allocation-tags-status`, before tagged spending shows up.

### Deploy

Needs Terraform 1.16, the AWS CLI, Node.js 24 and a Route 53 hosted zone for
the domain.

```bash
# Once: a bucket for Terraform state, kept separate from the site.
terraform -chdir=infra/bootstrap init && terraform -chdir=infra/bootstrap apply

# Point the site's state at it, and set an address for budget alerts.
cp infra/site/backend.hcl.example infra/site/backend.hcl      # fill in the bucket
echo 'budget_alert_email = "you@example.com"' > infra/site/terraform.tfvars

terraform -chdir=infra/site init -backend-config=backend.hcl
terraform -chdir=infra/site apply
(cd spike && npm ci) && (cd web && npm ci)
./scripts/deploy.sh
```

To take the site down, `terraform -chdir=infra/site destroy` removes the
bucket, distribution, certificate, DNS records and budget. The state bucket is
left in place.

## Licence

[Apache 2.0](LICENSE).
