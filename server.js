// Vikingchat server
//
// Serves the mobile chat UI, stores chat transcripts on disk, proxies chat
// turns to Azure OpenAI, and uses OpenViking (https://github.com/volcengine/OpenViking)
// as the context database: conversation sessions, long-term memory about the
// user, and uploaded documents (viking://resources/uploads).
//
// Environment (see .env.example):
//   endpoint / AZURE_OPENAI_ENDPOINT        Azure OpenAI resource URL
//   "api key" / AZURE_OPENAI_API_KEY        Azure OpenAI key
//   AZURE_OPENAI_DEPLOYMENT                 chat deployment (default gpt-5.1)
//   AZURE_OPENAI_API_VERSION                optional; legacy versioned path
//   OPENVIKING_URL                          default http://127.0.0.1:1933
//   OPENVIKING_API_KEY                      OpenViking API key (root key)
//   OPENVIKING_USER                         user namespace (default "default")
//   DATA_DIR                                where chats are stored (default ./data)
//   HOST / PORT                             listen address (default 0.0.0.0:3000)

import http from "node:http";
import { readFile, writeFile, mkdir, readdir, unlink, rename } from "node:fs/promises";
import { Readable } from "node:stream";
import { randomUUID } from "node:crypto";
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
const DATA_DIR = path.resolve(env.DATA_DIR || path.join(__dirname, "data"));
const CHATS_DIR = path.join(DATA_DIR, "chats");

const OV_URL = (env.OPENVIKING_URL ?? "http://127.0.0.1:1933").replace(/\/+$/, "");
const OV_KEY = env.OPENVIKING_API_KEY || "";
const OV_USER = env.OPENVIKING_USER || "default";
const OV_ENABLED = Boolean(OV_URL) && env.OPENVIKING_DISABLED !== "1";
const UPLOADS_URI = "viking://resources/uploads";
const MEMORIES_URI = `viking://user/${OV_USER}/memories`;
const EXTRACT_DELAY_MS = Number(env.OPENVIKING_EXTRACT_DELAY_MS || 90_000);
const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;

const SYSTEM_PROMPT =
  env.SYSTEM_PROMPT ||
  "You are Vikingchat, a friendly and concise assistant. Answer in the user's language. " +
  "When the context below contains memories about the user, use them naturally without announcing them. " +
  "When it contains document excerpts, ground your answer in them and mention the document name you used.";

// ---------------------------------------------------------------------------
// Small helpers

const MIME = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
  ".ico": "image/x-icon", ".json": "application/json; charset=utf-8",
  ".webmanifest": "application/manifest+json",
};

class HttpError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

