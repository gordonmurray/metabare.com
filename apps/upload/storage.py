import logging
import os

import boto3
import lancedb
import numpy as np
import pyarrow as pa
from pathlib import Path

logger = logging.getLogger(__name__)

STORAGE_PATH = Path("./storage")
IMAGES_PATH = STORAGE_PATH / "images"
LANCE_PATH = STORAGE_PATH / "lance-data" / "images"

def save_image_to_local(filename, contents):
    os.makedirs(IMAGES_PATH, exist_ok=True)
    with open(IMAGES_PATH / filename, "wb") as f:
        f.write(contents)


def save_image_to_s3(filename, contents):
    """Upload the image directly to S3 so it is reachable via
    CloudFront the instant the /latest response references it. The
    minute-cron sync still runs for Lance data files.
    """
    bucket = os.environ.get("R2_BUCKET")
    if not bucket:
        logger.info("R2_BUCKET unset; skipping direct S3 upload")
        return

    kwargs = {}
    if endpoint := os.environ.get("R2_ENDPOINT"):
        kwargs["endpoint_url"] = endpoint
    if access_key := os.environ.get("R2_ACCESS_KEY_ID"):
        kwargs["aws_access_key_id"] = access_key
    if secret_key := os.environ.get("R2_SECRET_ACCESS_KEY"):
        kwargs["aws_secret_access_key"] = secret_key

    s3 = boto3.client("s3", **kwargs)
    s3.put_object(
        Bucket=bucket,
        Key=f"lance/images/{filename}",
        Body=contents,
        ContentType="image/jpeg",
        Tagging="retention=auto-expire",
    )
    logger.info("s3 put_object ok (%s)", filename)

def save_vector_to_lance(filename, vector):
    os.makedirs(LANCE_PATH.parent, exist_ok=True)

    # Create or open Lance dataset
    db = lancedb.connect(LANCE_PATH.parent)
    table_name = LANCE_PATH.name

    try:
        table = db.open_table(table_name)
    except ValueError:
        table = db.create_table(
            table_name,
            schema=pa.schema([
                ("id", pa.string()),
                ("path", pa.string()),
                ("vector", pa.list_(pa.float32(), 512)),
            ])
        )

    # Check for existing vector
    existing = table.to_arrow().filter(pa.compute.equal(table.to_arrow()['id'], filename))
    if existing:
        return

    # Add new vector
    table.add([{
        "id": filename,
        "path": str(IMAGES_PATH / filename),
        "vector": vector.tolist(),
    }])