# Vikingchat

A mobile-first chat app on **Azure OpenAI** with **OpenViking** as its context
database: chat history you can return to, long-term memory about you, and a
place to upload documents that the assistant can read.

```
public/index.html   chat UI (drawer with Chats / Documents / Memory)
server.js           Node server: chat storage, Azure OpenAI, OpenViking client
docker/start.sh     container entrypoint: writes ov.conf, starts OpenViking + Node
Dockerfile          OpenViking image + Node, one container
render.yaml         Render blueprint (Docker service + persistent disk)
```

## How it works

- **Chats** are stored as JSON under `DATA_DIR/chats`. The drawer lists them,
  you can reopen or delete any of them.
- **Memory**: every chat is mirrored into an OpenViking *session*. About 90
  seconds after a chat goes quiet, OpenViking extracts memories (profile,
  preferences, entities, events) into `viking://user/<user>/memories` using
  the cheap processing model. On every new message the server searches those
  memories and puts the hits in the system prompt.
- **Documents**: the paperclip uploads a file to OpenViking
  (`viking://resources/uploads/<name>`), which parses, summarises and embeds
  it with the processing and embedding deployments. Relevant passages are
  retrieved for each message and cited under the reply.

Models used:

| Purpose | Env var | Default |
| --- | --- | --- |
| Answering | `AZURE_OPENAI_DEPLOYMENT` | `gpt-5.1` |
| Memory extraction, document summaries | `AZURE_OPENAI_PROCESSING_DEPLOYMENT` | `gpt-5.4-mini` |
| Embeddings for search | `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | `text-embedding-3-large` |

## Configuration

| Variable | Purpose | Default |
| --- | --- | --- |
| `AZURE_OPENAI_ENDPOINT` (or `endpoint`) | Azure OpenAI resource URL | required |
| `AZURE_OPENAI_API_KEY` (or `api key`) | Azure OpenAI key | required |
| `AZURE_OPENAI_API_VERSION` | optional; use the legacy versioned chat path | unset (v1 API) |
| `OPENVIKING_URL` | OpenViking server | `http://127.0.0.1:1933` |
| `OPENVIKING_API_KEY` | OpenViking key | generated in Docker |
| `OPENVIKING_USER` | user namespace for memories | `default` |
| `OPENVIKING_DISABLED` | `1` runs without memory/documents | unset |
| `DATA_DIR` | chat storage | `./data` (`/app/.openviking/vikingchat` in Docker) |
| `HOST` / `PORT` | listen address | `0.0.0.0` / `3000` |

There is no login yet: everyone who opens the app shares one memory space, so
keep the URL private.

## Deploy on Render (from a phone)

The blueprint deploys one Docker service with a 1 GB persistent disk mounted at
`/app/.openviking`. The disk is what keeps chats, memories and documents across
restarts, and disks need a paid instance (Starter is enough to try).

1. Render dashboard → New → **Blueprint** → pick this repo and branch.
2. Fill in `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_API_KEY` when prompted.
3. Apply. The first build pulls the OpenViking image and takes a few minutes.

Or as a plain Web Service: runtime Docker, add a disk at `/app/.openviking`,
set the two Azure variables. The status pill in the app shows
"memory on" once OpenViking is ready (it can take a minute after each start).

If the service runs out of memory on Starter (512 MB), move it to Standard.

## Run locally

```bash
docker build -t vikingchat .
docker run --rm -p 3000:3000 -v vikingchat-data:/app/.openviking \
  -e AZURE_OPENAI_ENDPOINT="https://YOUR-RESOURCE.openai.azure.com/" \
  -e AZURE_OPENAI_API_KEY="YOUR-KEY" vikingchat
```

Without Docker (no memory or documents unless you run OpenViking yourself):

```bash
export AZURE_OPENAI_ENDPOINT=... AZURE_OPENAI_API_KEY=... OPENVIKING_DISABLED=1
npm start
```

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | server, model and OpenViking status |
| GET/POST | `/api/chats` | list / create chats |
| GET/DELETE | `/api/chats/:id` | load / delete a chat |
| POST | `/api/chats/:id/messages` | `{content}` → `{message, chat, usedMemories}` |
| GET | `/api/memory` | what OpenViking remembers about the user |
| GET/POST/DELETE | `/api/documents` | list / upload (`file` form field) / delete (`?uri=`) |
| GET | `/api/documents/tasks/:id` | processing status of an upload |

## Preview mode

Opened without a server (for example as a static page) the UI switches to
preview mode with simulated replies, so layout can be checked on a device.
