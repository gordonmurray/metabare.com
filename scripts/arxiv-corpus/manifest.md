# Multi-vector demo manifest

Hand-curated demo queries for the arXiv page corpus rendered by `build_corpus.py`. Each row maps a rendered page to a query phrased for a side-by-side `single-vector vs multi-vector` video shot: the query is the line the audience reads on a phone screen, the *expected winner* is what should land at top-1 under MaxSim late interaction, and *why single-CLIP misses* explains why the `?backend=lance` panel returns something visibly different.

Authorship rules I'm working to:

- Query phrasing points at a *thing on the page*, not at the paper's topic. "the diagram with encoder and decoder stacks" is a multi-vector win; "transformer architecture" is a single-vector tie.
- Two seconds, phone-screen readable. If it doesn't fit on one line at ~40 characters, trim.
- Featured rows below are the ~12 pages that carry a recognisable figure. The rest of the corpus is *context* — useful as distractors so the demo isn't picking from a corpus where only one page mentions "attention".

Status as of build: 34 pages rendered across 6 papers, ~35 MB on disk. Today these images sit in `data/` only; ingestion through a ColPali-style upload path waits on Firn ≥ v0.7.0 and a Metabare encoder swap.

## Featured demo queries

The pages I'd shoot first. Each one I've eyeballed and the iconic figure is confirmed on the page named.

| # | Paper | Page | sha8 | Demo query | Expected winner | Why single-CLIP misses |
|---|---|---|---|---|---|---|
| 1 | Vaswani 2017 | 3 | `f37259c9` | "the encoder-decoder block diagram" | Figure 1, full Transformer architecture | Page is mostly dense text with one centred diagram; pooled CLIP vector averages to "academic page" and ties with any page that mentions "attention". |
| 2 | Vaswani 2017 | 4 | `ce2940ff` | "the scaled dot-product attention diagram" | Figure 2, two stacked attention figures | Single-vector pools the page's text + figures into one point; the specific "scaled dot product" diagram is a small fraction of the visual area. |
| 3 | He 2015 (ResNet) | 1 | `4429bfd6` | "the plot showing deeper plain nets train worse" | Figure 1, training error vs iterations for 20 vs 56-layer plain nets | Both panels are small chart insets; single-CLIP latches onto the page's text density. |
| 4 | He 2015 (ResNet) | 2 | `6286dba8` | "the residual block with the skip connection" | Figure 2, top of page, the famous x + F(x) block | Residual block is one small diagram on a text-heavy page. |
| 5 | He 2015 (ResNet) | 4 | `335968a4` | "plain net vs ResNet architectures side by side" | Figure 3, the tall side-by-side network stack | The side-by-side stack is structurally distinct under MaxSim but blurs to "vertical block diagram" under CLIP. |
| 6 | He 2015 (ResNet) | 5 | `65049d91` | "the training curves for ResNet-152" | Figure 4, training curves for 18 / 34 / 152-layer nets | Two stacked line plots on a text page; CLIP returns whichever page has the most "training" text. |
| 7 | Dosovitskiy 2020 (ViT) | 3 | `0243c4c8` | "the image cut into patches feeding a transformer" | Figure 1, the patch-grid + position-embedding pipeline | The patch grid is structurally specific (rows of small image tiles + token boxes); pooled CLIP smears it. |
| 8 | Radford 2021 (CLIP) | 2 | `fc036e79` | "the contrastive pretraining matrix" | Figure 1, the three-panel pipeline with the I·T similarity grid | Meta-amusing: single-CLIP is bad at recognising its own architecture figure because the grid + two encoders pool to a uniform point. |
| 9 | Radford 2021 (CLIP) | 3 | `4a57d559` | "the efficiency plot comparing CLIP to image caption baselines" | Figure 2, zero-shot transfer efficiency curves | A small line plot in the top-left; the rest of the page is text. |
| 10 | Faysse 2024 (ColPali) | 2 | `38e4b21c` | "OCR pipeline next to a vision-LLM pipeline" | Figure 1, standard retrieval vs ColPali side by side, both with MaxSim panels | Page-spanning figure with two parallel pipelines; CLIP averages to "diagram-ish" but can't bind "OCR" or "vision-LLM" to specific halves. |
| 11 | LIGO 2016 (GW150914) | 2 | `d0e31ee5` | "the gravitational wave chirp signal" | Figure 1, strain vs time for H1 and L1 with the time-frequency spectrogram below | Iconic recognisable plot; this is the highest "wow" frame in the corpus. |
| 12 | LIGO 2016 (GW150914) | 4 | `ea2b6afe` | "the waveform reconstruction with binary black hole parameters" | Figure 2/3, waveform reconstruction and parameter inference | Specialist plot type; single-CLIP returns generic "physics plot" pages. |

## Full corpus

All 34 rendered pages. Featured pages above are bold. Non-featured rows are deliberately included as distractors so the demo runs against a mix of pages, not a hand-picked set where the answer is obvious. SHA8 is the first 8 hex of the SHA-256 of the JPEG bytes, matching the filename suffix in `data/<arxiv_id>/page-NN-<sha8>.jpg`.

### Vaswani et al. 2017 — *Attention Is All You Need* (`1706.03762`)
Licence: arXiv non-exclusive.

