import { FormEvent, useState } from "react";
import { api, ApiError, setToken, User } from "../lib/api";

export default function Login({ onLogin }: { onLogin: (user: User) => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = mode === "login" ? await api.login(username, password) : await api.register(username, password);
      setToken(res.access_token);
      onLogin(res.user);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth">
      <form onSubmit={submit}>
        <h1>VikingChat</h1>
        <div className="sub">A deep agent with memory, documents and skills stored in OpenViking.</div>
        <input placeholder="Username" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus required minLength={3} />
        <input placeholder="Password (min 8 chars)" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={8} />
        {error && <div className="notice error">{error}</div>}
        <button className="primary" type="submit" disabled={busy}>
          {busy ? "…" : mode === "login" ? "Sign in" : "Create account"}
        </button>
        <div className="switch">
          {mode === "login" ? (
            <>
              No account? <a href="#" onClick={(e) => { e.preventDefault(); setMode("register"); }}>Register</a>
            </>
          ) : (
            <>
              Have an account? <a href="#" onClick={(e) => { e.preventDefault(); setMode("login"); }}>Sign in</a>
            </>
          )}
        </div>
      </form>
    </div>
  );
}
