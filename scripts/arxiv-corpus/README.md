# arXiv page-image corpus

Small reproducible build of a document-page corpus for the upcoming Metabare multi-vector demo.

Six papers, ~30 hand-picked pages, rendered at 150 DPI to JPEG and named by SHA-256 (mirroring Metabare's existing `<sha>.jpg` id scheme). The pages chosen are the ones whose figures land in a 2-second video frame: the Transformer architecture, the residual block, the ViT patch grid, CLIP's contrastive pipeline, ColPali's late-interaction diagram, the LIGO GW150914 chirp plot.

## Build

```bash
cd scripts/arxiv-corpus
docker build -t metabare-arxiv-corpus .
docker run --rm -v "$PWD/data:/work/data" metabare-arxiv-corpus
```

Outputs land under `data/<arxiv_id>/page-NN-<sha8>.jpg` (and `data/manifest.json`). The `data/` directory is gitignored. The build is idempotent: existing PDFs and rendered pages are reused on re-runs.

Re-curating? Edit `papers.json`, then re-run. `pages` lists the 1-indexed pages to render. `highlight_pages` annotates which pages carry the iconic figure (used to author demo queries in `manifest.md`).

## Authoring demo queries

After the build, fill in `manifest.md` (sibling file, tracked in git, no binaries). One row per page with the query that will land it during the video.

The query phrasing matters as much as the image:

- Short enough to read on a phone in two seconds.
- Specific enough that the single-vector `?backend=lance` path returns a wrong-but-plausible neighbour (so the side-by-side diff is visible).
- About *something on the page*, not about the paper's topic — "the page with the patch-grid diagram", not "vision transformers".

## Ingestion

Today these images sit on disk only. When Firn ≥ v0.7.0 lands and Metabare's upload service is swapped to a ColPali-style encoder, a future ingest script will POST each JPEG through `/upload` (or whatever the multi-vector upload path becomes). Until then the corpus is a static asset.

## Licences

Most arXiv papers are under arXiv's non-exclusive distribution licence; LIGO's GW150914 PRL paper is CC-BY 3.0. Per-paper licence is recorded in `papers.json` and propagated into `manifest.json`. Re-distributing the rendered pages publicly (e.g. embedding in the video) is fine for these papers but the licence row in the manifest is the source of truth if there's ever a question.
