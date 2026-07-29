# Plan

What is left to build, in the order it should be built, as a sequence of
reviewable pull requests.

This file is the source of truth for what happens next. It is kept current: an
item is ticked when its pull request is merged, and if reality diverges from
the plan the plan changes rather than being quietly ignored.

## How each item lands

Every numbered item below is **one pull request**, reviewed and merged before
the next begins. A pull request is ready when:

- Continuous integration is green. All seven jobs, not most of them.
- Anything asynchronous it adds has a metric.
- Anything it creates in AWS has a stated monthly cost and a teardown path,
  and the cost table in the README is updated in the same change.
- Anything it measures commits the raw evidence alongside the conclusion.
  Numbers without their data are opinions.
- Anything it cannot do is written down as a limitation rather than left for
  a reader to discover.

Items are ordered by dependency. Where an item is blocked, the blocker is
named.

## Where things stand

Working today: notes are stored, indexed and searched on EKS, with Firn backed
by S3 and no static AWS credentials. Search is hybrid, running entirely on CPU.
The same smoke test passes against the local stack and the cluster.

Not built: everything to do with screenshots, images, GPUs, autoscaling,
dashboards, and every benchmark. No screenshot has been through this system.

Known gaps carried from earlier work, each addressed by an item below:

- No queue consumer, so S3 event notifications are switched off.
- Concurrent reprocessing of one item is not serialised.
- Firn runs as a single replica; concurrent writers are documented as safe by
  Firn but unverified here.
- Re-indexing to fewer chunks leaves orphan rows that cannot be removed.
- Creating the Terraform state bucket is a manual step.
- No budget alert is configured on the running environment.

---

## Ingestion

Nothing involving screenshots can start until work arrives through a queue
rather than through a synchronous API call.

### 1. Terraform bootstrap for remote state

Create the state bucket and its versioning from code, so a fresh clone has a
documented path rather than a manual `aws s3api create-bucket`.

**Done when** a new environment can be stood up from an empty account with two
commands, and the bootstrap is separate from the environment it serves so
destroying one does not touch the other.

### 2. SQS consumer for the ingestion queue

A worker that long-polls the CPU queue, processes messages through the existing
ingestion path, extends visibility for slow work, acknowledges only on success,
and stops taking new messages on SIGTERM.

Reuses `IngestionService` unchanged. Two implementations of an idempotency rule
is two chances to get it wrong.

**Done when** a message that fails five times lands in the dead-letter queue
with enough context to debug; a redelivered message produces no second Firn
row; and a worker killed mid-batch returns its work to the queue.

**Note** this is the change that makes concurrent reprocessing reachable, so it
lands with item 3 or immediately before it.

### 3. Conditional write on the item record

A compare-and-swap on the canonical item document, so two workers processing
the same item cannot have the older pipeline version win by finishing last.

**Blocked by** item 2, which is what makes this reachable.

**Done when** a test drives two concurrent writers at one item and the newer
processing identity always survives.

### 4. Turn on S3 event notifications

Flip `enable_s3_notifications`, now that something consumes the queue.

**Blocked by** item 2.

**Done when** an object written to `raw/` is searchable without anything
calling the API, and the queue drains to zero.

---

## Screenshots

### 5. Screenshot upload

`POST /v1/uploads/presign` and the direct upload path, with content-type and
size validation, and a thumbnail written to `derived/thumbnails/`.

**Done when** a screenshot can be uploaded, appears in object storage under the
right prefix, and produces an item record with the image stage pending rather
than not-applicable.

### 6. Choose an OCR engine

A measured comparison of the candidates on the corpus that matters: terminal
captures, cloud console screens, and dashboards. Accuracy on small
white-on-black monospace text is the interesting axis, not accuracy on
document scans.

Measure per-image CPU latency, memory, install size, licence, and language
support. Commit the images, the transcripts and the scoring script.

**Done when** the choice is recorded with the evidence behind it, including
what the losing engines were bad at.

### 7. OCR in the ingestion path

Extract text, write it to `derived/ocr/`, index it into `screenshots-text`,
and populate result excerpts and thumbnails.

**Blocked by** items 5 and 6.

**Done when** a screenshot is findable by text visible in it, a failed
extraction retries and then reaches the dead-letter queue, and line structure
survives well enough to be readable in a result card.

---

## Retrieval quality

### 8. Relevance dataset and harness

A curated set of screenshots and notes with a versioned query set and manual
judgements, plus a runner reporting recall@k and nDCG.

Redacted or synthetic only. Real personal screenshots do not go in the
repository.

**Done when** a single command produces a relevance report, and the numbers
for the current system are committed as the baseline everything later is
compared against.

### 9. Revisit the text embedding model

The current choice rests on operational measurements plus a relevance
evaluation too small to be decisive: twelve queries, where the gap between two
models was a single query. Re-run it against item 8's dataset.

**Blocked by** item 8.

**Done when** the model is either confirmed or changed, with the re-index that
a change implies proven to replace rows in place.

### 10. Search filters and paging

Date filtering through `_ingested_at`, paging, and a `kind` filter by
namespace selection. Firn cannot filter on arbitrary attributes, so anything
beyond these needs a different approach and should be called out rather than
half-built.

---

## GPU inference

### 11. OpenCLIP export and model repository

A reproducible export into a Triton model repository in S3, with model
revision, preprocessing version, CUDA and backend versions recorded in a
manifest.

