# MetaBare

Semantic search over your own images and notes, with the machine learning
running in your browser.

> **Status: planning.** There is no application here yet: nothing runs and
> nothing is deployed. It is being built in small, measured steps, and this
> README will describe each part as it lands.

## The idea

Searching images by what is in them needs an embedding model: something that
turns a picture, or a phrase like "a terminal showing an error", into a vector
that can be compared with others. Usually that model runs on a server, often
on a GPU, and someone pays for it whether or not anyone is searching.

MetaBare will run the models in the browser instead, on the device's GPU
through WebGPU, or on its CPU through WebAssembly where WebGPU is not
available. The server's job shrinks to storing files and vectors.

The plan:

1. **Local-only search.** Drop a folder of images or notes into the page; they
   are embedded and searched on your device, and nothing is uploaded.
2. **A saved library.** Optionally store originals in Amazon S3 and vectors in
   [Firn](https://github.com/gordonmurray/firnflow), an S3-backed search engine,
   so a library survives a cleared browser and is reachable from another
   device.

Every claim about speed, size or cost will link to the raw measurements behind
it, scoped to the device, browser and date it was measured on.

## In this repository

| Path | What |
| --- | --- |
| `eval/technical-notes.json` | 24 synthetic technical notes and 12 queries with relevance labels, for evaluating text search |

## Licence

[Apache 2.0](LICENSE).
