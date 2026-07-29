"""Query-time retrieval and result merging.

Result scores have to be merged transparently, and a relevance score should
only be shown if its meaning can be explained. This module is where that
promise is kept.

Why Reciprocal Rank Fusion rather than score normalisation:

MetaBare queries several Firn namespaces per search, and the scores that come
back are not on one scale. A single-vector query returns an L2 distance where
lower is better and the range depends on the embedding dimension. A BM25 query
returns a relevance score where higher is better and the range depends on
corpus statistics. A hybrid query returns Firn's own RRF fusion of the two.
Min-max normalising these into a shared 0..1 range would produce a number that
looks comparable and is not: the top hit of every list normalises to 1.0
whether it was an excellent match or the best of a bad set.

RRF discards the magnitudes and fuses ranks instead, so it needs no
assumptions about scale or direction. The cost is that the output score has no
absolute meaning, only an ordering, which is exactly what is written into
:attr:`metabare.models.SearchHit.score`'s description and shown to the user.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from .config import FirnSettings, firn_settings
from .embeddings import TextEncoder
from .firn import FirnClient, FirnError, FirnNotFoundError, QueryMode, QueryResult
from .models import ItemKind, RecordDocument, RetrievalPath, SearchHit, SearchResponse
from .observability import (
    firn_requests_total,
    get_logger,
    query_encode_duration_seconds,
    search_duration_seconds,
    search_results_returned,
    searches_total,
)
from .storage import ObjectStore

logger = get_logger(__name__)

# The standard RRF constant from Cormack et al. (2009). It damps the influence
# of the very top ranks so a single list cannot dominate the fusion. Left at
# the published default rather than tuned, because tuning it against a corpus
# this small would be fitting noise, and there is nothing to measure against
# yet.
RRF_K = 60


@dataclass(slots=True)
class _Candidate:
    """A record accumulating rank contributions from each namespace it hit."""

    record_id: int
    score: float = 0.0
    paths: set[RetrievalPath] = field(default_factory=set)
    contributions: list[str] = field(default_factory=list)


def _path_for(namespace: str, mode: QueryMode, settings: FirnSettings) -> RetrievalPath:
    if namespace == settings.screenshots_image_namespace:
        return RetrievalPath.IMAGE_VECTOR
    if mode is QueryMode.HYBRID:
        return RetrievalPath.HYBRID
    if mode is QueryMode.FULLTEXT:
        return RetrievalPath.LEXICAL
    return RetrievalPath.TEXT_VECTOR


def reciprocal_rank_fusion(
    results: dict[str, QueryResult],
    settings: FirnSettings,
    *,
    rrf_k: int = RRF_K,
) -> list[_Candidate]:
    """Fuse per-namespace result lists into one ranking.

    Each list contributes ``1 / (rrf_k + rank)`` to every record it returned,
    with rank 1-based. A record found by two namespaces accumulates both
    contributions, which is the behaviour we want: a screenshot whose OCR text
    matches lexically *and* whose text vector matches semantically is a better
    answer than one that only did the former.

    Firn returns lists already ordered best-first in every mode, so position in
    the list is the rank regardless of score direction. That is precisely why
    the direction mismatch documented in :class:`metabare.firn.QueryMode` does
    not need resolving here.
    """
    candidates: dict[int, _Candidate] = {}
    for namespace, result in results.items():
        path = _path_for(namespace, result.mode, settings)
        for rank, hit in enumerate(result.hits, start=1):
            candidate = candidates.setdefault(hit.id, _Candidate(record_id=hit.id))
            contribution = 1.0 / (rrf_k + rank)
            candidate.score += contribution
            candidate.paths.add(path)
            candidate.contributions.append(f"{namespace} rank {rank} ({path.value})")
    return sorted(candidates.values(), key=lambda c: (-c.score, c.record_id))


def _explain(candidate: _Candidate) -> str:
    joined = ", ".join(candidate.contributions)
    return (
        f"Reciprocal Rank Fusion over {len(candidate.contributions)} retrieval "
        f"path(s): {joined}. Higher is better within this result set only."
    )


def _primary_path(paths: set[RetrievalPath]) -> RetrievalPath:
    """Report HYBRID when more than one distinct path found the record."""
    if len(paths) > 1:
        return RetrievalPath.HYBRID
    return next(iter(paths))


class SearchService:
    """Runs a query across the configured namespaces and assembles results."""

    def __init__(
        self,
        *,
        firn: FirnClient,
        store: ObjectStore,
        encoder: TextEncoder,
        settings: FirnSettings | None = None,
    ) -> None:
        self._firn = firn
        self._store = store
        self._encoder = encoder
        self._settings = settings or firn_settings()

    async def _query_namespace(
        self, namespace: str, vector: list[float], text: str, k: int
    ) -> tuple[str, QueryResult | None, bool]:
        """Query one namespace, degrading rather than failing.

        Returns ``(namespace, result, degraded)``. ``degraded`` is True when
        the namespace answered, but through a reduced retrieval path.

        Two kinds of failure are handled, both observed against a real Firn:

        A namespace that has never been written answers ``/query`` with 200 and
        an empty result list, so it needs no special case. ``FirnNotFoundError``
        is still caught because ``GET /ns/{ns}`` does 404 in that state and a
        future Firn version could align the two.

        A namespace with no BM25 index answers a hybrid query with a 500. That
        should not happen, because ingestion builds the index on first write,
        but if it does the fallback to a vector-only query returns useful
        results instead of nothing. Anything else degrades this one retrieval
        path rather than the whole search: a partial page beats an error, and
        the response says it was degraded so the caller is not misled.
        """
        try:
            result = await self._firn.query(
                namespace, vector=vector, text=text, k=k, include_vector=False
            )
        except FirnNotFoundError:
            firn_requests_total.labels(operation="query", outcome="absent").inc()
            return namespace, None, True
        except FirnError as exc:
            firn_requests_total.labels(operation="query", outcome="error").inc()
            logger.warning(
                "hybrid query failed, retrying without the lexical half",
                namespace=namespace,
                error=str(exc),
            )
            try:
                fallback = await self._firn.query(
                    namespace, vector=vector, k=k, include_vector=False
                )
            except FirnError as fallback_exc:
                firn_requests_total.labels(operation="query", outcome="error").inc()
                logger.warning("firn query failed", namespace=namespace, error=str(fallback_exc))
                return namespace, None, True
            firn_requests_total.labels(operation="query", outcome="degraded").inc()
            return namespace, fallback, True
        firn_requests_total.labels(operation="query", outcome="success").inc()
        return namespace, result, False

    async def search(self, query: str, *, limit: int = 10) -> SearchResponse:
        """Search notes and screenshots, returning merged, hydrated results."""
        started = time.perf_counter()
        query = query.strip()
        if not query:
            searches_total.labels(outcome="empty").inc()
            return SearchResponse(
                query=query,
                hits=[],
                total=0,
                took_ms=0.0,
                namespaces_queried=[],
            )

        encode_started = time.perf_counter()
        vector = (await self._encoder.encode_query_async(query)).tolist()
        query_encode_duration_seconds.observe(time.perf_counter() - encode_started)

        # Over-fetch per namespace so fusion has enough depth to reorder
        # meaningfully. Fetching exactly `limit` from each list would let a
        # record that ranks 11th everywhere beat one that ranks 1st in a single
        # list, purely because the 11th was never seen.
        per_namespace_k = max(limit * 2, 20)
        namespaces = list(self._settings.text_namespaces)

        responses = await asyncio.gather(
            *(self._query_namespace(ns, vector, query, per_namespace_k) for ns in namespaces)
        )
        results = {ns: result for ns, result, _ in responses if result is not None}
        degraded = any(was_degraded for _, _, was_degraded in responses)

        if not results:
            searches_total.labels(outcome="no_backend").inc()
            took_ms = (time.perf_counter() - started) * 1000
            return SearchResponse(
                query=query,
                hits=[],
                total=0,
                took_ms=took_ms,
                namespaces_queried=namespaces,
                degraded=True,
                degraded_reason="no retrieval path was available",
            )

        fused = reciprocal_rank_fusion(results, self._settings)
        top = fused[:limit]
        orphaned = 0
        documents = await self._store.get_records([c.record_id for c in top])

        hits: list[SearchHit] = []
        for rank, candidate in enumerate(top, start=1):
            document = documents.get(candidate.record_id)
            if document is None:
                # The index knows about a record whose document is missing or
                # unreadable. Drop it rather than fabricating a card, and say
                # so: it means the index and object storage have diverged.
                logger.warning(
                    "record document missing for indexed row",
                    record_id=candidate.record_id,
                )
                orphaned += 1
                continue
            hits.append(self._to_hit(document, candidate, rank))

        took_ms = (time.perf_counter() - started) * 1000
        search_duration_seconds.observe(took_ms / 1000)
        search_results_returned.observe(len(hits))
        searches_total.labels(outcome="success").inc()

        # `total` counts what could actually be rendered. Reporting len(fused)
        # would promise more results than the API can return, because a
        # candidate whose record document is missing is dropped during
        # hydration and can never appear on any page.
        reasons: list[str] = []
        if degraded:
            reasons.append("one or more retrieval paths were unavailable or reduced")
        if orphaned:
            reasons.append(
                f"{orphaned} indexed row(s) had no record document and were dropped; "
                "the index and object storage have diverged"
            )

        return SearchResponse(
            query=query,
            hits=hits,
            total=max(len(fused) - orphaned, len(hits)),
            took_ms=took_ms,
            namespaces_queried=list(results),
            degraded=bool(reasons),
            degraded_reason="; ".join(reasons),
        )

    @staticmethod
    def _to_hit(document: RecordDocument, candidate: _Candidate, rank: int) -> SearchHit:
        excerpt = document.text if len(document.text) <= 400 else document.text[:397] + "..."
        return SearchHit(
            record_id=document.record_id,
            item_id=document.item_id,
            kind=ItemKind(document.kind),
            title=document.title,
            excerpt=excerpt,
            source_key=document.source_key,
            thumbnail_key=document.thumbnail_key,
            content_type=document.content_type,
            created_at=document.created_at,
            ingested_at=document.ingested_at,
            chunk_index=document.chunk_index,
            chunk_count=document.chunk_count,
            retrieval_path=_primary_path(candidate.paths),
            rank=rank,
            score=candidate.score,
            score_explanation=_explain(candidate),
        )
