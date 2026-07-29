"""CPU text embedding via ONNX Runtime.

This is load-bearing: query encoding must run on stable CPU capacity so
search stays usable while no GPU exists. That rules out waking a
GPU to embed a short query, and it argues strongly for ONNX Runtime over
PyTorch. A torch-based image is roughly 2 GB and pulls CUDA wheels it will
never use on a CPU node; onnxruntime plus tokenizers is tens of megabytes,
starts in well under a second, and has no GPU dependency to accidentally
inherit. Container image size is not a vanity metric here: it is pull time,
and pull time is a large part of how long a cold start takes.

Pooling is configurable because it is a property of the model, not a
preference. BGE pools the CLS token; E5 and MiniLM mean-pool over the
attention mask. Getting this wrong produces embeddings that are plausible,
normalised, and quietly much worse at retrieval, which is exactly the kind of
failure that is hard to notice without an evaluation.
"""

from __future__ import annotations

import asyncio
import threading
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import numpy.typing as npt

from .config import EncoderSettings, encoder_settings

if TYPE_CHECKING:  # pragma: no cover - typing only
    import onnxruntime as ort
    from tokenizers import Tokenizer

FloatArray = npt.NDArray[np.float32]


class Pooling(StrEnum):
    """How token embeddings collapse to one vector."""

    CLS = "cls"
    MEAN = "mean"


class EncoderError(RuntimeError):
    """The encoder could not be prepared or run."""


