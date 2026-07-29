"""FastAPI application: upload, search, item status, health, metrics.

Two choices here are worth stating plainly, because they are architectural
rather than cosmetic:

``/healthz`` never touches a dependency. It answers "is this process alive",
and a liveness probe that fails when S3 is briefly unreachable would restart a
pod that was working correctly, turning a dependency blip into an outage.

``/readyz`` does check dependencies, and reports *degraded* rather than
unhealthy when only the index is missing. A fresh deployment has no Firn
namespaces at all, and the application is meant to stay searchable and accept
uploads without GPU capacity present; refusing traffic because an optional
retrieval path is absent would break that promise.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

from .. import __version__
from ..config import encoder_settings, firn_settings, service_settings, storage_settings
from ..embeddings import TextEncoder
from ..firn import FirnClient
from ..ids import PIPELINE_VERSION
from ..ingest import IngestionError, IngestionService
from ..models import ItemRecord, SearchResponse
from ..observability import (
    REGISTRY,
    build_info,
    configure_logging,
    get_logger,
    searches_total,
)
from ..search import SearchService
from ..storage import ObjectStore

logger = get_logger(__name__)

MAX_NOTE_BYTES = 1_000_000


class NoteRequest(BaseModel):
    """A note submitted directly, rather than uploaded as an object."""

    body: str = Field(min_length=1, description="Note content, plain text or Markdown")
    title: str = Field(default="", max_length=200)
    filename: str = Field(default="", max_length=255)
    content_type: str = Field(default="text/markdown", pattern=r"^text/(plain|markdown)$")


class ItemStatusResponse(BaseModel):
    """Just the processing state, for polling without the full record."""

    item_id: str
    state: str
    text_stage: str
    image_stage: str
    chunk_count: int
    attempts: int
    error: str = ""


class HealthResponse(BaseModel):
    status: str
    version: str
    pipeline_version: str


class ReadyResponse(BaseModel):
    status: str = Field(description="'ready' or 'not_ready'")
    object_storage: bool
    firn: bool
    encoder: bool
    index_present: bool = Field(description="False on a fresh deployment; not an error")
    detail: str = ""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build shared clients once, and load the model before serving traffic.

    The encoder is loaded eagerly at startup rather than on first request. A
    lazy load would put a multi-second model load inside whichever unlucky
    search arrived first, and would let the pod pass its readiness probe while
    still unable to serve a query.
    """
    configure_logging()
    settings = service_settings()

    firn_client = FirnClient()
    store = ObjectStore()
    encoder = TextEncoder()

    build_info.labels(
        environment=settings.environment,
        pipeline_version=PIPELINE_VERSION,
        text_model=encoder_settings().model_version,
    ).set(1)

    logger.info(
        "loading text encoder",
        model=encoder_settings().model_version,
        dimension=encoder_settings().dimension,
    )
    load_started = time.perf_counter()
    try:
        encoder.load()
    except Exception as exc:
        logger.error("text encoder failed to load", error=str(exc))
        raise
    logger.info("text encoder ready", seconds=round(time.perf_counter() - load_started, 2))

    app.state.firn = firn_client
    app.state.store = store
    app.state.encoder = encoder
    app.state.search = SearchService(firn=firn_client, store=store, encoder=encoder)
    app.state.ingest = IngestionService(firn=firn_client, store=store, encoder=encoder)

    try:
        yield
    finally:
        await firn_client.aclose()


app = FastAPI(
    title="MetaBare",
    version=__version__,
    summary="Search your screenshots and notes",
    lifespan=lifespan,
)


# These are `async` deliberately. FastAPI runs a non-async dependency in an
# anyio worker thread, on the assumption that a sync callable might block. All
# four of these do nothing but read an attribute off `app.state`, so that
# thread hop is pure overhead on every request that has a dependency, and it
# puts a thread boundary in front of the entire API for no reason. Declaring
# them async lets FastAPI await them inline on the event loop.
async def get_search(request: Request) -> SearchService:
    service: SearchService = request.app.state.search
    return service


async def get_ingest(request: Request) -> IngestionService:
    service: IngestionService = request.app.state.ingest
    return service


async def get_store(request: Request) -> ObjectStore:
    store: ObjectStore = request.app.state.store
    return store


async def get_firn(request: Request) -> FirnClient:
    client: FirnClient = request.app.state.firn
    return client


