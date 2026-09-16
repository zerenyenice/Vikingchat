import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import { api, ApiError, Message, Session, streamMessage, ToolEvent } from "../lib/api";
import BuildSkillDialog from "./BuildSkillDialog";
import MessageBubble from "./MessageBubble";

type Props = {
  session: Session;
  onSessionChanged: (s: Session) => void;
  onSkillBuilt: () => void;
  onToggleRight: () => void;
};

export default function ChatView({ session, onSessionChanged, onSkillBuilt, onToggleRight }: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [showBuild, setShowBuild] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    let cancelled = false;
    setMessages([]);
    setError(null);
    api
      .getSession(session.id)
      .then((s) => {
        if (!cancelled) setMessages(s.messages);
      })
      .catch((err) => !cancelled && setError(String(err)));
    return () => {
      cancelled = true;
      abortRef.current?.abort();
    };
  }, [session.id]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function send(e?: FormEvent) {
    e?.preventDefault();
    const content = input.trim();
    if (!content || sending) return;
    setInput("");
    setSending(true);
    setError(null);
    const pendingId = `pending-${Date.now()}`;
    setMessages((prev) => [
      ...prev,
      { id: `${pendingId}-u`, role: "user", content },
      { id: pendingId, role: "assistant", content: "", streaming: true, toolEvents: [] },
    ]);
    const controller = new AbortController();
    abortRef.current = controller;
    const patch = (fn: (m: Message) => Message) =>
      setMessages((prev) => prev.map((m) => (m.id === pendingId ? fn(m) : m)));
    try {
      for await (const ev of streamMessage(session.id, content, controller.signal)) {
        if (ev.type === "user_message") {
          setMessages((prev) => prev.map((m) => (m.id === `${pendingId}-u` ? { ...ev.message } : m)));
        } else if (ev.type === "token") {
          patch((m) => ({ ...m, content: m.content + ev.text }));
        } else if (ev.type === "tool_call" || ev.type === "tool_result") {
          const { type, id, name, args, content: c, status } = ev;
          const toolEvent: ToolEvent = { type, id, name, args, content: c, status };
          patch((m) => ({ ...m, toolEvents: [...(m.toolEvents ?? []), toolEvent] }));
        } else if (ev.type === "done") {
          patch((m) => ({ ...ev.message, toolEvents: m.toolEvents, streaming: false }));
        } else if (ev.type === "error") {
          patch((m) => ({ ...m, content: m.content || `The agent run failed: ${ev.message}`, streaming: false, meta: { error: true } }));
          setError(ev.message);
        }
      }
      const fresh = await api.listSessions();
      const updated = fresh.find((s) => s.id === session.id);
      if (updated) onSessionChanged(updated);
    } catch (err) {
      if (!(err instanceof DOMException && err.name === "AbortError")) {
        const msg = err instanceof ApiError ? err.message : String(err);
        setError(msg);
        patch((m) => ({ ...m, streaming: false, content: m.content || `Request failed: ${msg}`, meta: { error: true } }));
      }
    } finally {
      setSending(false);
      abortRef.current = null;
    }
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  }

  async function saveMemory() {
    setNotice(null);
    setError(null);
    try {
      await api.commitSession(session.id);
      setNotice("OpenViking is archiving this session and extracting memories.");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  const isSkill = session.kind === "skill";
  const hasUserMessages = messages.some((m) => m.role === "user");

  return (
    <section className="main">
      <header>
        <h1 title={session.title}>{session.title}</h1>
        <span className={`badge ${isSkill ? "skill" : ""}`}>{isSkill ? "skill session" : "chat"}</span>
        {session.skill_name && <span className="badge ok" title={session.skill_uri ?? ""}>skill: {session.skill_name}</span>}
        <button onClick={saveMemory} disabled={!hasUserMessages || sending} title="Archive this session in OpenViking now and extract long-term memories">
          Save memories
        </button>
        {isSkill && (
          <button className="primary" onClick={() => setShowBuild(true)} disabled={!hasUserMessages || sending}>
            {session.skill_name ? "Rebuild skill" : "Build skill"}
          </button>
        )}
        <button className="ghost" onClick={onToggleRight} title="Toggle side panel">☰</button>
      </header>
      <div className="messages">
        {messages.length === 0 && (
          <div className="empty">
            {isSkill ? (
              <>
                <h2>Teach the agent a procedure</h2>
                Describe the task, walk through it together, correct the agent as you go. When it looks right, click <b>Build skill</b> and the
                session becomes a reusable skill stored in OpenViking.
              </>
            ) : (
              <>
                <h2>Ask anything</h2>
                The agent remembers your preferences, can search your uploaded documents and applies the skills you have built. Say
                "remember that…" to store something for later.
              </>
            )}
          </div>
        )}
        {messages.map((m) => (
          <MessageBubble key={m.id} message={m} />
        ))}
        <div ref={bottomRef} />
      </div>
      {(error || notice) && (
        <div style={{ padding: "0 20px" }}>
          {error && <div className="notice error">{error}</div>}
          {notice && <div className="notice ok">{notice}</div>}
        </div>
      )}
      <form className="composer" onSubmit={send}>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKey}
          placeholder={isSkill ? "Explain the procedure you want to turn into a skill…" : "Message the agent… (Enter to send, Shift+Enter for newline)"}
          rows={2}
          disabled={sending}
        />
        {sending ? (
          <button type="button" onClick={() => abortRef.current?.abort()}>Stop</button>
        ) : (
          <button type="submit" className="primary" disabled={!input.trim()}>Send</button>
        )}
      </form>
      {showBuild && (
        <BuildSkillDialog
          session={session}
          onClose={() => setShowBuild(false)}
          onBuilt={(built) => {
            onSessionChanged(built.session);
            onSkillBuilt();
            api.getSession(session.id).then((s) => setMessages(s.messages)).catch(() => undefined);
          }}
        />
      )}
    </section>
  );
}
