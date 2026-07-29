"""Configuration, resolved from the environment.

One settings object per concern, so a service only fails to start on
configuration it actually uses. The API does not need SQS; the worker does not
need the search namespace list.

Namespace names are configuration rather than constants because a comparison
against another search engine needs the same corpus indexed twice, and a
benchmark run needs to point at throwaway namespaces without a code change.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


class StorageSettings(BaseSettings):
    """Object storage. S3 in every environment; MinIO is S3 with an endpoint."""

    model_config = _ENV | SettingsConfigDict(env_prefix="METABARE_")

    bucket: str = Field(default="metabare-dev", description="Data bucket")
    region: str = Field(default="eu-west-1")
    s3_endpoint_url: str = Field(
        default="",
        description="Set for MinIO. Empty means real S3 via the default AWS chain",
    )
    # Only ever set for local MinIO. On EKS, credentials come from Pod Identity
    # or IRSA and these stay empty. No static AWS credentials anywhere,
    # including Kubernetes Secrets.
    s3_access_key_id: str = Field(default="")
    s3_secret_access_key: str = Field(default="")

    @property
    def use_path_style(self) -> bool:
        """MinIO needs path-style addressing; real S3 does not."""
        return bool(self.s3_endpoint_url)


class FirnSettings(BaseSettings):
    """The Firn service and the namespaces MetaBare uses.

    Three namespaces, because a Firn namespace holds exactly one vector field
    of one kind and dimension, fixed by its first write.
    """

    model_config = _ENV | SettingsConfigDict(env_prefix="FIRN_")

    url: str = Field(default="http://firn:3000")
    api_key: str = Field(default="", description="Bearer token, read/write scope")
    admin_api_key: str = Field(default="", description="Bearer token, admin scope")
    timeout_seconds: float = Field(default=10.0, gt=0)
    connect_timeout_seconds: float = Field(default=3.0, gt=0)

    notes_text_namespace: str = Field(default="notes-text")
    screenshots_text_namespace: str = Field(default="screenshots-text")
    screenshots_image_namespace: str = Field(default="screenshots-image")

    @field_validator("url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @property
    def text_namespaces(self) -> tuple[str, ...]:
        """Namespaces searchable with a dedicated text embedding."""
        return (self.notes_text_namespace, self.screenshots_text_namespace)


class EncoderSettings(BaseSettings):
    """The CPU text encoder.

    A hard architectural constraint: query encoding runs on stable CPU
    capacity so search stays usable while no GPU exists.
    """

    model_config = _ENV | SettingsConfigDict(env_prefix="ENCODER_")

    model_id: str = Field(
        default="intfloat/e5-small-v2",
        description="Hugging Face repo id. Chosen by the evaluation in benchmarks/runners/",
    )
    model_revision: str = Field(
        default="ffb93f3bd4047442299a41ebb6fa998a38507c52",
        description=(
            "Pinned to a commit rather than a branch. A branch would let two builds of "
            "the same source commit bake different model bytes."
        ),
    )
    # Candidates disagree on where the export lives: BAAI and
    # sentence-transformers publish onnx/model.onnx, intfloat publishes
    # model.onnx at the repo root. Configurable because hardcoding one layout
    # turns "different path" into "model does not support ONNX".
    onnx_file: str = Field(default="model.onnx")
    pooling: str = Field(
        default="mean",
        description="'cls' or 'mean'. A property of the model, not a preference",
    )
    dimension: int = Field(default=384, gt=0)
    max_sequence_length: int = Field(default=512, gt=0)
    cache_dir: str = Field(default="/var/cache/metabare/models")
    # E5 is trained asymmetrically. Omitting these, or applying the query
    # prefix to passages, degrades retrieval while producing embeddings that
    # look entirely normal.
    query_prefix: str = Field(default="query: ")
    passage_prefix: str = Field(default="passage: ")
    intra_op_threads: int = Field(
        default=1,
        ge=0,
        description="ONNX Runtime threads. 1 keeps CPU requests small and predictable",
    )

    @property
    def model_version(self) -> str:
        """Stable identifier recorded in every item record."""
        return f"{self.model_id}@{self.model_revision}"


class QueueSettings(BaseSettings):
    """SQS queues driving ingestion."""

    model_config = _ENV | SettingsConfigDict(env_prefix="METABARE_")

    cpu_queue_url: str = Field(default="")
    gpu_queue_url: str = Field(default="")
    region: str = Field(default="eu-west-1")
    sqs_endpoint_url: str = Field(default="", description="Set for local emulation")
    max_messages: int = Field(default=10, ge=1, le=10)
    wait_time_seconds: int = Field(default=20, ge=0, le=20)
    visibility_timeout_seconds: int = Field(
        default=300,
        ge=1,
        description="Must exceed normal processing time",
    )
    visibility_extension_seconds: int = Field(default=120, ge=1)


class ServiceSettings(BaseSettings):
    """Cross-cutting service settings."""

    model_config = _ENV | SettingsConfigDict(env_prefix="METABARE_")

    environment: str = Field(default="local")
    log_level: str = Field(default="INFO")
    log_format: str = Field(default="json", description="'json' or 'console'")
    host: str = Field(default="0.0.0.0")  # noqa: S104 - container-local bind
    port: int = Field(default=8080)
    shutdown_grace_seconds: float = Field(
        default=30.0,
        gt=0,
        description="Time allowed to finish in-flight work before exit",
    )


@lru_cache(maxsize=1)
def storage_settings() -> StorageSettings:
    return StorageSettings()


@lru_cache(maxsize=1)
def firn_settings() -> FirnSettings:
    return FirnSettings()


@lru_cache(maxsize=1)
def encoder_settings() -> EncoderSettings:
    return EncoderSettings()


@lru_cache(maxsize=1)
def queue_settings() -> QueueSettings:
    return QueueSettings()


@lru_cache(maxsize=1)
def service_settings() -> ServiceSettings:
    return ServiceSettings()


def reset_settings_cache() -> None:
    """Clear cached settings. Tests use this after changing the environment."""
    for fn in (
        storage_settings,
        firn_settings,
        encoder_settings,
        queue_settings,
        service_settings,
    ):
        fn.cache_clear()
