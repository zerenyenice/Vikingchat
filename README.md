# Vikingchat

A mobile-first chat app on **Azure OpenAI**, answered by a **LangChain Deep
Agent** that keeps its own long-term memory and documents in **OpenViking**.

```
public/index.html   chat UI (drawer with Chats / Documents / Memory)
server.js           Node server: UI, chat storage, uploads, calls the agent
agent/server.py     Deep Agent service (deepagents + langchain-openviking)
docker/start.sh     container entrypoint: writes ov.conf, starts OpenViking, agent, Node
Dockerfile          OpenViking image + Python agent venv + Node, one container
render.yaml         Render blueprint (Docker service + persistent disk)
```

## How it works

- **Chats** are stored as JSON under `DATA_DIR/chats`. The drawer lists them,
  you can reopen or delete any of them. Each chat is also mirrored into an
  OpenViking session.
- **Agentic memory**: every turn runs through a Deep Agent
  (`create_deep_agent`). Its `/memories/` folder is a custom deepagents
  backend that maps onto `viking://user/<user>/memories`, so the agent's
  built-in `write_file` / `edit_file` / `read_file` / `ls` tools operate on
  real OpenViking memory files. `/memories/profile.md` is loaded into the
  system prompt at the start of every turn; the agent decides on its own what
  else to save (preferences/, entities/, events/) and when to look things up
  (`viking_find` over the memories). Nothing is extracted in the background.
- **Documents**: the paperclip uploads a file to OpenViking
  (`viking://resources/uploads/<name>`), which parses, summarises and embeds
  it. The agent searches them with `viking_find` / `viking_read` when a
  question calls for it and names the document it used.
- **Fallback**: if the agent service is down, the Node server answers with a
  plain model call plus retrieved context, so chat keeps working.

Models used:

| Purpose | Env var | Default |
| --- | --- | --- |
| Answering | `AZURE_OPENAI_DEPLOYMENT` | `gpt-5.1` |
| Memory extraction, document summaries | `AZURE_OPENAI_PROCESSING_DEPLOYMENT` | `gpt-5.4-mini` |
| Embeddings for search | `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | `text-embedding-3-large` |

## Memory design

Vikingchat's memory follows the taxonomy that agent-memory research converged on
(CoALA; the 2026 memory surveys; Mem0, Zep, A-MEM, LangMem, MemoryOS). It is
implemented in `agent/memory.py` and stored as Markdown in OpenViking under
`viking://user/<user>/memories`.

**Five kinds, each a file the agent writes with the `remember` tool:**

| Kind | Cognitive type | File | Loaded every turn |
| --- | --- | --- | --- |
| profile | semantic | `profile.md` | yes |
| preference | semantic | `preferences/<topic>.md` | yes |
| procedure | procedural | `procedures/<topic>.md` | yes |
| entity | semantic | `entities/<name>.md` | on match |
| event | episodic | `events/<date>-<slug>.md` | 5 most recent |
| reflection | reflective | `reflections/<date>.md` | on match |

**Practices drawn from the research:**

- **Provenance on every record.** Each line is
  `- [YYYY-MM-DD · chat <id> · stated|inferred] text`, so every fact carries a
  date, the chat it came from, and whether the user said it or the agent
  inferred it.
- **Never overwrite; supersede.** A replaced fact keeps its line, tagged
  `· superseded YYYY-MM-DD`, so temporal questions ("where did I live before")
  still work. `forget` is a soft delete that supersedes.
- **Always-load core, retrieve the rest.** Profile, preferences, procedures and
  recent events go into every prompt; entities, older events and documents come
  through hybrid retrieval (OpenViking vector search + keyword grep, reranked by
  score, recency and kind, cut to a token budget).
- **Hot path + background consolidation.** The agent saves in the turn (hot
  path). A scheduled "sleep" pass (`/consolidate`, cheap model) dedupes, resolves
  contradictions, ages old events into a reflection, prunes, and rebuilds
  `_index.md`.
- **Governance.** An append-only audit log (`memory-audit.jsonl`) records every
  write; the Memory tab lets the user read and delete anything; memory is treated
  as data, never as instructions; secrets are never stored.

