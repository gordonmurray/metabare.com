from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import torch
from transformers import CLIPProcessor, CLIPModel
from dotenv import load_dotenv
import lancedb
import os
import logging

import firn_client

logging.basicConfig(level=logging.INFO)
load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://metabare.com",
        "https://www.metabare.com",
        "https://metabare-search.fly.dev",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# CLIP text encoder (same model as the upload path)
model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32")
processor = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")

# S3 / MinIO backend for the lance path
R2_ENDPOINT = os.getenv("R2_ENDPOINT")
R2_BUCKET = os.getenv("R2_BUCKET")
R2_ACCESS = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET = os.getenv("R2_SECRET_ACCESS_KEY")
BASE_IMAGE_URL = os.getenv("BASE_IMAGE_URL", "https://metabare.com/")
SEARCH_BACKEND = os.getenv("SEARCH_BACKEND", "lance").lower()


def _encode_query(text: str):
    with torch.no_grad():
        inputs = processor(
            text=[text], return_tensors="pt", padding=True, truncation=True
        )
        vec = model.get_text_features(**inputs).squeeze()
    return (vec / vec.norm()).cpu().numpy().astype("float32")


def _open_lance_table():
    opts = {
        "aws_access_key_id": R2_ACCESS,
        "aws_secret_access_key": R2_SECRET,
        "region": os.getenv("R2_REGION", "us-east-1"),
        "endpoint": R2_ENDPOINT,
    }
    # lancedb-rs refuses plain HTTP endpoints unless allow_http is set;
    # R2 is always https, MinIO in local dev is http.
    if (R2_ENDPOINT or "").startswith("http://"):
        opts["allow_http"] = "true"
    db = lancedb.connect(f"s3://{R2_BUCKET}/lance/lance-data/", storage_options=opts)
    try:
        return db.open_table("images")
    except Exception as e:
        raise HTTPException(404, detail=f"Lance table not found: {e}")


def _lance_search(text_vec, k):
    tbl = _open_lance_table()
    try:
        rows = tbl.search(text_vec).limit(max(k * 3, 10)).to_arrow().to_pylist()
    except Exception as e:
        raise HTTPException(500, detail=f"Lance search failed: {e}")

    seen = set()
    results = []
    for r in rows:
        r.pop("vector", None)
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        results.append({
            "filename": r["id"],
            "url": f"{BASE_IMAGE_URL}lance/images/{r['id']}",
            "score": r.get("_distance"),
        })
        if len(results) >= k:
            break
    return results


def _firn_search(text_vec, k):
    try:
        rows = firn_client.query(text_vec, k=max(k * 3, 10))
    except Exception as e:
        raise HTTPException(500, detail=f"Firn query failed: {e}")

    seen = set()
    results = []
    for r in rows:
        name = r["filename"]
        if not name or name in seen:
            continue
        seen.add(name)
        results.append({
            "filename": name,
            "url": f"{BASE_IMAGE_URL}lance/images/{name}",
            "score": r["score"],
        })
        if len(results) >= k:
            break
    return results


@app.get("/health")
async def health():
    return {"status": "healthy", "backend": SEARCH_BACKEND}


@app.get("/search")
async def search_images(
    text: str = Query(..., description="Text to search for"),
    backend: str = Query(None, description="Override: 'lance' or 'firn'"),
    k: int = Query(3, ge=1, le=50),
):
    if not text.strip():
        raise HTTPException(400, "Query cannot be empty")

    active = (backend or SEARCH_BACKEND).lower()
    if active not in {"lance", "firn"}:
        raise HTTPException(400, f"Unknown backend: {active}")

    text_vec = _encode_query(text)
    logging.info("query=%r backend=%s k=%s", text, active, k)

    results = _firn_search(text_vec, k) if active == "firn" else _lance_search(text_vec, k)
    return {"results": results, "backend": active}


@app.get("/latest")
async def latest_images():
    # Firn v0.3.0 added a cursor-paginated /list endpoint ordered
    # by _ingested_at, so /latest goes through Firn directly. The
    # recent.json manifest stopgap in the original plan is skipped.
    try:
        rows = firn_client.list_recent(limit=9, order="desc")
    except Exception as e:
        raise HTTPException(500, detail=f"Firn list failed: {e}")

    results = [
        {"filename": r["filename"], "url": f"{BASE_IMAGE_URL}lance/images/{r['filename']}"}
        for r in rows
        if r["filename"]
    ]
    return {"results": results}
