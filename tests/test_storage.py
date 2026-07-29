"""Object storage adapter, against moto."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from metabare.ids import content_sha256, item_id, record_id
from metabare.models import (
    ItemKind,
    ItemRecord,
    ItemState,
    RecordDocument,
    SourceObject,
    StageState,
)
from metabare.storage import (
    ObjectStore,
    RecordIdCollisionError,
    StorageError,
    item_key,
    raw_key,
    record_document_key,
)


def make_item(item: str, *, state: ItemState = ItemState.COMPLETE) -> ItemRecord:
    now = datetime.now(UTC)
    return ItemRecord(
        item_id=item,
        kind=ItemKind.NOTE,
        source=SourceObject(
            bucket="metabare-test",
            key=f"raw/notes/{item}.md",
            content_hash=item,
            content_type="text/markdown",
            size_bytes=10,
        ),
        created_at=now,
        updated_at=now,
        pipeline_version="1",
        processing_id=item,
        state=state,
        text_stage=StageState.COMPLETE,
    )


def make_record(record: int, item: str) -> RecordDocument:
    now = datetime.now(UTC)
    return RecordDocument(
        record_id=record,
        item_id=item,
        namespace="notes-text",
        kind=ItemKind.NOTE,
        title="t",
        text="body",
        created_at=now,
        ingested_at=now,
    )


class TestKeyLayout:
    def test_raw_keys_separate_by_kind(self) -> None:
        assert raw_key(ItemKind.NOTE, "abc", ".md") == "raw/notes/abc.md"
        assert raw_key(ItemKind.SCREENSHOT, "abc", ".png") == "raw/screenshots/abc.png"

    def test_extension_is_normalised(self) -> None:
        assert raw_key(ItemKind.NOTE, "abc", "md") == "raw/notes/abc.md"
        assert raw_key(ItemKind.NOTE, "abc", "") == "raw/notes/abc"

    def test_record_key_is_hex_padded(self) -> None:
        assert record_document_key(255) == "derived/records/00000000000000ff.json"

    def test_derived_prefixes_are_independent(self) -> None:
        """Lifecycle rules apply per prefix, so they must not interleave."""
        assert item_key("a").startswith("derived/items/")
        assert record_document_key(1).startswith("derived/records/")


class TestRoundTrip:
    async def test_bytes(self, store: ObjectStore) -> None:
        await store.put_bytes("raw/notes/x.md", b"hello", "text/markdown")
        assert await store.get_bytes("raw/notes/x.md") == b"hello"

    async def test_missing_key_returns_none(self, store: ObjectStore) -> None:
        assert await store.get_bytes("nope") is None
        assert await store.get_item("0" * 64) is None
        assert await store.get_record(1) is None

    async def test_item_record(self, store: ObjectStore) -> None:
        item = "a" * 64
        await store.put_item(make_item(item))
        loaded = await store.get_item(item)
        assert loaded is not None
        assert loaded.item_id == item
        assert loaded.state is ItemState.COMPLETE

    async def test_corrupt_json_raises_rather_than_looking_empty(self, store: ObjectStore) -> None:
        """A half-written document must not be read as a valid one."""
        await store.put_bytes(item_key("b" * 64), b"{not json", "application/json")
        with pytest.raises(StorageError, match="not valid JSON"):
            await store.get_item("b" * 64)


class TestRecordCollisionDetection:
    async def test_same_item_overwrites_freely(self, store: ObjectStore) -> None:
        item = "c" * 64
        rid = record_id(item)
        await store.put_record(make_record(rid, item))
        await store.put_record(make_record(rid, item))
        loaded = await store.get_record(rid)
        assert loaded is not None
        assert loaded.item_id == item

    async def test_unused_ids_pass(self, store: ObjectStore) -> None:
        await store.check_record_ids("a" * 64, [1, 2, 3])

    async def test_own_ids_pass(self, store: ObjectStore) -> None:
        item = "b" * 64
        rid = record_id(item)
        await store.put_record(make_record(rid, item))
        await store.check_record_ids(item, [rid])

    async def test_different_item_at_same_id_is_refused(self, store: ObjectStore) -> None:
        """The UInt64 fold is lossy; a clash must be loud, not silent.

        Constructed directly rather than searched for, because finding a real
        SHA-256 collision in the top 64 bits is not something a test can do.
        """
        rid = 12345
        await store.put_record(make_record(rid, "d" * 64))
        with pytest.raises(RecordIdCollisionError, match="already"):
            await store.check_record_ids("e" * 64, [rid])

    async def test_collision_message_names_both_items(self, store: ObjectStore) -> None:
        rid = 999
        await store.put_record(make_record(rid, "f" * 64))
        with pytest.raises(RecordIdCollisionError) as excinfo:
            await store.check_record_ids("0" * 64, [rid])
        message = str(excinfo.value)
        assert "f" * 64 in message
        assert "0" * 64 in message

    async def test_a_clash_anywhere_in_the_batch_is_caught(self, store: ObjectStore) -> None:
        """A chunked item checks every chunk, not just the first."""
        await store.put_record(make_record(77, "1" * 64))
        with pytest.raises(RecordIdCollisionError):
            await store.check_record_ids("2" * 64, [10, 20, 77, 90])


class TestDeleteRecord:
    async def test_removes_the_document(self, store: ObjectStore) -> None:
        item = "3" * 64
        rid = record_id(item)
        await store.put_record(make_record(rid, item))
        await store.delete_record(rid)
        assert await store.get_record(rid) is None

    async def test_deleting_an_absent_record_is_not_an_error(self, store: ObjectStore) -> None:
        await store.delete_record(4242)


class TestBatchHydration:
    async def test_returns_only_what_exists(self, store: ObjectStore) -> None:
        item = "a" * 64
        present = record_id(item, 0)
        absent = record_id(item, 1)
        await store.put_record(make_record(present, item))
        found = await store.get_records([present, absent])
        assert set(found) == {present}

    async def test_empty_input(self, store: ObjectStore) -> None:
        assert await store.get_records([]) == {}

    async def test_a_broken_document_does_not_fail_the_page(self, store: ObjectStore) -> None:
        """One unreadable hit should cost that hit, not the whole search."""
        item = "a" * 64
        good = record_id(item, 0)
        bad = record_id(item, 1)
        await store.put_record(make_record(good, item))
        await store.put_bytes(record_document_key(bad), b"{broken", "application/json")
        found = await store.get_records([good, bad])
        assert set(found) == {good}


class TestHealth:
    async def test_reachable_bucket(self, store: ObjectStore) -> None:
        assert await store.reachable() is True


class TestIdentityIntegration:
    async def test_content_addressed_originals_deduplicate(self, store: ObjectStore) -> None:
        """Identical notes stored twice occupy one object and one identity."""
        body = b"# same note"
        digest = content_sha256(body)
        key = raw_key(ItemKind.NOTE, digest, ".md")
        first = await store.put_bytes(key, body, "text/markdown")
        second = await store.put_bytes(key, body, "text/markdown")
        assert first.key == second.key
        from metabare.ids import SourceRef

        ref = SourceRef(bucket=store.bucket, key=key, content_hash=digest)
        assert item_id(ref) == item_id(ref)
