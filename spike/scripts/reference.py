"""Reference embeddings for the spike fixtures, from the original PyTorch weights.

The browser runs ONNX exports of these models. Comparing its vectors with
these shows how much the export, the precision and the browser's image
preprocessing move the result.

Run with:
  uv run --no-project --index https://download.pytorch.org/whl/cpu \
    --index-strategy unsafe-best-match \
    --with torch==2.14.1 --with transformers==5.19.0 \
    --with sentence-transformers==6.1.0 --with pillow==12.3.0 \
    python scripts/reference.py
"""

from __future__ import annotations

import json
import platform
from pathlib import Path

import PIL
import sentence_transformers
import torch
import transformers
from PIL import Image
from sentence_transformers import SentenceTransformer
from transformers import CLIPModel, CLIPProcessor

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "public" / "fixtures"
OUT = ROOT / "results" / "reference.json"

CLIP = ("openai/clip-vit-base-patch32", "3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268")
MINILM = ("sentence-transformers/all-MiniLM-L6-v2", "1110a243fdf4706b3f48f1d95db1a4f5529b4d41")


def unit(t: torch.Tensor) -> list[list[float]]:
    return torch.nn.functional.normalize(t, dim=-1).tolist()


def main() -> None:
    fx = json.loads((FIXTURES / "fixtures.json").read_text())
    torch.manual_seed(0)

    model = CLIPModel.from_pretrained(CLIP[0], revision=CLIP[1]).eval()
    proc = CLIPProcessor.from_pretrained(CLIP[0], revision=CLIP[1])
    images = [Image.open(FIXTURES / i["file"]).convert("RGB") for i in fx["images"]]
    queries = [q["text"] for q in fx["image_queries"]]
    with torch.no_grad():
        # CLIP's embedding is the pooled output through its projection layer.
        # Spelled out because get_image_features changed its return type in
        # transformers 5.
        pix = proc(images=images, return_tensors="pt")
        tok = proc(text=queries, return_tensors="pt", padding=True)
        img = model.visual_projection(
            model.vision_model(pixel_values=pix["pixel_values"]).pooler_output
        )
        txt = model.text_projection(
            model.text_model(
                input_ids=tok["input_ids"], attention_mask=tok["attention_mask"]
            ).pooler_output
        )

    st = SentenceTransformer(MINILM[0], revision=MINILM[1], device="cpu")
    notes = st.encode([n["text"] for n in fx["notes"]], normalize_embeddings=True)
    note_q = st.encode([q["text"] for q in fx["note_queries"]], normalize_embeddings=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "label": "reference",
                "device": "pytorch-cpu",
                "dtype": "fp32",
                "env": {
                    "python": platform.python_version(),
                    "torch": torch.__version__,
                    "transformers": transformers.__version__,
                    "sentence_transformers": sentence_transformers.__version__,
                    "pillow": PIL.__version__,
                    "clip": {"repo": CLIP[0], "revision": CLIP[1]},
                    "minilm": {"repo": MINILM[0], "revision": MINILM[1]},
                },
                "vectors": {
                    "images": unit(img),
                    "image_queries": unit(txt),
                    "notes": notes.tolist(),
                    "note_queries": note_q.tolist(),
                },
            }
        )
        + "\n"
    )
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
