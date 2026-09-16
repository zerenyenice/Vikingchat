export type User = { id: string; username: string };
export type SessionKind = "chat" | "skill";
export type Session = {
  id: string;
  title: string;
  kind: SessionKind;
  created_at: string;
  updated_at: string;
  skill_name: string | null;
  skill_uri: string | null;
  skill_built_at: string | null;
};
export type ToolCall = { name: string; args?: unknown };
export type Message = {
  id: number | string;
  role: "user" | "assistant";
  content: string;
  meta?: { tool_calls?: ToolCall[]; error?: boolean; skill_built?: { name: string; uri: string } };
  created_at?: string;
  streaming?: boolean;
  toolEvents?: ToolEvent[];
};
export type ToolEvent = {
  type: "tool_call" | "tool_result";
  id?: string;
  name?: string;
  args?: unknown;
  content?: string;
  status?: string;
};
export type Document = {
  id: string;
  filename: string;
  uri: string;
  size: number;
  status: string;
  task_id: string | null;
  created_at: string;
  overview?: string;
};
export type Skill = { name: string; description: string; uri?: string; [key: string]: unknown };
export type MemoryItem = { uri: string; abstract?: string; kind?: string; score?: number; [key: string]: unknown };

const TOKEN_KEY = "vikingchat.token";

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}
export function setToken(token: string | null) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const res = await fetch(path, { ...init, headers });
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  const data = text ? safeJson(text) : null;
  if (!res.ok) {
    const detail = (data && (data.detail ?? data.message)) || text || res.statusText;
    throw new ApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data as T;
}

function safeJson(text: string): any {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export const api = {
  health: () => request<{ status: string; model: string; openviking: { healthy: boolean; url: string; error?: string } }>("/api/health"),
  register: (username: string, password: string) =>
    request<{ access_token: string; user: User }>("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  login: (username: string, password: string) => {
    const body = new URLSearchParams({ username, password });
    return request<{ access_token: string; user: User }>("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
    });
  },
  me: () => request<User>("/api/auth/me"),

  listSessions: () => request<Session[]>("/api/sessions"),
  createSession: (kind: SessionKind, title?: string) =>
    request<Session>("/api/sessions", { method: "POST", body: JSON.stringify({ kind, title }) }),
  getSession: (id: string) => request<Session & { messages: Message[] }>(`/api/sessions/${id}`),
  renameSession: (id: string, title: string) =>
    request<Session>(`/api/sessions/${id}`, { method: "PATCH", body: JSON.stringify({ title }) }),
  deleteSession: (id: string) => request<void>(`/api/sessions/${id}`, { method: "DELETE" }),
  commitSession: (id: string) => request<{ status: string }>(`/api/sessions/${id}/commit`, { method: "POST" }),
  buildSkill: (id: string, body: { name?: string; notes?: string }) =>
    request<{ name: string; uri: string; description: string; tags: string[]; skill_md: string; session: Session }>(
      `/api/sessions/${id}/build-skill`,
      { method: "POST", body: JSON.stringify(body) },
    ),

  listDocuments: (refresh = false) => request<Document[]>(`/api/documents${refresh ? "?refresh=true" : ""}`),
  uploadDocument: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<Document>("/api/documents", { method: "POST", body: form });
  },
  getDocument: (id: string) => request<Document>(`/api/documents/${id}`),
  deleteDocument: (id: string) => request<void>(`/api/documents/${id}`, { method: "DELETE" }),

  listSkills: () => request<Skill[]>("/api/skills"),
  getSkill: (name: string) => request<Skill & { content?: string }>(`/api/skills/${encodeURIComponent(name)}`),
  deleteSkill: (name: string) => request<void>(`/api/skills/${encodeURIComponent(name)}`, { method: "DELETE" }),

  memory: (query?: string) =>
    request<{ mode: "browse" | "search"; items: MemoryItem[] }>(
      `/api/memory${query ? `?query=${encodeURIComponent(query)}` : ""}`,
    ),
  readMemory: (uri: string) => request<{ uri: string; content: string }>(`/api/memory/read?uri=${encodeURIComponent(uri)}`),
  deleteMemory: (uri: string) => request<void>(`/api/memory?uri=${encodeURIComponent(uri)}`, { method: "DELETE" }),
};

export type StreamEvent =
  | { type: "user_message"; message: Message }
  | { type: "token"; text: string }
  | ({ type: "tool_call" } & ToolEvent)
  | ({ type: "tool_result" } & ToolEvent)
  | { type: "done"; message: Message }
  | { type: "error"; message: string };

/** POST a message and yield SSE events as they arrive. */
export async function* streamMessage(sessionId: string, content: string, signal?: AbortSignal): AsyncGenerator<StreamEvent> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(`/api/sessions/${sessionId}/messages`, {
    method: "POST",
    headers,
    body: JSON.stringify({ content }),
    signal,
  });
  if (!res.ok || !res.body) {
    const text = await res.text();
    throw new ApiError(res.status, text || res.statusText);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      for (const line of frame.split("\n")) {
        if (line.startsWith("data: ")) {
          try {
            yield JSON.parse(line.slice(6)) as StreamEvent;
          } catch {
            /* ignore malformed frame */
          }
        }
      }
    }
  }
}
