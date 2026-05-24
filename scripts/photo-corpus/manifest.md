# Photo demo manifest

Hand-curated demo queries for the 20-photo corpus fetched by `fetch.py`. Same shape as `arxiv-corpus/manifest.md`: each row maps a photo to the query the audience reads on a phone screen, the *expected winner* under MaxSim late interaction, and a one-line note on why single-CLIP would visibly miss.

Authorship rules (same as the doc manifest, with one extra rule that matters more here):

- The query must name **two or more distinct things in the photo**. *"cluttered desk"* is a single-vector tie; *"the desk with the green plant next to the keyboard"* is a multi-vector win — the win comes from attribute binding, and binding only matters when there are two things to bind.
- Two seconds, phone-screen readable. If it doesn't fit on one line at ~40 characters, trim.
- Five of the 20 photos were spot-checked visually during build (A1, A2, A7, B1, B2, C1, C2 confirmed; the rest are described from source metadata only and may need query refinement on first dry-run).

Status as of build: 20 photos, ~7 MB on disk after normalising to max-edge 1920 px. Ingestion path waits on Firn ≥ v0.7.0 and the upload service's encoder swap.

## Featured demo queries

The headline shots. Each one I'd pull into a side-by-side video frame: left panel `?backend=lance` (single-vector CLIP), right panel `?backend=firn` (multi-vector). The expected winner is the same image in both panels — what differs is what the *single-vector* panel returns instead.

| # | sha8 | corpus_id | Demo query | Expected winner | Why single-CLIP misses |
|---|---|---|---|---|---|
| 1 | `ff4903bb` | A1 | "the desk with glasses on an open book and a coffee cup" | Ron Lach's chaotic desk (books, glasses, paper cup, laptop, crumpled paper) | Three named attributes on a desk-shaped page; pooled CLIP averages to "desk-ish" and returns any laptop-on-table shot. |
| 2 | `0c66e2ac` | A2 | "the laptop buried in yellow paper with a succulent" | Tara Winstead's laptop + crumpled yellow paper + green succulent in white pot | The succulent is a small region; CLIP averages to "messy desk" and could just as easily return A4 / A6. Multi-vector binds "yellow paper" and "succulent" to distinct regions. |
| 3 | `0c66e2ac` | A2 | "the desk with sticky notes that say take a break" | Same A2 photo — sticky notes on the wall behind the laptop are visibly readable | This is the ColPali angle on a photo: patch tokens align to the text *on the sticky notes*. Single-CLIP cannot read sticky note text at all; it returns "any desk with sticky notes". |
| 4 | `568a3669` | A7 | "the abandoned room with a green fan and glass bottles" | N1CE's dusty room (table fan, oil bottles, painting, plastic bag) | Very different aesthetic from the rest of the corpus — visually distinctive but CLIP latches onto "room interior" and returns clean desk shots. Multi-vector binds "fan" + "glass bottles" + "abandoned" to specific patches. |
| 5 | `d6ad4f6e` | B1 | "a woman with a green floral tattoo holding a white mug" | Anthony Tran's back-view portrait (black tank top with logo, green/red lotus tattoo, gray cardigan, white mug) | This is the *"man with a logo on his shirt"* shape from PR #49, dialled up. Single-CLIP knows "tattoo" + "mug" + "woman" but cannot bind colour + shape + location. Multi-vector lights up "green floral tattoo" against the back region and "white mug" against the hand region independently. |
| 6 | `d6ad4f6e` | B1 | "a person in a black tank top with a back tattoo" | Same B1 photo | Variant query — proves the binding works in either direction. |
| 7 | `195a4dbc` | B2 | "a man with a skull tattoo holding coffee" | Alexey Demidov's portrait (blonde curls, hand skull-and-flowers tattoo, green wristband, brown coffee cup) | "Skull tattoo" is a region-specific concept; pooled CLIP returns generic "person with tattoos" candidates. The skull + coffee binding is exactly the case multi-vector token-level matching wins. |
| 8 | `195a4dbc` | B2 | "a man with curly blonde hair and a green wristband" | Same B2 photo | Two more attributes from the same photo, binding to head and wrist regions. |
| 9 | `74fb5492` | C1 | "the shop sign that says we're open" | Tim Mossholder's "Come In We're Open" classic sign | Borderline case — single-CLIP knows the iconic open sign well, so the delta may be small. Stronger phrasing below. |
| 10 | `74fb5492` | C1 | "a glass door sign reading come in we're open" | Same C1 | More specific. ColPali's text-patch tokens match the word "come in" against the cursive script region and "open" against the red lettering region. |
| 11 | `5deec74b` | C2 | "the neon sign that says Paris coffee shop" | Lesli Whitecotton's blue-sky vintage sign | "Paris" + "coffee shop" together is the multi-vector specificity — single-CLIP knows "neon sign" generally and "Paris" semantically (Eiffel Tower etc.) but cannot bind the specific three-word phrase to one signboard. |

## Full corpus by category

All 20 photos. Featured rows above are bold. The non-featured rows are deliberately included in the corpus as *distractors* — the demo only feels meaningful if there are multiple plausible candidates and multi-vector picks the right one. SHA8 is the first 8 hex of the SHA-256 of the normalised JPEG bytes, matching the filename suffix in `data/<full-sha>.jpg`.

