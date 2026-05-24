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


def query_mv(vectors: List[List[float]], k: int = 10) -> List[Dict[str, Any]]:
    """Query the multivector namespace with a bag of sub-vectors.

    Returns [{"filename", "score", "firn_id"}]. Score semantics
    follow the multivector index (cosine MaxSim) and are sorted
    best-first by Firn.
    """
    payload = {"vectors": vectors, "k": k}
    url = f"{FIRN_URL}/ns/{FIRN_MV_NAMESPACE}/query"
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
