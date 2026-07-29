"""Firn index lifecycle.

Guards the fix for a real trap: a populated namespace with no BM25 index
answers the product's main kind of query with a 500.
"""

from __future__ import annotations

import asyncio

import pytest

from metabare.config import EncoderSettings, FirnSettings
from metabare.indexing import IndexManager
from metabare.ingest import IngestionService
from metabare.storage import ObjectStore
from tests.conftest import FakeEncoder, FakeFirn


@pytest.fixture
def manager(firn: FakeFirn) -> IndexManager:
    return IndexManager(firn, vector_index_min_rows=5)  # type: ignore[arg-type]


class TestEnsureFtsIndex:
    async def test_builds_the_index_when_missing(
        self, manager: IndexManager, firn: FakeFirn
    ) -> None:
        firn.namespaces["notes-text"] = {1: {"id": 1}}
        assert await manager.ensure_fts_index("notes-text") is True
        assert ("notes-text", "fts") in firn.index_builds

    async def test_is_a_no_op_on_an_absent_namespace(
        self, manager: IndexManager, firn: FakeFirn
    ) -> None:
        """A namespace does not exist until its first write, and an empty one
        answers full-text queries fine, so there is nothing to build."""
        assert await manager.ensure_fts_index("notes-text") is False
        assert firn.index_builds == []

    async def test_builds_only_once(self, manager: IndexManager, firn: FakeFirn) -> None:
        firn.namespaces["notes-text"] = {1: {"id": 1}}
        for _ in range(5):
            await manager.ensure_fts_index("notes-text")
        assert firn.index_builds.count(("notes-text", "fts")) == 1

    async def test_concurrent_callers_build_once(
        self, manager: IndexManager, firn: FakeFirn
    ) -> None:
        """A burst of parallel ingests must not start several builds."""
        firn.namespaces["notes-text"] = {1: {"id": 1}}
        await asyncio.gather(*(manager.ensure_fts_index("notes-text") for _ in range(10)))
        assert firn.index_builds.count(("notes-text", "fts")) == 1

    async def test_recognises_an_index_built_elsewhere(
        self, manager: IndexManager, firn: FakeFirn
    ) -> None:
        """Another replica may have built it already."""
        firn.namespaces["notes-text"] = {1: {"id": 1}}
        firn.fts_indexes.add("notes-text")
        assert await manager.ensure_fts_index("notes-text") is True
        assert firn.index_builds == []

    async def test_a_build_failure_does_not_raise(
        self, manager: IndexManager, firn: FakeFirn
    ) -> None:
        """Rows are already committed and vector-searchable. Failing the whole
        ingest because an index build failed would lose useful work."""

        async def failing(namespace: str) -> str:
            from metabare.firn import FirnUnavailableError

            raise FirnUnavailableError("index service down")

        firn.namespaces["notes-text"] = {1: {"id": 1}}
        firn.build_fts_index = failing  # type: ignore[method-assign]
        assert await manager.ensure_fts_index("notes-text") is False

    async def test_a_reported_failure_is_not_cached_as_success(
        self, manager: IndexManager, firn: FakeFirn
    ) -> None:
        async def not_succeeded(operation_id: str, **kwargs: object) -> dict[str, object]:
            return {"operation_id": operation_id, "status": "failed", "error": "disk full"}

        firn.namespaces["notes-text"] = {1: {"id": 1}}
        firn.wait_for_operation = not_succeeded  # type: ignore[method-assign]
        assert await manager.ensure_fts_index("notes-text") is False
        assert manager.is_fts_ready("notes-text") is False


class TestVectorIndex:
    async def test_not_built_below_the_threshold(
        self, manager: IndexManager, firn: FakeFirn
    ) -> None:
        """An IVF_PQ index over a handful of rows partitions nothing useful."""
        firn.namespaces["notes-text"] = {i: {"id": i} for i in range(4)}
        assert await manager.maybe_build_vector_index("notes-text") is False
        assert firn.index_builds == []

    async def test_built_at_the_threshold(self, manager: IndexManager, firn: FakeFirn) -> None:
        firn.namespaces["notes-text"] = {i: {"id": i} for i in range(5)}
        assert await manager.maybe_build_vector_index("notes-text") is True
        assert ("notes-text", "vector") in firn.index_builds

    async def test_not_rebuilt_when_present(self, manager: IndexManager, firn: FakeFirn) -> None:
        firn.namespaces["notes-text"] = {i: {"id": i} for i in range(10)}
        firn.vector_indexes.add("notes-text")
        assert await manager.maybe_build_vector_index("notes-text") is False

    async def test_absent_namespace_is_a_no_op(self, manager: IndexManager, firn: FakeFirn) -> None:
        assert await manager.maybe_build_vector_index("notes-text") is False


class TestIngestionGuaranteesTheIndex:
    async def test_first_note_leaves_the_namespace_hybrid_capable(
        self,
        firn: FakeFirn,
        store: ObjectStore,
        encoder: FakeEncoder,
        firn_config: FirnSettings,
    ) -> None:
        """The end-to-end guarantee: after one note, a hybrid query works.

        Without this, the very first search on a new deployment fails with a
        500, which is the worst possible time for it to happen.
        """
        service = IngestionService(
            firn=firn,  # type: ignore[arg-type]
            store=store,
            encoder=encoder,
            firn_config=firn_config,
            encoder_config=EncoderSettings(model_id="fake/encoder", model_revision="test"),
        )
        await service.ingest_note("# A note\n\nwith some body text")

        assert "notes-text" in firn.fts_indexes
        result = await firn.query("notes-text", vector=[0.1] * 8, text="body", k=5)
        assert len(result.hits) == 1
