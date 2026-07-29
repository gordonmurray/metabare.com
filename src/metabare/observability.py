"""Structured logging and Prometheus metrics.

Metrics are declared here rather than at their call sites, so the full
application-metric surface can be read in one place and a new asynchronous
stage has an obvious place to add one.

Label cardinality is kept deliberately small. ``kind`` has two values,
``stage`` and ``outcome`` a handful each. Nothing is labelled with an item id,
a query string, or anything else unbounded, because a Prometheus series per
uploaded screenshot would cost more to store than the screenshots.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

from .config import ServiceSettings, service_settings

# A dedicated registry keeps test runs from tripping over duplicate
# registration in the default one.
REGISTRY = CollectorRegistry(auto_describe=True)

# Latency buckets chosen for how this workload actually behaves: a warm Firn query is
# sub-millisecond, a cold indexed one is around a second, and a cold
# unindexed one is tens of seconds. The default prometheus_client buckets top
# out at 10s and would put every cold query in +Inf.
_LATENCY_BUCKETS = (
    0.001,
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
    60.0,
)
_DURATION_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 300.0)

uploads_total = Counter(
    "metabare_uploads_total",
    "Items accepted for ingestion",
    ["kind", "outcome"],
    registry=REGISTRY,
)
searches_total = Counter(
    "metabare_searches_total",
    "Search requests served",
    ["outcome"],
    registry=REGISTRY,
)
search_duration_seconds = Histogram(
    "metabare_search_duration_seconds",
    "End-to-end search latency as seen by the client",
    buckets=_LATENCY_BUCKETS,
    registry=REGISTRY,
)
search_results_returned = Histogram(
    "metabare_search_results_returned",
    "Number of hits returned per search",
    buckets=(0, 1, 2, 5, 10, 20, 50, 100),
    registry=REGISTRY,
)
query_encode_duration_seconds = Histogram(
    "metabare_query_encode_duration_seconds",
    "CPU text embedding latency for a single query",
    buckets=_LATENCY_BUCKETS,
    registry=REGISTRY,
)
stage_duration_seconds = Histogram(
    "metabare_stage_duration_seconds",
    "Duration of one ingestion stage",
    ["stage"],
    buckets=_DURATION_BUCKETS,
    registry=REGISTRY,
)
stage_failures_total = Counter(
    "metabare_stage_failures_total",
    "Ingestion stage failures",
    ["stage", "reason"],
    registry=REGISTRY,
)
ingestion_duration_seconds = Histogram(
    "metabare_ingestion_duration_seconds",
    "Time from source object stored to item first searchable",
    ["kind"],
    buckets=_DURATION_BUCKETS,
    registry=REGISTRY,
)
messages_total = Counter(
    "metabare_queue_messages_total",
    "SQS messages by disposition",
    ["queue", "outcome"],
    registry=REGISTRY,
)
messages_in_flight = Gauge(
    "metabare_queue_messages_in_flight",
    "Messages currently being processed by this worker",
    ["queue"],
    registry=REGISTRY,
)
orphaned_index_rows = Counter(
    "metabare_orphaned_index_rows_total",
    (
        "Firn rows left behind by a re-index that produced fewer chunks. Firn has no "
        "row-level delete, so these persist in the namespace until it is rebuilt. Their "
        "record documents are removed so they cannot surface as results, but they still "
        "occupy space and can occupy a top-k slot."
    ),
    registry=REGISTRY,
)
index_build_failures_total = Counter(
    "metabare_index_build_failures_total",
    "Firn index builds that did not succeed. A non-zero value for a BM25 index "
    "means that namespace answers hybrid queries with a 500 until the next write "
    "retries the build",
    ["namespace", "kind"],
    registry=REGISTRY,
)
firn_requests_total = Counter(
    "metabare_firn_requests_total",
    "Calls MetaBare made to Firn",
    ["operation", "outcome"],
    registry=REGISTRY,
)
build_info = Gauge(
    "metabare_build_info",
    "Build and pipeline metadata as labels; the value is always 1",
    ["environment", "pipeline_version", "text_model"],
    registry=REGISTRY,
)


def configure_logging(settings: ServiceSettings | None = None) -> None:
    """Configure structured logging once per process.

    JSON in every deployed environment, because these logs are read by
    machines first. ``console`` is available for local work where a human is
    reading them directly.
    """
    settings = settings or service_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if settings.log_format == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
