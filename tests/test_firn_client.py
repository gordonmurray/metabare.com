"""Firn client, against a mocked transport.

Every response asserted here was checked against a real Firn. When the
pinned version moves, these are the tests to re-read against the new API: they
encode what MetaBare believes about Firn, so if the belief is wrong they are
where it shows.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from metabare.config import FirnSettings
from metabare.firn import (
    FirnAuthError,
    FirnBadRequestError,
    FirnClient,
    FirnNotFoundError,
    FirnUnavailableError,
    QueryMode,
    Row,
)

BASE = "http://firn.test:3000"


@pytest.fixture
def client(firn_config: FirnSettings) -> FirnClient:
    return FirnClient(firn_config)


class TestRowPayload:
    def test_single_vector(self) -> None:
        assert Row(id=1, vector=[0.5], text="t").to_payload() == {
            "id": 1,
            "vector": [0.5],
            "text": "t",
        }

    def test_multivector(self) -> None:
        payload = Row(id=1, vectors=[[0.1], [0.2]]).to_payload()
        assert payload["vectors"] == [[0.1], [0.2]]
        assert "vector" not in payload

    def test_omitted_text_is_absent_not_null(self) -> None:
        """Latest-write-wins replaces the row, so an explicit null would clear
        text that the caller may have meant to leave alone. Omission is the
        honest encoding of 'this row has no text'."""
        assert "text" not in Row(id=1, vector=[0.1]).to_payload()

    def test_requires_exactly_one_vector_field(self) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            Row(id=1).to_payload()
        with pytest.raises(ValueError, match="exactly one"):
            Row(id=1, vector=[0.1], vectors=[[0.1]]).to_payload()


class TestQuery:
    @respx.mock
    async def test_single_vector_mode(self, client: FirnClient) -> None:
        route = respx.post(f"{BASE}/ns/notes-text/query").mock(
            return_value=httpx.Response(
                200,
                json={
                    "query_id": "q1",
                    "results": [
                        {
                            "id": 7,
                            "score": 0.25,
                            "text": "hello",
                            "ingested_at_micros": 1,
                            "vector": None,
                        }
                    ],
                },
            )
        )
        result = await client.query("notes-text", vector=[0.1, 0.2], k=5)
        assert result.mode is QueryMode.VECTOR
        assert result.hits[0].id == 7
        assert result.hits[0].score == 0.25
        body = route.calls[0].request.read()
        assert b'"include_vector":false' in body.replace(b" ", b"")

    @respx.mock
    async def test_hybrid_mode_when_text_and_vector_present(self, client: FirnClient) -> None:
        respx.post(f"{BASE}/ns/notes-text/query").mock(
            return_value=httpx.Response(200, json={"query_id": "q", "results": []})
        )
        result = await client.query("notes-text", vector=[0.1], text="spot", k=3)
        assert result.mode is QueryMode.HYBRID

    @respx.mock
    async def test_fulltext_mode(self, client: FirnClient) -> None:
        respx.post(f"{BASE}/ns/notes-text/query").mock(
            return_value=httpx.Response(200, json={"query_id": "q", "results": []})
        )
        result = await client.query("notes-text", text="spot", k=3)
        assert result.mode is QueryMode.FULLTEXT

    def test_score_direction_is_exposed(self) -> None:
        """The whole point of returning the mode: L2 and BM25 disagree."""
        assert QueryMode.VECTOR.higher_is_better is False
        assert QueryMode.MULTIVECTOR.higher_is_better is False
        assert QueryMode.FULLTEXT.higher_is_better is True
        assert QueryMode.HYBRID.higher_is_better is True

    async def test_requires_a_query_field(self, client: FirnClient) -> None:
        with pytest.raises(ValueError, match="at least one"):
            await client.query("notes-text", k=5)

    async def test_rejects_both_vector_forms(self, client: FirnClient) -> None:
        with pytest.raises(ValueError, match="at most one"):
            await client.query("notes-text", vector=[0.1], vectors=[[0.1]], k=5)

    async def test_rejects_semantic_cache_with_filter(self, client: FirnClient) -> None:
        """Firn v1 rejects this combination; failing here is clearer than a 400."""
        with pytest.raises(ValueError, match="semantic cache"):
            await client.query(
                "notes-text", vector=[0.1], k=5, filter_expr="id > 1", semantic_cache=True
            )

    @respx.mock
    async def test_semantic_cache_block_is_sent(self, client: FirnClient) -> None:
        route = respx.post(f"{BASE}/ns/notes-text/query").mock(
            return_value=httpx.Response(200, json={"query_id": "q", "results": []})
        )
        await client.query(
            "notes-text", vector=[0.1], k=5, semantic_cache=True, semantic_min_similarity=0.99
        )
        body = route.calls[0].request.read().replace(b" ", b"")
        assert b'"semantic_cache":{"enabled":true,"min_similarity":0.99}' in body


class TestUpsert:
    @respx.mock
    async def test_returns_accepted_count(self, client: FirnClient) -> None:
        respx.post(f"{BASE}/ns/notes-text/upsert").mock(
            return_value=httpx.Response(200, json={"upserted": 2})
        )
        count = await client.upsert(
            "notes-text", [Row(id=1, vector=[0.1]), Row(id=2, vector=[0.2])]
        )
        assert count == 2

    async def test_empty_batch_makes_no_request(self, client: FirnClient) -> None:
        assert await client.upsert("notes-text", []) == 0

    async def test_duplicate_ids_rejected_before_the_network(self, client: FirnClient) -> None:
        """Firn returns 400 for this; catching it locally saves a round-trip
        and gives a message that names the offending id."""
        with pytest.raises(ValueError, match="duplicate id 1"):
            await client.upsert("notes-text", [Row(id=1, vector=[0.1]), Row(id=1, vector=[0.2])])


class TestNamespaceInfo:
    @respx.mock
    async def test_absent_namespace_is_none_not_an_error(self, client: FirnClient) -> None:
        """A namespace does not exist until its first write. On a fresh
        deployment that is the normal state, not a failure."""
        respx.get(f"{BASE}/ns/notes-text").mock(return_value=httpx.Response(404))
        assert await client.namespace_info("notes-text") is None

    @respx.mock
    async def test_parses_metadata(self, client: FirnClient) -> None:
        respx.get(f"{BASE}/ns/notes-text").mock(
            return_value=httpx.Response(
                200,
                json={
                    "namespace": "notes-text",
                    "kind": "single",
                    "vector_dim": 384,
                    "row_count": 12,
                    "fragment_count": 2,
                    "has_vector_index": False,
                    "has_fts_index": True,
                    "has_scalar_index": True,
                    "table_version": 9,
                },
            )
        )
        info = await client.namespace_info("notes-text")
        assert info is not None
        assert info.vector_dim == 384
        assert info.has_fts_index is True
        assert info.has_vector_index is False


class TestErrorMapping:
    @respx.mock
    async def test_400_is_not_retried(self, client: FirnClient) -> None:
        route = respx.post(f"{BASE}/ns/notes-text/upsert").mock(
            return_value=httpx.Response(400, json={"error": "dimension mismatch"})
        )
        with pytest.raises(FirnBadRequestError, match="dimension mismatch"):
            await client.upsert("notes-text", [Row(id=1, vector=[0.1])])
        assert route.call_count == 1

    @respx.mock
    async def test_401_maps_to_auth_error(self, client: FirnClient) -> None:
        respx.get(f"{BASE}/ns/notes-text").mock(return_value=httpx.Response(401))
        with pytest.raises(FirnAuthError):
            await client.namespace_info("notes-text")

    @respx.mock
    async def test_5xx_is_retried_then_surfaces(self, client: FirnClient) -> None:
        route = respx.post(f"{BASE}/ns/notes-text/upsert").mock(return_value=httpx.Response(503))
        with pytest.raises(FirnUnavailableError):
            await client.upsert("notes-text", [Row(id=1, vector=[0.1])])
        assert route.call_count == 3

    @respx.mock
    async def test_retry_succeeds_on_a_later_attempt(self, client: FirnClient) -> None:
        route = respx.post(f"{BASE}/ns/notes-text/upsert").mock(
            side_effect=[
                httpx.Response(503),
                httpx.Response(200, json={"upserted": 1}),
            ]
        )
        assert await client.upsert("notes-text", [Row(id=1, vector=[0.1])]) == 1
        assert route.call_count == 2

    @respx.mock
    async def test_transport_error_is_retried(self, client: FirnClient) -> None:
        route = respx.post(f"{BASE}/ns/notes-text/upsert").mock(
            side_effect=httpx.ConnectError("refused")
        )
        with pytest.raises(FirnUnavailableError):
            await client.upsert("notes-text", [Row(id=1, vector=[0.1])])
        assert route.call_count == 3


class TestAuthHeaders:
    @respx.mock
    async def test_admin_routes_prefer_the_admin_key(self) -> None:
        settings = FirnSettings(url=BASE, api_key="rw", admin_api_key="admin")
        client = FirnClient(settings)
        route = respx.post(f"{BASE}/ns/notes-text/fts-index").mock(
            return_value=httpx.Response(202, json={"operation_id": "op1"})
        )
        await client.build_fts_index("notes-text")
        assert route.calls[0].request.headers["authorization"] == "Bearer admin"

    @respx.mock
    async def test_admin_routes_fall_back_to_the_readwrite_key(self) -> None:
        """Firn's own single-key fallback: with no admin key configured, the
        read/write key authorises admin routes."""
        settings = FirnSettings(url=BASE, api_key="rw")
        client = FirnClient(settings)
        route = respx.post(f"{BASE}/ns/notes-text/compact").mock(
            return_value=httpx.Response(202, json={"operation_id": "op2"})
        )
        await client.compact("notes-text")
        assert route.calls[0].request.headers["authorization"] == "Bearer rw"

    @respx.mock
    async def test_no_header_when_unauthenticated(self, client: FirnClient) -> None:
        route = respx.get(f"{BASE}/ns/notes-text").mock(return_value=httpx.Response(404))
        await client.namespace_info("notes-text")
        assert "authorization" not in route.calls[0].request.headers


class TestMaintenance:
    @respx.mock
    async def test_scalar_index_rejects_unsupported_columns(self, client: FirnClient) -> None:
        """Only id and _ingested_at exist. Anything else is a caller bug."""
        with pytest.raises(ValueError, match="unsupported scalar index column"):
            await client.build_scalar_index("notes-text", "content_type")

    @respx.mock
    async def test_operation_404_is_surfaced(self, client: FirnClient) -> None:
        """404 means unknown or aged out; treating it as 'still running' would
        hang a poller forever."""
        respx.get(f"{BASE}/operations/gone").mock(return_value=httpx.Response(404))
        with pytest.raises(FirnNotFoundError):
            await client.operation("gone")

    @respx.mock
    async def test_wait_for_operation_returns_on_success(self, client: FirnClient) -> None:
        respx.get(f"{BASE}/operations/op1").mock(
            side_effect=[
                httpx.Response(200, json={"operation_id": "op1", "status": "running"}),
                httpx.Response(200, json={"operation_id": "op1", "status": "succeeded"}),
            ]
        )
        result = await client.wait_for_operation("op1", poll_interval_seconds=0.001)
        assert result["status"] == "succeeded"

    @respx.mock
    async def test_wait_for_operation_returns_failure_rather_than_raising(
        self, client: FirnClient
    ) -> None:
        respx.get(f"{BASE}/operations/op2").mock(
            return_value=httpx.Response(
                200, json={"operation_id": "op2", "status": "failed", "error": "disk full"}
            )
        )
        result = await client.wait_for_operation("op2", poll_interval_seconds=0.001)
        assert result["status"] == "failed"
        assert result["error"] == "disk full"


class TestHealth:
    @respx.mock
    async def test_health_is_false_on_transport_error(self, client: FirnClient) -> None:
        """Health must never raise; a probe that throws is a probe that fails
        for the wrong reason."""
        respx.get(f"{BASE}/health").mock(side_effect=httpx.ConnectError("down"))
        assert await client.health() is False

    @respx.mock
    async def test_health_is_true_on_ok(self, client: FirnClient) -> None:
        respx.get(f"{BASE}/health").mock(return_value=httpx.Response(200, text="ok"))
        assert await client.health() is True
