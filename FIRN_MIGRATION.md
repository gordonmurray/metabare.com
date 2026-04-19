# FIRN_MIGRATION.md: Move Metabare to Firn-on-S3

## Purpose

Showcase Firn by cutting Metabare's live, public search path from direct-Lance-on-R2 to Firn-on-S3, deployed on a single EC2 instance in `eu-west-1`. The site remains user-facing and behaves the same from the browser; the backend proves Firn works end-to-end on real infrastructure.

Firn docs live at `github.com/gordonmurray/firnflow`; consult that repo before touching Firn config. This file is authoritative for Metabare-side changes only.

## Scope

**Changes in this migration**

- Search backend: direct Lance to Firn.
- Upload backend: additionally writes to Firn (dual-write during cutover; direct-Lance write removed in P4).
- Host: Fly.io to single EC2 in `eu-west-1`.
- Object store: Cloudflare R2 to AWS S3, with CloudFront for image delivery.
- `/latest`: full-table scan on lance to Firn's cursor-paginated `/list` endpoint (available in Firn v0.3.0).
- TLS / public ingress: Fly edge to ALB + ACM cert.
- Secrets: Fly secrets to AWS Secrets Manager.

**Explicitly unchanged**

- Frontend stack: plain HTML/CSS/JS, no React/bundler/TS.
- Embedding model: CLIP ViT-B/32, 512-dim L2-normalised vectors.
- Upload UX in the browser.

**Non-goals (deferred)**

- Benchmark harness, k6, Playwright, regression gating.
- Multi-backend matrix (R2 + S3 + GCS + MinIO). S3 only for this pass.
- Corpus change. No 40K COCO dataset; the existing user-uploaded corpus carries over.
- Hybrid BM25 + vector search. Firn supports it but Metabare has no caption data to index.

If any of these feel tempting mid-way, stop and re-read this section.

## Target architecture

```
                     Route53 (or external DNS)
                              │
         ┌────────────────────┼─────────────────────┐
         ▼                                          ▼
  cdn.metabare.com                          metabare.com
  CloudFront -> S3 bucket (OAC)             ALB + ACM cert
                │                                   │
                │                                   ▼
                │                            EC2 (t3.medium, eu-west-1)
                │                            docker-compose:
                │                              - nginx (static frontend)
                │                              - metabare-upload (FastAPI)
                │                              - metabare-search (FastAPI)
                │                              - firn (ghcr.io/gordonmurray/firnflow)
                │                            instance profile -> S3 + Secrets Manager
                │                                   │
                └───────────────────────────────────┘
                  upload writes image bytes and
                  Lance rows (via Firn) into the
                  same bucket. /latest is served by
                  Firn's /list endpoint.
```

- **ALB + ACM** terminates TLS for `metabare.com` and forwards to nginx on the instance.
- **CloudFront + S3 OAC** serves image bytes from `cdn.metabare.com`. Origin is the same S3 bucket as the Lance data; OAC keeps the bucket private.
- **Instance profile IAM role** grants the EC2 box `s3:{Get,Put,List,Delete}Object` on the bucket and `secretsmanager:GetSecretValue` on the migration's secrets. No AWS access keys in env.
- **Secrets Manager** holds anything not IAM-role-granted. Current footprint is small, with no R2 creds to carry forward.
- **SSM Session Manager** is the access path to the instance. No public port 22.

**Why CloudFront does not impede showing Firn's performance**: image bytes never traverse Firn. Only the `/search` and `/list` JSON does. Keeping image delivery on a separate, cached path means `/search` latency in devtools is a clean Firn number, not polluted by image transfer time.

## Migration facts

### Schema mismatch

| | Metabare today | Firn |
|---|---|---|
| `id` | `string`, `<sha256-hex>.jpg` | `UInt64` |
| `path` | `string` (unused) | not present |
| `vector` | `list<float32, 512>` | `FixedSizeList<Float32, 512>` |
| `text` | not present | `Utf8` (nullable) |
| (row insertion time) | not tracked | `_ingested_at` (microsecond, system, v0.3.0+) |

