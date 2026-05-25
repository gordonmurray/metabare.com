"""Multivector image encoder client.

Delegates to the encoder service (apps/encoder). The model lives in
one process so the upload container stays light. A bag is a
list[list[float]] of variable outer length but fixed inner dim;
Firn's multivector wire shape accepts that directly.
"""

import io
import logging
import os

import requests

logger = logging.getLogger(__name__)

ENCODER_URL = os.getenv("ENCODER_URL", "http://encoder:8080")
# ColPali on CPU under memory pressure can take well over a minute per image
# (swap I/O dominates the inference). 5 minutes leaves headroom; we can tighten
# this once the encoder is on a roomier host.
ENCODER_TIMEOUT_SECONDS = float(os.getenv("ENCODER_TIMEOUT_SECONDS", "300"))


def vectorize_image_mv(image_bytes: bytes) -> list[list[float]]:
    """Encode an image into a bag of sub-vectors via the encoder service."""
    files = {"file": ("image.jpg", io.BytesIO(image_bytes), "image/jpeg")}
    resp = requests.post(
        f"{ENCODER_URL}/encode-image",
        files=files,
        timeout=ENCODER_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    body = resp.json()
    logger.info("encoder image bag: %s x %s", body.get("bag_size"), body.get("sub_dim"))
    return body["vectors"]
