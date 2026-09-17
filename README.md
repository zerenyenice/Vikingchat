# Vikingchat

A mobile-first chat UI with a tiny Node server that proxies to **Azure OpenAI**.
No dependencies: Node 18+ is all you need.

```
public/index.html   the chat UI (works standalone in "preview mode" too)
server.js           static files + POST /api/chat proxy to Azure OpenAI
```

## Configuration

The server reads these environment variables (see `.env.example`):

| Variable | Purpose | Default |
| --- | --- | --- |
| `endpoint` or `AZURE_OPENAI_ENDPOINT` | Azure OpenAI resource URL | required |
| `api key` or `AZURE_OPENAI_API_KEY` | Azure OpenAI key | required |
| `AZURE_OPENAI_DEPLOYMENT` | deployment name in that resource | `gpt-4o` |
| `AZURE_OPENAI_API_VERSION` | REST API version | `2024-10-21` |
| `HOST` | listen address | `0.0.0.0` |
| `PORT` | listen port | `3000` |

`0.0.0.0` means "every network interface", so other devices on the same
Wi-Fi can reach the server.

## Run it and open it on your phone

```bash
export endpoint="https://YOUR-RESOURCE.openai.azure.com/"
export "api key=YOUR-KEY"                # or AZURE_OPENAI_API_KEY=YOUR-KEY
export AZURE_OPENAI_DEPLOYMENT=gpt-4o    # your deployment name
npm start
```

The server prints its LAN address, for example `http://192.168.1.23:3000`.
Open that on a phone connected to the same Wi-Fi. If it does not load,
allow the port through your computer's firewall.

### Reachable from anywhere (public URL)

Any HTTP tunnel works; two common ones:

```bash
# Cloudflare quick tunnel (no account needed)
cloudflared tunnel --url http://localhost:3000

# ngrok
ngrok http 3000
```

Both print a public `https://` URL you can open on mobile data.

### Docker

```bash
docker build -t vikingchat .
docker run --rm -p 3000:3000 \
  -e endpoint="https://YOUR-RESOURCE.openai.azure.com/" \
  -e "api key=YOUR-KEY" -e AZURE_OPENAI_DEPLOYMENT=gpt-4o vikingchat
```

## API

- `GET /api/health` → `{ ok, configured, deployment, apiVersion }`
- `POST /api/chat` with `{ "messages": [{ "role": "user", "content": "Hi" }] }`
  → `{ reply, usage }`. Only `user` and `assistant` roles are forwarded; the
  system prompt is set server-side (`SYSTEM_PROMPT` env var to override).

## Preview mode

If the page cannot reach `/api/health` (for example when it is opened as a
static file or hosted without the server), it switches to **preview mode**:
replies are simulated locally so the layout and controls can still be
checked on a device.
