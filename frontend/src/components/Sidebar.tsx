import { Session, SessionKind, User } from "../lib/api";

type Props = {
  user: User;
  sessions: Session[];
  activeId: string | null;
  healthy: boolean | null;
  onSelect: (id: string) => void;
  onCreate: (kind: SessionKind) => void;
  onDelete: (id: string) => void;
  onLogout: () => void;
};

export default function Sidebar({ user, sessions, activeId, healthy, onSelect, onCreate, onDelete, onLogout }: Props) {
  return (
    <aside className="sidebar">
      <header>
        <div className="brand" title={healthy === null ? "checking OpenViking…" : healthy ? "OpenViking connected" : "OpenViking unreachable"}>
          <span className={`dot ${healthy === false ? "bad" : ""}`} />
          VikingChat
        </div>
      </header>
      <div className="actions">
        <button className="primary" onClick={() => onCreate("chat")}>+ Chat</button>
        <button onClick={() => onCreate("skill")} title="Teach the agent a procedure, then build it into a reusable skill">
          + Skill session
        </button>
      </div>
      <div className="session-list">
        {sessions.length === 0 && <div className="hint" style={{ padding: 12, color: "var(--muted)", fontSize: 13 }}>No sessions yet.</div>}
        {sessions.map((s) => (
          <div key={s.id} className={`session-item ${s.id === activeId ? "active" : ""}`} onClick={() => onSelect(s.id)}>
            <span className={`kind ${s.kind}`}>{s.kind === "skill" ? (s.skill_name ? "built" : "skill") : "chat"}</span>
            <span className="title" title={s.title}>{s.title}</span>
            <button
              className="ghost del"
              title="Delete session"
              onClick={(e) => {
                e.stopPropagation();
                onDelete(s.id);
              }}
            >
              ×
            </button>
          </div>
        ))}
      </div>
      <footer>
        <span>@{user.username}</span>
        <button className="ghost" onClick={onLogout}>Sign out</button>
      </footer>
    </aside>
  );
}