@app.get("/healthz", response_model=HealthResponse, tags=["ops"])
async def healthz() -> HealthResponse:
    """Liveness. Deliberately checks nothing external."""
    return HealthResponse(status="ok", version=__version__, pipeline_version=PIPELINE_VERSION)


@app.get("/readyz", response_model=ReadyResponse, tags=["ops"])
async def readyz(
    response: Response,
    store: Annotated[ObjectStore, Depends(get_store)],
    firn: Annotated[FirnClient, Depends(get_firn)],
    request: Request,
) -> ReadyResponse:
    """Readiness. Reports an empty index as degraded, not unhealthy."""
    storage_ok = await store.reachable()
    firn_ok = await firn.health()
    encoder_ok = getattr(request.app.state, "encoder", None) is not None

    index_present = False
    if firn_ok:
        for namespace in firn_settings().text_namespaces:
            if await firn.namespace_info(namespace) is not None:
                index_present = True
                break

    ready = storage_ok and firn_ok and encoder_ok
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    detail = ""
    if ready and not index_present:
        detail = "no Firn namespace has data yet; search will return no results"

    return ReadyResponse(
        status="ready" if ready else "not_ready",
        object_storage=storage_ok,
        firn=firn_ok,
        encoder=encoder_ok,
        index_present=index_present,
        detail=detail,
    )


@app.get("/metrics", tags=["ops"])
async def metrics() -> Response:
    return Response(content=generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)


@app.post(
    "/v1/notes",
    response_model=ItemRecord,
    status_code=status.HTTP_201_CREATED,
    tags=["ingest"],
)
async def create_note(
    payload: NoteRequest,
    ingest: Annotated[IngestionService, Depends(get_ingest)],
) -> ItemRecord:
    """Store, index and return a note.

    Synchronous because note ingestion needs no GPU and completes in
    milliseconds. Screenshots, which need OCR, go through the queue instead.
    """
    if len(payload.body.encode()) > MAX_NOTE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"note body exceeds {MAX_NOTE_BYTES} bytes",
        )
    try:
        return await ingest.ingest_note(
            payload.body,
            filename=payload.filename,
            title=payload.title,
            content_type=payload.content_type,
        )
    except IngestionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@app.get("/v1/items/{item_id}", response_model=ItemRecord, tags=["items"])
async def get_item(
    item_id: str,
    store: Annotated[ObjectStore, Depends(get_store)],
) -> ItemRecord:
    record = await store.get_item(item_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown item")
    return record


@app.get("/v1/items/{item_id}/status", response_model=ItemStatusResponse, tags=["items"])
async def get_item_status(
    item_id: str,
    store: Annotated[ObjectStore, Depends(get_store)],
) -> ItemStatusResponse:
    record = await store.get_item(item_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown item")
    return ItemStatusResponse(
        item_id=record.item_id,
        state=record.state.value,
        text_stage=record.text_stage.value,
        image_stage=record.image_stage.value,
        chunk_count=record.chunk_count,
        attempts=record.attempts,
        error=record.error,
    )


@app.get("/v1/search", response_model=SearchResponse, tags=["search"])
async def search(
    search_service: Annotated[SearchService, Depends(get_search)],
    q: Annotated[str, Query(min_length=1, max_length=1000, description="Search query")],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> SearchResponse:
    """Search notes and screenshots.

    Runs entirely on CPU, and must stay that way: a search must not wait for a
    GPU node to start merely to embed a short query.
    """
    try:
        return await search_service.search(q, limit=limit)
    except Exception as exc:
        searches_total.labels(outcome="error").inc()
        logger.error("search failed", error=str(exc), query_length=len(q))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="search is unavailable"
        ) from exc


@app.get("/v1/config", tags=["ops"])
async def config() -> dict[str, Any]:
    """Non-secret runtime configuration, for debugging a deployed pod.

    Deliberately excludes every credential and bearer token.
    """
    firn = firn_settings()
    encoder = encoder_settings()
    return {
        "environment": service_settings().environment,
        "version": __version__,
        "pipeline_version": PIPELINE_VERSION,
        "bucket": storage_settings().bucket,
        "region": storage_settings().region,
        "firn_url": firn.url,
        "namespaces": {
            "notes_text": firn.notes_text_namespace,
            "screenshots_text": firn.screenshots_text_namespace,
            "screenshots_image": firn.screenshots_image_namespace,
        },
        "text_model": encoder.model_version,
        "text_dimension": encoder.dimension,
    }