**Skills** use Deep Agents' native Skills System. Each skill is
`skills/<name>/SKILL.md` in the Agent Skills format: YAML frontmatter with
`name`, `description` (what it does and when to use it) and `allowed-tools`
(the tools the procedure relies on), then `## When to use`, `## Steps` and
`## Notes`. The agent's `/skills/` folder is routed to that OpenViking
directory, so `create_deep_agent(skills=["/skills/"])` lists every skill in
the system prompt at the start of each turn (progressive disclosure: name and
description up front, `read_file` for the full steps). The only writer is the
`create_skill` tool; the "Create skill" button just asks the agent in chat to
distil one from the conversation. Consolidation never touches skills. Manage
them in the Skills tab.

The Memory tab in the app groups records by kind, shows provenance, and has a
Consolidate button. See the research write-up for sources and rationale.

## Configuration

| Variable | Purpose | Default |
| --- | --- | --- |
| `AZURE_OPENAI_ENDPOINT` (or `endpoint`) | Azure OpenAI resource URL | required |
| `AZURE_OPENAI_API_KEY` (or `api key`) | Azure OpenAI key | required |
| `AZURE_OPENAI_API_VERSION` | optional; use the legacy versioned chat path | unset (v1 API) |
| `OPENVIKING_URL` | OpenViking server | `http://127.0.0.1:1933` |
| `OPENVIKING_API_KEY` | OpenViking key | generated in Docker |
| `OPENVIKING_ACCOUNT` / `OPENVIKING_USER` | identity asserted to OpenViking (trusted mode) | `default` |
| `OPENVIKING_DISABLED` | `1` runs without memory/documents | unset |
| `AGENT_URL` | Deep Agent service | `http://127.0.0.1:8100` |
| `AGENT_DISABLED` | `1` skips the agent and uses plain model calls | unset |
| `DATA_DIR` | chat storage (ephemeral unless a disk is mounted) | `./data` (`/app/.openviking/vikingchat` in Docker) |
| `HOST` / `PORT` | listen address | `0.0.0.0` / `3000` |

There is no login yet: everyone who opens the app shares one memory space, so
keep the URL private.

## Deploy on Render (from a phone)

The blueprint deploys one Docker service without a persistent disk: chats,
memories and documents live inside the container and reset on every deploy or
restart, which is fine for testing. To keep them across restarts later, add a
disk mounted at `/app/.openviking`.

Three processes run in the container (OpenViking, the Python agent, Node), so
the blueprint picks the **Standard** instance (2 GB RAM). You can try
**Starter** or **Free** (512 MB), but expect out-of-memory restarts.

1. Render dashboard → New → **Blueprint** → pick this repo and branch.
2. Fill in `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_API_KEY` when prompted.
3. Apply. The first build pulls the OpenViking image and takes a few minutes.

Or as a plain Web Service: runtime Docker, add a disk at `/app/.openviking`,
set the two Azure variables. The status pill in the app shows
"memory on" once OpenViking is ready (it can take a minute after each start).

## Run locally

```bash
docker build -t vikingchat .
docker run --rm -p 3000:3000 -v vikingchat-data:/app/.openviking \
  -e AZURE_OPENAI_ENDPOINT="https://YOUR-RESOURCE.openai.azure.com/" \
  -e AZURE_OPENAI_API_KEY="YOUR-KEY" vikingchat
```

Without Docker (needs a running OpenViking server for memory and documents):

```bash
export AZURE_OPENAI_ENDPOINT=... AZURE_OPENAI_API_KEY=... OPENVIKING_API_KEY=...
python -m venv .venv && .venv/bin/pip install -r agent/requirements.txt
.venv/bin/python agent/server.py &      # Deep Agent on 127.0.0.1:8100
npm start                                # UI on :3000
```

Or without any of that (`OPENVIKING_DISABLED=1 AGENT_DISABLED=1 npm start`)
for a plain chat.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | server, model and OpenViking status |
| GET/POST | `/api/chats` | list / create chats |
| GET/DELETE | `/api/chats/:id` | load / delete a chat |
| POST | `/api/chats/:id/messages` | `{content}` → `{message, chat, via}`; message carries `sources`, `memoryUpdated`, `toolCalls` |
| GET | `/api/memory` | what OpenViking remembers about the user |
| GET/POST/DELETE | `/api/documents` | list / upload (`file` form field) / delete (`?uri=`) |
| GET | `/api/documents/tasks/:id` | processing status of an upload |

## Preview mode

Opened without a server (for example as a static page) the UI switches to
preview mode with simulated replies, so layout can be checked on a device.
