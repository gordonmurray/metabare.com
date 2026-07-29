"""Canonical item metadata schema.

Firn stores four columns and no arbitrary metadata, so everything the product
needs to render a search result lives here, in object storage, with Firn
holding only the id, the vector and the searchable text. That split is the
point rather than a workaround: S3 is the source of truth and the index is
rebuildable from it.

Three documents exist per ingested item:

``raw/{notes,screenshots}/{item_id}...``
    The original bytes, immutable.

``derived/items/{item_id}.json`` -> :class:`ItemRecord`
    The canonical record. Carries processing state, versions and the list of
    Firn rows produced from the item. This is what ``GET /v1/items/{id}``
    serves and what the worker consults to decide whether redelivered work is
    a duplicate or a version bump.

``derived/records/{record_key}.json`` -> :class:`RecordDocument`
    A denormalised, read-optimised projection, one per Firn row. A search hit
    returns only a ``UInt64``; this document is what turns it back into a
    result card in a single GET, with no join and no lookup table. Item-level
    fields are duplicated into it deliberately, and the duplication is bounded
    because the overwhelming majority of items produce exactly one record.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1


class ItemKind(StrEnum):
    """What sort of thing was ingested. Determines which pipeline runs."""

    NOTE = "note"
    SCREENSHOT = "screenshot"


class StageState(StrEnum):
    """State of one asynchronous processing stage.

    ``NOT_APPLICABLE`` is distinct from ``PENDING`` on purpose: a note has no
    image-embedding stage at all, and the UI must not show it as work that is
    still outstanding.
    """

    NOT_APPLICABLE = "not_applicable"
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


class ItemState(StrEnum):
    """Overall item state, derived from the stage states.

    ``SEARCHABLE`` is the state that matters for the product promise: a
    screenshot becomes findable by its text before its optional image vector
    exists. An item sitting in ``SEARCHABLE`` is useful,
    not broken.
    """

    RECEIVED = "received"
    SEARCHABLE = "searchable"
    COMPLETE = "complete"
    FAILED = "failed"


class RetrievalPath(StrEnum):
    """Why a result matched. Surfaced in the UI."""

    LEXICAL = "lexical"
    TEXT_VECTOR = "text_vector"
    IMAGE_VECTOR = "image_vector"
    HYBRID = "hybrid"


class SourceObject(BaseModel):
    """The stored original, and the durable attributes identity is built from."""

    model_config = ConfigDict(frozen=True)

    bucket: str
    key: str
    content_hash: str = Field(description="Lowercase hex SHA-256 of the object bytes")
    version_id: str = Field(default="", description="S3 version id, empty if versioning is off")
    content_type: str
    size_bytes: int = Field(ge=0)
    filename: str = Field(default="", description="Original filename as supplied by the user")


class DerivedArtefacts(BaseModel):
    """Keys of derived objects, so they can be found and lifecycle-managed.

    Keys rather than URLs: the bucket is known from configuration, and storing
    URLs would bake the CDN hostname into durable data.
    """

    ocr_text_key: str = ""
    thumbnail_key: str = ""
    metadata_key: str = ""


class ItemRecord(BaseModel):
    """Canonical per-item metadata. Stored at ``derived/items/{item_id}.json``."""

    schema_version: int = SCHEMA_VERSION
    item_id: str = Field(min_length=64, max_length=64)
    kind: ItemKind
    source: SourceObject

    title: str = ""
    excerpt: str = Field(default="", description="Short preview for result cards")

    # Timestamps are timezone-aware UTC throughout. Naive datetimes are a
    # reliability bug in an ingestion pipeline, not a formatting preference.
    created_at: datetime = Field(description="When the source object was stored")
    updated_at: datetime = Field(description="When this record was last written")

    pipeline_version: str
    model_versions: dict[str, str] = Field(
        default_factory=dict,
        description="Role to model revision, e.g. {'text-embedding': 'bge-small-en-v1.5@onnx'}",
    )
    processing_id: str = Field(
        min_length=64,
        max_length=64,
        description="Identity of the work that produced this record; stale means re-index",
    )

    state: ItemState = ItemState.RECEIVED
    text_stage: StageState = StageState.PENDING
    image_stage: StageState = StageState.NOT_APPLICABLE

    chunk_count: int = Field(default=0, ge=0)
    record_ids: list[int] = Field(
        default_factory=list,
        description="Firn row ids produced from this item, in chunk order",
    )

    derived: DerivedArtefacts = Field(default_factory=DerivedArtefacts)

    error: str = Field(default="", description="Last failure reason, empty when healthy")
    attempts: int = Field(default=0, ge=0)

    def recompute_state(self) -> ItemState:
        """Derive the overall state from the stage states.

        Failure of any stage is terminal for the item's state even if another
        stage succeeded, so a partially failed item is never reported as
        complete. An item whose text is indexed is ``SEARCHABLE`` regardless of
        the image stage.
        """
        if StageState.FAILED in (self.text_stage, self.image_stage):
            return ItemState.FAILED
        if self.text_stage is not StageState.COMPLETE:
            return ItemState.RECEIVED
        if self.image_stage in (StageState.COMPLETE, StageState.NOT_APPLICABLE):
            return ItemState.COMPLETE
        return ItemState.SEARCHABLE


class RecordDocument(BaseModel):
    """Read-optimised projection of one Firn row.

    Stored at ``derived/records/{record_key}.json`` where ``record_key`` is the
    16-hex-character form of the Firn ``UInt64`` id, so a search hit resolves
    with one GET.
    """

    schema_version: int = SCHEMA_VERSION
    record_id: int = Field(ge=0, lt=2**64)
    item_id: str = Field(min_length=64, max_length=64)
    namespace: str

    chunk_index: int = Field(default=0, ge=0)
    chunk_count: int = Field(default=1, ge=1)

    kind: ItemKind
    title: str = ""
    text: str = Field(default="", description="The chunk text that was indexed")
    source_key: str = ""
    content_type: str = ""
    thumbnail_key: str = ""

    created_at: datetime
    ingested_at: datetime


class SearchHit(BaseModel):
    """One search result, as returned by the API."""

    record_id: int
    item_id: str
    kind: ItemKind
    title: str
    excerpt: str
    source_key: str
    thumbnail_key: str = ""
    content_type: str = ""
    created_at: datetime
    ingested_at: datetime
    chunk_index: int = 0
    chunk_count: int = 1

    retrieval_path: RetrievalPath
    rank: int = Field(ge=1, description="1-based position in the merged result list")
    score: float = Field(
        description=(
            "Fused rank score. Higher is better. This is a Reciprocal Rank Fusion "
            "score, not a probability or a similarity; it is comparable within one "
            "response and meaningless across responses."
        )
    )
    score_explanation: str = ""


class SearchResponse(BaseModel):
    """The full search response, including how the answer was produced."""

    query: str
    hits: list[SearchHit]
    total: int
    took_ms: float
    namespaces_queried: list[str]
    degraded: bool = Field(
        default=False,
        description="True when a retrieval path was unavailable and results are partial",
    )
    degraded_reason: str = ""
