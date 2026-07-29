"""Object storage adapter.

All S3 access goes through here so it can be mocked in tests and so the S3 key
layout lives in exactly one place. boto3 is synchronous, so the async surface
offloads to a worker thread rather than pretending to be async; a thread hop is
cheap next to an S3 round-trip.

``offload_to_thread`` exists to turn that off. Tests drive this against an
in-process mock where the "I/O" is a dictionary lookup, so the thread buys
nothing and costs something: crossing a thread boundary into a mocking library
that patches botocore has hung in at least one environment. Production keeps
the thread; tests call straight through.

The layout:

    raw/screenshots/{item_id}{ext}     original screenshot bytes, immutable
    raw/notes/{item_id}{ext}           original note bytes, immutable
    derived/items/{item_id}.json       canonical ItemRecord
    derived/records/{record_key}.json  read-optimised RecordDocument per Firn row
    derived/ocr/{item_id}.txt          extracted text
    derived/thumbnails/{item_id}.webp  result-card thumbnail
    firn/                              Firn's own tables, written by Firn

Each prefix is lifecycle-managed independently, which is the point of keeping
them separate rather than interleaving derived data with originals.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ParamSpec, TypeVar

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from .config import StorageSettings, storage_settings
from .ids import record_key
from .models import ItemKind, ItemRecord, RecordDocument

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mypy_boto3_s3.client import S3Client

P = ParamSpec("P")
T = TypeVar("T")

RAW_SCREENSHOTS = "raw/screenshots"
RAW_NOTES = "raw/notes"
DERIVED_ITEMS = "derived/items"
DERIVED_RECORDS = "derived/records"
DERIVED_OCR = "derived/ocr"
DERIVED_THUMBNAILS = "derived/thumbnails"
FIRN_PREFIX = "firn"


class StorageError(RuntimeError):
    """Object storage failure."""


class RecordIdCollisionError(StorageError):
    """Two different items folded to the same Firn UInt64 row id.

    Astronomically unlikely (see :func:`metabare.ids.record_id`) but silent and
    data-losing if unchecked, because Firn's upsert is latest-write-wins: the
    second item would simply replace the first's row. Detected on write and
    raised rather than logged, so the message goes to the DLQ with context
    instead of quietly corrupting the index.
    """


def raw_key(kind: ItemKind, item_id: str, extension: str) -> str:
    """Return the raw object key for an item."""
    prefix = RAW_SCREENSHOTS if kind is ItemKind.SCREENSHOT else RAW_NOTES
    ext = extension if extension.startswith(".") or not extension else f".{extension}"
    return f"{prefix}/{item_id}{ext}"


def item_key(item_id: str) -> str:
    return f"{DERIVED_ITEMS}/{item_id}.json"


def record_document_key(record_id: int) -> str:
    return f"{DERIVED_RECORDS}/{record_key(record_id)}.json"


def ocr_key(item_id: str) -> str:
    return f"{DERIVED_OCR}/{item_id}.txt"


def thumbnail_key(item_id: str) -> str:
    return f"{DERIVED_THUMBNAILS}/{item_id}.webp"


@dataclass(frozen=True, slots=True)
class StoredObject:
    """An object as written, with the attributes identity is derived from."""

    bucket: str
    key: str
    version_id: str
    size_bytes: int


def _build_client(settings: StorageSettings) -> S3Client:
    # Bounded retries and timeouts. The default boto3 retry mode is legacy and
    # less well behaved under throttling.
    config = BotoConfig(
        region_name=settings.region,
        retries={"max_attempts": 4, "mode": "standard"},
        connect_timeout=5,
        read_timeout=30,
        s3={"addressing_style": "path" if settings.use_path_style else "auto"},
    )
    kwargs: dict[str, Any] = {"config": config}
    if settings.s3_endpoint_url:
        kwargs["endpoint_url"] = settings.s3_endpoint_url
    # Static keys only ever appear for local MinIO. On EKS these are empty and
    # boto3 resolves credentials through Pod Identity or IRSA.
    if settings.s3_access_key_id and settings.s3_secret_access_key:
        kwargs["aws_access_key_id"] = settings.s3_access_key_id
        kwargs["aws_secret_access_key"] = settings.s3_secret_access_key
    client: S3Client = boto3.client("s3", **kwargs)
    return client


class ObjectStore:
    """Typed access to MetaBare's object layout."""

    def __init__(
        self,
        settings: StorageSettings | None = None,
        *,
        client: S3Client | None = None,
        offload_to_thread: bool = True,
    ) -> None:
        self._settings = settings or storage_settings()
        self._client = client or _build_client(self._settings)
        self._offload_to_thread = offload_to_thread

    async def _call(self, fn: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
        """Run a blocking boto3 call, on a worker thread unless disabled.

        Blocking the event loop is only acceptable because the caller has
        explicitly said the underlying call is not really I/O.
        """
        if self._offload_to_thread:
            return await asyncio.to_thread(fn, *args, **kwargs)
        return fn(*args, **kwargs)

    @property
    def bucket(self) -> str:
        return self._settings.bucket

    # ---- primitives -------------------------------------------------------

    def _put_bytes_sync(self, key: str, data: bytes, content_type: str) -> StoredObject:
        try:
            response = self._client.put_object(
                Bucket=self.bucket, Key=key, Body=data, ContentType=content_type
            )
        except ClientError as exc:  # pragma: no cover - network failure path
            raise StorageError(f"put {key} failed: {exc}") from exc
        return StoredObject(
            bucket=self.bucket,
            key=key,
            version_id=response.get("VersionId", "") or "",
            size_bytes=len(data),
        )

    def _get_bytes_sync(self, key: str) -> bytes | None:
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                return None
            raise StorageError(f"get {key} failed: {exc}") from exc
        body: bytes = response["Body"].read()
        return body

    async def put_bytes(self, key: str, data: bytes, content_type: str) -> StoredObject:
        return await self._call(self._put_bytes_sync, key, data, content_type)

    async def get_bytes(self, key: str) -> bytes | None:
        """Return object bytes, or None if the key does not exist."""
        return await self._call(self._get_bytes_sync, key)

    async def put_json(self, key: str, payload: dict[str, Any]) -> StoredObject:
        data = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        return await self.put_bytes(key, data, "application/json")

    async def get_json(self, key: str) -> dict[str, Any] | None:
        raw = await self.get_bytes(key)
        if raw is None:
            return None
        try:
            parsed: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError as exc:
            # A truncated or half-written derived object must never be mistaken
            # for a completed item. Treating it as absent means
            # the item is reprocessed, which is safe because processing is
            # idempotent.
            raise StorageError(f"{key} is not valid JSON: {exc}") from exc
        return parsed

    # ---- typed documents --------------------------------------------------

    async def put_item(self, item: ItemRecord) -> StoredObject:
        return await self.put_json(item_key(item.item_id), item.model_dump(mode="json"))

    async def get_item(self, item_id: str) -> ItemRecord | None:
        payload = await self.get_json(item_key(item_id))
        return None if payload is None else ItemRecord.model_validate(payload)

    async def check_record_ids(self, item_id_value: str, record_ids: list[int]) -> None:
        """Raise if any record id already belongs to a different item.

        This is the collision check described in :class:`RecordIdCollisionError`,
        and it is called **before** the Firn upsert rather than after. Firn's
        upsert is latest-write-wins, so checking afterwards would detect the
        collision only once the other item's index row had already been
        overwritten: loud, but too late to prevent the loss.

        It is not atomic. Two writers could both read "absent" and both write.
        Closing that window needs a conditional write, and given the collision
        probability of roughly 2.7e-8 at a million records the race adds
        essentially nothing to a risk that is already negligible. The point of
        the check is to turn a silent corruption into a debuggable failure, not
        to make it impossible.
        """
        existing = await asyncio.gather(
            *(self.get_json(record_document_key(rid)) for rid in record_ids),
            return_exceptions=True,
        )
        for rid, document in zip(record_ids, existing, strict=True):
            if not isinstance(document, dict):
                continue
            owner = document.get("item_id")
            if owner is not None and owner != item_id_value:
                raise RecordIdCollisionError(
                    f"record id {rid} ({record_key(rid)}) already belongs to item "
                    f"{owner}, refusing to overwrite with item {item_id_value}"
                )

    async def put_record(self, document: RecordDocument) -> StoredObject:
        """Write a record document.

        Callers write records only after :meth:`check_record_ids` has passed
        for the whole batch, so this does not re-check.
        """
        return await self.put_json(
            record_document_key(document.record_id), document.model_dump(mode="json")
        )

    def _delete_sync(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:  # pragma: no cover - network failure path
            raise StorageError(f"delete {key} failed: {exc}") from exc

    async def delete_record(self, record_id: int) -> None:
        """Remove a record document.

        Used when re-indexing produces fewer chunks than a previous run. See
        :meth:`metabare.ingest.IngestionService._retire_orphan_records`.
        """
        await self._call(self._delete_sync, record_document_key(record_id))

    async def get_record(self, record_id: int) -> RecordDocument | None:
        payload = await self.get_json(record_document_key(record_id))
        return None if payload is None else RecordDocument.model_validate(payload)

    async def get_records(self, record_ids: list[int]) -> dict[int, RecordDocument]:
        """Fetch several record documents concurrently.

        Search hydration is the hot path: k hits become k independent GETs, so
        they run together rather than in series. A missing or unreadable
        document drops that hit rather than failing the whole search, because a
        partial result page is more useful than an error.
        """
        results = await asyncio.gather(
            *(self.get_record(rid) for rid in record_ids),
            return_exceptions=True,
        )
        found: dict[int, RecordDocument] = {}
        for rid, result in zip(record_ids, results, strict=True):
            if isinstance(result, RecordDocument):
                found[rid] = result
        return found

    # ---- health -----------------------------------------------------------

    def _head_bucket_sync(self) -> bool:
        try:
            self._client.head_bucket(Bucket=self.bucket)
        except ClientError:
            return False
        return True

    async def reachable(self) -> bool:
        """Whether the bucket is reachable and permitted. Used by /readyz."""
        return await self._call(self._head_bucket_sync)
