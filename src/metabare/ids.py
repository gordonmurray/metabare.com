"""Identity derivation for MetaBare items and Firn rows.

Processing has to be idempotent under at-least-once S3 and SQS delivery,
using an identity built from durable source attributes. This module is the
single place that identity is computed.

Two distinct identities exist, and conflating them is the mistake this module
exists to prevent:

**Item identity** answers "which source object is this?". It is derived from
the bucket, key, S3 version id and content hash. It is deliberately *not*
affected by pipeline or model versions, because reprocessing the same object
with a newer pipeline must land on the *same* Firn row and replace it, rather
than accumulating a second copy alongside the first.

**Processing identity** answers "has this exact work already been done?". It is
the item identity plus the pipeline and model versions. The worker compares it
against the stored item record to decide whether a redelivered message is a
duplicate to skip or a version bump to reprocess. Without it, a model change
could not trigger a controlled re-index.

Firn's primary key is a `UInt64`, so the 128-plus bits of a SHA-256 have to
be folded down. That is done once, here,
with the collision properties documented in `record_id`.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

# Bumped when a change to extraction, normalisation, chunking or embedding
# means previously ingested items would now produce different index content.
# A bump makes every item's processing identity stale, which is the signal for
# a controlled re-index. It does not change item identity, so the re-index
# replaces rows in place.
PIPELINE_VERSION = "1"

_HEX64 = re.compile(r"\A[0-9a-f]{64}\Z")

# Field separator for identity preimages. A byte that cannot appear in an S3
# key, a hex digest or a version id, so no combination of field values can be
# re-partitioned into a different combination.
_SEP = b"\x1f"


def content_sha256(data: bytes) -> str:
    """Return the lowercase hex SHA-256 of raw object bytes."""
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True, slots=True)
class SourceRef:
    """The durable attributes of a stored source object.

    Attributes:
        bucket: Object storage bucket holding the original.
        key: Object key within the bucket.
        content_hash: Lowercase hex SHA-256 of the object bytes.
        version_id: S3 object version id when bucket versioning is enabled.
            Empty string when it is not. Included in identity so that two
            versions of the same key are distinct items rather than one item
            that silently changes underneath the index.
    """

    bucket: str
    key: str
    content_hash: str
    version_id: str = ""

    def __post_init__(self) -> None:
        if not self.bucket:
            raise ValueError("bucket must not be empty")
        if not self.key:
            raise ValueError("key must not be empty")
        if not _HEX64.match(self.content_hash):
            raise ValueError(
                f"content_hash must be 64 lowercase hex characters, got {self.content_hash!r}"
            )


def item_id(source: SourceRef) -> str:
    """Return the canonical item identity as 64 lowercase hex characters.

    Stable across pipeline versions by design. Two S3 events for the same
    object version produce the same value, which is what makes duplicate
    delivery a no-op rather than a duplicate row.
    """
    preimage = _SEP.join(
        (
            b"metabare-item-v1",
            source.bucket.encode(),
            source.key.encode(),
            source.version_id.encode(),
            source.content_hash.encode(),
        )
    )
    return hashlib.sha256(preimage).hexdigest()


def processing_id(item: str, *, pipeline_version: str, model_versions: dict[str, str]) -> str:
    """Return the processing identity for an item under a given pipeline.

    Args:
        item: The item identity from :func:`item_id`.
        pipeline_version: :data:`PIPELINE_VERSION` at the time of processing.
        model_versions: Map of role to model revision, for example
            ``{"text-embedding": "bge-small-en-v1.5@onnx"}``. Sorted before
            hashing so dictionary ordering cannot change the result.

    A stored item record whose ``processing_id`` differs from the one computed
    now is stale and must be reprocessed. One that matches is a duplicate
    delivery and can be acknowledged without work.
    """
    if not _HEX64.match(item):
        raise ValueError(f"item must be 64 lowercase hex characters, got {item!r}")
    parts = [b"metabare-processing-v1", item.encode(), pipeline_version.encode()]
    for role in sorted(model_versions):
        parts.append(role.encode())
        parts.append(model_versions[role].encode())
    return hashlib.sha256(_SEP.join(parts)).hexdigest()


def record_id(item: str, chunk_index: int = 0) -> int:
    """Return the Firn row id (``UInt64``) for one chunk of an item.

    Firn's primary key is a ``UInt64`` and its ``/upsert`` is latest-write-wins
    keyed on that id, so this value is what makes reprocessing converge on one
    row instead of appending.

    Folding SHA-256 to 64 bits admits collisions. By the birthday bound the
    probability of any collision in a corpus of n records is about
    ``n^2 / 2^65``: roughly 2.7e-8 at one million records, 2.7e-6 at ten
    million. Negligible, but not zero, and a collision would silently
    overwrite an unrelated record. It is therefore *detected* rather than
    merely assumed away: each record's stored document carries its full
    ``item_id``, and the worker refuses to write when the document already at
    that address belongs to a different item. See
    :meth:`metabare.storage.ObjectStore.put_record`.
    """
    if not _HEX64.match(item):
        raise ValueError(f"item must be 64 lowercase hex characters, got {item!r}")
    if chunk_index < 0:
        raise ValueError(f"chunk_index must be non-negative, got {chunk_index}")
    preimage = _SEP.join((b"metabare-record-v1", item.encode(), str(chunk_index).encode()))
    digest = hashlib.sha256(preimage).digest()
    return int.from_bytes(digest[:8], "big")


def record_key(record: int) -> str:
    """Return the zero-padded 16-character hex form of a Firn row id.

    Used as the S3 object key component for a record document, so a search hit
    (which returns only the ``UInt64``) resolves to storage with no separate
    lookup table.
    """
    if not 0 <= record < 2**64:
        raise ValueError(f"record id out of UInt64 range: {record}")
    return f"{record:016x}"
