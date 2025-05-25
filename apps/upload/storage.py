import os
import numpy as np
import lancedb
import pyarrow as pa
from pathlib import Path

STORAGE_PATH = Path("./storage")
IMAGES_PATH = STORAGE_PATH / "images"
LANCE_PATH = STORAGE_PATH / "lance-data" / "images"

def save_image_to_local(filename, contents):
    os.makedirs(IMAGES_PATH, exist_ok=True)
    with open(IMAGES_PATH / filename, "wb") as f:
        f.write(contents)

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