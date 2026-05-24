"""Multivector text encoder for the demo path.

Sibling to apps/upload/vectorize_mv.py. Reuses the CLIP text
encoder already loaded by main.py and reshapes the 512-dim pooled
text vector into a bag of (MV_BAG_SIZE x MV_SUB_DIM) sub-vectors
so the multivector query path can be exercised end-to-end without
a real ColPali producer. See the upload-side module's docstring
for the longer rationale and the drop-in path for ColPali.
"""

import os

import numpy as np
import torch

MV_BAG_SIZE = int(os.getenv("MV_BAG_SIZE", "16"))
MV_SUB_DIM = int(os.getenv("MV_SUB_DIM", "32"))
CLIP_DIM = MV_BAG_SIZE * MV_SUB_DIM

if CLIP_DIM != 512:
    raise RuntimeError(
        f"MV_BAG_SIZE * MV_SUB_DIM must equal 512 for the CLIP-reshape stub "
        f"(got {MV_BAG_SIZE} * {MV_SUB_DIM} = {CLIP_DIM}). The real ColPali "
        f"encoder will lift this constraint."
    )


def encode_query_mv(text: str, model, processor) -> list[list[float]]:
    """Encode a text query into a bag of sub-vectors.

    Takes the CLIP model and processor from the caller so the search
    app keeps a single shared model instance instead of loading a
    second copy.
    """
    with torch.no_grad():
        inputs = processor(text=[text], return_tensors="pt", padding=True, truncation=True)
        pooled = model.get_text_features(**inputs).squeeze()
    pooled = pooled / pooled.norm()
    bag = pooled.cpu().numpy().astype("float32").reshape(MV_BAG_SIZE, MV_SUB_DIM)
    return [sub.tolist() for sub in bag]
