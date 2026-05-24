# Photo corpus for the multi-vector demo

Companion to `scripts/arxiv-corpus/`. The arXiv set covers ColPali's home ground (document-page retrieval); this photo set covers the *visceral* multi-vector wins — cluttered scenes, attribute binding on people, text-inside-photo — that translate into a watchable social-media demo without requiring the viewer to know what an ablation study is.

Twenty hand-curated photos split across three categories: cluttered scenes (10), attribute binding on people (4), text in photo (6). Sourced from Unsplash and Pexels under their respective free-use licences; attribution is recorded per row in `photos.json` and propagated into `manifest.json` for the credits crawl on any video reuse.

## Build

```bash
cd scripts/photo-corpus
docker build -t metabare-photo-corpus .
docker run --rm -v "$PWD/data:/work/data" metabare-photo-corpus
```

Outputs land at `data/<sha256>.jpg`, with `data/manifest.json` capturing full attribution and `data/by-corpus-id.json` mapping the corpus IDs (A1..A10, B1..B4, C1..C6) to their resolved SHA-256 filenames. The `data/` directory is gitignored. The fetch is idempotent: re-runs reuse `data/<sha>.jpg` files that already exist.

Photos are normalised on fetch to max-edge 1920 px JPEG at quality 88. The SHA-256 is computed over the *normalised* bytes so the corpus is reproducible from the source URLs in `photos.json` regardless of any future upstream image edits.

## Authoring demo queries

After the fetch, fill in `manifest.md` (sibling file, tracked in git) — same structure as `arxiv-corpus/manifest.md`. One row per photo with the demo query, the expected multi-vector match, and a one-line note on why single-CLIP would visibly miss.

For this corpus the query authoring rule is sharper than for documents: each query must name two or more distinct things in the photo. *"cluttered desk"* is a single-vector tie; *"the desk with the green plant next to the keyboard"* is a multi-vector win.

## Licences and attribution

- **Unsplash**: photos are free to use commercially, no attribution required, attribution appreciated. Each row in `photos.json` carries `photographer` and `attribution_url` — keep these on hand for the credits crawl on any video reuse.
- **Pexels**: same shape — free commercial use, attribution appreciated. Same attribution fields populated.

If any photo ends up in a published video frame, the manifest is the source of truth for the credits.

## Ingestion

Sits on disk only today. The ingest path through Metabare's `/upload` endpoint will land once Firn ≥ v0.7.0 is tagged and the upload service is swapped to a ColPali-style encoder. The current single-vector CLIP path would happily ingest these photos but would not demonstrate anything multi-vector specific.
