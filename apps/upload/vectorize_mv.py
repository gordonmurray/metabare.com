"""Multivector image encoder for the demo path.

The production target is a ColPali-style late-interaction encoder
that emits a bag of patch-token vectors per image. That model is not
wired up yet; this module is a stub that reuses the existing CLIP
ViT-B/32 image features and reshapes the 512-dim pooled vector into
a bag of (MV_BAG_SIZE x MV_SUB_DIM) sub-vectors. The defaults
(16 x 32) split the CLIP vector losslessly.

Why not zeros: the wire contract is exercised either way, but a
CLIP-derived bag also returns semantically coherent results, so the
side-by-side frontend can show the multivector path picking from
the same image set rather than uniform noise. Search quality will
not exceed the single-vector path until the real ColPali producer
lands.

Drop-in replacement: re-implement vectorize_image_mv to call the
ColPali processor and return its patch tokens. No call-site
changes are required as long as the return shape stays
list[list[float]] with fixed inner dimension within a namespace.
"""

import io
import os

from PIL import Image
import torch

from vectorize import model, processor

MV_BAG_SIZE = int(os.getenv("MV_BAG_SIZE", "16"))
MV_SUB_DIM = int(os.getenv("MV_SUB_DIM", "32"))
CLIP_DIM = MV_BAG_SIZE * MV_SUB_DIM

if CLIP_DIM != 512:
    raise RuntimeError(
        f"MV_BAG_SIZE * MV_SUB_DIM must equal 512 for the CLIP-reshape stub "
        f"(got {MV_BAG_SIZE} * {MV_SUB_DIM} = {CLIP_DIM}). The real ColPali "
        f"encoder will lift this constraint."
    )


def vectorize_image_mv(image_bytes: bytes) -> list[list[float]]:
    """Encode an image into a bag of sub-vectors."""
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    inputs = processor(images=image, return_tensors="pt")
    with torch.no_grad():
        pooled = model.get_image_features(**inputs).squeeze()
    pooled = pooled / pooled.norm()
    bag = pooled.cpu().numpy().astype("float32").reshape(MV_BAG_SIZE, MV_SUB_DIM)
    return [sub.tolist() for sub in bag]
