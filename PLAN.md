# Plan

What is left to build, in the order it should be built, as a sequence of
reviewable pull requests.

This file is the source of truth for what happens next. It is kept current: an
item is ticked when its pull request is merged, and if reality diverges from
the plan the plan changes rather than being quietly ignored.

## How each item lands

Every numbered item is **one pull request**, reviewed and merged before the
next begins. No item depends on a later one to be safe: where two changes are
related, the earlier one ships with a constraint that the later one removes,
and the constraint is stated in the item.

A pull request is ready when:

- Continuous integration is green. All seven jobs, not most of them.
- Anything asynchronous it adds has a metric, and no metric carries an
  unbounded label such as a run id, item id or query string.
- Anything it creates in AWS has a stated monthly cost and a teardown path,
  and the cost table in the README is updated in the same change.
- Anything it measures commits the raw data alongside the conclusion. Numbers
  without their data are opinions.
- Anything it cannot do is written down as a limitation rather than left for a
  reader to discover.
- Where it settles a decision with a real tradeoff, the reasoning goes in the
  pull request description, which is the public record.

## Where things stand

Working today: notes are stored, indexed and searched on EKS, with Firn backed
by S3 and no static AWS credentials. Search is hybrid, running entirely on CPU.
The same smoke test passes against the local stack and the cluster.

Not built: everything to do with screenshots, images, GPUs, autoscaling,
dashboards, benchmarks and the public interface. No screenshot has been through
this system.

Known weaknesses, each scheduled below: no queue consumer, so S3 event
notifications are off; no compare-and-swap on the item record; Firn's
concurrent-writer safety unverified, so it runs one replica; orphan rows left
by a re-chunk that Firn cannot delete individually; manual state bucket
creation; and no budget alert on the running environment.

---

## Foundations

### 1. Terraform bootstrap for remote state

Create the state bucket and its versioning from code, so a fresh clone has a
documented path rather than a manual `aws s3api create-bucket`.

**Done when** a new environment stands up from an empty account with two
commands, and the bootstrap is a separate state from the environment it serves,
so destroying one cannot touch the other.

### 2. Cost and usage data foundation

Cost and Usage export into S3, Glue and Athena to query it, and the cost
allocation tags activated in Billing.

**This comes before anything expensive, not after.** Tag activation is not
retroactive and billing data lags by up to a day, so a benchmark run before
this exists produces spend that can never be attributed to it. Getting this
wrong is not recoverable by fixing it later.

**Done when** a query returns yesterday's spend broken down by the `Component`
and `CapacityType` tags, and the delay between spend and its appearance is
measured and written down.

**Cost** Athena is charged per terabyte scanned and the export is S3 storage.
Both are negligible here, but they are new line items and belong in the table.

### 3. Budget alerting on the running environment

Set `budget_alert_email` and apply, so the environment is not running unwatched.

**Done when** `terraform output budget_alerting` reports enabled, and a test
notification has been received rather than assumed.

---

## Ingestion

Nothing involving screenshots can start until work arrives through a queue
rather than a synchronous API call.

### 4. SQS consumer for the ingestion queue

A worker that long-polls the CPU queue, processes messages through the existing
ingestion path, extends visibility for slow work, acknowledges only on success,
and stops taking new messages on SIGTERM.

Reuses `IngestionService` unchanged. Two implementations of an idempotency rule
is two chances to get it wrong.

**Ships with a single replica**, and the deployment says why. Concurrent
writers to Firn are unverified and concurrent reprocessing of one item is not
serialised. Items 5 and 6 remove those constraints; until then one replica is
what makes this safe, and the manifest carries a comment saying so.

**Done when** a message failing five times lands in the dead-letter queue with
enough context to debug, a redelivered message produces no second Firn row, and
a worker killed mid-batch returns its work to the queue.

### 5. Conditional write on the item record

A compare-and-swap on the canonical item document, so two workers processing
the same item cannot have the older pipeline version win by finishing last.

**Blocked by** item 4, which is what makes this reachable.

**Done when** a test drives two concurrent writers at one item and the newer
processing identity always survives, and the single-replica note added in item
4 is narrowed to cover only the Firn constraint.

### 6. Validate concurrent Firn writers

Firn documents compare-and-swap safety on S3. This project has never verified
it, and runs one Firn replica and one worker replica as a result.