function sendJson(res, status, body) {
  res.writeHead(status, { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" });
  res.end(JSON.stringify(body));
}

async function readBody(req, limit = 1_000_000) {
  return new Promise((resolve, reject) => {
    let size = 0; const chunks = [];
    req.on("data", (c) => {
      size += c.length;
      if (size > limit) { reject(new HttpError(413, "Request body too large")); req.destroy(); return; }
      chunks.push(c);
    });
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}

async function readJson(req) {
  try { return JSON.parse((await readBody(req)) || "{}"); }
  catch (err) { if (err instanceof HttpError) throw err; throw new HttpError(400, `Invalid JSON body: ${err.message}`); }
}

const clip = (s, n) => (s.length > n ? s.slice(0, n - 1) + "…" : s);
const log = (...a) => console.log(new Date().toISOString(), ...a);

// ---------------------------------------------------------------------------
// Chat storage: one JSON file per chat plus an index, under DATA_DIR/chats.

let indexCache = null;
let indexLock = Promise.resolve();

async function ensureDirs() { await mkdir(CHATS_DIR, { recursive: true }); }

async function loadIndex() {
  if (indexCache) return indexCache;
  try { indexCache = JSON.parse(await readFile(path.join(CHATS_DIR, "index.json"), "utf8")); }
  catch { indexCache = []; }
  return indexCache;
}

function withIndex(fn) {
  const run = indexLock.then(async () => {
    const idx = await loadIndex();
    const out = await fn(idx);
    const tmp = path.join(CHATS_DIR, "index.json.tmp");
    await writeFile(tmp, JSON.stringify(idx));
    await rename(tmp, path.join(CHATS_DIR, "index.json"));
    return out;
  });
  indexLock = run.catch(() => {});
  return run;
}

const chatPath = (id) => path.join(CHATS_DIR, `${id}.json`);
const isValidId = (id) => /^[0-9a-f-]{8,64}$/i.test(id);

async function listChats() {
  const idx = await loadIndex();
  return [...idx].sort((a, b) => b.updatedAt - a.updatedAt);
}

async function loadChat(id) {
  if (!isValidId(id)) throw new HttpError(400, "Bad chat id");
  try { return JSON.parse(await readFile(chatPath(id), "utf8")); }
  catch { throw new HttpError(404, "Chat not found"); }
}

async function saveChat(chat) {
  chat.updatedAt = Date.now();
  await writeFile(chatPath(chat.id), JSON.stringify(chat));
  await withIndex((idx) => {
    const summary = { id: chat.id, title: chat.title, updatedAt: chat.updatedAt, createdAt: chat.createdAt, messageCount: chat.messages.length };
    const i = idx.findIndex((c) => c.id === chat.id);
    if (i >= 0) idx[i] = summary; else idx.push(summary);
  });
  return chat;
}

async function createChat() {
  const now = Date.now();
  const chat = { id: randomUUID(), title: "New chat", createdAt: now, updatedAt: now, messages: [] };
  await saveChat(chat);
  return chat;
}

async function deleteChat(id) {
  if (!isValidId(id)) throw new HttpError(400, "Bad chat id");
  await unlink(chatPath(id)).catch(() => {});
  await withIndex((idx) => { const i = idx.findIndex((c) => c.id === id); if (i >= 0) idx.splice(i, 1); });
}

// ---------------------------------------------------------------------------
// OpenViking client

async function ov(pathname, { method = "GET", body, timeout = 30_000 } = {}) {
  if (!OV_ENABLED) throw new HttpError(503, "OpenViking is not configured");
  const headers = {};
  if (OV_KEY) headers.Authorization = `Bearer ${OV_KEY}`;
  if (body !== undefined && !(body instanceof FormData)) { headers["Content-Type"] = "application/json"; body = JSON.stringify(body); }
  let r;
  try {
    r = await fetch(OV_URL + pathname, { method, headers, body, signal: AbortSignal.timeout(timeout) });
  } catch (err) {
    throw new HttpError(503, `OpenViking unreachable: ${err.name === "TimeoutError" ? "timed out" : err.message}`);
  }
  const text = await r.text();
  let data; try { data = JSON.parse(text); } catch { data = { raw: text }; }
  if (!r.ok || data.status === "error") {
    const msg = data?.error?.message || data?.detail || clip(text, 200) || r.statusText;
    throw new HttpError(r.status >= 500 ? 502 : r.status || 502, `OpenViking: ${msg}`);
  }
  return data.result !== undefined ? data.result : data;
}

let ovReadyCache = { at: 0, value: false, detail: "" };
async function ovReady() {
  if (!OV_ENABLED) return { ready: false, detail: "disabled" };
  if (Date.now() - ovReadyCache.at < 5_000) return { ready: ovReadyCache.value, detail: ovReadyCache.detail };
  let value = false, detail = "";
  try {
    const r = await fetch(`${OV_URL}/ready`, { signal: AbortSignal.timeout(3_000) });
    value = r.ok; detail = value ? "ready" : `HTTP ${r.status}`;
  } catch (err) { detail = err.name === "TimeoutError" ? "timed out" : "unreachable"; }
  ovReadyCache = { at: Date.now(), value, detail };
  return { ready: value, detail };
}

async function ensureSession(chatId) {
  try { await ov("/api/v1/sessions", { method: "POST", body: { session_id: chatId } }); }
  catch (err) { if (!/exist|409|ALREADY/i.test(err.message)) throw err; }
}

async function syncMessages(chatId, messages) {
  await ensureSession(chatId);
  await ov(`/api/v1/sessions/${encodeURIComponent(chatId)}/messages/batch`, {
    method: "POST",
    body: { messages: messages.map(({ role, content }) => ({ role, content })) },
  });
}

// Memory extraction is expensive (a model call), so run it once the chat has
// been quiet for a while instead of after every message.
const extractTimers = new Map();
function scheduleExtract(chatId) {
  clearTimeout(extractTimers.get(chatId));
  extractTimers.set(chatId, setTimeout(async () => {
    extractTimers.delete(chatId);
    try {
      await ov(`/api/v1/sessions/${encodeURIComponent(chatId)}/extract`, { method: "POST", body: {}, timeout: 120_000 });
      log(`[memory] extracted from chat ${chatId}`);
    } catch (err) { log(`[memory] extract failed for ${chatId}: ${err.message}`); }
  }, EXTRACT_DELAY_MS));
}

async function readContent(uri, maxChars) {
  try {
    const content = await ov(`/api/v1/content/read?uri=${encodeURIComponent(uri)}`);
    return clip(typeof content === "string" ? content : JSON.stringify(content), maxChars);
  } catch { return ""; }
}

// Pull relevant memories and document passages for a user message.
async function gatherContext(query) {
  const out = { memories: [], resources: [] };
  if (!OV_ENABLED || !(await ovReady()).ready) return out;
  const find = (body) => ov("/api/v1/search/find", { method: "POST", body, timeout: 15_000 }).catch((err) => {
    log(`[context] find failed: ${err.message}`); return null;
  });
  const [mem, res] = await Promise.all([
    find({ query, target_uri: MEMORIES_URI, context_type: ["memory"], limit: 6 }),
    find({ query, target_uri: UPLOADS_URI, context_type: ["resource"], limit: 4 }),
  ]);
  for (const m of mem?.memories ?? []) {
    const text = (await readContent(m.uri, 900)) || m.abstract || "";
    if (text) out.memories.push({ uri: m.uri, score: m.score, text });
  }
  for (const r of res?.resources ?? []) {
    const text = (await readContent(r.uri, 2500)) || r.abstract || "";
    if (text) out.resources.push({ uri: r.uri, score: r.score, name: displayName(r.uri), text });
  }
  return out;
}

function displayName(uri) {
  const rel = uri.replace(UPLOADS_URI + "/", "").replace(/\/+$/, "");
  return rel.split("/")[0] || rel;
}

function buildSystemPrompt(ctx) {
  let prompt = SYSTEM_PROMPT;
  if (ctx.memories.length) {
    prompt += "\n\n## What you remember about the user\n" + ctx.memories.map((m) => `- ${m.text.replace(/\s+/g, " ").trim()}`).join("\n");
  }
  if (ctx.resources.length) {
    prompt += "\n\n## Relevant excerpts from the user's documents\n" +
      ctx.resources.map((r, i) => `[${i + 1}] ${r.name}\n${r.text.trim()}`).join("\n\n");
  }
  return prompt;
}

// ---------------------------------------------------------------------------
// Azure OpenAI

async function askModel(systemPrompt, history) {
  if (!ENDPOINT || !API_KEY) throw new HttpError(503, "Server is not configured: set the Azure OpenAI endpoint and api key.");
  const url = API_VERSION
    ? `${ENDPOINT}/openai/deployments/${encodeURIComponent(DEPLOYMENT)}/chat/completions?api-version=${encodeURIComponent(API_VERSION)}`
    : `${ENDPOINT}/openai/v1/chat/completions`;
  let upstream;
  try {
    upstream = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "api-key": API_KEY },
      body: JSON.stringify({
        model: DEPLOYMENT,
        messages: [{ role: "system", content: systemPrompt }, ...history.map(({ role, content }) => ({ role, content }))],
        max_completion_tokens: 1200,
      }),
      signal: AbortSignal.timeout(90_000),
    });
  } catch (err) {
    throw new HttpError(502, err.name === "TimeoutError" ? "The model took too long to answer." : err.message);
  }
  const text = await upstream.text();
  let data = {}; try { data = JSON.parse(text); } catch { /* non-JSON error body */ }
  if (!upstream.ok) {
    const detail = data?.error?.message || clip(text, 300) || upstream.statusText;
    log(`[chat] upstream ${upstream.status}: ${detail}`);
    throw new HttpError(502, `Azure OpenAI error (${upstream.status}): ${detail}`);
  }
  return { reply: data?.choices?.[0]?.message?.content ?? "", usage: data.usage ?? null };
}

// ---------------------------------------------------------------------------
// Route handlers

async function handleTurn(req, res, chatId) {
  const { content } = await readJson(req);
  const text = String(content ?? "").trim().slice(0, 8000);
  if (!text) throw new HttpError(400, "Message is empty.");
  const chat = await loadChat(chatId);

  const userMsg = { role: "user", content: text, ts: Date.now() };
  chat.messages.push(userMsg);
  if (chat.messages.length === 1) chat.title = clip(text.replace(/\s+/g, " "), 48);

  const ctx = await gatherContext(text);
  const history = chat.messages.slice(-40);
  const { reply, usage } = await askModel(buildSystemPrompt(ctx), history);
  const sources = ctx.resources.map((r) => ({ name: r.name, uri: r.uri }));
  const assistantMsg = { role: "assistant", content: reply || "(empty reply)", ts: Date.now(), sources };
  chat.messages.push(assistantMsg);
  await saveChat(chat);

  if (OV_ENABLED) {
    syncMessages(chat.id, [userMsg, assistantMsg])
      .then(() => scheduleExtract(chat.id))
      .catch((err) => log(`[session] sync failed for ${chat.id}: ${err.message}`));
  }
  sendJson(res, 200, { chat: { id: chat.id, title: chat.title, updatedAt: chat.updatedAt }, message: assistantMsg, usage, usedMemories: ctx.memories.length });
}

async function handleUpload(req, res) {
  const len = Number(req.headers["content-length"] || 0);
  if (len > MAX_UPLOAD_BYTES) throw new HttpError(413, "File is larger than 25 MB.");
  const webReq = new Request("http://local/upload", { method: "POST", headers: req.headers, body: Readable.toWeb(req), duplex: "half" });
  let form;
  try { form = await webReq.formData(); } catch (err) { throw new HttpError(400, `Could not read upload: ${err.message}`); }
  const file = form.get("file");
  if (!file || typeof file === "string") throw new HttpError(400, "Send the document as a 'file' form field.");
  if (file.size > MAX_UPLOAD_BYTES) throw new HttpError(413, "File is larger than 25 MB.");

  const safeName = file.name.replace(/[^\w.\- ()\[\]]+/g, "_").trim() || "document";
  const folder = safeName.replace(/\.[^.]+$/, "").replace(/\s+/g, "-").toLowerCase() || "document";

  const fd = new FormData();
  fd.append("file", file, safeName);
  const temp = await ov("/api/v1/resources/temp_upload", { method: "POST", body: fd, timeout: 120_000 });
  const tempId = temp.temp_file_id || temp.id || temp.path;
  if (!tempId) throw new HttpError(502, "OpenViking did not return a temp_file_id.");

  const task = await ov("/api/v1/resources", {
    method: "POST",
    body: { temp_file_id: tempId, to: `${UPLOADS_URI}/${folder}`, reason: `Uploaded from Vikingchat: ${safeName}` },
    timeout: 120_000,
  });
  log(`[docs] queued ${safeName} -> ${UPLOADS_URI}/${folder} (task ${task.task_id})`);
  sendJson(res, 202, { taskId: task.task_id, name: safeName, uri: `${UPLOADS_URI}/${folder}` });
}

async function listDocuments() {
  let entries;
  try { entries = await ov(`/api/v1/fs/ls?uri=${encodeURIComponent(UPLOADS_URI + "/")}&sort_by=mtime`); }
  catch (err) { if (err.status === 404) return []; throw err; }
  return (Array.isArray(entries) ? entries : entries?.entries ?? []).map((e) => ({
    name: e.name, uri: e.uri, isDir: e.isDir ?? e.is_dir ?? false, size: e.size ?? null, modified: e.modTime ?? e.mtime ?? null,
  }));
}

async function memoryOverview() {
  let entries = [];
  try {
    const r = await ov(`/api/v1/fs/ls?uri=${encodeURIComponent(MEMORIES_URI + "/")}&recursive=true&limit=200`);
    entries = Array.isArray(r) ? r : r?.entries ?? [];
  } catch (err) { if (err.status !== 404) throw err; }
  const files = entries.filter((e) => !(e.isDir ?? e.is_dir)).slice(0, 40);
  const items = [];
  for (const f of files) {
    const text = await readContent(f.uri, 600);
    if (text) items.push({ name: f.name, uri: f.uri, category: f.uri.replace(MEMORIES_URI + "/", "").split("/")[0].replace(/\.md$/, ""), text });
  }
  return { user: OV_USER, count: files.length, items };
}

async function serveStatic(req, res, urlPath) {
  const rel = urlPath === "/" ? "/index.html" : decodeURIComponent(urlPath);
  const file = path.normalize(path.join(PUBLIC_DIR, rel));
  if (!file.startsWith(PUBLIC_DIR)) { res.writeHead(403); return res.end("Forbidden"); }
  try {
    const data = await readFile(file);
    res.writeHead(200, { "Content-Type": MIME[path.extname(file)] || "application/octet-stream", "Cache-Control": "no-cache" });
    res.end(data);
  } catch { res.writeHead(404, { "Content-Type": "text/plain" }); res.end("Not found"); }
}

async function route(req, res) {
  const url = new URL(req.url, "http://localhost");
  const p = url.pathname, m = req.method;
  let match;

  if (p === "/api/health") {
    const ready = await ovReady();
    return sendJson(res, 200, {
      ok: true, configured: Boolean(ENDPOINT && API_KEY), deployment: DEPLOYMENT, apiVersion: API_VERSION || "v1",
      openviking: { enabled: OV_ENABLED, ready: ready.ready, detail: ready.detail, user: OV_USER },
    });
  }
  if (p === "/api/chats" && m === "GET") return sendJson(res, 200, { chats: await listChats() });
  if (p === "/api/chats" && m === "POST") return sendJson(res, 201, { chat: await createChat() });
  if ((match = p.match(/^\/api\/chats\/([^/]+)$/))) {
    const id = match[1];
    if (m === "GET") return sendJson(res, 200, { chat: await loadChat(id) });
    if (m === "DELETE") {
      await deleteChat(id);
      if (OV_ENABLED) ov(`/api/v1/sessions/${encodeURIComponent(id)}`, { method: "DELETE" }).catch(() => {});
      return sendJson(res, 200, { ok: true });
    }
  }
  if ((match = p.match(/^\/api\/chats\/([^/]+)\/messages$/)) && m === "POST") return handleTurn(req, res, match[1]);
  if (p === "/api/memory" && m === "GET") return sendJson(res, 200, await memoryOverview());
  if (p === "/api/documents" && m === "GET") return sendJson(res, 200, { documents: await listDocuments() });
  if (p === "/api/documents" && m === "POST") return handleUpload(req, res);
  if (p === "/api/documents" && m === "DELETE") {
    const uri = url.searchParams.get("uri") || "";
    if (!uri.startsWith(UPLOADS_URI + "/")) throw new HttpError(400, "Only uploaded documents can be deleted.");
    await ov(`/api/v1/fs?uri=${encodeURIComponent(uri)}&recursive=true`, { method: "DELETE" });
    return sendJson(res, 200, { ok: true });
  }
  if ((match = p.match(/^\/api\/documents\/tasks\/([\w-]+)$/)) && m === "GET") {
    const t = await ov(`/api/v1/tasks/${encodeURIComponent(match[1])}`);
    return sendJson(res, 200, { status: t.status, rootUri: t.result?.root_uri ?? t.root_uri ?? null, error: t.error ?? null });
  }
  if (p.startsWith("/api/")) throw new HttpError(404, "No such API route");
  if (m !== "GET" && m !== "HEAD") { res.writeHead(405); return res.end(); }
  return serveStatic(req, res, p);
}

const server = http.createServer((req, res) => {
  route(req, res).catch((err) => {
    const status = err instanceof HttpError ? err.status : 500;
    if (status >= 500) log(`[error] ${req.method} ${req.url}: ${err.stack || err.message}`);
    if (!res.headersSent) sendJson(res, status, { error: err.message || "Internal error" });
    else res.end();
  });
});

function lanAddresses() {
  const out = [];
  for (const list of Object.values(networkInterfaces())) for (const a of list || []) if (a.family === "IPv4" && !a.internal) out.push(a.address);
  return out;
}

await ensureDirs();
server.listen(PORT, HOST, async () => {
  log(`Vikingchat listening on http://${HOST}:${PORT}`);
  if (HOST === "0.0.0.0" || HOST === "::") for (const ip of lanAddresses()) log(`  on your network: http://${ip}:${PORT}`);
  log(ENDPOINT && API_KEY
    ? `Azure OpenAI: ${ENDPOINT} (deployment "${DEPLOYMENT}", ${API_VERSION ? "api-version " + API_VERSION : "v1 API"})`
    : "WARNING: Azure OpenAI endpoint/api key not set; chat will return 503.");
  log(`Chats stored in ${CHATS_DIR}`);
  if (OV_ENABLED) { const r = await ovReady(); log(`OpenViking: ${OV_URL} (${r.detail}${OV_KEY ? ", key set" : ", NO key"})`); }
  else log("OpenViking: disabled (no memory or documents)");
});
