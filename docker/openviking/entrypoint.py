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


def build_config() -> dict:
    embedding_dim = int(env("OV_EMBEDDING_DIMENSION", "1536"))
    dense = {
        "provider": env("OV_EMBEDDING_PROVIDER", "openai"),
        "model": env("OV_EMBEDDING_MODEL", "text-embedding-3-small"),
        "api_key": env("OV_EMBEDDING_API_KEY") or env("OPENAI_API_KEY"),
        "api_base": env("OV_EMBEDDING_API_BASE", "https://api.openai.com/v1"),
        "dimension": embedding_dim,
        "input": env("OV_EMBEDDING_INPUT", "text"),
    }
    vlm = {
        "provider": env("OV_VLM_PROVIDER", "openai"),
        "model": env("OV_VLM_MODEL", "gpt-4o-mini"),
        "api_key": env("OV_VLM_API_KEY") or env("OPENAI_API_KEY"),
        "api_base": env("OV_VLM_API_BASE", "https://api.openai.com/v1"),
    }
    server: dict = {
        "host": env("OV_SERVER_HOST", "0.0.0.0"),
        "port": int(env("OV_SERVER_PORT", "1933")),
        "cors_origins": ["*"],
    }
    root_key = env("OPENVIKING_ROOT_API_KEY")
    if root_key:
        server["root_api_key"] = root_key
        server["auth_mode"] = "api_key"
    config = {
        "storage": {
            "workspace": env("OV_WORKSPACE", "/data"),
            "vectordb": {"backend": env("OV_VECTORDB_BACKEND", "local"), "dimension": embedding_dim},
        },
        "embedding": {"dense": {k: v for k, v in dense.items() if v is not None}},
        "vlm": {k: v for k, v in vlm.items() if v is not None},
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
            print("[openviking] WARNING: no embedding/VLM API key configured (OPENAI_API_KEY or OV_*_API_KEY)", flush=True)

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
