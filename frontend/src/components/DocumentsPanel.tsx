import { ChangeEvent, DragEvent, useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, Document } from "../lib/api";

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export default function DocumentsPanel() {
  const [docs, setDocs] = useState<Document[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [over, setOver] = useState(false);
  const [selected, setSelected] = useState<Document | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async (refreshStatus = false) => {
    try {
      setDocs(await api.listDocuments(refreshStatus));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void refresh(true);
  }, [refresh]);

  useEffect(() => {
    const pending = docs.some((d) => !["completed", "failed"].includes(d.status));
    if (!pending) return;
    const t = setInterval(() => void refresh(true), 5000);
    return () => clearInterval(t);
  }, [docs, refresh]);

  async function upload(files: FileList | null) {
    if (!files || !files.length) return;
    setBusy(true);
    setError(null);
    try {
      for (const file of Array.from(files)) {
        await api.uploadDocument(file);
      }
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setOver(false);
    void upload(e.dataTransfer.files);
  }

  async function remove(doc: Document) {
    if (!confirm(`Delete "${doc.filename}" from OpenViking?`)) return;
    try {
      await api.deleteDocument(doc.id);
      setSelected(null);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  async function open(doc: Document) {
    try {
      setSelected(await api.getDocument(doc.id));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  return (
    <div className="panel">
      <h3>Documents</h3>
      <div className="hint">Files are parsed and indexed by OpenViking. The agent searches them when a question could be answered from your files.</div>
      <div
        className={`dropzone ${over ? "over" : ""}`}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
      >
        {busy ? "Uploading…" : "Drop files here or click to upload"}
        <input ref={inputRef} type="file" multiple hidden onChange={(e: ChangeEvent<HTMLInputElement>) => void upload(e.target.files)} />
      </div>
      {error && <div className="notice error">{error}</div>}
      {docs.map((doc) => (
        <div key={doc.id} className="card">
          <div className="row">
            <span className="name" title={doc.filename}>{doc.filename}</span>
            <span className={`status ${doc.status}`}>{doc.status}</span>
          </div>
          <div className="uri">{doc.uri}</div>
          <div className="row">
            <span className="desc">{formatSize(doc.size)}</span>
            <span>
              <button className="ghost" onClick={() => open(doc)}>overview</button>
              <button className="ghost danger" onClick={() => remove(doc)}>delete</button>
            </span>
          </div>
        </div>
      ))}
      {selected && (
        <div className="modal-backdrop" onClick={() => setSelected(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h2>{selected.filename}</h2>
            <div className="uri">{selected.uri}</div>
            <pre>{selected.overview}</pre>
            <div className="footer"><button onClick={() => setSelected(null)}>Close</button></div>
          </div>
        </div>
      )}
    </div>
  );
}