### `u64` derivation from SHA-256

```python
def sha256_to_u64(filename: str) -> int:
    # filename is "<sha256-hex>.jpg"; strip ".jpg"
    digest = bytes.fromhex(filename.removesuffix(".jpg"))
    return int.from_bytes(digest[:8], "little")
```

The SHA-256 hex stays source-of-truth for the filename and the image URL. The `u64` is purely Firn's primary key. Collision risk for 8 random bytes is negligible at any plausible corpus size.

Store the `<sha>.jpg` filename in Firn's `text` column so the filename round-trips on query and list. The search path needs it back to build the `cdn.metabare.com/lance/images/<hex>.jpg` URL.

### Endpoint mapping

| Metabare endpoint | Today | After cutover |
|---|---|---|
| `POST /upload` | local Lance write + cron-to-R2 | `POST /ns/images/upsert` on Firn + S3 `PutObject` for the image bytes |
| `GET /search?text=...` | `lancedb.connect(R2); table.search(vec)` | `POST /ns/images/query` via Firn |
| `GET /latest` | full-table scan in Python | `GET /ns/images/list?order_by=_ingested_at&order=desc&limit=9` via Firn |

### Environment variables

New or changed on upload + search:

```
FIRN_URL=http://firn:3000
SEARCH_BACKEND=lance|firn      # default "lance" until P3, then "firn"
S3_BUCKET=<bucket-name>
AWS_REGION=eu-west-1
BASE_IMAGE_URL=https://cdn.metabare.com/
```

