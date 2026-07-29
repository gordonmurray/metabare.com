"""Shared fixtures.

The unit suite never downloads a model and never talks to AWS. A real ONNX
encoder would add tens of seconds and a network dependency to every run, and a
real bucket would make the suite unrunnable offline, so both are substituted
here. The behaviour that actually depends on real embeddings, namely retrieval
quality, is measured by the evaluation in ``benchmarks/`` instead of asserted
in unit tests, because a unit test cannot tell a good embedding from a bad one.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from typing import Any

import boto3
import numpy as np
import pytest
from moto import mock_aws

from metabare.config import (
    EncoderSettings,
    FirnSettings,
    StorageSettings,
    reset_settings_cache,
)
from metabare.embeddings import FloatArray, TextEncoder
from metabare.storage import ObjectStore

TEST_BUCKET = "metabare-test"
TEST_REGION = "eu-west-1"


@pytest.fixture(autouse=True)
def _clean_settings_cache() -> Iterator[None]:
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture(autouse=True)
def _fake_aws_credentials() -> Iterator[None]:
    """Stop boto3 from finding real credentials during a test run.

    Without this, a developer with a working AWS profile could have a test
    silently reach a real account.
    """
    previous = {
        key: os.environ.get(key)
        for key in (
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
            "AWS_PROFILE",
            "AWS_DEFAULT_REGION",
        )
    }
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = TEST_REGION
    os.environ.pop("AWS_PROFILE", None)
    yield
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


class FakeEncoder(TextEncoder):
    """Deterministic stand-in for the ONNX encoder.

    Produces a stable unit vector per input string by seeding a PRNG with the
    text's digest, so identical text embeds identically across runs and
    processes, and different text embeds differently. That is all the ingestion
    and search plumbing needs; it does not need the vectors to be *meaningful*.
    """

    def __init__(self, dimension: int = 8) -> None:
        settings = EncoderSettings(
            model_id="fake/encoder",
            model_revision="test",
            dimension=dimension,
            query_prefix="query: ",
            passage_prefix="",
        )
        super().__init__(settings)
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    def load(self) -> None:
        return

    def encode(self, texts: list[str]) -> FloatArray:
        if not texts:
            return np.zeros((0, self._dimension), dtype=np.float32)
        rows = []
        for text in texts:
            seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")
            rng = np.random.default_rng(seed)
            vector = rng.standard_normal(self._dimension).astype(np.float32)
            rows.append(vector / np.linalg.norm(vector))
        return np.stack(rows).astype(np.float32)

    # No thread. The real encoder offloads because ONNX inference blocks;
    # this one is a seeded PRNG, so a worker thread would add a boundary for
    # nothing. Tests should not cross one unless they are testing it.
    async def encode_query_async(self, query: str) -> FloatArray:
        return self.encode_query(query)

    async def encode_passages_async(self, passages: list[str]) -> FloatArray:
        return self.encode_passages(passages)


@pytest.fixture
def encoder() -> FakeEncoder:
    return FakeEncoder()


@pytest.fixture
def storage_config() -> StorageSettings:
    return StorageSettings(bucket=TEST_BUCKET, region=TEST_REGION)


@pytest.fixture
def store(storage_config: StorageSettings) -> Iterator[ObjectStore]:
    with mock_aws():
        client = boto3.client("s3", region_name=TEST_REGION)
        client.create_bucket(
            Bucket=TEST_BUCKET,
            CreateBucketConfiguration={"LocationConstraint": "eu-west-1"},
        )
        # offload_to_thread=False: moto patches botocore in-process, so the
        # "I/O" here is a dictionary lookup. Handing that to a worker thread
        # buys nothing and has hung in at least one environment.
        yield ObjectStore(storage_config, client=client, offload_to_thread=False)


@pytest.fixture
def firn_config() -> FirnSettings:
    return FirnSettings(
        url="http://firn.test:3000",
        notes_text_namespace="notes-text",
        screenshots_text_namespace="screenshots-text",
        screenshots_image_namespace="screenshots-image",
    )


class FakeFirn:
    """In-memory Firn double with the semantics that matter.

    Reproduces the behaviours MetaBare's correctness depends on, each of which
    was verified against a real Firn 0.9.4 rather than assumed:

    * ``/upsert`` is latest-write-wins keyed on ``id``, and rejects duplicate
      ids within one request.
    * A namespace does not exist until its first write, so ``GET /ns/{ns}``
      returns nothing before then.
    * ``/query`` against a namespace that **has rows but no BM25 index** fails
      with a 500 when ``text`` is supplied. Modelling that here is what keeps
      the ingestion-time index guarantee under test.
    * ``/query`` against a namespace that has never been written returns an
      empty result set rather than a 404, for every kind of query including
      full-text. The 500 above is specific to a populated, unindexed
      namespace, which is exactly why it is easy to miss in a fresh
      environment and then hit in a real one.

    Ranking quality is not something a double can or should fake, so results
    come back in insertion order and the tests that care about ordering
    exercise the fusion function directly.
    """

    def __init__(self) -> None:
        self.namespaces: dict[str, dict[int, dict[str, Any]]] = {}
        self.upsert_calls: list[tuple[str, int]] = []
        self.fts_indexes: set[str] = set()
        self.vector_indexes: set[str] = set()
        self.index_builds: list[tuple[str, str]] = []

    async def upsert(self, namespace: str, rows: Any) -> int:
        seen: set[int] = set()
        for row in rows:
            if row.id in seen:
                raise ValueError(f"duplicate id {row.id} within one upsert batch")
            seen.add(row.id)
        table = self.namespaces.setdefault(namespace, {})
        for row in rows:
            table[row.id] = row.to_payload()
        self.upsert_calls.append((namespace, len(list(rows))))
        return len(seen)

    async def query(self, namespace: str, **kwargs: Any) -> Any:
        from metabare.firn import FirnUnavailableError, Hit, QueryMode, QueryResult

        text = kwargs.get("text")
        table = self.namespaces.get(namespace, {})
        if text and table and namespace not in self.fts_indexes:
            raise FirnUnavailableError(f"POST /ns/{namespace}/query returned 500: no BM25 index")
        k = kwargs.get("k", 10)
        hits = [
            Hit(id=rid, score=1.0, text=row.get("text"), ingested_at_micros=0)
            for rid, row in list(table.items())[:k]
        ]
        mode = QueryMode.HYBRID if text else QueryMode.VECTOR
        return QueryResult(mode=mode, hits=hits, query_id="fake")

    async def health(self) -> bool:
        return True

    async def namespace_info(self, namespace: str) -> Any:
        from metabare.firn import NamespaceInfo

        table = self.namespaces.get(namespace)
        if table is None:
            return None
        return NamespaceInfo(
            namespace=namespace,
            kind="single",
            vector_dim=8,
            row_count=len(table),
            fragment_count=1,
            has_vector_index=namespace in self.vector_indexes,
            has_fts_index=namespace in self.fts_indexes,
            has_scalar_index=True,
            table_version=len(self.upsert_calls),
        )

    async def build_fts_index(self, namespace: str) -> str:
        self.index_builds.append((namespace, "fts"))
        self.fts_indexes.add(namespace)
        return f"op-fts-{namespace}"

    async def build_vector_index(self, namespace: str, **kwargs: Any) -> str:
        self.index_builds.append((namespace, "vector"))
        self.vector_indexes.add(namespace)
        return f"op-vector-{namespace}"

    async def wait_for_operation(self, operation_id: str, **kwargs: Any) -> dict[str, Any]:
        return {"operation_id": operation_id, "status": "succeeded", "error": None}

    async def aclose(self) -> None:
        return


@pytest.fixture
def firn() -> FakeFirn:
    return FakeFirn()
