"""Ingestion pipeline.

Shared by the API's direct note creation path and, eventually, the SQS-driven
worker. Keeping one implementation means the idempotency guarantees hold
identically whichever entry point is used.

The order of writes matters and is not arbitrary:

1. Original bytes to object storage. Nothing else happens until the source is
   durable, so a crash mid-pipeline can always be resumed from the source.
2. Firn upsert. Latest-write-wins on a deterministic id, so a repeat converges.
3. Record documents, then the item record last.

The item record is written last on purpose. It is the only document that
claims an item is complete, so it cannot say so before the things it describes
exist. A crash between steps leaves an item that looks incomplete and gets
reprocessed, which is safe; the reverse would leave an item that claims to be
searchable while its index rows are missing, which is not.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from .config import EncoderSettings, FirnSettings, encoder_settings, firn_settings
from .embeddings import TextEncoder
from .firn import FirnClient, Row
from .ids import (
    PIPELINE_VERSION,
    SourceRef,
    content_sha256,
    item_id,
    processing_id,
    record_id,
)
from .indexing import IndexManager
from .models import (
    DerivedArtefacts,
    ItemKind,
    ItemRecord,
    ItemState,
    RecordDocument,
    SourceObject,
    StageState,
)
from .observability import (
    get_logger,
    ingestion_duration_seconds,
    orphaned_index_rows,
    stage_duration_seconds,
    stage_failures_total,
    uploads_total,
)
from .storage import ObjectStore, item_key, raw_key
from .text import chunk, derive_title, excerpt, normalise

logger = get_logger(__name__)


class IngestionError(RuntimeError):
    """Ingestion failed in a way the caller should surface."""


class IngestionService:
    """Turns a note or screenshot into durable objects and Firn rows."""

    def __init__(
        self,
        *,
        firn: FirnClient,
        store: ObjectStore,
        encoder: TextEncoder,
        firn_config: FirnSettings | None = None,
        encoder_config: EncoderSettings | None = None,
        indexes: IndexManager | None = None,
    ) -> None:
        self._firn = firn
        self._store = store
        self._encoder = encoder
        self._firn_config = firn_config or firn_settings()
        self._encoder_config = encoder_config or encoder_settings()
        self._indexes = indexes or IndexManager(firn)

    @property
    def _model_versions(self) -> dict[str, str]:
        return {"text-embedding": self._encoder_config.model_version}

    async def ingest_note(
        self,
        body: str,
        *,
        filename: str = "",
        title: str = "",
        content_type: str = "text/markdown",
        force: bool = False,
    ) -> ItemRecord:
        """Ingest a text or Markdown note.

        Args:
            body: The note content.
            filename: Original filename, if the note came from a file.
            title: Explicit title. Derived from the content when omitted.
            content_type: MIME type of the original.
            force: Reprocess even when the stored processing identity matches.
                Used by controlled re-indexing, not by normal delivery.

        Returns:
            The item record as written. Re-ingesting identical content returns
            the existing record without touching Firn.
        """
        started = time.perf_counter()
        normalised = normalise(body)
        if not normalised:
            uploads_total.labels(kind=ItemKind.NOTE.value, outcome="rejected").inc()
            raise IngestionError("note body is empty after normalisation")

        raw_bytes = normalised.encode("utf-8")
        digest = content_sha256(raw_bytes)
        extension = ".md" if content_type == "text/markdown" else ".txt"
        provisional = SourceRef(
            bucket=self._store.bucket,
            key=raw_key(ItemKind.NOTE, digest, extension),
            content_hash=digest,
        )
        # The raw key embeds the content hash rather than the item id, because
        # the item id is derived from the key and the key cannot depend on it.
        # Content-addressed storage of originals also means identical notes
        # stored twice occupy one object.
        identity = item_id(provisional)
        expected_processing = processing_id(
            identity,
            pipeline_version=PIPELINE_VERSION,
            model_versions=self._model_versions,
        )

        existing = await self._store.get_item(identity)
        if (
            existing is not None
            and existing.processing_id == expected_processing
            and existing.state is not ItemState.FAILED
            and not force
        ):
            # Duplicate delivery of work already done. Acknowledge without
            # repeating the embedding or the Firn write.
            uploads_total.labels(kind=ItemKind.NOTE.value, outcome="duplicate").inc()
            return existing

        now = datetime.now(UTC)
        stored = await self._store.put_bytes(provisional.key, raw_bytes, content_type)
        source = SourceObject(
            bucket=stored.bucket,
            key=stored.key,
            content_hash=digest,
            version_id=stored.version_id,
            content_type=content_type,
            size_bytes=stored.size_bytes,
            filename=filename,
        )

        record = ItemRecord(
            item_id=identity,
            kind=ItemKind.NOTE,
            source=source,
            title=title or derive_title(normalised, fallback=filename),
            excerpt=excerpt(normalised),
            created_at=existing.created_at if existing else now,
            updated_at=now,
            pipeline_version=PIPELINE_VERSION,
            model_versions=self._model_versions,
            processing_id=expected_processing,
            text_stage=StageState.RUNNING,
            # A note has no image-embedding stage at all. Marking it pending
            # would show the UI an outstanding step that will never run.
            image_stage=StageState.NOT_APPLICABLE,
            attempts=(existing.attempts + 1) if existing else 1,
            derived=DerivedArtefacts(metadata_key=item_key(identity)),
            # Carried forward so re-indexing can tell which chunk rows the
            # previous run produced and retire the ones it no longer does.
            record_ids=list(existing.record_ids) if existing else [],
            chunk_count=existing.chunk_count if existing else 0,
        )

        try:
            await self._index_text(
                record,
                normalised,
                namespace=self._firn_config.notes_text_namespace,
                now=now,
            )
        except Exception as exc:
            record.text_stage = StageState.FAILED
            record.error = f"{type(exc).__name__}: {exc}"
            record.state = record.recompute_state()
            await self._store.put_item(record)
            stage_failures_total.labels(stage="text_index", reason=type(exc).__name__).inc()
            uploads_total.labels(kind=ItemKind.NOTE.value, outcome="failed").inc()
            raise IngestionError(f"indexing note {identity} failed: {exc}") from exc

        record.text_stage = StageState.COMPLETE
        record.error = ""
        record.state = record.recompute_state()
        await self._store.put_item(record)

        elapsed = time.perf_counter() - started
        ingestion_duration_seconds.labels(kind=ItemKind.NOTE.value).observe(elapsed)
        uploads_total.labels(kind=ItemKind.NOTE.value, outcome="success").inc()
        return record

    async def _index_text(
        self,
        record: ItemRecord,
        text: str,
        *,
        namespace: str,
        now: datetime,
    ) -> None:
        """Embed, upsert to Firn, and write the per-record documents."""
        chunks = chunk(text)
        if not chunks:
            raise IngestionError("no indexable chunks produced")

        encode_started = time.perf_counter()
        vectors = await self._encoder.encode_passages_async(chunks)
        stage_duration_seconds.labels(stage="text_embed").observe(
            time.perf_counter() - encode_started
        )

        record_ids = [record_id(record.item_id, index) for index in range(len(chunks))]
        rows = [
            Row(id=rid, vector=vector.tolist(), text=chunk_text)
            for rid, vector, chunk_text in zip(record_ids, vectors, chunks, strict=True)
        ]

        # Checked before the Firn write, not after. Firn's upsert is
        # latest-write-wins, so a collision detected afterwards would already
        # have destroyed the other item's index row.
        await self._store.check_record_ids(record.item_id, record_ids)

        upsert_started = time.perf_counter()
        await self._firn.upsert(namespace, rows)
        stage_duration_seconds.labels(stage="firn_upsert").observe(
            time.perf_counter() - upsert_started
        )

        # A namespace with no BM25 index answers hybrid and full-text queries
        # with a 500, so the index is ensured as part of the write that creates
        # the namespace. Cheap after the first call, and it fails soft: the
        # rows are already committed and findable by vector either way.
        await self._indexes.ensure_fts_index(namespace)
        await self._indexes.maybe_build_vector_index(namespace)

        # Record documents are written after the Firn upsert. If this fails,
        # the index holds rows whose documents are missing; the search path
        # drops those hits and logs the divergence, and the item record still
        # says the stage did not complete, so it is reprocessed.
        for index, (rid, chunk_text) in enumerate(zip(record_ids, chunks, strict=True)):
            await self._store.put_record(
                RecordDocument(
                    record_id=rid,
                    item_id=record.item_id,
                    namespace=namespace,
                    chunk_index=index,
                    chunk_count=len(chunks),
                    kind=record.kind,
                    title=record.title,
                    text=chunk_text,
                    source_key=record.source.key,
                    content_type=record.source.content_type,
                    created_at=record.created_at,
                    ingested_at=now,
                )
            )

        await self._retire_orphan_records(record, record_ids)

        record.chunk_count = len(chunks)
        record.record_ids = record_ids

    async def _retire_orphan_records(
        self, record: ItemRecord, current_record_ids: list[int]
    ) -> None:
        """Drop record documents for chunks a re-index no longer produces.

        Re-indexing an item whose text now chunks into fewer pieces leaves the
        surplus rows behind, because ``record_id`` is a function of
        ``(item_id, chunk_index)`` and nothing rewrites index 4 when the new
        text only has three chunks.

        **Firn has no row-level delete.** Its only delete removes an entire
        namespace, so those surplus rows cannot be removed from the index by
        this code. What can be removed is their
        record document, and that is sufficient for correctness: the search
        path drops any hit whose document is missing, so a retired chunk can
        never be rendered as a result.

        The residual cost is real and worth stating: the orphaned vectors still
        occupy space in the namespace and can still occupy a slot in a top-k
        response, so effective recall for that query is slightly reduced until
        the namespace is rebuilt. Search over-fetches partly for this reason.
        """
        stale = [rid for rid in record.record_ids if rid not in set(current_record_ids)]
        if not stale:
            return
        logger.info(
            "retiring record documents for chunks a re-index no longer produces",
            item_id=record.item_id,
            stale_records=len(stale),
            previous_chunks=len(record.record_ids),
            current_chunks=len(current_record_ids),
        )
        for rid in stale:
            await self._store.delete_record(rid)
        orphaned_index_rows.inc(len(stale))
