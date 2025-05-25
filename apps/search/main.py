from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import torch
from transformers import CLIPProcessor, CLIPModel
from dotenv import load_dotenv
import lancedb
import os
import logging

# Setup
logging.basicConfig(level=logging.INFO)
load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://metabare.com",
        "https://www.metabare.com",
        "https://metabare-search.fly.dev"
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"]
)

# Load CLIP model
model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32")
processor = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")

# Environment config
R2_ENDPOINT = os.getenv("R2_ENDPOINT")
R2_BUCKET = os.getenv("R2_BUCKET")
R2_ACCESS = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET = os.getenv("R2_SECRET_ACCESS_KEY")
BASE_IMAGE_URL = os.getenv("BASE_IMAGE_URL", "https://metabare.com/")

@app.get("/search")
async def search_images(text: str = Query(..., description="Text to search for")):
    if not text.strip():
        raise HTTPException(400, "Query cannot be empty")

    # Vectorize the text
    with torch.no_grad():
        inputs = processor(text=[text], return_tensors="pt", padding=True, truncation=True)
        text_vec = model.get_text_features(**inputs).squeeze()
    text_vec = (text_vec / text_vec.norm()).cpu().numpy().astype("float32")

    logging.info(f"Query: '{text}'")
    logging.info(f"Vector (first 5 dims): {text_vec[:5]}")

    # Connect to LanceDB
    db = lancedb.connect(
        f"s3://{R2_BUCKET}/lance/lance-data/",
        storage_options={
            "aws_access_key_id": R2_ACCESS,
            "aws_secret_access_key": R2_SECRET,
            "region": "auto",
            "endpoint": R2_ENDPOINT,
        },
    )

    try:
        tbl = db.open_table("images")
    except Exception as e:
        raise HTTPException(404, detail=f"Lance table not found: {e}")

    try:
        raw_results = tbl.search(text_vec).limit(10).to_arrow().to_pylist()

        seen = set()
        results = []
        for r in raw_results:
            r.pop("vector", None)
            if r["id"] not in seen:
                seen.add(r["id"])
                results.append({
                    "filename": r["id"],
                    "url": f"{BASE_IMAGE_URL}lance/images/{r['id']}",
                    **r
                })
            if len(results) >= 3:
                break

        logging.info(f"Top 3 result IDs: {[r['id'] for r in results]}")

    except Exception as e:
        raise HTTPException(500, detail=f"Vector search failed: {e}")

    return {
        "results": results
    }