Removed: `R2_ENDPOINT`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`. boto3 uses the instance profile.

### Namespace

Firn namespace name: `images` (matches the existing Lance table name). Must be created against Firn v0.3.0 or later so the `_ingested_at` column that `/list` requires is present; namespaces created on earlier Firn versions return 501 on `/list`.

## Phases

Each phase has one definition of done. Do not start N+1 until N is done.

### P1: Local wiring

**Goal**: prove Metabare can talk to Firn end-to-end on a laptop. Nothing else.

- `docker-compose.yml` at repo root: MinIO, Firn (`ghcr.io/gordonmurray/firnflow:0.3.0`, pinned), metabare-upload, metabare-search.
- `apps/upload/firn_client.py`: `upsert(filename, vector) -> None`.
- `apps/search/firn_client.py`: `query(vector, k) -> list[dict]` and `list_recent(limit, order) -> list[dict]`.
- `apps/upload/main.py` dual-writes: keeps the existing local Lance write, additionally calls Firn upsert.
- `apps/search/main.py`: `SEARCH_BACKEND` env switch (plus `?backend=` query override) routes `/search` to Firn or direct Lance. `/latest` calls Firn `/list` directly.
- Smoke tests:
  - `scripts/smoke-firn.sh`: upsert, query, list on a synthetic vector.
  - `scripts/ingest-coco.sh`: batch-post N COCO images through `/upload`.
  - `scripts/parity-check.sh`: same text query against both backends, report top-k overlap.

**DoD**: both backends return sane results for the same query on the same local corpus, from a fresh `docker compose up`.

**Do not** deploy to AWS, touch R2, introduce k6/Playwright.

### P2: EC2 + S3 provisioned

**Goal**: same stack on real infrastructure.

Terraform under `infra/`:

- 1 x t3.medium in `eu-west-1` (CLIP memory floor; t3.small OOMs).
- 1 x S3 bucket `metabare-<short-suffix>`, SSE-S3, block public access on, versioning off.
- 1 x instance profile IAM role: `s3:GetObject/PutObject/ListBucket/DeleteObject` on the bucket, `secretsmanager:GetSecretValue` on the migration's secrets.
- 1 x ALB, 1 x ACM cert for `metabare.com` (DNS validation).
- 1 x CloudFront distribution for `cdn.metabare.com` with Origin Access Control onto the same S3 bucket.
- Security groups: ALB public 443; instance 80 from the ALB SG only; no public SSH.
- Standard tags: `Project=metabare`, `Env=prod`, `Owner=gordonmurray`, `ManagedBy=terraform`.
- User-data installs Docker + docker-compose, pulls the compose file, starts services with env pointing at the real bucket.

**DoD**: `terraform apply` succeeds; `docker compose up` on the instance works; upload, search, and `/latest` smoke tests pass with images served via `cdn.metabare.com`.

**Cost note**: t3.medium ~$30/mo, ALB ~$16/mo baseline, CloudFront egress traffic-dependent. Budget ~$50/mo idle.

### P3: DNS cutover

- Point `metabare.com` A/ALIAS record at the ALB.
- Point `cdn.metabare.com` CNAME at the CloudFront distribution.
- Flip `SEARCH_BACKEND=firn` as the default on the instance.
- Fly apps stay up for ~7 days as rollback.
- Dual-write stays on.

**DoD**: `metabare.com` serves from EC2; search returns through Firn; `/latest` returns through Firn's `/list`; images load via CloudFront; Fly stack untouched and available as rollback.

**Rollback**: point DNS back at Fly, toggle `SEARCH_BACKEND` back to `lance` if staying on EC2. `/latest` stays on Firn either way since Fly's `/latest` was never production-scale.

### P4: Simplify

- Drop the direct-Lance write from `apps/upload/main.py`.
- Remove the cron-to-R2 sync (`sync-to-r2.py`, `run-sync.sh`, `entrypoint.sh` cron wiring).
- Remove Fly-specific files (`fly.toml`, Dockerfile adjustments for Fly volumes).
- `fly apps destroy metabare-frontend metabare-upload metabare-search`.
- Delete the old R2 bucket after a one-week grace period.
- Update the local notes file to reflect the new reality (Fly and R2 references removed). The notes file is gitignored, so this is a local-only edit.

**DoD**: upload path has exactly one write (to Firn) plus the image `PutObject`. No cron, no R2 code, no Fly config. Local notes match deployed reality.

## Decisions open during P1/P2

Small but load-bearing. Decide once, write the answer into this file, stop re-deciding.

- **Corpus at cutover**: start Firn empty and let uploads backfill naturally, or one-shot migration script that reads the current R2 Lance table and upserts every row into Firn before P3? Decide in P2 based on current corpus size.
- **DNS provider**: Route53 simplifies ACM validation. If `metabare.com`'s zone is elsewhere today, decide whether to migrate the zone or do external DNS validation for ACM.
- **Firn image tag**: `ghcr.io/gordonmurray/firnflow:0.3.0` is the floor for `/list` support. Bump together when Firn cuts new releases.
- **Firn upsert dedupe**: local smoke testing showed a repeat upsert of the same `id` appears to append a new row rather than replace. Confirm against Firn docs and decide whether the upload path needs client-side dedupe before P4 removes the direct-Lance write (which currently catches duplicates).

## Files this plan creates or changes

```
.gitignore                             # added: ignores local artefacts
docker-compose.yml                     # P1
apps/upload/firn_client.py             # P1
apps/upload/main.py                    # edited: dual-write
apps/upload/storage.py                 # edited P4: direct-Lance write removed
apps/search/firn_client.py             # P1
apps/search/main.py                    # edited: SEARCH_BACKEND switch + /latest via Firn
scripts/smoke-firn.sh                  # P1 step 1
scripts/ingest-coco.sh                 # P1 step 2
scripts/parity-check.sh                # P1 step 2
infra/main.tf                          # P2
infra/variables.tf                     # P2
infra/outputs.tf                       # P2
infra/user-data.sh                     # P2
FIRN_PLAN.md                           # deleted (superseded by this file)
```

## Working agreement

For anyone picking up the migration in this directory:

1. Read this plan to orient on the forward direction.
2. Confirm which phase is current before writing code; if unclear, ask.
3. Propose-before-implement for anything non-trivial: one-line approach, wait for confirmation, then code.
4. If a phase's DoD is not achievable with the current design, stop and raise it. Do not extend scope silently.
