"""HTTP surface.

Exercised through the real FastAPI app with the dependency graph replaced,
rather than by calling the services directly, so that routing, validation,
status codes and serialisation are covered too. Those are the parts a caller
actually sees.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from metabare.api.app import app
from metabare.config import EncoderSettings, FirnSettings
from metabare.ingest import IngestionService
from metabare.search import SearchService
from metabare.storage import ObjectStore
from tests.conftest import FakeEncoder, FakeFirn


@pytest.fixture
async def client(
    firn: FakeFirn,
    store: ObjectStore,
    encoder: FakeEncoder,
    firn_config: FirnSettings,
) -> AsyncIterator[httpx.AsyncClient]:
    """An async client speaking directly to the ASGI app.

    Deliberately not Starlette's ``TestClient``. That wrapper drives a sync
    interface over an async app through a background thread and an anyio
    portal, and Starlette now warns that using it with httpx is deprecated. It
    has also been observed to hang on ``GET /healthz`` in at least one
    environment. ``ASGITransport`` calls the app directly in the running event
    loop, with no threads and no portal, which is both the supported approach
    and one fewer moving part.

    The lifespan is not run, which is intentional: the real one downloads a
    model and opens live clients. Setting the same ``app.state`` attributes it
    would set keeps routing, validation, dependencies and serialisation under
    test while skipping the network. The uncovered consequence is that eager
    model loading at startup is not exercised here; ``make smoke`` covers that
    against a real process.
    """
    app.state.firn = firn
    app.state.store = store
    app.state.encoder = encoder
    app.state.search = SearchService(
        firn=firn,  # type: ignore[arg-type]
        store=store,
        encoder=encoder,
        settings=firn_config,
    )
    app.state.ingest = IngestionService(
        firn=firn,  # type: ignore[arg-type]
        store=store,
        encoder=encoder,
        firn_config=firn_config,
        encoder_config=EncoderSettings(model_id="fake/encoder", model_revision="test"),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


class TestHealth:
    async def test_healthz_is_independent_of_dependencies(self, client: httpx.AsyncClient) -> None:
        """Liveness must not fail because S3 blipped; that would restart a
        pod that was working."""
        response = await client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    async def test_readyz_reports_an_empty_index_as_degraded_not_unhealthy(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/readyz")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ready"
        assert body["index_present"] is False
        assert "no Firn namespace has data yet" in body["detail"]

    async def test_metrics_are_exposed(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/metrics")
        assert response.status_code == 200
        assert "metabare_searches_total" in response.text


class TestNotes:
    async def test_create_returns_201_and_the_record(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/v1/notes", json={"body": "# Title\n\nbody text"})
        assert response.status_code == 201
        body = response.json()
        assert body["kind"] == "note"
        assert body["state"] == "complete"
        assert body["title"] == "Title"

    async def test_empty_body_is_rejected_by_validation(self, client: httpx.AsyncClient) -> None:
        assert (await client.post("/v1/notes", json={"body": ""})).status_code == 422

    async def test_whitespace_only_body_is_rejected_by_the_pipeline(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post("/v1/notes", json={"body": "   \n  "})
        assert response.status_code == 400
        assert "empty" in response.json()["detail"]

    async def test_oversized_note_is_refused(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/v1/notes", json={"body": "x" * 1_000_001})
        assert response.status_code == 413

    async def test_unsupported_content_type_is_rejected(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/v1/notes", json={"body": "text", "content_type": "application/pdf"}
        )
        assert response.status_code == 422

    async def test_repeated_submission_is_idempotent(self, client: httpx.AsyncClient) -> None:
        first = (await client.post("/v1/notes", json={"body": "same note"})).json()
        second = (await client.post("/v1/notes", json={"body": "same note"})).json()
        assert first["item_id"] == second["item_id"]
        assert first["record_ids"] == second["record_ids"]


class TestItems:
    async def test_get_item(self, client: httpx.AsyncClient) -> None:
        created = (await client.post("/v1/notes", json={"body": "note for retrieval"})).json()
        response = await client.get(f"/v1/items/{created['item_id']}")
        assert response.status_code == 200
        assert response.json()["item_id"] == created["item_id"]

    async def test_get_status_is_a_narrow_projection(self, client: httpx.AsyncClient) -> None:
        created = (await client.post("/v1/notes", json={"body": "note for status"})).json()
        response = await client.get(f"/v1/items/{created['item_id']}/status")
        assert response.status_code == 200
        body = response.json()
        assert body["state"] == "complete"
        assert body["image_stage"] == "not_applicable"
        assert "source" not in body

    async def test_unknown_item_is_404(self, client: httpx.AsyncClient) -> None:
        assert (await client.get(f"/v1/items/{'0' * 64}")).status_code == 404
        assert (await client.get(f"/v1/items/{'0' * 64}/status")).status_code == 404


class TestSearch:
    async def test_finds_an_ingested_note(self, client: httpx.AsyncClient) -> None:
        await client.post("/v1/notes", json={"body": "# Spot\n\nEKS spot interruption notice"})
        response = await client.get("/v1/search", params={"q": "spot interruption"})
        assert response.status_code == 200
        body = response.json()
        assert len(body["hits"]) == 1
        hit = body["hits"][0]
        assert hit["title"] == "Spot"
        assert hit["rank"] == 1
        assert "Reciprocal Rank Fusion" in hit["score_explanation"]

    async def test_search_on_an_empty_index_answers_rather_than_erroring(
        self, client: httpx.AsyncClient
    ) -> None:
        """The application stays searchable regardless."""
        response = await client.get("/v1/search", params={"q": "nothing here"})
        assert response.status_code == 200
        body = response.json()
        assert body["hits"] == []
        assert body["degraded"] is False

    async def test_missing_query_is_rejected(self, client: httpx.AsyncClient) -> None:
        assert (await client.get("/v1/search")).status_code == 422

    async def test_limit_is_bounded(self, client: httpx.AsyncClient) -> None:
        assert (await client.get("/v1/search", params={"q": "x", "limit": 0})).status_code == 422
        assert (await client.get("/v1/search", params={"q": "x", "limit": 51})).status_code == 422

    async def test_response_reports_which_namespaces_were_queried(
        self, client: httpx.AsyncClient
    ) -> None:
        await client.post("/v1/notes", json={"body": "transparency matters"})
        body = (await client.get("/v1/search", params={"q": "transparency"})).json()
        assert set(body["namespaces_queried"]) == {"notes-text", "screenshots-text"}
        assert body["took_ms"] >= 0


class TestConfigEndpoint:
    async def test_exposes_no_secrets(self, client: httpx.AsyncClient) -> None:
        body = (await client.get("/v1/config")).json()
        flattened = str(body).lower()
        for forbidden in ("api_key", "secret", "token", "password"):
            assert forbidden not in flattened

    async def test_reports_the_namespaces_and_model(self, client: httpx.AsyncClient) -> None:
        body = (await client.get("/v1/config")).json()
        assert body["namespaces"]["notes_text"]
        assert body["text_model"]