def _resolve_model_files(settings: EncoderSettings) -> tuple[Path, Path]:
    """Download (or reuse cached) ONNX weights and tokenizer.

    Returns (onnx_path, tokenizer_path). Network access happens here and
    nowhere else, so an air-gapped or pre-baked image only needs the cache
    directory populated.
    """
    from huggingface_hub import hf_hub_download

    cache_dir = Path(settings.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        onnx_path = hf_hub_download(
            repo_id=settings.model_id,
            filename=settings.onnx_file,
            revision=settings.model_revision,
            cache_dir=str(cache_dir),
        )
        tokenizer_path = hf_hub_download(
            repo_id=settings.model_id,
            filename="tokenizer.json",
            revision=settings.model_revision,
            cache_dir=str(cache_dir),
        )
    except Exception as exc:
        raise EncoderError(
            f"could not fetch {settings.model_id}@{settings.model_revision}: {exc}"
        ) from exc
    return Path(onnx_path), Path(tokenizer_path)


class TextEncoder:
    """A loaded ONNX text embedding model.

    Thread-safe for concurrent :meth:`encode` calls: ONNX Runtime sessions are
    safe to call from multiple threads, and the tokenizer is used under a lock
    because ``tokenizers`` encoders carry mutable truncation and padding state.
    """

    def __init__(
        self,
        settings: EncoderSettings | None = None,
        *,
        pooling: Pooling | None = None,
    ) -> None:
        self._settings = settings or encoder_settings()
        # Pooling comes from configuration by default because it is a property
        # of the chosen model. The override exists for the model evaluation,
        # which deliberately runs several models with different pooling.
        self._pooling = pooling or Pooling(self._settings.pooling)
        self._session: ort.InferenceSession | None = None
        self._tokenizer: Tokenizer | None = None
        self._input_names: frozenset[str] = frozenset()
        self._tokenizer_lock = threading.Lock()
        self._load_lock = threading.Lock()

    @property
    def dimension(self) -> int:
        return self._settings.dimension

    @property
    def model_version(self) -> str:
        return self._settings.model_version

    def _is_loaded(self) -> bool:
        return self._session is not None

    def load(self) -> None:
        """Load the model. Idempotent, and safe to call from several threads.

        Double-checked locking: the fast path avoids taking the lock on every
        call, and the second check inside the lock is what makes it correct
        when two threads race past the first.
        """
        if self._is_loaded():
            return
        with self._load_lock:
            if self._is_loaded():
                return
            import onnxruntime as ort
            from tokenizers import Tokenizer

            onnx_path, tokenizer_path = _resolve_model_files(self._settings)

            options = ort.SessionOptions()
            # Single-threaded by default. A query encoder sized at a fraction
            # of a core keeps the stable node small, and oversubscribing
            # threads inside a container with a CPU limit costs more in
            # scheduler contention than it wins in latency.
            options.intra_op_num_threads = self._settings.intra_op_threads
            options.inter_op_num_threads = 1
            options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

            session = ort.InferenceSession(
                str(onnx_path), sess_options=options, providers=["CPUExecutionProvider"]
            )
            tokenizer = Tokenizer.from_file(str(tokenizer_path))
            tokenizer.enable_truncation(max_length=self._settings.max_sequence_length)
            tokenizer.enable_padding()

            self._input_names = frozenset(i.name for i in session.get_inputs())
            self._tokenizer = tokenizer
            self._session = session

    def _tokenize(self, texts: list[str]) -> dict[str, npt.NDArray[np.int64]]:
        tokenizer = self._tokenizer
        if tokenizer is None:
            raise EncoderError("tokenizer not loaded; call load() first")
        with self._tokenizer_lock:
            encodings = tokenizer.encode_batch(texts)
        input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
        feeds: dict[str, npt.NDArray[np.int64]] = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
        }
        # Not every export takes token_type_ids; supplying an input the graph
        # does not declare is an error, so match the session's actual inputs.
        if "token_type_ids" in self._input_names:
            feeds["token_type_ids"] = np.zeros_like(input_ids)
        return {name: value for name, value in feeds.items() if name in self._input_names}

    def _pool(self, hidden: FloatArray, attention_mask: npt.NDArray[np.int64]) -> FloatArray:
        if self._pooling is Pooling.CLS:
            pooled = hidden[:, 0, :]
        else:
            mask = attention_mask[..., None].astype(np.float32)
            summed = (hidden * mask).sum(axis=1)
            # Guard against an all-padding row producing a divide by zero.
            counts = np.clip(mask.sum(axis=1), 1e-9, None)
            pooled = summed / counts
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        return (pooled / np.clip(norms, 1e-12, None)).astype(np.float32)

    def encode(self, texts: list[str]) -> FloatArray:
        """Encode texts to L2-normalised vectors, one row per input text."""
        if not texts:
            return np.zeros((0, self.dimension), dtype=np.float32)
        self.load()
        session = self._session
        if session is None:  # pragma: no cover - load() raises before this
            raise EncoderError("model not loaded")
        feeds = self._tokenize(texts)
        outputs = session.run(None, dict(feeds))
        hidden = np.asarray(outputs[0], dtype=np.float32)
        if hidden.ndim != 3:
            raise EncoderError(
                f"expected token embeddings with dimensions (batch, seq, dim), got {hidden.shape}"
            )
        vectors = self._pool(hidden, feeds["attention_mask"])
        if vectors.shape[1] != self.dimension:
            raise EncoderError(
                f"model produced dimension {vectors.shape[1]}, "
                f"configuration declares {self.dimension}"
            )
        return vectors

    def encode_query(self, query: str) -> FloatArray:
        """Encode a search query, applying the model's asymmetric prefix.

        BGE and E5 are trained with different instructions for queries and
        passages. Omitting the query prefix measurably degrades retrieval, and
        applying it to indexed passages degrades it too, so the two paths are
        separate methods rather than one method with a flag callers forget.
        """
        return np.asarray(self.encode([self._settings.query_prefix + query])[0], dtype=np.float32)

    def encode_passages(self, passages: list[str]) -> FloatArray:
        """Encode text destined for the index, with the passage-side prefix."""
        prefix = self._settings.passage_prefix
        return self.encode([prefix + passage for passage in passages] if prefix else passages)

    async def encode_query_async(self, query: str) -> FloatArray:
        """Encode off the event loop, so one query cannot stall the server.

        Overridable so a test double can answer without a thread. ONNX
        inference genuinely blocks and belongs on a worker; a fake that
        returns a seeded vector does not.
        """
        return await asyncio.to_thread(self.encode_query, query)

    async def encode_passages_async(self, passages: list[str]) -> FloatArray:
        return await asyncio.to_thread(self.encode_passages, passages)


_encoder: TextEncoder | None = None
_encoder_lock = threading.Lock()


def get_encoder(pooling: Pooling = Pooling.CLS) -> TextEncoder:
    """Return the process-wide encoder. Loading a model twice wastes memory."""
    global _encoder
    if _encoder is None:
        with _encoder_lock:
            if _encoder is None:
                _encoder = TextEncoder(pooling=pooling)
    return _encoder
