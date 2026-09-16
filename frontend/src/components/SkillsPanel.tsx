import { useCallback, useEffect, useState } from "react";
import { api, ApiError, Skill } from "../lib/api";

export default function SkillsPanel({ version }: { version: number }) {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<(Skill & { content?: string }) | null>(null);

  const refresh = useCallback(async () => {
    try {
      setError(null);
      setSkills(await api.listSkills());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh, version]);

  async function open(skill: Skill) {
    try {
      setSelected(await api.getSkill(skill.name));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  async function remove(skill: Skill) {
    if (!confirm(`Delete skill "${skill.name}"?`)) return;
    try {
      await api.deleteSkill(skill.name);
      setSelected(null);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  const content = selected ? (typeof selected.content === "string" ? selected.content : JSON.stringify(selected, null, 2)) : "";

  return (
    <div className="panel">
      <h3>Skills</h3>
      <div className="hint">Reusable procedures stored under <code>viking://agent/skills</code>. Start a skill session to create one.</div>
      {error && <div className="notice error">{error}</div>}
      {skills.length === 0 && !error && <div className="hint">No skills yet.</div>}
      {skills.map((skill) => (
        <div key={skill.name} className="card">
          <div className="row">
            <span className="name">{skill.name}</span>
            <span>
              <button className="ghost" onClick={() => open(skill)}>view</button>
              <button className="ghost danger" onClick={() => remove(skill)}>delete</button>
            </span>
          </div>
          {skill.description && <div className="desc">{skill.description}</div>}
        </div>
      ))}
      {selected && (
        <div className="modal-backdrop" onClick={() => setSelected(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h2>{selected.name}</h2>
            <pre><code>{content}</code></pre>
            <div className="footer"><button onClick={() => setSelected(null)}>Close</button></div>
          </div>
        </div>
      )}
    </div>
  );
}
