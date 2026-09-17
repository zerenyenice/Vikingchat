#!/usr/bin/env bash
# Starts OpenViking (embedded) and then the Vikingchat Node server.
set -euo pipefail

OV_HOME="${OPENVIKING_HOME:-/app/.openviking}"
export OPENVIKING_CONFIG_FILE="${OPENVIKING_CONFIG_FILE:-$OV_HOME/ov.conf}"
export OPENVIKING_CLI_CONFIG_FILE="${OPENVIKING_CLI_CONFIG_FILE:-$OV_HOME/ovcli.conf}"
mkdir -p "$OV_HOME" "${DATA_DIR:-$OV_HOME/vikingchat}"

AZ_ENDPOINT="${AZURE_OPENAI_ENDPOINT:-${endpoint:-}}"
AZ_ENDPOINT="${AZ_ENDPOINT%/}"
AZ_KEY="${AZURE_OPENAI_API_KEY:-}"
PROCESSING_MODEL="${AZURE_OPENAI_PROCESSING_DEPLOYMENT:-gpt-5.4-mini}"
EMBED_MODEL="${AZURE_OPENAI_EMBEDDING_DEPLOYMENT:-text-embedding-3-large}"
EMBED_DIM="${AZURE_OPENAI_EMBEDDING_DIMENSION:-3072}"
EMBED_API_VERSION="${AZURE_OPENAI_EMBEDDING_API_VERSION:-2024-10-21}"

# One key shared by both processes. Persist a generated key on the data disk
# so OpenViking's stored per-user data stays readable across restarts.
KEY_FILE="$OV_HOME/vikingchat/openviking.key"
if [ -z "${OPENVIKING_API_KEY:-}" ]; then
  if [ -s "$KEY_FILE" ]; then OPENVIKING_API_KEY="$(cat "$KEY_FILE")"
  else OPENVIKING_API_KEY="$(head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 40)"; printf '%s' "$OPENVIKING_API_KEY" > "$KEY_FILE"; fi
fi
export OPENVIKING_API_KEY

EMBEDDED=1
case "${OPENVIKING_URL:-http://127.0.0.1:1933}" in
  http://127.0.0.1:*|http://localhost:*) ;;
  *) EMBEDDED=0 ;;   # an external OpenViking server is configured
esac

if [ "$EMBEDDED" = "1" ]; then
  if [ -z "$AZ_ENDPOINT" ] || [ -z "$AZ_KEY" ]; then
    echo "[start] AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_API_KEY missing; OpenViking will not start." >&2
  else
    # Always regenerate ov.conf from the environment so key rotation works.
    if [ -n "${OPENVIKING_CONF_CONTENT:-}" ]; then
      printf '%s' "$OPENVIKING_CONF_CONTENT" > "$OPENVIKING_CONFIG_FILE"
    else
      cat > "$OPENVIKING_CONFIG_FILE" <<JSON
{
  "embedding": {
    "max_concurrent": 4,
    "max_retries": 3,
    "dense": {
      "provider": "azure",
      "api_key": "$AZ_KEY",
      "api_base": "$AZ_ENDPOINT",
      "api_version": "$EMBED_API_VERSION",
      "model": "$EMBED_MODEL",
      "dimension": $EMBED_DIM,
      "encoding_format": "float"
    }
  },
  "vlm": {
    "provider": "openai",
    "api_key": "$AZ_KEY",
    "api_base": "$AZ_ENDPOINT/openai/v1",
    "model": "$PROCESSING_MODEL",
    "max_retries": 3,
    "max_concurrent": 4
  },
  "storage": {
    "workspace": "$OV_HOME/data",
    "agfs": { "backend": "local" },
    "vectordb": { "backend": "local", "name": "context" }
  },
  "server": {
    "host": "127.0.0.1",
    "port": 1933,
    "root_api_key": "$OPENVIKING_API_KEY",
    "auth_mode": "api_key",
    "cors_origins": []
  }
}
JSON
    fi
    chmod 600 "$OPENVIKING_CONFIG_FILE"
    echo "[start] launching OpenViking (embedding=$EMBED_MODEL, processing=$PROCESSING_MODEL)"
    OPENVIKING_SERVER_HOST=127.0.0.1 OPENVIKING_SERVER_PORT=1933 OPENVIKING_WITH_BOT="${OPENVIKING_WITH_BOT:-0}" \
      openviking-entrypoint --without-bot &
    OV_PID=$!
    trap 'kill -TERM $OV_PID 2>/dev/null || true' TERM INT EXIT
  fi
fi

if [ "${AGENT_DISABLED:-0}" != "1" ]; then
  echo "[start] launching agent service on 127.0.0.1:${AGENT_PORT:-8100}"
  AGENT_HOST=127.0.0.1 AGENT_PORT="${AGENT_PORT:-8100}" /app/agent-venv/bin/python /app/vikingchat/agent/server.py &
  AGENT_PID=$!
  trap 'kill -TERM $AGENT_PID ${OV_PID:-} 2>/dev/null || true' TERM INT EXIT
fi

echo "[start] launching Vikingchat on :${PORT:-3000}"
cd /app/vikingchat
exec node server.js
