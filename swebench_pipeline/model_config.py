"""Local/cloud endpoint settings; credentials never enter run manifests."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_environment(path: Path | None = None) -> None:
    from dotenv import load_dotenv

    # Use the project root even when the CLI is invoked from another directory.
    load_dotenv(path if path is not None else PROJECT_ROOT / ".env", override=False)


@dataclass(frozen=True)
class ModelSettings:
    backend: str
    model: str
    base_url: str
    health_url: str | None
    api_key: str = field(repr=False)

    def require_credentials(self) -> None:
        if self.backend == "cloud" and not self.api_key:
            raise ValueError("Set CLOUD_API_KEY in .env or your shell before cloud inference")


def resolve_model_settings(args) -> ModelSettings:
    import local_config as cfg

    backend = args.backend
    if backend not in ("local", "cloud"):
        raise ValueError("SWEBENCH_BACKEND must be local or cloud")
    prefix = backend.upper()
    model = args.model or os.environ.get(f"{prefix}_MODEL", "").strip()
    base_url = args.base_url or os.environ.get(f"{prefix}_BASE_URL", "").strip()
    api_key = os.environ.get(f"{prefix}_API_KEY", "").strip()
    if backend == "local":
        model = model or cfg.get_model_config()["served_alias"]
        base_url = base_url or cfg.base_url()
        api_key = api_key or "EMPTY"
    elif not model or not base_url:
        raise ValueError("Cloud backend requires CLOUD_MODEL and CLOUD_BASE_URL in .env "
                         "or --model and --base-url")

    base_url = base_url.rstrip("/")
    validate_endpoint(base_url)
    health_url = args.health_url
    if health_url is None and backend == "local":
        # A custom local endpoint should not probe the default server by mistake.
        health_url = base_url.removesuffix("/v1") + "/health"
    if health_url:
        validate_endpoint(health_url)
    return ModelSettings(backend, model, base_url, health_url or None, api_key)


def validate_endpoint(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Model endpoint must be an absolute http:// or https:// URL")
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise ValueError("Model endpoint must not contain credentials, query parameters, or a fragment; "
                         "put the API key in .env")