**This must be settled before item 15**, where a GPU worker writes image
vectors while CPU workers write text vectors to the same Firn instance. That is
genuine concurrent writing, and discovering the answer then would mean
discovering it during a GPU benchmark.

Drive concurrent upserts from several writers at one namespace and at several,
under contention, and check for lost writes and for version conflicts.

**Done when** either more than one writer is proven safe under real contention
and the replica counts are raised, or the single-writer constraint is confirmed
as necessary, written down, and the GPU work in item 15 is designed around it.

### 7. Turn on S3 event notifications

Flip `enable_s3_notifications`, now that something consumes the queue.

**Blocked by** item 4.

**Done when** an object written to `raw/` becomes searchable without anything
calling the API, and the queue drains to zero.

---

## Screenshots

### 8. Screenshot upload

`POST /v1/uploads/presign` and the direct upload path, with content-type and
size validation, and a thumbnail written to `derived/thumbnails/`.

**Done when** a screenshot uploads, lands under the right prefix, and produces
an item record whose image stage is pending rather than not-applicable.

### 9. Choose an OCR engine

A measured comparison of the candidates on the corpus that matters: terminal
captures, cloud console screens and dashboards. Accuracy on small
white-on-black monospace text is the interesting axis, not accuracy on document
scans.

Measure per-image CPU latency, memory, install size, licence and language
support.

**Done when** the images, the transcripts, the scoring script and the results
are committed, and the pull request states the choice, what the losing engines
were bad at, and what would change the decision.

### 10. OCR in the ingestion path

Extract text, write it to `derived/ocr/`, index it into `screenshots-text`, and
populate result excerpts and thumbnails.

**Blocked by** items 8 and 9.

**Done when** a screenshot is findable by text visible in it, a failed
extraction retries and then reaches the dead-letter queue, and line structure
survives well enough to read in a result card.

---

## Retrieval quality

### 11. Relevance dataset and harness

A curated set of screenshots and notes with a versioned query set and manual
judgements, plus a runner reporting recall@k and nDCG.

Redacted or synthetic only. Real personal screenshots do not go in the
repository.

**Done when** one command produces a relevance report, and the current
system's numbers are committed as the baseline everything later is measured
against.

### 12. Revisit the text embedding model

The current choice rests on operational measurements plus a relevance
evaluation too small to be decisive: twelve queries, where the gap between two
models was a single query. Re-run it against item 11's dataset.

**Blocked by** item 11.

This item proves the **semantics** of a model change on a small controlled
corpus: that bumping the model version makes every item's processing identity
stale, that re-processing replaces rows in place rather than accumulating
copies, and that a dimension change needs a new namespace. Item 14 builds the
command that does it at scale.

**Done when** the model is confirmed or changed, and a test demonstrates a
version bump replacing rows in place with the row count unchanged.

### 13. Search filters and paging

Date filtering through `_ingested_at`, cursor paging, and restricting results
by kind through namespace selection. Firn cannot filter on arbitrary
attributes, so anything beyond these needs a different approach and should be
called out rather than half-built.

**Done when** each of these exists with tests: a date-bounded query returning
only items in range; paging that returns every result exactly once across
pages, proven on a corpus larger than one page; a kind restriction; and a
documented list of the filters that are not possible and why. The API
documentation reflects the new parameters.

### 14. Controlled re-index command

A resumable command that re-indexes everything after a pipeline or model
version change, safe to run against a live system.

**Blocked by** item 12, which establishes the semantics this automates.

Needs an answer for the orphan rows a re-chunk leaves behind, given Firn has no
row-level delete. Rebuilding the namespace may be the only option; if so, that
becomes a supported operation here rather than a discovery later.

**Done when** the command survives being interrupted and restarted without
duplicating work, reports progress, and a full re-index of the corpus leaves
the row count correct with no orphans.

---

## GPU inference

### 15. OpenCLIP export and model repository

A reproducible export into a Triton model repository in S3, with model
revision, preprocessing version, CUDA and backend versions recorded in a
manifest.

**Done when** the export runs from one command and produces a byte-identical
repository from the same inputs.

### 16. Triton on a fixed GPU node

An On-Demand GPU node, deliberately, because debugging a cold start and
debugging Spot interruption at once is a bad trade. A worker consumes the GPU
queue, encodes through Triton, and writes image vectors to
`screenshots-image`.

