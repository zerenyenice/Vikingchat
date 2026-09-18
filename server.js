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
//   OPENVIKING_ACCOUNT / OPENVIKING_USER    identity asserted to OpenViking (default "default")
//   AGENT_URL                               deep agent service (default http://127.0.0.1:8100)
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
const OV_ACCOUNT = env.OPENVIKING_ACCOUNT || "default";
const OV_ENABLED = Boolean(OV_URL) && env.OPENVIKING_DISABLED !== "1";
const UPLOADS_URI = "viking://resources/uploads";
const MEMORIES_URI = `viking://user/${OV_USER}/memories`;
const AGENT_URL = (env.AGENT_URL ?? "http://127.0.0.1:8100").replace(/\/+$/, "");
const AGENT_ENABLED = Boolean(AGENT_URL) && env.AGENT_DISABLED !== "1";
const MAX_UPLOAD_BYTES = Number(env.MAX_UPLOAD_MB || 10) * 1024 * 1024;

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
  // Trusted mode: the root key authenticates us, these headers say who we act as.
  const headers = { "X-OpenViking-Account": OV_ACCOUNT, "X-OpenViking-User": OV_USER };
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
    value = r.ok;
    if (!value) { const body = clip((await r.text().catch(() => "")).replace(/\s+/g, " "), 300); detail = `not ready (HTTP ${r.status}${body ? ": " + body : ""})`; }
    else detail = "ready";
  } catch (err) { detail = err.name === "TimeoutError" ? "timed out" : "unreachable (process not listening)"; }
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

// Deep agent service (agent/server.py): owns memory and document lookup.
let agentReadyCache = { at: 0, value: null };
async function agentHealth() {
  if (!AGENT_ENABLED) return { enabled: false, ready: false };
  // Cache a ready answer for a few seconds; re-check quickly while it is starting.
  const ttl = agentReadyCache.value?.ready ? 5_000 : 1_000;
  if (Date.now() - agentReadyCache.at < ttl && agentReadyCache.value) return agentReadyCache.value;
  let value;
  try {
    const r = await fetch(`${AGENT_URL}/health`, { signal: AbortSignal.timeout(3_000) });
    const h = await r.json();
    value = { enabled: true, ready: Boolean(h.ready), model: h.model, openviking: h.openviking, error: h.error ?? null };
  } catch (err) { value = { enabled: true, ready: false, error: err.name === "TimeoutError" ? "timed out" : "unreachable" }; }
  agentReadyCache = { at: Date.now(), value };
  return value;
}

async function runAgent(chat) {
  let r;
  try {
    r = await fetch(`${AGENT_URL}/chat`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: chat.id, messages: chat.messages.slice(-40).map(({ role, content }) => ({ role, content })) }),
      signal: AbortSignal.timeout(240_000),
    });
  } catch (err) {
    throw new HttpError(503, err.name === "TimeoutError" ? "The agent took too long to answer." : `Agent unreachable: ${err.message}`);
  }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new HttpError(r.status === 400 ? 400 : 502, data.error || `Agent error (${r.status})`);
  return data;
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

  let assistantMsg, usage = null, usedMemories = 0, via = "agent";
  const agent = AGENT_ENABLED ? await agentHealth() : { ready: false };
  if (agent.ready) {
    try {
      const out = await runAgent(chat);
      assistantMsg = { role: "assistant", content: out.reply || "(empty reply)", ts: Date.now(), sources: out.sources ?? [],
        memoryUpdated: Boolean(out.memory_updated), toolCalls: (out.tool_calls ?? []).map((t) => t.name) };
    } catch (err) {
      if (err.status === 400) { chat.messages.pop(); throw err; }
      log(`[agent] failed, falling back to direct model call: ${err.message}`);
    }
  }
  if (!assistantMsg) {
    // Fallback: plain model call with retrieved context (no agentic memory writes).
    via = "direct";
    const ctx = await gatherContext(text);
    const out = await askModel(buildSystemPrompt(ctx), chat.messages.slice(-40));
    usage = out.usage; usedMemories = ctx.memories.length;
    assistantMsg = { role: "assistant", content: out.reply || "(empty reply)", ts: Date.now(), sources: ctx.resources.map((r) => ({ name: r.name, uri: r.uri })) };
  }
  chat.messages.push(assistantMsg);
  await saveChat(chat);

  // Mirror the conversation into the OpenViking session (history + session-aware search).
  if (OV_ENABLED) syncMessages(chat.id, [userMsg, assistantMsg]).catch((err) => log(`[session] sync failed for ${chat.id}: ${err.message}`));
  sendJson(res, 200, { chat: { id: chat.id, title: chat.title, updatedAt: chat.updatedAt }, message: assistantMsg, usage, usedMemories, via });
}

