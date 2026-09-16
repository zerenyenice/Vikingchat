import { FormEvent, useState } from "react";
import { api, ApiError, Session } from "../lib/api";

type Built = { name: string; uri: string; description: string; tags: string[]; skill_md: string; session: Session };

export default function BuildSkillDialog({ session, onClose, onBuilt }: { session: Session; onClose: () => void; onBuilt: (b: Built) => void }) {
  const [name, setName] = useState(session.skill_name ?? "");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Built | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const built = await api.buildSkill(session.id, { name: name || undefined, notes: notes || undefined });
      setResult(built);
      onBuilt(built);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        {result ? (
          <>
            <h2>Skill built: {result.name}</h2>
            <div className="hint">Stored in OpenViking at <code>{result.uri}</code>. The agent will load it whenever a matching task comes up.</div>
            <pre>
              <code>{result.skill_md}</code>
            </pre>
            <div className="footer">
              <button className="primary" onClick={onClose}>Done</button>
            </div>
          </>
        ) : (
          <form onSubmit={submit} style={{ display: "contents" }}>
            <h2>{session.skill_name ? "Rebuild skill" : "Build skill from this session"}</h2>
            <div className="hint">
              The whole conversation is distilled into a <code>SKILL.md</code> (name, description, step-by-step instructions) and stored
              in OpenViking under <code>viking://agent/skills/</code>. {session.skill_name && <>This will update <code>{session.skill_name}</code>.</>}
            </div>
            <label>
              <div className="hint">Skill name (optional, kebab-case)</div>
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. weekly-status-report" />
            </label>
            <label>
              <div className="hint">Extra guidance for the builder (optional)</div>
              <textarea rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Anything to emphasise or leave out" />
            </label>
            {error && <div className="notice error">{error}</div>}
            <div className="footer">
              <button type="button" onClick={onClose} disabled={busy}>Cancel</button>
              <button type="submit" className="primary" disabled={busy}>{busy ? "Building…" : "Build skill"}</button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
