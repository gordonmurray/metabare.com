# metabare

A minimal image search engine showcasing [Firn](https://github.com/gordonmurray/firnflow) over [Lance](https://github.com/lancedb/lance) on AWS S3. Upload an image, CLIP turns it into a 512-dim vector, Lance stores it on S3, and Firn serves nearest-neighbour queries through a tiered RAM + NVMe cache.

Live at [metabare.com](https://metabare.com). Sample images from [COCO](https://cocodataset.org/).

## Structure

```
apps/
  frontend/   # nginx, static HTML/CSS/JS. Proxies API calls same-origin.
  upload/     # FastAPI. CLIP image encoding, dual-write to Lance and Firn.
  search/     # FastAPI. CLIP text encoding, vector search via Firn or Lance.
monitoring/   # Prometheus + Grafana provisioning (Firn dashboard).
infra/        # Terraform for the AWS stack. See infra/README.md.
scripts/      # ingest-coco.sh, parity-check.sh, smoke-firn.sh.
```

## Local development

```bash
docker compose up -d
./scripts/smoke-firn.sh          # upsert/query/list round-trip
COUNT=20 ./scripts/ingest-coco.sh
./scripts/parity-check.sh        # compare Firn and direct-Lance top-k
```

The local stack brings up MinIO, Firn, the three apps, Prometheus, and Grafana. Frontend is at `http://localhost:8082`, Grafana at `http://localhost:8082/grafana/`.

## Production deployment

Terraform under `infra/` provisions a single EC2 instance behind an ALB in `eu-west-1`, with CloudFront in front of an OAC-locked S3 bucket for image delivery. One apply does the full stack including ACM and Route 53 records. See `infra/README.md`.

## Observability

Firn exposes Prometheus metrics at `/metrics`. A provisioned Grafana dashboard is served at `/grafana/d/firn-overview` with cache hit rate, S3 request rate by operation, and query latency percentiles.

## Search backends

`/search` accepts an optional `?backend=lance|firn` query parameter. The upload path writes each image's vector to both Lance (on S3, via the upload container's sync cron) and Firn. This lets the site serve the same query through either backend so the caching win of Firn can be seen side by side.

## License

Apache-2.0.
