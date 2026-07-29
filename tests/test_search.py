"""Result merging.

Fusion is asserted directly rather than through the service, because it is the
one piece of retrieval logic whose correctness is a property of the algorithm
rather than of the data. The service tests below cover the plumbing around it:
degradation, hydration, and what happens when the index and object storage
disagree.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from metabare.config import FirnSettings
from metabare.firn import Hit, QueryMode, QueryResult
from metabare.ids import record_id
from metabare.models import ItemKind, RecordDocument, RetrievalPath
from metabare.search import RRF_K, SearchService, reciprocal_rank_fusion
from metabare.storage import ObjectStore
from tests.conftest import FakeEncoder, FakeFirn


def result(ids: list[int], mode: QueryMode = QueryMode.HYBRID) -> QueryResult:
    return QueryResult(
        mode=mode,
        hits=[Hit(id=i, score=1.0, text=None, ingested_at_micros=0) for i in ids],
        query_id="q",
    )


class TestReciprocalRankFusion:
    def test_single_list_preserves_order(self, firn_config: FirnSettings) -> None:
        fused = reciprocal_rank_fusion({"notes-text": result([3, 1, 2])}, firn_config)
        assert [c.record_id for c in fused] == [3, 1, 2]

    def test_agreement_across_lists_outranks_a_single_first_place(
        self, firn_config: FirnSettings
    ) -> None:
        """The behaviour RRF is chosen for.

        Record 2 is second in both lists; record 1 is first in one and absent
        from the other. Two seconds (2/62) beat one first (1/61).
        """
        fused = reciprocal_rank_fusion(
            {
                "notes-text": result([1, 2]),
                "screenshots-text": result([3, 2]),
            },
            firn_config,
        )
        assert fused[0].record_id == 2

    def test_scores_match_the_published_formula(self, firn_config: FirnSettings) -> None:
        fused = reciprocal_rank_fusion({"notes-text": result([5])}, firn_config)
        assert fused[0].score == pytest.approx(1.0 / (RRF_K + 1))

    def test_contributions_accumulate(self, firn_config: FirnSettings) -> None:
        fused = reciprocal_rank_fusion(
            {"notes-text": result([9]), "screenshots-text": result([9])}, firn_config
        )
        assert fused[0].score == pytest.approx(2.0 / (RRF_K + 1))
        assert len(fused[0].contributions) == 2

    def test_score_direction_is_irrelevant_to_fusion(self, firn_config: FirnSettings) -> None:
        """L2 distance and BM25 disagree on direction; rank fusion does not
        look at either, which is why the mismatch never needs resolving."""
        distances = QueryResult(
            mode=QueryMode.VECTOR,
            hits=[Hit(id=1, score=0.01, text=None, ingested_at_micros=0)],
            query_id="q",
        )
        relevance = QueryResult(
            mode=QueryMode.FULLTEXT,
            hits=[Hit(id=1, score=98.6, text=None, ingested_at_micros=0)],
            query_id="q",
        )
        fused = reciprocal_rank_fusion(
            {"notes-text": distances, "screenshots-text": relevance}, firn_config
        )
        assert fused[0].score == pytest.approx(2.0 / (RRF_K + 1))

    def test_ties_break_deterministically(self, firn_config: FirnSettings) -> None:
        """Two records at the same rank in different lists must not reorder
        between identical requests."""
        fused = reciprocal_rank_fusion(
            {"notes-text": result([7]), "screenshots-text": result([4])}, firn_config
        )
        assert [c.record_id for c in fused] == [4, 7]

    def test_empty_input(self, firn_config: FirnSettings) -> None:
        assert reciprocal_rank_fusion({}, firn_config) == []

    def test_image_namespace_is_labelled_as_an_image_path(self, firn_config: FirnSettings) -> None:
        fused = reciprocal_rank_fusion(
            {firn_config.screenshots_image_namespace: result([1], QueryMode.VECTOR)},
            firn_config,
        )
        assert RetrievalPath.IMAGE_VECTOR in fused[0].paths


async def seed_record(store: ObjectStore, record: int, item: str, text: str) -> None:
    now = datetime.now(UTC)
    await store.put_record(
        RecordDocument(
            record_id=record,
            item_id=item,
            namespace="notes-text",
            kind=ItemKind.NOTE,
            title="Title",
            text=text,
            source_key=f"raw/notes/{item}.md",
            created_at=now,
            ingested_at=now,
        )
    )


class TestSearchService:
    @pytest.fixture
    def service(
        self,
        firn: FakeFirn,
        store: ObjectStore,
        encoder: FakeEncoder,
        firn_config: FirnSettings,
    ) -> SearchService:
        return SearchService(
            firn=firn,  # type: ignore[arg-type]
            store=store,
            encoder=encoder,
            settings=firn_config,
        )

    async def test_empty_query_short_circuits(self, service: SearchService) -> None:
        response = await service.search("   ")
        assert response.hits == []
        assert response.namespaces_queried == []

    async def test_empty_index_answers_with_no_hits_and_no_degradation(
        self, service: SearchService
    ) -> None:
        """A fresh deployment has no namespaces at all.

        Firn answers a query against a never-written namespace with 200 and an
        empty list, for every kind of query. So this is an ordinary empty result,
        not a degraded one, and reporting it as degraded would cry wolf on the
        most common state of a new install.
        """
        response = await service.search("anything")
        assert response.hits == []
        assert response.total == 0
        assert response.degraded is False

    async def test_returns_hydrated_hits(
        self, service: SearchService, firn: FakeFirn, store: ObjectStore
    ) -> None:
        item = "a" * 64
        rid = record_id(item)
        firn.namespaces["notes-text"] = {rid: {"id": rid, "text": "spot interruption"}}
        firn.fts_indexes.add("notes-text")
        await seed_record(store, rid, item, "spot interruption")

        response = await service.search("spot")
        assert len(response.hits) == 1
        hit = response.hits[0]
        assert hit.record_id == rid
        assert hit.item_id == item
        assert hit.title == "Title"
        assert hit.rank == 1
        assert hit.retrieval_path is RetrievalPath.HYBRID

    async def test_score_carries_an_explanation(
        self, service: SearchService, firn: FakeFirn, store: ObjectStore
    ) -> None:
        """A score may only be shown if its meaning is stated."""
        item = "b" * 64
        rid = record_id(item)
        firn.namespaces["notes-text"] = {rid: {"id": rid, "text": "x"}}
        firn.fts_indexes.add("notes-text")
        await seed_record(store, rid, item, "x")
        response = await service.search("x")
        explanation = response.hits[0].score_explanation
        assert "Reciprocal Rank Fusion" in explanation
        assert "notes-text rank 1" in explanation

    async def test_missing_record_document_drops_the_hit(
        self, service: SearchService, firn: FakeFirn
    ) -> None:
        """The index knows a row the object store does not. Better to return
        fewer results than to invent a card for it."""
        firn.namespaces["notes-text"] = {42: {"id": 42, "text": "orphan"}}
        firn.fts_indexes.add("notes-text")
        response = await service.search("orphan")
        assert response.hits == []
        assert response.degraded is True

    async def test_an_unindexed_namespace_degrades_to_vector_only(
        self, service: SearchService, firn: FakeFirn, store: ObjectStore
    ) -> None:
        """A populated namespace with no BM25 index 500s on a hybrid query.

        Ingestion is supposed to prevent this, but if it happens the search
        must still return results through the vector half, and must say that
        it did so.
        """
        item = "c" * 64
        rid = record_id(item)
        firn.namespaces["notes-text"] = {rid: {"id": rid, "text": "only notes"}}
        await seed_record(store, rid, item, "only notes")

        response = await service.search("notes")
        assert response.hits, "vector fallback should still return the row"
        assert response.degraded is True
        assert "reduced" in response.degraded_reason
        assert response.hits[0].retrieval_path is RetrievalPath.TEXT_VECTOR

    async def test_limit_is_respected(
        self, service: SearchService, firn: FakeFirn, store: ObjectStore
    ) -> None:
        item = "d" * 64
        table = {}
        for index in range(25):
            rid = record_id(item, index)
            table[rid] = {"id": rid, "text": f"chunk {index}"}
            await seed_record(store, rid, item, f"chunk {index}")
        firn.namespaces["notes-text"] = table
        firn.fts_indexes.add("notes-text")
        response = await service.search("chunk", limit=5)
        assert len(response.hits) == 5
        assert response.total >= 5
        assert [h.rank for h in response.hits] == [1, 2, 3, 4, 5]

    async def test_excerpt_is_truncated(
        self, service: SearchService, firn: FakeFirn, store: ObjectStore
    ) -> None:
        item = "e" * 64
        rid = record_id(item)
        long_text = "y" * 900
        firn.namespaces["notes-text"] = {rid: {"id": rid, "text": long_text}}
        firn.fts_indexes.add("notes-text")
        await seed_record(store, rid, item, long_text)
        response = await service.search("y")
        assert len(response.hits[0].excerpt) <= 400
        assert response.hits[0].excerpt.endswith("...")
