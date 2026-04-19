"""Thin client for the local Firn instance during the migration.

Two functions:
- query, POSTs to /ns/{ns}/query for nearest-neighbour search.
- list_recent, GETs /ns/{ns}/list to pull the most recent rows
  (Firn v0.3.0+, ordered by the _ingested_at system column).

Both recover the "<sha>.jpg" filename from the text column that the
upload path stored.
"""

import logging
import os
from typing import Any, Dict, List

import numpy as np
import requests

logger = logging.getLogger(__name__)

FIRN_URL = os.getenv("FIRN_URL", "http://firn:3000")
FIRN_NAMESPACE = os.getenv("FIRN_NAMESPACE", "images")
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
