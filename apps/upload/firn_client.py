"""Thin client for the local Firn instance during the migration.

Two upsert functions:
- upsert posts a single-vector row into the single-vector namespace
  (Firn's original behaviour, what the existing /upload handler uses).
- upsert_mv posts a multivector row (a bag of sub-vectors) into the
  multivector namespace, fixed at first upsert per the v0.7.0 wire
  contract.

Shared conventions: u64 id derived from the first 8 bytes of the
SHA-256 digest; the full "<sha>.jpg" filename rides in the text
column so the filename can be recovered on query.

Caller policy: no retries here. Raises on non-2xx so /upload and
/upload-mv surface HTTP 500 while the dual-write is in force.
"""

import logging
import os

import numpy as np
import requests

logger = logging.getLogger(__name__)

FIRN_URL = os.getenv("FIRN_URL", "http://firn:3000")
FIRN_NAMESPACE = os.getenv("FIRN_NAMESPACE", "images")
FIRN_MV_NAMESPACE = os.getenv("FIRN_MV_NAMESPACE", "images-mv")
FIRN_TIMEOUT_SECONDS = float(os.getenv("FIRN_TIMEOUT_SECONDS", "10"))


def sha256_to_u64(sha_hex: str) -> int:
    """Stable u64 from the first 8 bytes of a SHA-256 digest."""
    digest = bytes.fromhex(sha_hex)
    return int.from_bytes(digest[:8], "little")


def upsert(filename: str, vector: np.ndarray) -> None:
    """Upsert one single-vector row into Firn.

    filename is "<sha256-hex>.jpg". Raises on HTTP error.
    """
    sha_hex = filename[:-4] if filename.endswith(".jpg") else filename
    row = {
        "id": sha256_to_u64(sha_hex),
        "vector": vector.astype("float32").tolist(),
        "text": filename,
    }
    url = f"{FIRN_URL}/ns/{FIRN_NAMESPACE}/upsert"
    resp = requests.post(url, json={"rows": [row]}, timeout=FIRN_TIMEOUT_SECONDS)
    resp.raise_for_status()
    logger.info("firn upsert ok (%s)", filename)


def upsert_mv(filename: str, vectors: list[list[float]], text: str = None) -> None:
    """Upsert one multivector row into the multivector namespace.

    vectors is a bag of sub-vectors with a fixed inner dimension.
    Firn 400s on empty bags or mixed inner dims; the namespace's
    kind and sub-dim are pinned at first upsert.

    text defaults to the filename so existing call sites continue
    to round-trip the filename on query. Callers that have richer
    indexable text (caption, OCR, page metadata) should pass it
    so the FTS index has something useful to score against and
    /search-mv can fuse multivector + BM25 via Firn's RRF.
    """
    sha_hex = filename[:-4] if filename.endswith(".jpg") else filename
    row = {
        "id": sha256_to_u64(sha_hex),
        "vectors": vectors,
        "text": text if text else filename,
    }
    url = f"{FIRN_URL}/ns/{FIRN_MV_NAMESPACE}/upsert"
    resp = requests.post(url, json={"rows": [row]}, timeout=FIRN_TIMEOUT_SECONDS)
    resp.raise_for_status()
    logger.info("firn upsert_mv ok (%s)", filename)
