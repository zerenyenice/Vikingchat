#!/usr/bin/env python3
"""Generate ov.conf from environment variables (unless one is mounted) and start the server.

Set OV_CONF_PATH to use a hand-written config instead; every OV_* variable below is then
ignored. See https://docs.openviking.ai for the full configuration reference.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

CONFIG_PATH = os.environ.get("OPENVIKING_CONFIG_FILE", "/config/ov.conf")


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


OPENAI_DEFAULT_BASE = "https://api.openai.com/v1"
AZURE_ENV_ENDPOINT = "AZURE_OPENAI_ENDPOINT"
AZURE_ENV_API_KEY = "AZURE_OPENAI_API_KEY"
AZURE_ENV_API_VERSION = "OPENAI_API_VERSION"


def model_section(prefix: str, *, default_model: str, extra: dict | None = None) -> dict:
    """Build the ``embedding.dense`` / ``vlm`` block from ``<prefix>_*`` variables.

    ``provider`` is one of OpenViking's providers (``openai``, ``azure``, ``volcengine``, ...).
    For ``openai`` the key falls back to ``OPENAI_API_KEY``; for ``azure`` the endpoint, key and
    API version fall back to the standard ``AZURE_OPENAI_ENDPOINT`` / ``AZURE_OPENAI_API_KEY`` /
    ``OPENAI_API_VERSION`` variables, and ``model`` is the Azure *deployment* name.
    """
    provider = (env(f"{prefix}_PROVIDER", "openai") or "openai").lower()
    section: dict = {
        "provider": provider,
        "model": env(f"{prefix}_MODEL", default_model),
        "api_key": env(f"{prefix}_API_KEY"),
        "api_base": env(f"{prefix}_API_BASE"),
        "api_version": env(f"{prefix}_API_VERSION"),
    }
    if provider == "azure":
        section["api_key"] = section["api_key"] or env(AZURE_ENV_API_KEY)
        section["api_base"] = section["api_base"] or env(AZURE_ENV_ENDPOINT)
        section["api_version"] = section["api_version"] or env(AZURE_ENV_API_VERSION)
        if not section["api_base"]:
            print(
                f"[openviking] ERROR: {prefix}_PROVIDER=azure needs {prefix}_API_BASE or "
                f"{AZURE_ENV_ENDPOINT} (https://<resource>.openai.azure.com)",
                file=sys.stderr,
                flush=True,
            )
            raise SystemExit(1)
    elif provider == "openai":
        section["api_key"] = section["api_key"] or env("OPENAI_API_KEY")
        section["api_base"] = section["api_base"] or OPENAI_DEFAULT_BASE
    section.update(extra or {})
    return {k: v for k, v in section.items() if v is not None}


def build_config() -> dict:
    embedding_dim = int(env("OV_EMBEDDING_DIMENSION", "1536"))
    dense = model_section(
        "OV_EMBEDDING",
        default_model="text-embedding-3-small",
        extra={"dimension": embedding_dim, "input": env("OV_EMBEDDING_INPUT", "text")},
    )
    vlm = model_section("OV_VLM", default_model="gpt-4o-mini")
    server: dict = {
        "host": env("OV_SERVER_HOST", "0.0.0.0"),
        "port": int(env("OV_SERVER_PORT", "1933")),
        "cors_origins": ["*"],
    }
    # "trusted" mode: OpenViking trusts the X-OpenViking-Account/User headers that the backend
    # sets per logged-in user, so every user gets an isolated namespace. When the server binds
    # to a non-loopback address (always the case in Docker) OpenViking additionally requires a
    # root API key, which the backend presents on every request.
    server["auth_mode"] = env("OV_AUTH_MODE", "trusted")
    root_key = env("OPENVIKING_ROOT_API_KEY")
    if root_key:
        server["root_api_key"] = root_key
    elif server["host"] not in ("127.0.0.1", "localhost", "::1"):
        print(
            "[openviking] ERROR: OPENVIKING_ROOT_API_KEY is not set. OpenViking refuses to run "
            f"auth_mode={server['auth_mode']!r} on {server['host']} without a root API key. "
            "Set OPENVIKING_ROOT_API_KEY in .env (e.g. `openssl rand -hex 32`).",
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(1)
    config = {
        "storage": {
            "workspace": env("OV_WORKSPACE", "/data"),
            "vectordb": {"backend": env("OV_VECTORDB_BACKEND", "local"), "dimension": embedding_dim},
        },
        "embedding": {"dense": dense},
        "vlm": vlm,
        "server": server,
    }
    return config


def main() -> int:
    mounted = env("OV_CONF_PATH")
    if mounted and os.path.exists(mounted):
        config_path = mounted
        print(f"[openviking] using mounted config {config_path}", flush=True)
    else:
        config = build_config()
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(config, fh, indent=2)
        config_path = CONFIG_PATH
        redacted = json.loads(json.dumps(config))
        for section in ("embedding", "vlm", "server"):
            node = redacted.get(section, {})
            for holder in ([node] + list(node.values()) if isinstance(node, dict) else []):
                if isinstance(holder, dict):
                    for key in ("api_key", "root_api_key"):
                        if holder.get(key):
                            holder[key] = "***"
        print(f"[openviking] generated {config_path}:\n{json.dumps(redacted, indent=2)}", flush=True)
        if not config["embedding"]["dense"].get("api_key") or not config["vlm"].get("api_key"):
            print(
                "[openviking] WARNING: no embedding/VLM API key configured "
                "(OV_*_API_KEY, OPENAI_API_KEY or AZURE_OPENAI_API_KEY)",
                flush=True,
            )

    cmd = [
        "openviking-server",
        "--config", config_path,
        "--host", env("OV_SERVER_HOST", "0.0.0.0"),
        "--port", env("OV_SERVER_PORT", "1933"),
    ] + sys.argv[1:]
    print("[openviking] exec:", " ".join(cmd), flush=True)
    os.execvp(cmd[0], cmd)
    return 0  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
