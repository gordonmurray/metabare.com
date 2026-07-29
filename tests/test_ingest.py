"""Note ingestion.

The idempotency assertions are written against the *observable* effect (how
many Firn rows exist, how many upserts were issued) rather than against the
internal identity functions, because the requirement is about outcomes: a
duplicate S3 event must not create a duplicate record, whatever the mechanism.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from metabare.config import EncoderSettings, FirnSettings
from metabare.ids import PIPELINE_VERSION
from metabare.ingest import IngestionError, IngestionService
from metabare.models import ItemKind, ItemState, RecordDocument, StageState
from metabare.storage import ObjectStore
from metabare.text import DEFAULT_MAX_CHARS
from tests.conftest import FakeEncoder, FakeFirn

NOTE = "# EKS Spot\n\nNode drained after a two minute interruption notice."


@pytest.fixture
def service(
    firn: FakeFirn,
    store: ObjectStore,
    encoder: FakeEncoder,
    firn_config: FirnSettings,
) -> IngestionService:
    return IngestionService(
        firn=firn,  # type: ignore[arg-type]
        store=store,
        encoder=encoder,
        firn_config=firn_config,
        encoder_config=EncoderSettings(model_id="fake/encoder", model_revision="test", dimension=8),
    )


class TestHappyPath:
    async def test_returns_a_complete_record(self, service: IngestionService) -> None:
        record = await service.ingest_note(NOTE)
        assert record.kind is ItemKind.NOTE
        assert record.state is ItemState.COMPLETE
        assert record.text_stage is StageState.COMPLETE
        assert record.chunk_count == 1
        assert len(record.record_ids) == 1

    async def test_a_note_has_no_image_stage(self, service: IngestionService) -> None:
        """Not 'pending'. A note will never have an image vector, and showing
        the user an outstanding step that never runs is a lie."""
        record = await service.ingest_note(NOTE)
        assert record.image_stage is StageState.NOT_APPLICABLE

    async def test_derives_a_title_from_the_heading(self, service: IngestionService) -> None:
        record = await service.ingest_note(NOTE)
        assert record.title == "EKS Spot"

    async def test_explicit_title_wins(self, service: IngestionService) -> None:
        record = await service.ingest_note(NOTE, title="Chosen")
        assert record.title == "Chosen"

    async def test_writes_the_original_bytes(
        self, service: IngestionService, store: ObjectStore
    ) -> None:
        record = await service.ingest_note(NOTE)
        raw = await store.get_bytes(record.source.key)
        assert raw is not None
        assert b"EKS Spot" in raw

    async def test_writes_a_record_document_per_chunk(
        self, service: IngestionService, store: ObjectStore
    ) -> None:
        record = await service.ingest_note(NOTE)
        document = await store.get_record(record.record_ids[0])
        assert document is not None
        assert document.item_id == record.item_id
        assert document.namespace == "notes-text"
        assert document.chunk_count == 1

    async def test_upserts_to_the_notes_namespace(
        self, service: IngestionService, firn: FakeFirn
    ) -> None:
        await service.ingest_note(NOTE)
        assert list(firn.namespaces) == ["notes-text"]

    async def test_records_the_model_and_pipeline_versions(self, service: IngestionService) -> None:
        """Derived data must record what produced it."""
        record = await service.ingest_note(NOTE)
        assert record.pipeline_version == PIPELINE_VERSION
        assert record.model_versions["text-embedding"] == "fake/encoder@test"


class TestIdempotency:
    async def test_reingesting_identical_content_is_a_no_op(
        self, service: IngestionService, firn: FakeFirn
    ) -> None:
        """The core §12 guarantee: duplicate delivery, one row, no rework."""
        first = await service.ingest_note(NOTE)
        upserts_after_first = len(firn.upsert_calls)
        second = await service.ingest_note(NOTE)

        assert second.item_id == first.item_id
        assert second.record_ids == first.record_ids
        assert len(firn.upsert_calls) == upserts_after_first
        assert len(firn.namespaces["notes-text"]) == 1

    async def test_different_content_is_a_different_item(
        self, service: IngestionService, firn: FakeFirn
    ) -> None:
        first = await service.ingest_note(NOTE)
        second = await service.ingest_note(NOTE + "\n\nAnd another line.")
        assert first.item_id != second.item_id
        assert len(firn.namespaces["notes-text"]) == 2

    async def test_force_reprocesses_the_same_row(
        self, service: IngestionService, firn: FakeFirn
    ) -> None:
        """Controlled re-indexing writes the same id again, so the row count
        does not grow. This is what a pipeline bump must do."""
        first = await service.ingest_note(NOTE)
        before = len(firn.upsert_calls)
        second = await service.ingest_note(NOTE, force=True)
        assert len(firn.upsert_calls) == before + 1
        assert second.record_ids == first.record_ids
        assert len(firn.namespaces["notes-text"]) == 1

    async def test_attempts_are_counted(self, service: IngestionService) -> None:
        await service.ingest_note(NOTE)
        second = await service.ingest_note(NOTE, force=True)
        assert second.attempts == 2

    async def test_created_at_survives_reprocessing(self, service: IngestionService) -> None:
        """Re-indexing must not make an old item look newly created."""
        first = await service.ingest_note(NOTE)
        second = await service.ingest_note(NOTE, force=True)
        assert second.created_at == first.created_at
        assert second.updated_at >= first.updated_at

    async def test_a_model_version_change_triggers_reprocessing(
        self,
        firn: FakeFirn,
        store: ObjectStore,
        encoder: FakeEncoder,
        firn_config: FirnSettings,
    ) -> None:
        """A stale processing identity means redo the work, in place."""
        old = IngestionService(
            firn=firn,  # type: ignore[arg-type]
            store=store,
            encoder=encoder,
            firn_config=firn_config,
            encoder_config=EncoderSettings(model_id="fake/encoder", model_revision="v1"),
        )
        new = IngestionService(
            firn=firn,  # type: ignore[arg-type]
            store=store,
            encoder=encoder,
            firn_config=firn_config,
            encoder_config=EncoderSettings(model_id="fake/encoder", model_revision="v2"),
        )
        first = await old.ingest_note(NOTE)
        before = len(firn.upsert_calls)
        second = await new.ingest_note(NOTE)

        assert len(firn.upsert_calls) == before + 1, "stale identity should reprocess"
        assert second.record_ids == first.record_ids, "must replace in place, not append"
        assert len(firn.namespaces["notes-text"]) == 1
        assert second.model_versions["text-embedding"] == "fake/encoder@v2"


class TestChunking:
    async def test_a_long_note_produces_several_rows(
        self, service: IngestionService, firn: FakeFirn
    ) -> None:
        body = "\n\n".join(["paragraph " * 60] * 12)
        record = await service.ingest_note(body)
        assert record.chunk_count > 1
        assert len(record.record_ids) == record.chunk_count
        assert len(firn.namespaces["notes-text"]) == record.chunk_count

    async def test_chunk_ids_are_unique(self, service: IngestionService) -> None:
        body = "x" * (DEFAULT_MAX_CHARS * 4)
        record = await service.ingest_note(body)
        assert len(set(record.record_ids)) == len(record.record_ids)

    async def test_every_chunk_gets_a_document(
        self, service: IngestionService, store: ObjectStore
    ) -> None:
        body = "y" * (DEFAULT_MAX_CHARS * 3)
        record = await service.ingest_note(body)
        documents = await store.get_records(record.record_ids)
        assert len(documents) == record.chunk_count
        assert {d.chunk_index for d in documents.values()} == set(range(record.chunk_count))


class TestFailures:
    async def test_empty_note_is_rejected(self, service: IngestionService) -> None:
        with pytest.raises(IngestionError, match="empty"):
            await service.ingest_note("   \n\n   ")

    async def test_a_firn_failure_leaves_a_failed_record_not_a_missing_one(
        self, service: IngestionService, firn: FakeFirn, store: ObjectStore
    ) -> None:
        """A crash mid-pipeline must be visible and retryable, not invisible."""

        async def explode(*args: object, **kwargs: object) -> int:
            raise RuntimeError("firn is down")

        firn.upsert = explode  # type: ignore[method-assign]
        with pytest.raises(IngestionError, match="firn is down"):
            await service.ingest_note(NOTE)

        # The item record exists and says the stage failed, so a retry knows
        # there is work to redo rather than treating the item as complete.
        stored = await store.get_item(await _only_item_id(store))
        assert stored is not None
        assert stored.state is ItemState.FAILED
        assert stored.text_stage is StageState.FAILED
        assert "firn is down" in stored.error

    async def test_a_failed_item_is_retried_rather_than_skipped(
        self, service: IngestionService, firn: FakeFirn
    ) -> None:
        calls = {"n": 0}
        original = firn.upsert

        async def flaky(namespace: str, rows: object) -> int:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient")
            return await original(namespace, rows)

        firn.upsert = flaky  # type: ignore[method-assign]
        with pytest.raises(IngestionError):
            await service.ingest_note(NOTE)

        # No force flag: a FAILED record must not be mistaken for completed
        # work just because its processing identity matches.
        record = await service.ingest_note(NOTE)
        assert record.state is ItemState.COMPLETE
        assert calls["n"] == 2


async def _only_item_id(store: ObjectStore) -> str:
    from metabare.ids import SourceRef, content_sha256, item_id
    from metabare.storage import raw_key
    from metabare.text import normalise

    body = normalise(NOTE).encode()
    digest = content_sha256(body)
    return item_id(
        SourceRef(
            bucket=store.bucket,
            key=raw_key(ItemKind.NOTE, digest, ".md"),
            content_hash=digest,
        )
    )


class TestReindexingCleansUpAfterItself:
    """Regression tests for issues found in review of the first implementation."""

    async def test_fewer_chunks_retires_the_surplus_record_documents(
        self,
        service: IngestionService,
        store: ObjectStore,
        firn: FakeFirn,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A re-index that produces fewer chunks must not leave stale results.

        `record_id` is a function of (item_id, chunk_index), so nothing
        rewrites chunk 4 when the new text only has three chunks. Firn has no
        row-level delete, so the surplus rows stay in the namespace; removing
        their record documents is what stops them being rendered, because the
        search path drops any hit it cannot hydrate.
        """
        long_body = "\n\n".join(["paragraph " * 60] * 12)
        first = await service.ingest_note(long_body, title="fixed")
        assert first.chunk_count > 2
        stale_ids = list(first.record_ids)

        # The item identity must not change, or this is a new item rather than
        # a re-index. So the body stays identical and the chunker is what
        # changes, standing in for a pipeline version that chunks differently.
        monkeypatch.setattr("metabare.ingest.chunk", lambda text, **kw: [text[:100]])
        second = await service.ingest_note(long_body, title="fixed", force=True)

        assert second.chunk_count == 1
        assert second.record_ids == stale_ids[:1]

        surviving = await store.get_records(stale_ids)
        assert set(surviving) == {stale_ids[0]}, "surplus record documents should be gone"

    async def test_a_search_cannot_render_a_retired_chunk(
        self,
        firn: FakeFirn,
        store: ObjectStore,
        encoder: FakeEncoder,
        firn_config: FirnSettings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The end-to-end consequence: stale Firn rows never become results."""
        from metabare.search import SearchService

        service = IngestionService(
            firn=firn,  # type: ignore[arg-type]
            store=store,
            encoder=encoder,
            firn_config=firn_config,
            encoder_config=EncoderSettings(model_id="fake/encoder", model_revision="test"),
        )
        body = "\n\n".join(["paragraph " * 60] * 12)
        first = await service.ingest_note(body)
        assert first.chunk_count > 1

        monkeypatch.setattr("metabare.ingest.chunk", lambda text, **kw: [text[:100]])
        await service.ingest_note(body, force=True)

        # The stale rows are still in Firn; that is the documented limitation.
        assert len(firn.namespaces["notes-text"]) == first.chunk_count

        searcher = SearchService(
            firn=firn,  # type: ignore[arg-type]
            store=store,
            encoder=encoder,
            settings=firn_config,
        )
        response = await searcher.search("paragraph", limit=20)
        rendered = {hit.record_id for hit in response.hits}
        assert rendered == {first.record_ids[0]}, "only the surviving chunk is renderable"

    async def test_collision_is_detected_before_the_firn_write(
        self, service: IngestionService, store: ObjectStore, firn: FakeFirn
    ) -> None:
        """Checking after the upsert would be too late.

        Firn's upsert is latest-write-wins, so a collision detected after the
        write has already destroyed the other item's row. The check must
        therefore happen first, and no Firn write may occur when it fails.
        """
        from metabare.ids import SourceRef, content_sha256, item_id
        from metabare.ids import record_id as derive_record_id
        from metabare.storage import raw_key
        from metabare.text import normalise

        body = "a note that will collide"
        digest = content_sha256(normalise(body).encode())
        identity = item_id(
            SourceRef(
                bucket=store.bucket,
                key=raw_key(ItemKind.NOTE, digest, ".md"),
                content_hash=digest,
            )
        )
        # Squat on the record id this note will derive, under a different item.
        await store.put_record(
            RecordDocument(
                record_id=derive_record_id(identity, 0),
                item_id="9" * 64,
                namespace="notes-text",
                kind=ItemKind.NOTE,
                created_at=datetime.now(UTC),
                ingested_at=datetime.now(UTC),
            )
        )

        with pytest.raises(IngestionError, match="already belongs to item"):
            await service.ingest_note(body)

        assert firn.upsert_calls == [], "no Firn write may happen once a collision is known"
