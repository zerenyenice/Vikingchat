import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, ApiError, MemoryItem } from "../lib/api";

export default function MemoryPanel({ version }: { version: number }) {
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [mode, setMode] = useState<"browse" | "search">("browse");
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<{ uri: string; content: string } | null>(null);

  const load = useCallback(async (q?: string) => {
    try {
      setError(null);
      const res = await api.memory(q || undefined);
      setItems(res.items);
      setMode(res.mode);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void load(query);
  }, [load, version]); // eslint-disable-line react-hooks/exhaustive-deps

  function search(e: FormEvent) {
    e.preventDefault();
    void load(query);
  }

  async function open(item: MemoryItem) {
    try {
      setSelected(await api.readMemory(item.uri));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  async function forget(item: MemoryItem) {
    if (!confirm(`Forget ${item.uri}?`)) return;
    try {
      await api.deleteMemory(item.uri);
      setSelected(null);
      await load(query);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  const files = items.filter((i) => !i.uri.endsWith("/") && (i.is_dir !== true));

  return (
    <div className="panel">
      <h3>Memory</h3>
      <div className="hint">
        Long-term memory lives under <code>viking://user/memories</code>. The agent recalls it automatically and stores new facts as
        you chat; "Save memories" commits a session for extraction.
      </div>
      <form onSubmit={search} style={{ display: "flex", gap: 6 }}>
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search memories…" />
        <button type="submit">Go</button>
        <button type="button" className="ghost" onClick={() => void load()}>↻</button>
      </form>
      {error && <div className="notice error">{error}</div>}
      {files.length === 0 && !error && <div className="hint">{mode === "search" ? "No matches." : "Nothing remembered yet."}</div>}
      {files.map((item) => (
        <div key={item.uri} className="card">
          <div className="row">
            <span className="name" title={item.uri}>{item.uri.replace("viking://user/memories/", "")}</span>
            {typeof item.score === "number" && <span className="status">{item.score.toFixed(2)}</span>}
          </div>
          {item.abstract && <div className="desc">{String(item.abstract)}</div>}
          <div className="row">
            <span />
            <span>
              <button className="ghost" onClick={() => open(item)}>read</button>
              <button className="ghost danger" onClick={() => forget(item)}>forget</button>
            </span>
          </div>
        </div>
      ))}
      {selected && (
        <div className="modal-backdrop" onClick={() => setSelected(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h2 style={{ fontSize: 14, wordBreak: "break-all" }}>{selected.uri}</h2>
            <pre>{selected.content}</pre>
            <div className="footer"><button onClick={() => setSelected(null)}>Close</button></div>
          </div>
        </div>
      )}
    </div>
  );
}
