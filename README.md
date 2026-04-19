# metabare

**metabare.com** is a lightweight digital asset manager (DAM) focused on simplicity, fast deployment, and zero-framework frontend. It enables users to upload, search, and view image metadata stored as Lance format vectors, with optional tracking of interactions and syncing to object storage like R2 (coming soon).

## Features

* Plain HTML/CSS/JS frontend, no build tooling
* Upload API backed by FastAPI + Lance
* R2-compatible storage sync
* Search API
* Optional analytics for image views/clicks (in progress)
* Fly.io ready: each app is containerized and deployable independently

## Structure

```bash
metabare.com/
├── apps/
│   ├── frontend/      # Static site (HTML/CSS/JS)
│   ├── upload/        # FastAPI app for uploads + vectorization
│   └── search/        # FastAPI search/list API
├── README.md
```

## Deployment (Fly.io)

Each app folder includes its own `fly.toml`. Example:

```bash
cd apps/frontend
fly apps create metabare-frontend
fly deploy

cd ../upload
fly apps create metabare-upload
fly deploy

cd ../search
fly apps create metabare-search
fly deploy
```

> Note: persistent volume needed for `upload` app to store LanceDB.

## Usage

* Open the UI (`frontend`) in your browser
* Select image to upload
* Search within your images
