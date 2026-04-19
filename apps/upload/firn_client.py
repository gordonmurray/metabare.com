"""Thin client for the local Firn instance during the migration.

One function, upsert, posts a single row into Firn. The u64 id is
derived from the first 8 bytes of the SHA-256 digest; the full
"<sha>.jpg" filename rides in the text column so the filename can
be recovered on query.

Caller policy: no retries here. Raises on non-2xx so the /upload
handler surfaces HTTP 500 while the dual-write is in force.
"""

import logging
import os

import numpy as np
import requests

logger = logging.getLogger(__name__)

FIRN_URL = os.getenv("FIRN_URL", "http://firn:3000")
FIRN_NAMESPACE = os.getenv("FIRN_NAMESPACE", "images")
FIRN_TIMEOUT_SECONDS = float(os.getenv("FIRN_TIMEOUT_SECONDS", "10"))


def sha256_to_u64(sha_hex: str) -> int:
    """Stable u64 from the first 8 bytes of a SHA-256 digest."""
    digest = bytes.fromhex(sha_hex)
    return int.from_bytes(digest[:8], "little")


def upsert(filename: str, vector: np.ndarray) -> None:
    """Upsert one row into Firn.

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
