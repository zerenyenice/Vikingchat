import { useCallback, useEffect, useState } from "react";
import ChatView from "./components/ChatView";
import DocumentsPanel from "./components/DocumentsPanel";
import Login from "./components/Login";
import MemoryPanel from "./components/MemoryPanel";
import Sidebar from "./components/Sidebar";
import SkillsPanel from "./components/SkillsPanel";
import { api, getToken, Session, SessionKind, setToken, User } from "./lib/api";

type Tab = "documents" | "skills" | "memory";

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(true);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("documents");
  const [showRight, setShowRight] = useState(true);
  const [healthy, setHealthy] = useState<boolean | null>(null);
  const [skillsVersion, setSkillsVersion] = useState(0);

  useEffect(() => {
    if (!getToken()) {
      setChecking(false);
      return;
    }
    api
      .me()
      .then(setUser)
      .catch(() => setToken(null))
      .finally(() => setChecking(false));
  }, []);

  useEffect(() => {
    api.health().then((h) => setHealthy(h.openviking.healthy)).catch(() => setHealthy(false));
  }, [user]);

  const loadSessions = useCallback(async () => {
    const list = await api.listSessions();
    setSessions(list);
    setActiveId((current) => current ?? list[0]?.id ?? null);
  }, []);

  useEffect(() => {
    if (user) void loadSessions();
  }, [user, loadSessions]);

  async function createSession(kind: SessionKind) {
    const s = await api.createSession(kind);
    setSessions((prev) => [s, ...prev]);
    setActiveId(s.id);
  }

  async function deleteSession(id: string) {
    if (!confirm("Delete this session?")) return;
    await api.deleteSession(id);
    setSessions((prev) => prev.filter((s) => s.id !== id));
    setActiveId((current) => (current === id ? null : current));
  }

  function logout() {
    setToken(null);
    setUser(null);
    setSessions([]);
    setActiveId(null);
  }

  if (checking) return <div className="auth">Loading…</div>;
  if (!user) return <Login onLogin={setUser} />;

  const active = sessions.find((s) => s.id === activeId) ?? null;

  return (
    <div className={`layout ${showRight ? "show-right" : ""}`}>
      <Sidebar
        user={user}
        sessions={sessions}
        activeId={activeId}
        healthy={healthy}
        onSelect={setActiveId}
        onCreate={(kind) => void createSession(kind)}
        onDelete={(id) => void deleteSession(id)}
        onLogout={logout}
      />
      {active ? (
        <ChatView
          key={active.id}
          session={active}
          onSessionChanged={(s) => setSessions((prev) => prev.map((x) => (x.id === s.id ? s : x)))}
          onSkillBuilt={() => setSkillsVersion((v) => v + 1)}
          onToggleRight={() => setShowRight((v) => !v)}
        />
      ) : (
        <section className="main">
          <div className="empty">
            <h2>Welcome, {user.username}</h2>
            Start a <b>chat</b> to talk to the agent, or a <b>skill session</b> to teach it a procedure it can reuse later.
            {healthy === false && (
              <div className="notice error" style={{ marginTop: 16 }}>
                OpenViking is not reachable. Memory, documents and skills will not work until it is running.
              </div>
            )}
          </div>
        </section>
      )}
      <aside className="right">
        <div className="tabs">
          {(["documents", "skills", "memory"] as Tab[]).map((t) => (
            <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>
              {t[0].toUpperCase() + t.slice(1)}
            </button>
          ))}
        </div>
        {tab === "documents" && <DocumentsPanel />}
        {tab === "skills" && <SkillsPanel version={skillsVersion} />}
        {tab === "memory" && <MemoryPanel version={skillsVersion} />}
      </aside>
    </div>
  );
}
