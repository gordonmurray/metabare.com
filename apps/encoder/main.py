"""Multivector encoder service.

Owns a single ColPali model instance and exposes two endpoints used
by the upload and search apps. Keeping the model in one process
lets the rest of the stack stay on the lighter CLIP-era footprint
and keeps total resident RAM under the host's headroom.

Endpoints:
    POST /encode-image  multipart file → {"vectors": [[f32, ...], ...]}
    POST /encode-text   JSON {"text": "..."} → {"vectors": [[f32, ...], ...]}
    GET  /health        readiness probe; 503 until the model finishes loading
"""

import io
import logging
import os
import time
from typing import List

import torch
from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel
from PIL import Image

from colpali_engine.models import ColPali, ColPaliProcessor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MODEL_NAME = os.getenv("ENCODER_MODEL", "vidore/colpali-v1.3")
DEVICE = os.getenv("ENCODER_DEVICE", "cpu")
DTYPE_NAME = os.getenv("ENCODER_DTYPE", "bfloat16")
DTYPE = {
    "bfloat16": torch.bfloat16,
    "float16": torch.float16,
    "float32": torch.float32,
}[DTYPE_NAME]

app = FastAPI()
_model = None
_processor = None
_ready = False


@app.on_event("startup")
def load_model():
    global _model, _processor, _ready
    started = time.time()
    logger.info("loading %s on %s (%s)", MODEL_NAME, DEVICE, DTYPE_NAME)
    _model = ColPali.from_pretrained(MODEL_NAME, torch_dtype=DTYPE, device_map=DEVICE).eval()
    _processor = ColPaliProcessor.from_pretrained(MODEL_NAME)
    _ready = True
    logger.info("model loaded in %.1fs", time.time() - started)


@app.get("/health")
def health():
    if not _ready:
        raise HTTPException(503, "model not loaded yet")
    return {"status": "healthy", "model": MODEL_NAME, "dtype": DTYPE_NAME}


def _bag_from_tensor(tensor: torch.Tensor) -> List[List[float]]:
    """Squeeze a (1, seq, dim) tensor into a list[list[float]]."""
    return tensor.squeeze(0).to(torch.float32).cpu().numpy().tolist()


@app.post("/encode-image")
async def encode_image(file: UploadFile = File(...)):
    if not _ready:
        raise HTTPException(503, "model not loaded yet")
    if not file.content_type.startswith("image/"):
        raise HTTPException(400, "File must be an image")

    contents = await file.read()
    image = Image.open(io.BytesIO(contents)).convert("RGB")
    batch = _processor.process_images([image]).to(_model.device)
    with torch.no_grad():
        embedding = _model(**batch)
    bag = _bag_from_tensor(embedding)
    return {"vectors": bag, "bag_size": len(bag), "sub_dim": len(bag[0])}


class EncodeTextRequest(BaseModel):
    text: str


@app.post("/encode-text")
async def encode_text(req: EncodeTextRequest):
    if not _ready:
        raise HTTPException(503, "model not loaded yet")
    if not req.text.strip():
        raise HTTPException(400, "Query cannot be empty")

    batch = _processor.process_queries([req.text]).to(_model.device)
    with torch.no_grad():
        embedding = _model(**batch)
    bag = _bag_from_tensor(embedding)
    return {"vectors": bag, "bag_size": len(bag), "sub_dim": len(bag[0])}
