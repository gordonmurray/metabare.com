from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from vectorize import vectorize_image
from storage import save_image_to_local, save_vector_to_lance
import hashlib
import os
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://metabare.com",
        "https://www.metabare.com",
        "https://metabare-frontend.fly.dev"
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"]
)

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    if not file.content_type.startswith('image/'):
        raise HTTPException(status_code=400, detail="File must be an image")

    contents = await file.read()
    sha256_hash = hashlib.sha256(contents).hexdigest()
    filename = f"{sha256_hash}.jpg"

    try:
        logger.info(f"Processing image: {filename}")
        save_image_to_local(filename, contents)
        vector = vectorize_image(contents)
        save_vector_to_lance(filename, vector)
        logger.info(f"Successfully processed image: {filename}")
    except Exception as e:
        logger.error(f"Error processing image {filename}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

    return {"filename": filename, "vector_saved": True}