**Blocked by** items 6 and 15. Item 6 because this is the first time two
different workers write to Firn concurrently.

The pull request settles whether the worker and Triton share a pod or run as
separate deployments, covering readiness ordering, independent scaling,
duplicate model loading and how cold start is measured either way.

**Cost** a `g4dn.xlarge` On-Demand node is roughly $0.60/hour in `eu-west-1`.
This is the first resource in the project that must be destroyed after every
session rather than merely should be.

**Done when** a screenshot produces an image vector through Triton, an OpenCLIP
text query retrieves visually related screenshots, fusion across all three
namespaces returns sensible results, and the teardown is verified by checking
the instance is gone rather than by trusting the command.

### 17. Scale to zero

Karpenter node pool and node class for GPU capacity, KEDA scaling the worker
from zero on queue depth, GPU taints so nothing else lands there, and safe
shutdown on interruption.

**Blocked by** item 16.

**Done when** an idle system has zero GPU pods and zero GPU instances, a new
job causes a node to appear and complete work, the node disappears after the
idle period, and search keeps working throughout.

### 18. Spot interruption recovery

A deliberate interruption during a batch, with the recovery evidence captured.

**Blocked by** item 17.

**Done when** an interrupted batch completes after the node is replaced, with
no duplicate rows and no lost items, and the evidence is committed.

---

## Observability

### 19. Metrics and dashboards

Prometheus and Grafana on the stable node, scrape configuration for the API,
Firn and the workers, and dashboards for the application and for Firn's cache
and object-storage behaviour.

The pull request settles metrics retention, from the disk the node actually
has rather than from a default.

**Cost** Prometheus storage is the thing to watch. The node is already at
8 GiB, and storage growing past the application data it observes is a known
failure here.

**Done when** dashboards are provisioned from files in the repository rather
than clicked together, a fresh cluster comes up with them present, and the
retention decision is stated with the disk arithmetic behind it.

### 20. GPU and inference metrics

DCGM exporter on GPU nodes, Triton's own metrics, and the queue and autoscaling
signals.

**Blocked by** items 17 and 19.

### 21. Cold-start instrumentation

The timestamps between an object landing in S3 and its image vector becoming
searchable, split across three places rather than forced into one:

- **Prometheus** carries aggregate durations and counts only. Histograms per
  stage, no run ids, no instance ids, no item ids in labels. A series per
  benchmark run would cost more to store than the screenshots.
- **S3** carries the per-run detail, as a structured run manifest under
  `benchmarks/results/` with every timestamp, the instance type, the image
  digest and the model revision.
- **Grafana** carries the presentation, with run boundaries as annotations
  rather than as label values.

**Blocked by** items 17 and 19.

**Done when** a single view shows queue growth, node creation, model readiness,
GPU activity, queue drain and return to zero on one aligned time axis, and the
metric cardinality is bounded by a fixed set of stage names.

---

## Measurement

Each item produces a report and its raw data. None of their conclusions may be
stated anywhere before the item is merged.

### 22. Cold-start report

What the path from queued work to first useful inference actually contains, and
which part dominates.

**Blocked by** item 21.

**Done when** the run manifests for at least five cold starts are committed
with a rendered breakdown per stage, the report names the dominant cost and the
variance between runs, and the reproduction command is stated.

### 23. GPU family comparison

G4dn, G5 and G6 measured on cost per thousand screenshots and time to first
inference, not on hourly price. Includes whether CPU-only processing is cheaper
end to end for small batches, which is a real possibility worth testing rather
than assuming.

**Blocked by** items 2 and 22. Item 2 because a benchmark whose spend cannot be
attributed afterwards is a benchmark that has to be run again.

**Cost** the largest deliberate spend in the project. Needs a budget, and an
automated teardown verified before the first run rather than after.

**Done when** the manifest records region, instance types, capacity type, image
digest, model revision, dataset version and price source with timestamps; the
raw per-run results are committed; the rankings are published under several
definitions of better; and the conclusions carry their uncertainty and their
regional and time limits.

### 24. S3 lifecycle experiment

Run on copies, on test prefixes, never on the only copy of anything.

**Done when** the report covers transition fees, retrieval fees, minimum object
sizes and minimum storage durations, not only the storage price difference;
bytes and object counts by prefix and storage class are graphed before and
after; the effect on opening a search result is measured; and the modelled cost
without lifecycle rules is stated next to the billed cost with them.

