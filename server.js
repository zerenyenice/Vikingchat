// Vikingchat server: serves the mobile chat UI and proxies chat requests to
// Azure OpenAI so the API key never reaches the browser.
//
// Configuration comes from environment variables (see .env.example):
//   endpoint / AZURE_OPENAI_ENDPOINT   Azure OpenAI resource URL
//   "api key" / AZURE_OPENAI_API_KEY   Azure OpenAI key
//   AZURE_OPENAI_DEPLOYMENT            deployment name (default: gpt-5.1)
//   AZURE_OPENAI_API_VERSION           optional; when set, the legacy
//                                      /openai/deployments/... path is used
//                                      instead of the version-less v1 API
//   HOST / PORT                        listen address (default: 0.0.0.0:3000)

import http from "node:http";
import { readFile } from "node:fs/promises";
import { networkInterfaces } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PUBLIC_DIR = path.join(__dirname, "public");

const env = process.env;
const ENDPOINT = (env.AZURE_OPENAI_ENDPOINT || env.endpoint || "").replace(/\/+$/, "");
const API_KEY = env.AZURE_OPENAI_API_KEY || env["api key"] || env.api_key || "";
const DEPLOYMENT = env.AZURE_OPENAI_DEPLOYMENT || "gpt-5.1";
const API_VERSION = env.AZURE_OPENAI_API_VERSION || ""; // empty = v1 API
const HOST = env.HOST || "0.0.0.0";
const PORT = Number(env.PORT || 3000);

const SYSTEM_PROMPT =
  env.SYSTEM_PROMPT ||
  "You are Vikingchat, a friendly and concise assistant. Answer in the user's language.";

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".json": "application/json; charset=utf-8",
  ".webmanifest": "application/manifest+json",
};

function sendJson(res, status, body) {
  const data = JSON.stringify(body);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
  });
  res.end(data);
}

async function readBody(req, limit = 1_000_000) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on("data", (c) => {
      size += c.length;
      if (size > limit) {
        reject(new Error("Request body too large"));
        req.destroy();
        return;
      }
      chunks.push(c);
    });
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}

function sanitizeMessages(input) {
  if (!Array.isArray(input)) return [];
  return input
    .filter((m) => m && (m.role === "user" || m.role === "assistant"))
    .map((m) => ({ role: m.role, content: String(m.content ?? "").slice(0, 8000) }))
    .slice(-40);
}

async function handleChat(req, res) {
  if (!ENDPOINT || !API_KEY) {
    return sendJson(res, 503, {
      error: "Server is not configured: set the endpoint and api key environment variables.",
    });
  }
  let payload;
  try {
    payload = JSON.parse((await readBody(req)) || "{}");
  } catch (err) {
    return sendJson(res, 400, { error: `Invalid request body: ${err.message}` });
  }
  const messages = sanitizeMessages(payload.messages);
  if (messages.length === 0 || messages.at(-1).role !== "user") {
    return sendJson(res, 400, { error: "Send at least one user message." });
  }

  // The v1 API takes the deployment as "model" in the body and needs no
  // api-version; it supports every current model (gpt-4o, gpt-5.x, o-series).
  const url = API_VERSION
    ? `${ENDPOINT}/openai/deployments/${encodeURIComponent(DEPLOYMENT)}/chat/completions?api-version=${encodeURIComponent(API_VERSION)}`
    : `${ENDPOINT}/openai/v1/chat/completions`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 60_000);
  try {
    const upstream = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "api-key": API_KEY },
      // No temperature: gpt-5.x and o-series reject non-default values.
      // max_completion_tokens replaces the deprecated max_tokens.
      body: JSON.stringify({
        model: DEPLOYMENT,
        messages: [{ role: "system", content: SYSTEM_PROMPT }, ...messages],
        max_completion_tokens: 1200,
      }),
      signal: controller.signal,
    });
    const text = await upstream.text();
    let data = {};
    try {
      data = JSON.parse(text);
    } catch {
      /* non-JSON upstream error body */
    }
    if (!upstream.ok) {
      const detail = data?.error?.message || text.slice(0, 300) || upstream.statusText;
      console.error(`[chat] upstream ${upstream.status}: ${detail}`);
      return sendJson(res, 502, { error: `Azure OpenAI error (${upstream.status}): ${detail}` });
    }
    const reply = data?.choices?.[0]?.message?.content ?? "";
    return sendJson(res, 200, { reply, usage: data.usage ?? null });
  } catch (err) {
    const msg = err.name === "AbortError" ? "Upstream request timed out." : err.message;
    console.error(`[chat] ${msg}`);
    return sendJson(res, 502, { error: msg });
  } finally {
    clearTimeout(timer);
  }
}

async function serveStatic(req, res, urlPath) {
  const rel = urlPath === "/" ? "/index.html" : decodeURIComponent(urlPath);
  const file = path.normalize(path.join(PUBLIC_DIR, rel));
  if (!file.startsWith(PUBLIC_DIR)) {
    res.writeHead(403);
    return res.end("Forbidden");
  }
  try {
    const data = await readFile(file);
    res.writeHead(200, {
      "Content-Type": MIME[path.extname(file)] || "application/octet-stream",
      "Cache-Control": "no-cache",
    });
    res.end(data);
  } catch {
    res.writeHead(404, { "Content-Type": "text/plain" });
    res.end("Not found");
  }
}

const server = http.createServer(async (req, res) => {
  const { pathname } = new URL(req.url, "http://localhost");
  if (pathname === "/api/health") {
    return sendJson(res, 200, {
      ok: true,
      configured: Boolean(ENDPOINT && API_KEY),
      deployment: DEPLOYMENT,
      apiVersion: API_VERSION || "v1",
    });
  }
  if (pathname === "/api/chat") {
    if (req.method !== "POST") {
      res.writeHead(405, { Allow: "POST" });
      return res.end();
    }
    return handleChat(req, res);
  }
  if (req.method !== "GET" && req.method !== "HEAD") {
    res.writeHead(405);
    return res.end();
  }
  return serveStatic(req, res, pathname);
});

function lanAddresses() {
  const out = [];
  for (const list of Object.values(networkInterfaces())) {
    for (const a of list || []) {
      if (a.family === "IPv4" && !a.internal) out.push(a.address);
    }
  }
  return out;
}

server.listen(PORT, HOST, () => {
  console.log(`Vikingchat listening on http://${HOST}:${PORT}`);
  if (HOST === "0.0.0.0" || HOST === "::") {
    for (const ip of lanAddresses()) console.log(`  on your network: http://${ip}:${PORT}`);
  }
  console.log(
    ENDPOINT && API_KEY
      ? `Azure OpenAI: ${ENDPOINT} (deployment "${DEPLOYMENT}", ${API_VERSION ? "api-version " + API_VERSION : "v1 API"})`
      : "WARNING: Azure OpenAI endpoint/api key not set; /api/chat will return 503."
  );
});
