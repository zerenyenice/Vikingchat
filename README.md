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
| `AZURE_OPENAI_DEPLOYMENT` | deployment name in that resource (`gpt-5.1` or `gpt-5.4-mini`) | `gpt-5.1` |
| `AZURE_OPENAI_API_VERSION` | optional; set to use the legacy versioned path | unset (v1 API) |
| `HOST` | listen address | `0.0.0.0` |
| `PORT` | listen port | `3000` |

`0.0.0.0` means "every network interface", so other devices on the same
Wi-Fi can reach the server.

## Run it and open it on your phone

```bash
export endpoint="https://YOUR-RESOURCE.openai.azure.com/"
export "api key=YOUR-KEY"                # or AZURE_OPENAI_API_KEY=YOUR-KEY
export AZURE_OPENAI_DEPLOYMENT=gpt-5.1   # or gpt-5.4-mini
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

### Deploy from your phone (Render, free tier)

1. Sign in at https://dashboard.render.com with GitHub.
2. New → Web Service → pick the `Vikingchat` repo and this branch.
3. Leave the build command empty, start command `npm start`.
4. Add environment variables `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_API_KEY`
   (and `AZURE_OPENAI_DEPLOYMENT` if you want something other than `gpt-5.1`),
   then Create Web Service.

Render assigns `PORT` automatically and gives you an `https://…onrender.com`
URL. `render.yaml` in this repo pre-fills the same settings for a Blueprint
deploy. The free plan sleeps after 15 minutes without traffic, so the first
request after a pause takes about half a minute.

### Docker

```bash
docker build -t vikingchat .
docker run --rm -p 3000:3000 \
  -e endpoint="https://YOUR-RESOURCE.openai.azure.com/" \
  -e "api key=YOUR-KEY" -e AZURE_OPENAI_DEPLOYMENT=gpt-5.1 vikingchat
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
