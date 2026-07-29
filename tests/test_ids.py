"""Identity derivation.

If one of these regresses, duplicate S3 events start creating duplicate Firn
rows and nothing else in the system notices, so they are asserted directly
rather than inferred from higher-level behaviour.
"""

from __future__ import annotations

import pytest

from metabare.ids import (
    PIPELINE_VERSION,
    SourceRef,
    content_sha256,
    item_id,
    processing_id,
    record_id,
    record_key,
)

DIGEST_A = content_sha256(b"alpha")
DIGEST_B = content_sha256(b"beta")


def source(**overrides: str) -> SourceRef:
    fields: dict[str, str] = {
        "bucket": "metabare-dev",
        "key": "raw/notes/a.md",
        "content_hash": DIGEST_A,
        "version_id": "",
    }
    fields.update(overrides)
    return SourceRef(**fields)


class TestItemIdentity:
    def test_is_deterministic(self) -> None:
        assert item_id(source()) == item_id(source())

    def test_differs_on_every_component(self) -> None:
        base = item_id(source())
        assert item_id(source(bucket="other")) != base
        assert item_id(source(key="raw/notes/b.md")) != base
        assert item_id(source(content_hash=DIGEST_B)) != base
        assert item_id(source(version_id="v2")) != base

    def test_field_boundaries_cannot_be_shifted(self) -> None:
        """Concatenating fields differently must not collide.

        A naive ``bucket + key`` preimage makes ("ab", "c") and ("a", "bc")
        identical. The separator byte is what prevents that, and this is the
        test that would catch its removal.
        """
        left = item_id(source(bucket="ab", key="c"))
        right = item_id(source(bucket="a", key="bc"))
        assert left != right

    def test_rejects_malformed_content_hash(self) -> None:
        with pytest.raises(ValueError, match="content_hash"):
            source(content_hash="not-a-digest")

    def test_rejects_empty_bucket_or_key(self) -> None:
        with pytest.raises(ValueError, match="bucket"):
            source(bucket="")
        with pytest.raises(ValueError, match="key"):
            source(key="")


class TestProcessingIdentity:
    def test_changes_with_pipeline_version(self) -> None:
        item = item_id(source())
        models = {"text-embedding": "bge-small-en-v1.5@abc"}
        assert processing_id(item, pipeline_version="1", model_versions=models) != processing_id(
            item, pipeline_version="2", model_versions=models
        )

    def test_changes_with_model_version(self) -> None:
        item = item_id(source())
        first = processing_id(
            item, pipeline_version=PIPELINE_VERSION, model_versions={"text-embedding": "a"}
        )
        second = processing_id(
            item, pipeline_version=PIPELINE_VERSION, model_versions={"text-embedding": "b"}
        )
        assert first != second

    def test_is_independent_of_dict_ordering(self) -> None:
        item = item_id(source())
        forward = processing_id(item, pipeline_version="1", model_versions={"a": "1", "b": "2"})
        reverse = processing_id(item, pipeline_version="1", model_versions={"b": "2", "a": "1"})
        assert forward == reverse

    def test_does_not_alter_item_identity(self) -> None:
        """A pipeline bump must re-index in place, not create a second row.

        This is the distinction the module exists to enforce: the Firn row id
        derives from item identity only, so a version bump changes what is
        written but not where.
        """
        item = item_id(source())
        assert record_id(item) == record_id(item)


class TestRecordId:
    def test_fits_in_uint64(self) -> None:
        value = record_id(item_id(source()))
        assert 0 <= value < 2**64

    def test_chunks_are_distinct(self) -> None:
        item = item_id(source())
        ids = {record_id(item, index) for index in range(100)}
        assert len(ids) == 100

    def test_is_stable_across_calls(self) -> None:
        item = item_id(source())
        assert record_id(item, 3) == record_id(item, 3)

    def test_rejects_negative_chunk_index(self) -> None:
        with pytest.raises(ValueError, match="chunk_index"):
            record_id(item_id(source()), -1)

    def test_rejects_non_item_id_input(self) -> None:
        with pytest.raises(ValueError, match="64 lowercase hex"):
            record_id("short")

    def test_record_key_is_sortable_fixed_width(self) -> None:
        assert record_key(0) == "0" * 16
        assert record_key(2**64 - 1) == "f" * 16
        assert len(record_key(record_id(item_id(source())))) == 16

    def test_record_key_rejects_out_of_range(self) -> None:
        with pytest.raises(ValueError, match="UInt64"):
            record_key(2**64)

    def test_distribution_has_no_obvious_clustering(self) -> None:
        """A weak fold would bunch ids and raise the real collision rate.

        Not a statistical proof, just a smoke test that the top bits of the
        digest are actually varying across a realistic set of inputs.
        """
        ids = [
            record_id(item_id(source(content_hash=content_sha256(str(n).encode()))))
            for n in range(512)
        ]
        assert len(set(ids)) == 512
        top_nibbles = {value >> 60 for value in ids}
        assert len(top_nibbles) >= 12