### Cluttered scenes (10 photos)

| corpus_id | sha8 | Photographer (source) | Visible content (per source metadata or my spot-check) |
|---|---|---|---|
| **A1** | **`ff4903bb`** | Ron Lach (Pexels) | **books labelled "minimal project", glasses on an open notebook, paper coffee cup, laptop, crumpled paper — verified** |
| **A2** | **`0c66e2ac`** | Tara Winstead (Pexels) | **laptop buried in crumpled yellow paper, succulent in white pot, sticky notes on wall with readable text — verified** |
| A3 | `3ddce717` | Tara Winstead (Pexels) | electronics, books, scattered notes (metadata only) |
| A4 | `6655f220` | Yankrukov (Pexels) | crumpled papers, notebooks, stationery (metadata only) |
| A5 | `84b9ebe9` | Cottonbro (Pexels) | scattered papers, folders, briefcase, grayscale (metadata only) |
| A6 | `7439377c` | Cottonbro (Pexels) | flatlay study desk with laptop, notebooks, sticky notes (metadata only) |
| **A7** | **`568a3669`** | N1CE (Unsplash) | **abandoned room with green vintage fan, glass bottles, monitor, plastic bags, painting of a tree — verified** |
| A8 | `97eb4158` | Andrii Solok (Unsplash) | messy desk with scattered papers, scissors, keyboard (metadata only) |
| A9 | `02be0a2e` | Orlando García (Unsplash) | cluttered desk with computer monitor and speakers (metadata only) |
| A10 | `3488fc9f` | Sokha Michael (Unsplash) | a desk with art on it (metadata only) |

### Attribute binding on people (4 photos)

| corpus_id | sha8 | Photographer (source) | Visible content |
|---|---|---|---|
| **B1** | **`d6ad4f6e`** | Anthony Tran (Unsplash) | **woman back-view, hair in bun, black tank top with small triangle logo, large green/red lotus tattoo on back, gray cardigan around waist, holding white mug — verified** |
| **B2** | **`195a4dbc`** | Alexey Demidov (Unsplash) | **man with blonde curly hair, hand tattoo (skull with pink flowers), green wristband, holding a brown coffee cup. (Alt text mistakenly said "cell phone".) — verified** |
| B3 | `48903751` | Toa Heftiba (Unsplash) | woman in white and black striped shirt holding mug (metadata only) |
| B4 | `1c6ee05c` | Paolo Resteghini (Unsplash) | man sitting at a table with a laptop (metadata only — weakest attribute spread of the four) |

### Text in photo (6 photos)

| corpus_id | sha8 | Photographer (source) | Visible content |
|---|---|---|---|
| **C1** | **`74fb5492`** | Tim Mossholder (Unsplash) | **"Come in WE'RE OPEN" sign on glass, red lettering for OPEN, white cursive for "Come in" — verified** |
| **C2** | **`5deec74b`** | Lesli Whitecotton (Unsplash) | **vintage "PARIS COFFEE SHOP" neon sign against bright blue sky with lens flare — verified** |
| C3 | `b3d3f4ba` | Simon Ray (Unsplash) | sign on the side of a building advertising records (metadata only) |
| C4 | `fa0d5a77` | Fernando Venzano (Unsplash) | "love" neon light signage in white and red (metadata only) |
| C5 | `f21cd288` | Silas Lundquist (Unsplash) | "Casa de mode" sign on a building (metadata only) |
| C6 | `f9b94944` | Alvensia Angela (Unsplash) | theater marquee lit up at night (metadata only) |

## Attribution

Per Unsplash and Pexels licences, attribution is appreciated but not required. For any frame in a published video, include the photographer credit; `manifest.json` carries the full attribution URL per photo. If a single credits crawl is needed, the canonical attribution form is:

> Photos: Ron Lach, Tara Winstead, Yankrukov, Cottonbro (Pexels); N1CE, Andrii Solok, Orlando García, Sokha Michael, Anthony Tran, Alexey Demidov, Toa Heftiba, Paolo Resteghini, Tim Mossholder, Lesli Whitecotton, Simon Ray, Fernando Venzano, Silas Lundquist, Alvensia Angela (Unsplash).

## Things to revisit before shooting

- **B3, B4 may need re-scouting.** Both attribute-binding photos are described as relatively weak — single subject, not a strong attribute pair. If a dry run shows the single-vector path matches them on simple queries, a second scouting round with terms like *"musician portrait band shirt"*, *"barista coffee shop apron"*, *"reader bookshop glasses"* would add stronger B-category candidates.
- **C1 ("we're open" sign) is borderline.** Single-CLIP is well-trained on this iconic sign type. If the demo doesn't show a clear delta, swap to C2 or C3 as the lead text-in-photo query.
- **The cluttered-scene category over-represents desks.** Six of the ten photos are "messy office desk" shots. A future round could broaden to kitchen counters, workshop benches, art studios, market stalls — visually distinct corpus members make the video feel less repetitive.
- **No verification pass on A3–A6, A8–A10, B3, B4, C3–C6.** Read the JPEGs and refine the demo queries before recording. The descriptions in this manifest are source-metadata-only for those rows.
