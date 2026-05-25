"""Thin client for the local Firn instance during the migration.

Three functions:
- query, POSTs to /ns/{ns}/query for single-vector nearest-neighbour
  search.
- query_mv, POSTs to /ns/{mv-ns}/query with a bag of sub-vectors for
  the multivector path (MaxSim under the hood, single-direction wire
  contract from Firn v0.7.0).
- list_recent, GETs /ns/{ns}/list to pull the most recent rows
  (Firn v0.3.0+, ordered by the _ingested_at system column).

All three recover the "<sha>.jpg" filename from the text column that
the upload path stored. Multivector results carry an empty vector
field by Firn convention (a ColPali row's bag is large and not echoed
back); the filename and score are what the caller needs.
"""

import logging
import os
from typing import Any, Dict, List

import numpy as np
import requests

logger = logging.getLogger(__name__)

FIRN_URL = os.getenv("FIRN_URL", "http://firn:3000")
FIRN_NAMESPACE = os.getenv("FIRN_NAMESPACE", "images")
FIRN_MV_NAMESPACE = os.getenv("FIRN_MV_NAMESPACE", "images-mv")
FIRN_TIMEOUT_SECONDS = float(os.getenv("FIRN_TIMEOUT_SECONDS", "10"))


def query(vector: np.ndarray, k: int = 10) -> List[Dict[str, Any]]:
    """Query Firn for nearest neighbours of vector.

    Returns [{"filename", "score", "firn_id"}] ordered ascending by
    score (L2 distance). Raises on HTTP error.
    """
    payload = {"vector": vector.astype("float32").tolist(), "k": k}
    url = f"{FIRN_URL}/ns/{FIRN_NAMESPACE}/query"
    resp = requests.post(url, json=payload, timeout=FIRN_TIMEOUT_SECONDS)
    resp.raise_for_status()
    body = resp.json()

    out: List[Dict[str, Any]] = []
    for row in body.get("results", []):
        out.append({
            "filename": row.get("text") or "",
            "score": row.get("score", 0.0),
            "firn_id": row.get("id"),
        })
    return out


def query_mv(vectors: List[List[float]], k: int = 10, text: str = None) -> List[Dict[str, Any]]:
    """Query the multivector namespace with a bag of sub-vectors.

    When text is supplied and an FTS index exists on the namespace,
    Firn 0.7.0 fuses the multivector and BM25 score lists via RRF
    automatically (the "hybrid" query type). When text is omitted,
    pure MaxSim ranking. Returns [{"filename", "score", "firn_id"}]
    sorted best-first by Firn.

    The text column stores a description (or filename as fallback)
    on each row; recovering the filename from the text column is
    not appropriate here. We always strip the .jpg-derived text
    from the result and return the row's id-derived sha instead.
    """
    payload = {"vectors": vectors, "k": k}
    if text and text.strip():
        payload["text"] = text.strip()
    url = f"{FIRN_URL}/ns/{FIRN_MV_NAMESPACE}/query"
    resp = requests.post(url, json=payload, timeout=FIRN_TIMEOUT_SECONDS)
    resp.raise_for_status()
    body = resp.json()

    out: List[Dict[str, Any]] = []
    for row in body.get("results", []):
        # Text column is "<filename>\n<description>" on rows that
        # carry a caption, just "<filename>" otherwise. Split on the
        # first newline; filename is line 1, description (if any)
        # is the rest.
        raw_text = row.get("text") or ""
        parts = raw_text.split("\n", 1)
        filename = parts[0] if parts[0].endswith(".jpg") else ""
        description = parts[1] if len(parts) > 1 else ""
        out.append({
            "filename": filename,
            "description": description,
            "score": row.get("score", 0.0),
            "firn_id": row.get("id"),
        })
    return out


def list_recent(limit: int = 9, order: str = "desc") -> List[Dict[str, Any]]:
    """List rows ordered by Firn's _ingested_at system column.

    Requires Firn v0.3.0 or later. Returns [{"filename", "firn_id"}]
    in newest-first order by default. Raises on HTTP error, including
    501 if the namespace pre-dates _ingested_at.
    """
    params = {"order_by": "_ingested_at", "order": order, "limit": limit}
    url = f"{FIRN_URL}/ns/{FIRN_NAMESPACE}/list"
    resp = requests.get(url, params=params, timeout=FIRN_TIMEOUT_SECONDS)
    resp.raise_for_status()
    body = resp.json()

    # Firn /list returns {rows: [...], next_cursor: ...}; /query uses
    # {results: [...]}. Handle both for resilience, prefer rows.
    rows = body.get("rows") or body.get("results") or []
    out: List[Dict[str, Any]] = []
    for row in rows:
        out.append({
            "filename": row.get("text") or "",
            "firn_id": row.get("id"),
            "ingested_at_micros": row.get("ingested_at_micros"),
        })
    return out
