"""Multivector text encoder client.

Sibling to apps/upload/vectorize_mv.py. Posts the user's query text
to the encoder service and returns the bag of sub-vectors that the
multivector Firn namespace expects.
"""

import logging
import os

import requests

logger = logging.getLogger(__name__)

ENCODER_URL = os.getenv("ENCODER_URL", "http://encoder:8080")
# Query encoding is smaller than image encoding but still subject to ColPali
# inference time under load. 120s is comfortable headroom for an interactive
# query; we can tighten this on a roomier production host.
ENCODER_TIMEOUT_SECONDS = float(os.getenv("ENCODER_TIMEOUT_SECONDS", "120"))


def encode_query_mv(text: str) -> list[list[float]]:
    """Encode a text query into a bag of sub-vectors via the encoder service."""
    resp = requests.post(
        f"{ENCODER_URL}/encode-text",
        json={"text": text},
        timeout=ENCODER_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    body = resp.json()
    logger.info("encoder text bag: %s x %s", body.get("bag_size"), body.get("sub_dim"))
    return body["vectors"]