async function handleUpload(req, res) {
  const len = Number(req.headers["content-length"] || 0);
  if (len > MAX_UPLOAD_BYTES) throw new HttpError(413, `File is larger than ${MAX_UPLOAD_BYTES / 1048576} MB.`);
  const webReq = new Request("http://local/upload", { method: "POST", headers: req.headers, body: Readable.toWeb(req), duplex: "half" });
  let form;
  try { form = await webReq.formData(); } catch (err) { throw new HttpError(400, `Could not read upload: ${err.message}`); }
  const file = form.get("file");
  if (!file || typeof file === "string") throw new HttpError(400, "Send the document as a 'file' form field.");
  if (file.size > MAX_UPLOAD_BYTES) throw new HttpError(413, `File is larger than ${MAX_UPLOAD_BYTES / 1048576} MB.`);

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
    name: e.name || String(e.uri || "").replace(/\/+$/, "").split("/").pop() || "document",
    uri: e.uri, isDir: e.isDir ?? e.is_dir ?? false, size: e.size ?? null, modified: e.modTime ?? e.mtime ?? null,
  }));
}

async function memoryOverview() {
  let entries = [];
  try {
    const r = await ov(`/api/v1/fs/ls?uri=${encodeURIComponent(MEMORIES_URI + "/")}&recursive=true&limit=200`);
    entries = Array.isArray(r) ? r : r?.entries ?? [];
  } catch (err) { if (err.status !== 404) throw err; }
  // Hide OpenViking's seeded persona files; they are not facts about the user.
  const files = entries.filter((e) => !(e.isDir ?? e.is_dir) && !/\/(identity|soul)\.md$/.test(e.uri || "")).slice(0, 40);
  const items = [];
  for (const f of files) {
    const text = await readContent(f.uri, 600);
    if (text) items.push({ name: f.name, uri: f.uri, category: f.uri.replace(MEMORIES_URI + "/", "").split("/")[0].replace(/\.md$/, ""), text });
  }
  return { user: OV_USER, count: files.length, items };
}

// Diagnostics for remote debugging: readiness of each process, memory, and
// the tail of the OpenViking / agent logs written by docker/start.sh.
async function tailFile(file, lines = 80) {
  try {
    const text = await readFile(file, "utf8");
    const arr = text.split("\n");
    return arr.slice(-lines).join("\n");
  } catch { return null; }
}
async function probe(url, timeout = 3_000) {
  try {
    const r = await fetch(url, { signal: AbortSignal.timeout(timeout) });
    return { status: r.status, body: clip((await r.text().catch(() => "")).replace(/\s+/g, " "), 600) };
  } catch (err) { return { error: err.name === "TimeoutError" ? "timed out" : err.message }; }
}
async function diagnostics() {
  const logDir = env.LOG_DIR || path.join(path.dirname(DATA_DIR), "logs");
  let meminfo = null;
  try {
    const mi = await readFile("/proc/meminfo", "utf8");
    const pick = (k) => Number((mi.match(new RegExp(`^${k}:\\s+(\\d+)`, "m")) || [])[1] || 0);
    meminfo = { totalMB: Math.round(pick("MemTotal") / 1024), availableMB: Math.round(pick("MemAvailable") / 1024) };
  } catch {}
  let cgroup = null;
  try {
    const [limit, usage] = await Promise.all([readFile("/sys/fs/cgroup/memory.max", "utf8"), readFile("/sys/fs/cgroup/memory.current", "utf8")]);
    cgroup = { limitMB: limit.trim() === "max" ? null : Math.round(Number(limit) / 1048576), usageMB: Math.round(Number(usage) / 1048576) };
  } catch {}
  const [ovHealth, ovReadyProbe, agent, ovLog, agentLog] = await Promise.all([
    OV_ENABLED ? probe(`${OV_URL}/health`) : null,
    OV_ENABLED ? probe(`${OV_URL}/ready`) : null,
    AGENT_ENABLED ? probe(`${AGENT_URL}/health`) : null,
    tailFile(path.join(logDir, "openviking.log")),
    tailFile(path.join(logDir, "agent.log")),
  ]);
  return {
    time: new Date().toISOString(), startedAt: env.STARTED_AT ?? null, nodeUptimeSec: Math.round(process.uptime()),
    config: { deployment: DEPLOYMENT, apiVersion: API_VERSION || "v1", endpointSet: Boolean(ENDPOINT), keySet: Boolean(API_KEY),
      openvikingUrl: OV_URL, openvikingKeySet: Boolean(OV_KEY), agentUrl: AGENT_URL, dataDir: DATA_DIR, logDir },
    memory: { system: meminfo, container: cgroup, nodeRssMB: Math.round(process.memoryUsage().rss / 1048576) },
    openviking: { health: ovHealth, ready: ovReadyProbe }, agent,
    logs: { openviking: ovLog ?? "(no log file)", agent: agentLog ?? "(no log file)" },
  };
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
    const [ready, agent] = await Promise.all([ovReady(), agentHealth()]);
    return sendJson(res, 200, {
      ok: true, configured: Boolean(ENDPOINT && API_KEY), deployment: DEPLOYMENT, apiVersion: API_VERSION || "v1",
      openviking: { enabled: OV_ENABLED, ready: ready.ready, detail: ready.detail, user: OV_USER },
      agent,
    });
  }
  if (p === "/api/diagnostics" && m === "GET") return sendJson(res, 200, await diagnostics());
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
  if (AGENT_ENABLED) { const a = await agentHealth(); log(`Agent service: ${AGENT_URL} (${a.ready ? "ready" : a.error || "starting"})`); }
  else log("Agent service: disabled (direct model calls only)");
});
