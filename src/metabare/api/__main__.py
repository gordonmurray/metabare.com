"""Entrypoint for the API service."""

from __future__ import annotations

import uvicorn

from ..config import service_settings


def main() -> None:
    settings = service_settings()
    uvicorn.run(
        "metabare.api.app:app",
        host=settings.host,
        port=settings.port,
        log_config=None,  # structlog owns logging; uvicorn's config would fight it
        timeout_graceful_shutdown=int(settings.shutdown_grace_seconds),
    )


if __name__ == "__main__":
    main()