| Page | sha8 | Role |
|---|---|---|
| 1 | `0d7bc3fd` | context (title, abstract, intro) |
| 2 | `6bfd52f5` | context (background + model overview text) |
| **3** | **`f37259c9`** | **featured — Figure 1 Transformer architecture** |
| **4** | **`ce2940ff`** | **featured — Figure 2 attention diagrams** |
| 5 | `7c8122fd` | context (positional encoding equations + section 4) |
| 6 | `720f5e23` | context (training detail) |

### He et al. 2015 — *Deep Residual Learning* (`1512.03385`)
Licence: arXiv non-exclusive.

| Page | sha8 | Role |
|---|---|---|
| **1** | **`4429bfd6`** | **featured — Figure 1 motivating plot** |
| **2** | **`6286dba8`** | **featured — Figure 2 residual block** |
| 3 | `e2ab32e0` | context (residual learning section text) |
| **4** | **`335968a4`** | **featured — Figure 3 side-by-side architectures** |
| **5** | **`65049d91`** | **featured — Figure 4 training curves** |
| 6 | `ff6ab0ab` | context (experiment tables) |

### Dosovitskiy et al. 2020 — *ViT* (`2010.11929`)
Licence: arXiv non-exclusive.

| Page | sha8 | Role |
|---|---|---|
| 1 | `07a9cb70` | context (title, abstract) |
| 2 | `2a0bbe21` | context (intro) |
| **3** | **`0243c4c8`** | **featured — Figure 1 patch-grid pipeline** |
| 4 | `9357ea7e` | context (method section) |
| 5 | `42d52c71` | context (experiments) |
| 8 | `f53de99b` | context — likely position-embedding visualisations (worth a second look as a candidate featured row) |

### Radford et al. 2021 — *CLIP* (`2103.00020`)
Licence: arXiv non-exclusive.

| Page | sha8 | Role |
|---|---|---|
| 1 | `be415815` | context (title, abstract) |
| **2** | **`fc036e79`** | **featured — Figure 1 contrastive pretraining + zero-shot** |
| **3** | **`4a57d559`** | **featured — Figure 2 efficiency vs image-caption baseline** |
| 4 | `b5473e65` | context (approach section) |
| 5 | `ed07ce7c` | context (dataset details) |

### Faysse et al. 2024 — *ColPali* (`2407.01449`)
Licence: arXiv non-exclusive.

| Page | sha8 | Role |
|---|---|---|
| 1 | `86803865` | context (title, abstract) |
| **2** | **`38e4b21c`** | **featured — Figure 1 ColPali vs standard retrieval** |
| 3 | `2e1bf027` | context (related work) |
| 4 | `6557b2f5` | context (ViDoRe benchmark, Table 1) |
| 5 | `a8e49cce` | context (method section) |
| 6 | `ac914f07` | context (results) |

### LIGO Scientific Collaboration 2016 — *GW150914* (`1602.03837`)
Licence: CC-BY 3.0 (PRL open access).

| Page | sha8 | Role |
|---|---|---|
| 1 | `e53982a9` | context (title, abstract, introduction) |
| **2** | **`d0e31ee5`** | **featured — Figure 1 the chirp signal** |
| 3 | `9a6d4ee9` | context (detector noise discussion) |
| **4** | **`ea2b6afe`** | **featured — Figure 2/3 waveform reconstruction** |
| 5 | `44676b66` | context (statistical significance) |

## Query authoring notes

Where the single-vector path will visibly *fail* and the multi-vector path will visibly *win*, in order of strongest signal:

1. **Queries about a small figure on a text-heavy page.** Single-CLIP pools the page into one vector dominated by the bulk text. Multi-vector keeps a per-patch token, so a query about the figure matches the figure's region directly. Rows 1, 4, 6, 9 above are the cleanest examples.

2. **Queries that bind two concepts on the same page** (the *"a man with a logo on his shirt"* shape from the PR #49 description, applied to documents). Row 5 ("plain net vs ResNet *side by side*") and row 10 ("OCR pipeline *next to* a vision-LLM pipeline") both test the binding behaviour — single-CLIP can match "ResNet" or "OCR" but smears the side-by-side layout that's the point of the figure.

3. **Queries that reference text inside a figure caption.** ColPali's headline use case. Row 12 ("waveform reconstruction with binary black hole parameters") leans on caption text that's visually present in the figure but not in the surrounding page text.

Queries to avoid for the demo (single-CLIP handles them fine, no visible delta):

- "vision transformer" → both backends will find ViT page 1.
- "gravitational waves" → both backends will find LIGO page 1.
- "self attention" → too broad, multiple pages match.

## Things to revisit before shooting the video

- **ViT page 8** is in the corpus on the assumption it carries the position-embedding RGB visualisation. Worth one direct check before promoting it to a featured row.
- **Add 4–6 photo distractors** to the corpus before the video. Document-only retrieval shows ColPali at its best, but a pure-document corpus risks the video feeling like a niche use case. A handful of compositional Unsplash photos (the "man with a logo on his shirt" PR example) mixed in would let the same backend swap also demo attribute binding on natural images.
- **Per-paper licence row** in the manifest is the source of truth if the video re-publishes any of these page images. arXiv non-exclusive is fine for most uses; LIGO is CC-BY (attribute LIGO Scientific Collaboration + Virgo Collaboration in the credits crawl).
