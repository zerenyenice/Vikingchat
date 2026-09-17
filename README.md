# VikingChat

An agent chat application built on **[LangChain Deep Agents](https://docs.langchain.com/oss/python/deepagents)** and
**[OpenViking](https://github.com/volcengine/openviking)**, the agent-native context database.

Users log in and chat with a deep agent that:

- **remembers** – OpenViking sessions record every turn; relevant memories are recalled before each model call and
  new long-term facts are stored under `viking://~/memories` (the user's private OpenViking space)
- **reads your documents** – uploads are ingested as OpenViking resources (`viking://resources/users/<user>/…`) and
  searched semantically by the agent
- **learns skills** – a *skill session* is a chat in which you teach the agent a procedure; clicking **Build skill**
  distills the session into a `SKILL.md` stored under `viking://agent/skills/<name>`, which the agent loads
  on demand in later chats

```
┌──────────────┐   HTTPS/SSE    ┌───────────────────────────┐   HTTP    ┌──────────────────┐
│ React UI     │ ─────────────▶ │ FastAPI backend           │ ────────▶ │ OpenViking server│
│ (Vite/nginx) │ ◀───────────── │  • JWT auth (SQLite)      │ ◀──────── │  • sessions      │
└──────────────┘                │  • deepagents agent       │           │  • memories      │
                                │    + langchain-openviking │           │  • resources     │
                                │      tools & middleware   │           │  • skills        │
                                │  • skill builder          │           └──────────────────┘
                                └─────────────┬─────────────┘
                                              ▼  LLM (default: anthropic:claude-opus-5)
```

## Repository layout

| Path | What |
| --- | --- |
| `backend/` | FastAPI app. `app/agent/` builds the deep agent, streams turns, builds skills. `app/routers/` is the HTTP API. |
| `frontend/` | Vite + React + TypeScript UI (login, sessions, streaming chat, documents / skills / memory panels). |
| `docker/openviking/` | Dockerfile + entrypoint that generates `ov.conf` from environment variables and runs `openviking-server`. |
| `docker-compose.yml` | OpenViking + backend + frontend. |

## Quick start (Docker)

1. Copy the env template and fill in keys:

   ```bash
   cp .env.example .env
   # required: ANTHROPIC_API_KEY (agent LLM), OPENAI_API_KEY (OpenViking embeddings + VLM), SECRET_KEY,
   #           OPENVIKING_ROOT_API_KEY (shared secret between backend and OpenViking)
   # Azure OpenAI instead: AGENT_MODEL=azure_openai:<deployment>, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY,
   #           OV_EMBEDDING_PROVIDER=azure, OV_EMBEDDING_MODEL=<deployment>, OV_VLM_PROVIDER=azure, OV_VLM_MODEL=<deployment>
   ```

2. Build and start:

   ```bash
   docker compose up --build -d
   docker compose logs -f openviking   # wait for "Uvicorn running" / healthcheck to pass
   ```

3. Open <http://localhost:8080>, register a user and start chatting.

The OpenViking container installs `openviking` from PyPI and, on start, writes `/config/ov.conf` from the
`OV_*` variables (OpenAI-compatible embedding + VLM by default). To use another provider, either set the `OV_*`
variables (`provider`, `model`, `api_base`, `api_key`, `api_version`, `dimension`) or mount your own config and point
`OV_CONF_PATH` at it. For Azure OpenAI set `OV_EMBEDDING_PROVIDER=azure` / `OV_VLM_PROVIDER=azure` and use deployment
names as models; the endpoint, key and API version fall back to `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY` and
`OPENAI_API_VERSION`. The full option list is in OpenViking's configuration guide.

## Local development

Prerequisites: Python 3.11+, Node 20+, a running OpenViking server (see above, or `pip install openviking &&
openviking-server init && openviking-server`).

```bash
# backend
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp ../.env.example .env            # or export the variables
uvicorn app.main:app --reload --port 8000

# frontend (proxies /api to :8000)
cd frontend
npm install
npm run dev                        # http://localhost:5173

# tests
cd backend && python -m pytest -q
cd frontend && npm run typecheck
```

The backend tests run against a fake chat model and a fake OpenViking client, so they need no network or keys.

### End-to-end UI test

`frontend/e2e/smoke.mjs` drives the built UI in Chromium through register → chat (streamed reply) → document upload →
memory panel → skill session → **Build skill**, against a backend that uses the same fakes as the unit tests:

```bash
cd frontend && npm run build
cd ../backend && FRONTEND_DIST=../frontend/dist uvicorn tests.e2e_app:app --port 8011 &
cd ../frontend && npx playwright install chromium   # once; or set PW_CHROMIUM=/path/to/chrome
BASE_URL=http://127.0.0.1:8011 npm run e2e
```

## Configuration

All backend settings are environment variables (or `backend/.env`), see `backend/app/config.py`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `AGENT_MODEL` | `anthropic:claude-opus-5` | LangChain `provider:model` string for the deep agent (`azure_openai:<deployment>` for Azure) |
| `AZURE_OPENAI_ENDPOINT` / `AZURE_OPENAI_API_KEY` | – | Read by langchain-openai for `azure_openai:` models |
| `OPENAI_API_VERSION` | `2025-01-01-preview` | Azure OpenAI API version (backend default applies when unset) |
| `SKILL_BUILDER_MODEL` | same as `AGENT_MODEL` | Model used to distill skill sessions into `SKILL.md` |
| `OPENVIKING_URL` | `http://localhost:1933` | OpenViking server |
| `OPENVIKING_API_KEY` | – | OpenViking root key (compose sets it from `OPENVIKING_ROOT_API_KEY`) |
| `OPENVIKING_ACCOUNT` | `default` | OpenViking account; each app user maps to an OpenViking user (`X-OpenViking-User`) |
| `SECRET_KEY` | change me | JWT signing key |
| `DATA_DIR` | `./data` | SQLite DBs (users, sessions, transcripts, LangGraph checkpoints) and upload staging |
| `RECALL_LIMIT` / `RECALL_TOKEN_BUDGET` | `5` / `32000` | How much OpenViking context is injected per model call |
| `MEMORY_COMMIT_TOKEN_THRESHOLD` | `8000` | Auto-commit the OpenViking session (memory extraction) after this many pending tokens |
| `ALLOW_FORGET` | `false` | Expose the `viking_forget` tool to the agent |

## How the pieces fit

**Agent (`backend/app/agent/factory.py`).** One deep agent is compiled per user and session kind with
`create_deep_agent(...)`:

- tools from `langchain_openviking.create_openviking_tools` (`viking_find`, `viking_search`, `viking_read`,
  `viking_store`, `viking_add_resource`, `viking_add_skill`, …) plus the built-in deep-agent filesystem/todo/subagent tools
- `OpenVikingContextMiddleware`, which injects recalled memory before every model call and records the turn to the
  OpenViking session afterwards, auto-committing (memory extraction) once enough tokens accumulate
- a system prompt listing the user's installed skills (name + description) so the agent can `viking_read` the
  right `SKILL.md` when a task matches (progressive disclosure)
- a SQLite LangGraph checkpointer keyed by session id, so multi-turn state survives restarts

**Chat streaming (`app/agent/chat.py`).** `POST /api/sessions/{id}/messages` streams server-sent events:
`user_message`, `token`, `tool_call`, `tool_result`, `done` / `error`. Transcripts are also stored in SQLite for the UI.

**Documents (`app/routers/documents.py`).** Uploads are staged locally and pushed with `client.add_resource(path,
to="viking://resources/users/<user>/<slug>")`; OpenViking parses and indexes them asynchronously (status is polled
via the task API).

**Skills (`app/agent/skill_builder.py`).** *Build skill* renders the session transcript, asks the model for a structured
draft (`name`, `description`, `tags`, `instructions`), writes a SKILL.md with YAML frontmatter, validates it and calls
`client.add_skill(...)` (or `update_skill` when rebuilding). Agents for that user are rebuilt so the new skill appears
in their prompt immediately.

**Memory (`app/routers/memory.py`).** Browse/search/read/delete under `viking://~/memories` (OpenViking's per-user home alias; results come back as `viking://user/<id>/memories/...`); *Save memories* in the
chat header calls `commit_session` so OpenViking archives the session and extracts memories right away.

## API overview

| Method & path | Purpose |
| --- | --- |
| `POST /api/auth/register`, `POST /api/auth/login`, `GET /api/auth/me` | JWT auth |
| `GET/POST /api/sessions`, `GET/PATCH/DELETE /api/sessions/{id}` | Sessions (`kind`: `chat` or `skill`) |
| `POST /api/sessions/{id}/messages` | Send a message, SSE stream back |
| `POST /api/sessions/{id}/commit`, `GET /api/sessions/{id}/context` | Force memory extraction / inspect OpenViking session |
| `POST /api/sessions/{id}/build-skill` | Build a skill from a skill session |
| `GET/POST /api/documents`, `GET/DELETE /api/documents/{id}` | Upload & manage documents |
| `GET /api/skills`, `GET/DELETE /api/skills/{name}` | Installed skills |
| `GET /api/memory?query=`, `GET /api/memory/read?uri=`, `DELETE /api/memory?uri=` | Long-term memory |
| `GET /api/health` | Backend + OpenViking health |

## Notes

- OpenViking runs in **trusted auth mode** with `OPENVIKING_ROOT_API_KEY` as a shared secret: the backend presents the
  key on every request and OpenViking scopes the request to the `X-OpenViking-Account`/`X-OpenViking-User` headers the
  backend sets for the logged-in user. Anyone holding the key can act as any user, so keep the OpenViking port on a
  private network (or drop the `ports:` mapping for the `openviking` service). OpenViking refuses to start without a
  root key when bound to a non-loopback address, and its `api_key` mode ignores the per-user headers, which is why
  `trusted` is used. Set `OV_AUTH_MODE` to override.
- OpenViking is AGPL-3.0 licensed; this project only talks to it over HTTP.
