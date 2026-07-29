"""Firn index lifecycle.

This module exists because of a behaviour found by running the stack rather
than by reading the docs: **a full-text or hybrid query against a namespace
with no BM25 index returns HTTP 500**, not an empty result and not a graceful
degradation to vector-only ranking. Since a Firn namespace is created by its
first write and starts with no indexes, every namespace is briefly in a state
where the product's main kind of query fails outright.

Two facts make the fix cheap:

* Building the BM25 index is fast on a small table (tens of milliseconds in
  local testing), so doing it on the first write costs almost nothing.
* Rows written *after* the build are still returned by full-text queries, so
  the index does not need rebuilding per write. This was verified rather than
  assumed, because the v0.9.1 release notes describe the opposite behaviour for
  the scalar index on ``id``, where post-build rows land in unindexed fragments
  until a compaction folds them in.

The vector index is treated differently on purpose. An IVF_PQ index over a
handful of rows is pointless: Firn defaults its partition count to
``sqrt(row_count)``, and a brute-force scan of a tiny table is fast anyway.
It is therefore only built once a namespace is large enough to need it.
"""

from __future__ import annotations

import asyncio

from .firn import FirnClient, FirnError
from .observability import get_logger, index_build_failures_total

logger = get_logger(__name__)

# Below this row count, a brute-force scan is fine and an IVF_PQ index would be
# built over too little data to partition sensibly. Above it, Firn's own
# published figures make the index the difference between roughly a second and
# roughly 25 seconds per cold query.
DEFAULT_VECTOR_INDEX_MIN_ROWS = 10_000


class IndexManager:
    """Ensures a namespace has the indexes its queries require.

    Caches the fact that a namespace has been checked, so the common path costs
    nothing. The cache is per-process and deliberately not invalidated: an
    index cannot disappear while the namespace exists, and if the namespace is
    deleted the process will see errors that a stale cache is not the cause of.
    """

    def __init__(
        self,
        firn: FirnClient,
        *,
        vector_index_min_rows: int = DEFAULT_VECTOR_INDEX_MIN_ROWS,
    ) -> None:
        self._firn = firn
        self._vector_index_min_rows = vector_index_min_rows
        self._fts_ready: set[str] = set()
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, namespace: str) -> asyncio.Lock:
        return self._locks.setdefault(namespace, asyncio.Lock())

    def is_fts_ready(self, namespace: str) -> bool:
        """Whether this process has confirmed a BM25 index exists.

        The search path uses this to choose between a hybrid and a vector-only
        query, so that an unindexed namespace degrades to working
        vector search instead of a 500.
        """
        return namespace in self._fts_ready

    async def ensure_fts_index(self, namespace: str) -> bool:
        """Ensure a BM25 index exists. Returns True if one is present.

        Safe to call on every write: after the first success it is a set
        lookup. Concurrent callers for the same namespace serialise on a lock
        so a burst of parallel ingests does not start several index builds.
        """
        if namespace in self._fts_ready:
            return True

        async with self._lock_for(namespace):
            if namespace in self._fts_ready:
                return True
            try:
                info = await self._firn.namespace_info(namespace)
                if info is None:
                    # No data yet, so nothing to index. Not an error: the
                    # namespace does not exist until its first write.
                    return False
                if info.has_fts_index:
                    self._fts_ready.add(namespace)
                    return True

                logger.info("building BM25 index", namespace=namespace, rows=info.row_count)
                operation = await self._firn.build_fts_index(namespace)
                result = await self._firn.wait_for_operation(operation, timeout_seconds=300)
                if result.get("status") != "succeeded":
                    logger.error(
                        "BM25 index build failed",
                        namespace=namespace,
                        error=result.get("error"),
                    )
                    index_build_failures_total.labels(namespace=namespace, kind="fts").inc()
                    return False
                self._fts_ready.add(namespace)
                logger.info("BM25 index ready", namespace=namespace)
                return True
            except (FirnError, TimeoutError) as exc:
                # Ingestion must not fail because an index could not be built:
                # the rows are already committed and searchable by vector. The
                # next write retries.
                logger.warning("could not ensure BM25 index", namespace=namespace, error=str(exc))
                return False

    async def maybe_build_vector_index(self, namespace: str) -> bool:
        """Build an IVF_PQ index if the namespace is big enough to want one.

        Returns True if a build was started. Does not wait for completion: a
        vector index build over a large table takes minutes, and blocking an
        ingest on it would exhaust the message's visibility timeout.
        """
        try:
            info = await self._firn.namespace_info(namespace)
        except FirnError as exc:
            logger.warning("could not read namespace info", namespace=namespace, error=str(exc))
            return False
        if info is None or info.has_vector_index:
            return False
        if info.row_count < self._vector_index_min_rows:
            return False
        try:
            operation = await self._firn.build_vector_index(namespace)
        except FirnError as exc:
            logger.warning(
                "vector index build failed to start", namespace=namespace, error=str(exc)
            )
            return False
        logger.info(
            "started IVF_PQ build",
            namespace=namespace,
            rows=info.row_count,
            operation_id=operation,
        )
        return True
