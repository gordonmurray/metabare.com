"""Async HTTP client for Firn.

Every call here maps to a documented Firn endpoint, checked against firnflow
v0.9.4. Nothing in this file may assume an API that Firn does not actually
have, and a change to the pinned Firn version means re-checking all of it.

Two behaviours are worth knowing before reading the code:

* ``/upsert`` is latest-write-wins keyed on ``id`` and replaces the row **in
  full**. Omitting ``text`` on a second write clears it rather than leaving the
  previous value, so callers always send the complete row.
* Score direction depends on the query mode. A single-vector query returns an
  L2 distance where lower is better; FTS and hybrid return relevance scores
  where higher is better. :class:`QueryMode` is returned alongside results so
  the caller cannot forget which it is holding.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import TracebackType
from typing import Any, Self

import httpx

from .config import FirnSettings, firn_settings

# Retry only what is plausibly transient. A 400 is a bug in the request and
# will fail identically on retry; a 401/403 is a misconfiguration. Retrying
# either wastes the message's visibility timeout.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 0.25


class FirnError(RuntimeError):
    """Base class for Firn failures."""


class FirnBadRequestError(FirnError):
    """Firn rejected the request. Not retryable."""


class FirnAuthError(FirnError):
    """Missing, invalid, or insufficiently scoped bearer token. Not retryable."""


class FirnNotFoundError(FirnError):
    """Namespace or operation does not exist.

    For a namespace this is normal before its first write: Firn has no create
    call, and ``GET /ns/{ns}`` returns 404 until data exists.
    """


class FirnUnavailableError(FirnError):
    """Transport failure or a retryable status that survived all attempts."""


class QueryMode(StrEnum):
    """Which ranking Firn applied, which determines the score direction."""

    VECTOR = "vector"
    MULTIVECTOR = "multivector"
    FULLTEXT = "fulltext"
    HYBRID = "hybrid"

    @property
    def higher_is_better(self) -> bool:
        """True when a larger score means a better match.

        Single-vector and multivector queries return an L2 distance, so lower
        is better. BM25 and RRF fusion return relevance, so higher is better.
        """
        return self in (QueryMode.FULLTEXT, QueryMode.HYBRID)


@dataclass(frozen=True, slots=True)
class Row:
    """One row for ``/upsert``."""

    id: int
    vector: Sequence[float] | None = None
    vectors: Sequence[Sequence[float]] | None = None
    text: str | None = None

    def to_payload(self) -> dict[str, Any]:
        if (self.vector is None) == (self.vectors is None):
            raise ValueError("exactly one of vector or vectors must be set")
        payload: dict[str, Any] = {"id": self.id}
        if self.vector is not None:
            payload["vector"] = [float(v) for v in self.vector]
        elif self.vectors is not None:
            payload["vectors"] = [[float(v) for v in sub] for sub in self.vectors]
        if self.text is not None:
            payload["text"] = self.text
        return payload


@dataclass(frozen=True, slots=True)
class Hit:
    """One row from ``/query`` or ``/list``."""

    id: int
    score: float
    text: str | None
    ingested_at_micros: int | None
    vector: list[float] | None = None


@dataclass(frozen=True, slots=True)
class QueryResult:
    """A query response, carrying the mode so score direction is unambiguous."""

    mode: QueryMode
    hits: list[Hit]
    query_id: str


@dataclass(frozen=True, slots=True)
class NamespaceInfo:
    """``GET /ns/{ns}`` metadata."""

    namespace: str
    kind: str
    vector_dim: int
    row_count: int
    fragment_count: int
    has_vector_index: bool
    has_fts_index: bool
    has_scalar_index: bool
    table_version: int


class FirnClient:
    """Async client. One instance per process; it owns a connection pool."""

    def __init__(
        self,
        settings: FirnSettings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or firn_settings()
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self._settings.url,
            timeout=httpx.Timeout(
                self._settings.timeout_seconds,
                connect=self._settings.connect_timeout_seconds,
            ),
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ---- request plumbing -------------------------------------------------

    def _headers(self, *, admin: bool = False) -> dict[str, str]:
        # Firn falls back to the read/write key for admin routes when no
        # separate admin key is configured, so prefer the admin key and fall
        # back rather than sending nothing.
        token = ""
        if admin:
            token = self._settings.admin_api_key or self._settings.api_key
        else:
            token = self._settings.api_key or self._settings.admin_api_key
        return {"Authorization": f"Bearer {token}"} if token else {}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        admin: bool = False,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                response = await self._client.request(
                    method,
                    path,
                    json=json,
                    params=params,
                    headers=self._headers(admin=admin),
                )
            except httpx.HTTPError as exc:
                last_error = exc
            else:
                if response.status_code not in _RETRYABLE_STATUS:
                    self._raise_for_status(response)
                    return response
                last_error = FirnUnavailableError(
                    f"{method} {path} returned {response.status_code}: {response.text[:200]}"
                )

            if attempt < _MAX_ATTEMPTS:
                # Full jitter. Several workers retrying a recovering Firn in
                # lockstep would otherwise re-create the load that failed it.
                delay = random.uniform(0, _BACKOFF_BASE_SECONDS * 2 ** (attempt - 1))  # noqa: S311
                await asyncio.sleep(delay)

        raise FirnUnavailableError(
            f"{method} {path} failed after {_MAX_ATTEMPTS} attempts"
        ) from last_error

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.is_success:
            return
        detail = response.text[:500]
        status = response.status_code
        if status == 404:
            raise FirnNotFoundError(detail)
        if status in (401, 403):
            raise FirnAuthError(f"{status}: {detail}")
        if 400 <= status < 500:
            raise FirnBadRequestError(f"{status}: {detail}")
        raise FirnUnavailableError(f"{status}: {detail}")

    # ---- read -------------------------------------------------------------

    async def health(self) -> bool:
        """Liveness. Firn exposes no separate readiness endpoint."""
        try:
            response = await self._client.get("/health", timeout=2.0)
        except httpx.HTTPError:
            return False
        return response.is_success

    async def namespace_info(self, namespace: str) -> NamespaceInfo | None:
        """Return namespace metadata, or None if it has no data yet."""
        try:
            response = await self._request("GET", f"/ns/{namespace}")
        except FirnNotFoundError:
            return None
        body = response.json()
        return NamespaceInfo(
            namespace=body.get("namespace", namespace),
            kind=body.get("kind", "single"),
            vector_dim=int(body.get("vector_dim", 0)),
            row_count=int(body.get("row_count", 0)),
            fragment_count=int(body.get("fragment_count", 0)),
            has_vector_index=bool(body.get("has_vector_index", False)),
            has_fts_index=bool(body.get("has_fts_index", False)),
            has_scalar_index=bool(body.get("has_scalar_index", False)),
            table_version=int(body.get("table_version", 0)),
        )

    async def query(
        self,
        namespace: str,
        *,
        vector: Sequence[float] | None = None,
        vectors: Sequence[Sequence[float]] | None = None,
        text: str | None = None,
        k: int = 10,
        include_vector: bool = False,
        filter_expr: str | None = None,
        nprobes: int | None = None,
        semantic_cache: bool = False,
        semantic_min_similarity: float | None = None,
    ) -> QueryResult:
        """Run a vector, full-text, or hybrid query.

        ``include_vector`` defaults to False here although Firn's own default
        is True: the search path never needs the stored vector back, and
        returning it inflates both the response and the cached payload.

        A filtered request cannot use the semantic cache in Firn v1; asking for
        both raises rather than silently dropping one.
        """
        if vector is not None and vectors is not None:
            raise ValueError("set at most one of vector or vectors")
        if vector is None and vectors is None and not text:
            raise ValueError("at least one of vector, vectors or text is required")
        if semantic_cache and filter_expr:
            raise ValueError("Firn v1 rejects semantic cache lookups on filtered queries")

        payload: dict[str, Any] = {"k": k, "include_vector": include_vector}
        if vector is not None:
            payload["vector"] = [float(v) for v in vector]
        if vectors is not None:
            payload["vectors"] = [[float(v) for v in sub] for sub in vectors]
        if text:
            payload["text"] = text
        if filter_expr:
            payload["filter"] = filter_expr
        if nprobes is not None:
            payload["nprobes"] = nprobes
        if semantic_cache:
            block: dict[str, Any] = {"enabled": True}
            if semantic_min_similarity is not None:
                block["min_similarity"] = semantic_min_similarity
            payload["semantic_cache"] = block

        has_vector = vector is not None or vectors is not None
        if has_vector and text:
            mode = QueryMode.HYBRID
        elif vectors is not None:
            mode = QueryMode.MULTIVECTOR
        elif vector is not None:
            mode = QueryMode.VECTOR
        else:
            mode = QueryMode.FULLTEXT

        response = await self._request("POST", f"/ns/{namespace}/query", json=payload)
        body = response.json()
        hits = [
            Hit(
                id=int(row["id"]),
                score=float(row.get("score", 0.0)),
                text=row.get("text"),
                ingested_at_micros=row.get("ingested_at_micros"),
                vector=row.get("vector"),
            )
            for row in body.get("results", [])
        ]
        return QueryResult(mode=mode, hits=hits, query_id=str(body.get("query_id", "")))

    async def list_recent(
        self,
        namespace: str,
        *,
        limit: int = 50,
        order: str = "desc",
        cursor: str | None = None,
    ) -> tuple[list[Hit], str | None]:
        """Page rows by ``_ingested_at``. Returns (rows, next_cursor).

        Note the response key is ``rows`` here, not ``results`` as on /query.
        """
        params: dict[str, Any] = {"order_by": "_ingested_at", "order": order, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        response = await self._request("GET", f"/ns/{namespace}/list", params=params)
        body = response.json()
        rows = [
            Hit(
                id=int(row["id"]),
                score=0.0,
                text=row.get("text"),
                ingested_at_micros=row.get("ingested_at_micros"),
                vector=row.get("vector"),
            )
            for row in body.get("rows", [])
        ]
        return rows, body.get("next_cursor")

    # ---- write ------------------------------------------------------------

    async def upsert(self, namespace: str, rows: Sequence[Row]) -> int:
        """Insert or replace rows. Returns the count Firn accepted.

        Idempotent by ``id`` since Firn v0.9.0, which is what makes MetaBare's
        at-least-once SQS delivery safe. Firn rejects duplicate ids within a
        single request with 400, so that is caught here with a clearer message
        than the server's.
        """
        if not rows:
            return 0
        seen: set[int] = set()
        for row in rows:
            if row.id in seen:
                raise ValueError(f"duplicate id {row.id} within one upsert batch")
            seen.add(row.id)
        payload = {"rows": [row.to_payload() for row in rows]}
        response = await self._request("POST", f"/ns/{namespace}/upsert", json=payload)
        return int(response.json().get("upserted", 0))

    # ---- maintenance ------------------------------------------------------

    async def build_vector_index(
        self,
        namespace: str,
        *,
        num_partitions: int | None = None,
        num_sub_vectors: int | None = None,
        num_bits: int | None = None,
    ) -> str:
        """Start an IVF_PQ build. Returns the operation id.

        Not optional at any real corpus size: without a vector index every
        query is a brute-force scan of object storage.
        """
        payload: dict[str, Any] = {"kind": "ivf_pq"}
        if num_partitions is not None:
            payload["num_partitions"] = num_partitions
        if num_sub_vectors is not None:
            payload["num_sub_vectors"] = num_sub_vectors
        if num_bits is not None:
            payload["num_bits"] = num_bits
        response = await self._request("POST", f"/ns/{namespace}/index", admin=True, json=payload)
        return str(response.json()["operation_id"])

    async def build_fts_index(self, namespace: str) -> str:
        """Start a BM25 index build. Returns the operation id."""
        response = await self._request("POST", f"/ns/{namespace}/fts-index", admin=True)
        return str(response.json()["operation_id"])

    async def build_scalar_index(self, namespace: str, column: str = "_ingested_at") -> str:
        """Start a BTree build on ``id`` or ``_ingested_at``."""
        if column not in ("id", "_ingested_at"):
            raise ValueError(f"unsupported scalar index column: {column}")
        response = await self._request(
            "POST", f"/ns/{namespace}/scalar-index", admin=True, json={"column": column}
        )
        return str(response.json()["operation_id"])

    async def compact(self, namespace: str) -> str:
        """Start a compaction. Returns the operation id."""
        response = await self._request("POST", f"/ns/{namespace}/compact", admin=True)
        return str(response.json()["operation_id"])

    async def operation(self, operation_id: str) -> dict[str, Any]:
        """Poll a background operation.

        A 404 means unknown *or aged out*, so it is surfaced as
        :class:`FirnNotFoundError` rather than treated as 'still running'.
        """
        response = await self._request("GET", f"/operations/{operation_id}")
        result: dict[str, Any] = response.json()
        return result

    async def wait_for_operation(
        self,
        operation_id: str,
        *,
        timeout_seconds: float = 600.0,
        poll_interval_seconds: float = 2.0,
    ) -> dict[str, Any]:
        """Poll until the operation succeeds, fails, or the timeout expires."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        while True:
            status = await self.operation(operation_id)
            if status.get("status") in ("succeeded", "failed"):
                return status
            if loop.time() >= deadline:
                raise TimeoutError(
                    f"operation {operation_id} still {status.get('status')} "
                    f"after {timeout_seconds}s"
                )
            await asyncio.sleep(poll_interval_seconds)
