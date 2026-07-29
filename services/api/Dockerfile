# Upload and search API.
#
# Built from the repository root so the shared `metabare` package is in the
# build context. Only the `api`, `storage` and `encoder` extras are installed,
# so this image does not carry the worker's dependencies.
#
# Deliberately no PyTorch anywhere in this tree: onnxruntime plus tokenizers
# keeps the image small, and image size is pull time, which is part of how long
# a cold start takes.

FROM python:3.12.13-slim-bookworm AS builder

# uv as a pinned binary rather than a combined uv+python tag: this pins both
# the interpreter patch version and the uv version exactly, and the combined
# tags are not published for every uv release.
COPY --from=ghcr.io/astral-sh/uv:0.11.21 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first, in their own layer, so a source-only change does not
# re-resolve or re-download the environment.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project \
        --extra api --extra storage --extra encoder

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --extra api --extra storage --extra encoder

# Bake the embedding model into the image.
#
# The general rule is to keep model artefacts out of the application image.
# That rule is aimed at the GPU path, where Triton's model repository lives in
# S3 and model download is measured separately. It is inverted here,
# deliberately:
#
#   - The alternative is downloading from Hugging Face on every pod start,
#     which puts a public internet dependency in the startup path of the
#     service that must stay available. A rate limit or an
#     outage at huggingface.co would then stop search from starting.
#   - It is 133 MB against a 128 MB image, so the pod goes from "pull, then
#     download, then load" to "pull, then load".
#   - Baking it makes the image digest pin the model, so a rollback rolls back
#     the model too and a benchmark run is reproducible from the digest alone.
#
# The GPU path will still load from S3, where that reasoning does apply.
ARG ENCODER_MODEL_ID=intfloat/e5-small-v2
# Pinned to a commit, not a branch. "main" would let two builds of the same
# source commit bake different model bytes, which defeats the point of pinning
# the model by image digest.
ARG ENCODER_MODEL_REVISION=ffb93f3bd4047442299a41ebb6fa998a38507c52
ARG ENCODER_ONNX_FILE=model.onnx
ENV ENCODER_MODEL_ID=${ENCODER_MODEL_ID} \
    ENCODER_MODEL_REVISION=${ENCODER_MODEL_REVISION} \
    ENCODER_ONNX_FILE=${ENCODER_ONNX_FILE} \
    ENCODER_CACHE_DIR=/opt/metabare/models

# The venv's interpreter directly, NOT `uv run`. `uv run` re-syncs the
# environment and includes dev dependencies by default, which silently put
# pytest, mypy, ruff and moto into the shipped image. Trivy found it via
# moto's dummy CA keys and example AWS key ids, which is a fair catch: a test
# mocking library has no business in a production container.
RUN /app/.venv/bin/python -c "\
from metabare.config import encoder_settings; \
from metabare.embeddings import _resolve_model_files; \
paths = _resolve_model_files(encoder_settings()); \
print('baked', paths)"


FROM python:3.12.13-slim-bookworm AS runtime

# curl is not installed; the healthcheck uses the interpreter that is already
# present. Fewer packages, fewer CVEs to triage.
RUN groupadd --gid 10001 metabare \
    && useradd --uid 10001 --gid metabare --create-home --shell /usr/sbin/nologin metabare

COPY --from=builder --chown=metabare:metabare /app/.venv /app/.venv
COPY --from=builder --chown=metabare:metabare /app/src /app/src
COPY --from=builder --chown=metabare:metabare /opt/metabare/models /opt/metabare/models

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ENCODER_CACHE_DIR=/opt/metabare/models \
    # The model is already present, so refuse to reach the network at startup.
    # Without this, huggingface_hub still contacts the hub to revalidate and
    # writes lock files, which fails under a read-only root filesystem and
    # would reintroduce the dependency this layer exists to remove.
    HF_HUB_OFFLINE=1 \
    # huggingface_hub and some libraries write here regardless; point them at
    # a path the pod mounts writable rather than at a read-only home directory.
    HF_HOME=/tmp/hf \
    XDG_CACHE_HOME=/tmp/cache

WORKDIR /app
USER metabare
EXPOSE 8080

ENTRYPOINT ["metabare-api"]