### 25. Firn against a managed search cluster

Same corpus, same queries, same judgements, both indexed from the same derived
data. The comparison target is created for the test window and destroyed
automatically afterwards.

**Blocked by** item 11, which is where the queries and judgements come from.

The pull request states which comparison target was chosen and why, and does
not compare a durable configuration against a deliberately fragile one.

**Done when** storage amplification, ingestion throughput, query latency
percentiles cold and warm, relevance, idle cost and cost per thousand queries
are all reported from the same corpus; the teardown is proven; and the report
scopes every claim to the workload, region, dataset and window tested.

---

## Product and release

### 26. Search interface

A server-rendered page for searching and uploading. Result cards showing the
thumbnail or note, the excerpt, why it matched, and the processing state when
an image vector is still pending.

**Done when** search, upload and item status all work from the browser with no
build step; a relevance score is only shown alongside an explanation of what it
means; and the page is usable on a phone.

### 27. Security review before anything is public

A deliberate pass before item 28, not after.

**Done when** each of these has an answer written down: how uploads are
authenticated and authorised; what an unauthenticated visitor can reach; upload
size, rate and content-type limits and what happens when they are exceeded;
whether any personal data can be served to anyone but its owner; what the
object storage and bucket policy allow; whether the search API can be used to
enumerate items; and what is logged that should not be.

### 28. Public access

Ingress, TLS and authentication.

**Blocked by** items 26 and 27.

The pull request settles the ingress and authentication approach and states its
fixed cost.

**Cost** a load balancer is roughly $18/month plus capacity units, and it is
the first always-on networking cost in the project. It gets its own decision
rather than arriving as a side effect of item 26.

**Done when** the hostname serves over TLS, unauthenticated access is limited
to what item 27 decided it should be, the cost table includes it, and the
teardown removes the DNS record as well as the balancer.

### 29. Billed cost reporting

The reporting layer over the data foundation from item 2: dashboards showing
billed spend next to the near-real-time estimates.

**Blocked by** items 2 and 19.

**Done when** estimated and billed figures are visibly distinct and never
presented as the same number; spend is broken down by component and capacity
type; and the dashboard shows the fixed platform cost separately from the
workload-variable cost.

### 30. Demo capture

The screenshots and the recording: an empty cluster, a batch upload, the queue
rising, a node appearing, the queue draining, and the return to zero.

**Blocked by** items 21 and 26.

**Done when** the captures are reproducible from documented steps, use
synthetic or redacted data only, contain no account identifiers or personal
information, and the dashboard definitions behind them are in the repository.

### 31. Write-up

At least one substantial article, with the drafts and the supporting notes kept
alongside the evidence they cite.

**Blocked by** items 22 and 25, because most of what it claims depends on them.

**Done when** every number in the article links to committed data, every claim
is scoped to what was tested, and the surprises and failures are included
rather than only the results.

### 32. Public polish and release

Final README with links to the reports and the article, an honest limitations
section, a release tag, and a scan for secrets and personal data before it is
advertised.

**Done when** a competent reader can deploy, benchmark and destroy the system
from the README alone, and the repository passes a secrets and personal-data
scan.

---

## Hardening

Not blocking, but each is a known weakness that is easy to forget because it is
invisible.

### 33. Restore from object storage

Prove the index can be rebuilt from `raw/` and `derived/` alone, by deleting a
namespace and reconstructing it.

**Done when** the rebuild is a command, its duration is measured, and the
result matches the original.

### 34. Encryption decision before real data

SSE-S3 is in use because KMS adds a per-request charge and Firn makes many
small reads. That trade runs the other way once the bucket holds real personal
screenshots.

**Done when** the decision is revisited with the request-cost arithmetic, and
either changed or confirmed with the reasoning stated.

---

## Open questions

Not scheduled, because the answer changes what gets built.

- Whether the fixed platform cost is acceptable for what this does. ECS or a
  single instance would be substantially cheaper for the same workload.
- Whether Firn gaining scalar attribute columns would let the three namespaces
  collapse into one with a predicate.
- Whether OCR text and note prose are similar enough for one embedding model to
  serve both well.
- What happens to relevance once the corpus is large enough that an IVF_PQ
  index is mandatory rather than optional.