**Done when** the export runs from a single command and produces a byte-identical
repository from the same inputs.

### 12. Triton on a fixed GPU node

An On-Demand GPU node, deliberately, because debugging a cold start and
debugging Spot interruption at the same time is a bad trade. Worker consumes
the GPU queue, encodes through Triton, writes image vectors to
`screenshots-image`.

**Blocked by** item 11.

**Cost** a `g4dn.xlarge` On-Demand node is roughly $0.60/hour in `eu-west-1`.
This item creates the first resource in the project that must be destroyed
after every session rather than merely should be.

**Done when** a screenshot produces an image vector through Triton, an
OpenCLIP text query retrieves visually related screenshots, and the three-way
fusion across all namespaces returns sensible results.

### 13. Scale to zero

Karpenter node pool and node class for GPU capacity, KEDA scaling the worker
from zero on queue depth, GPU taints so nothing else lands there, and safe
shutdown on interruption.

**Blocked by** item 12.

**Done when** an idle system has zero GPU pods and zero GPU instances, a new
job causes a node to appear and complete work, the node disappears after the
idle period, and search keeps working throughout.

### 14. Spot interruption recovery

A deliberate interruption during a batch, with the recovery evidence captured.

**Blocked by** item 13.

**Done when** an interrupted batch completes after the node is replaced, with
no duplicate rows and no lost items.

---

## Observability

### 15. Metrics and dashboards

Prometheus and Grafana on the stable node, with scrape configuration for the
API, Firn and the workers, and dashboards for the application and for Firn's
cache and object-storage behaviour.

**Cost** Prometheus storage is the thing to watch here. Retention is a
decision to make from measurements, and the node is already at 8 GiB.

**Done when** the dashboards are provisioned from files in the repository
rather than clicked together, and a fresh cluster comes up with them present.

### 16. GPU and inference metrics

DCGM exporter on GPU nodes, Triton's own metrics, and the queue and
autoscaling signals.

**Blocked by** items 13 and 15.

### 17. Cold-start instrumentation

The twenty timestamps between an object landing in S3 and its image vector
being searchable, emitted as metrics and assembled into one timeline.

**Blocked by** items 13 and 15.

**Done when** a single Grafana view shows queue growth, node creation, model
readiness, GPU activity, queue drain and return to zero, on one aligned time
axis.

---

## Measurement

Each of these produces a report and its raw data. None of their conclusions
may be stated anywhere before the item is merged.

### 18. Cold-start report

What the path from queued work to first useful inference actually contains,
and which part dominates.

**Blocked by** item 17.

### 19. GPU family comparison

G4dn, G5 and G6 measured on cost per thousand screenshots and time to first
inference, not on hourly price. Includes whether CPU-only processing is
cheaper end to end for small batches, which is a real possibility worth
testing rather than assuming.

**Blocked by** item 18.

**Cost** the largest deliberate spend in the project. Needs a budget and an
automated teardown that is verified before the first run, not after.

### 20. S3 lifecycle experiment

Run on copies, on test prefixes, never on the only copy of anything. Measures
transition fees, retrieval fees, minimum object sizes and minimum durations,
not just the storage price difference.

### 21. Firn against a managed search cluster

Same corpus, same queries, same judgements, both indexed from the same derived
data. The comparison target is created for the test window and destroyed
automatically afterwards.

**Blocked by** item 8, which is where the queries and judgements come from.

**Done when** the report states its scope explicitly and makes no claim beyond
the workload, region, dataset and window tested.

---

## Product

### 22. Search interface

A server-rendered page for searching and uploading. Result cards showing the
thumbnail or note, the excerpt, why it matched, and the processing state when
an image vector is still pending.

### 23. Public access

Ingress, TLS and authentication, once there is something worth showing.

**Cost** a load balancer is roughly $18/month plus capacity units, and it is
the first always-on networking cost in the project. It needs its own decision
rather than arriving as a side effect of item 22.

### 24. Billed cost reporting

Cost and Usage export into S3, queried with Athena, surfaced next to the
estimates. Estimated and billed figures must be labelled distinctly and never
presented as the same thing.

---

## Hardening

These are not blocking, but each is a known weakness and should not be
forgotten because it is invisible.

### 25. Validate concurrent Firn writers

Firn documents compare-and-swap safety. This project has not verified it, and
runs one replica as a result, which means a brief search outage during any
update.

**Done when** either more than one replica is proven safe under real
contention, or the single-replica constraint is confirmed as necessary and
documented as such.

### 26. Controlled re-index

A command that re-indexes everything after a pipeline or model version change,
resumable and safe to run against a live system.

Also needs an answer for the orphan rows a re-chunk leaves behind, given Firn
has no row-level delete. Rebuilding the namespace may be the only option, and
if so that should be a supported operation rather than a discovery.

### 27. Restore from object storage

Prove the index can be rebuilt from `raw/` and `derived/` alone, by deleting a
namespace and reconstructing it.

**Done when** the rebuild is a command, its duration is measured, and the
result matches the original.

---

## Open questions

Not scheduled, because the answer changes what gets built.

- Whether the fixed platform cost is acceptable for what this does. ECS or a
  single instance would be substantially cheaper for the same workload.
- Whether Firn gaining scalar attribute columns would let the three namespaces
  collapse into one with a predicate.
- Whether OCR text and note prose are similar enough for one embedding model
  to serve both well.
- What happens to search relevance once the corpus is large enough that an
  IVF_PQ index is mandatory rather than optional.
