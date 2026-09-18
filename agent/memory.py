"""Vikingchat memory module.

Design (see README "Memory design"):

* Five memory kinds stored as Markdown files in OpenViking under
  ``viking://user/<user>/memories``:
    profile.md                 semantic  – stable identity facts (always loaded)
    preferences/<topic>.md     semantic  – how the user wants things done (always loaded)
    procedures/<topic>.md      procedural – rules the assistant learned about how to help (always loaded)
    entities/<name>.md         semantic  – people, projects, organisations and their relations
    events/<date>-<slug>.md    episodic  – dated decisions, plans, milestones
    reflections/<date>.md      reflective – periodic summaries written by consolidation
    _index.md                  table of contents with one-line summaries (always loaded)
* Every record line carries provenance: ``- [YYYY-MM-DD · chat abcd1234 · stated] text``.
  Facts are never silently overwritten: a superseded line is kept and tagged
  ``superseded YYYY-MM-DD`` so temporal questions still work.
* Retrieval is hybrid: OpenViking vector search + keyword grep, merged and
  reranked by score, recency and kind, then cut to a token budget.
* Consolidation runs in the background (or on demand): merges duplicates,
  marks contradictions, prunes stale episodic memory into a reflection, and
  rebuilds the index. This is the "sleep" phase most systems in the
  literature rely on.
* An append-only audit log records every write.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("vikingchat.memory")

KINDS = ("profile", "preference", "procedure", "entity", "event")
KIND_DIR = {"profile": "", "preference": "preferences", "procedure": "procedures", "entity": "entities", "event": "events"}
ALWAYS_LOADED_DIRS = ("preferences", "procedures")
SKIP_FILES = {"identity.md", "soul.md"}  # OpenViking's seeded agent-persona files
RECORD_RE = re.compile(r"^- \[(\d{4}-\d{2}-\d{2})(?: · chat ([0-9a-f]{4,12}))?(?: · (stated|inferred|imported))?(?: · superseded (\d{4}-\d{2}-\d{2}))?\]\s*(.*)$")


def slugify(text: str, max_len: int = 48) -> str:
    text = re.sub(r"[^\w\s-]", "", text.lower(), flags=re.UNICODE)
    text = re.sub(r"[\s_-]+", "-", text).strip("-")
    return (text[:max_len].rstrip("-")) or "note"


def today() -> str:
    return date.today().isoformat()


def _is_not_found(err: Exception) -> bool:
    s = str(err).lower()
    return "not_found" in s or "not found" in s or "404" in s or "no such" in s or "does not exist" in s


@dataclass
class Record:
    text: str
    date: str
    chat: str | None = None
    source: str = "stated"
    superseded: str | None = None

    def render(self) -> str:
        meta = [self.date]
        if self.chat:
            meta.append(f"chat {self.chat}")
        meta.append(self.source)
        if self.superseded:
            meta.append(f"superseded {self.superseded}")
        return f"- [{' · '.join(meta)}] {self.text}"

    @classmethod
    def parse(cls, line: str) -> "Record | None":
        m = RECORD_RE.match(line.strip())
        if not m:
            return None
        return cls(text=m.group(5), date=m.group(1), chat=m.group(2), source=m.group(3) or "stated", superseded=m.group(4))


@dataclass
class Hit:
    uri: str
    rel: str
    kind: str
    text: str
    score: float
    why: list[str] = field(default_factory=list)


class MemoryStore:
    """All memory operations, over an OpenViking SDK client."""

    def __init__(self, client: Any, root_uri: str, lock: threading.Lock, *, audit_path: str | None = None) -> None:
        self.client = client
        self.root = root_uri.rstrip("/")
        self.lock = lock
        self.audit_path = Path(audit_path) if audit_path else None
        self.dirty_since: float | None = None
        self.last_write: float | None = None
        self.last_consolidation: dict[str, Any] | None = None

    # ------------------------------------------------------------------ paths
    def uri(self, rel: str) -> str:
        rel = rel.strip("/")
        return f"{self.root}/{rel}" if rel else self.root

    def rel(self, uri: str) -> str:
        return uri.replace(self.root + "/", "", 1).replace(self.root, "", 1).strip("/")

    @staticmethod
    def kind_of(rel: str) -> str:
        top = rel.split("/")[0]
        if rel == "profile.md":
            return "profile"
        return {"preferences": "preference", "procedures": "procedure", "entities": "entity", "events": "event", "reflections": "reflection"}.get(top, "other")

    def path_for(self, kind: str, topic: str | None, text: str) -> str:
        if kind == "profile":
            return "profile.md"
        if kind == "event":
            return f"events/{today()}-{slugify(topic or text)}.md"
        if not topic:
            raise ValueError(f"{kind} memories need a topic (e.g. 'language', 'project-vikingchat', 'ali-yilmaz').")
        return f"{KIND_DIR[kind]}/{slugify(topic)}.md"

    # ------------------------------------------------------------ raw file io
    def list_files(self) -> list[dict[str, Any]]:
        try:
            with self.lock:
                raw = self.client.ls(self.root + "/", recursive=True, node_limit=500)
        except Exception as err:  # noqa: BLE001
            if _is_not_found(err):
                return []
            raise
        if isinstance(raw, dict):
            raw = raw.get("entries") or raw.get("result") or []
        out = []
        for e in raw or []:
            if not isinstance(e, dict) or e.get("isDir", e.get("is_dir")) or not e.get("uri"):
                continue
            rel = self.rel(e["uri"])
            name = rel.split("/")[-1]
            if name in SKIP_FILES or name.startswith("."):
                continue
            out.append({"uri": e["uri"], "rel": rel, "kind": self.kind_of(rel), "size": e.get("size"), "modified": e.get("modTime") or e.get("mtime")})
        return out

    def read(self, rel: str) -> str | None:
        try:
            with self.lock:
                content = self.client.read(self.uri(rel))
        except Exception as err:  # noqa: BLE001
            if _is_not_found(err):
                return None
            raise
        return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)

    def write(self, rel: str, content: str, *, actor: str, chat: str | None = None, reason: str = "") -> None:
        uri = self.uri(rel)
        with self.lock:
            try:
                self.client.write(uri, content, mode="replace")
            except Exception as err:  # noqa: BLE001
                parent = uri.rsplit("/", 1)[0]
                if parent == self.root or not _is_not_found(err):
                    raise
                self.client.mkdir(parent)
                self.client.write(uri, content, mode="replace")
        now = time.time()
        self.last_write = now
        self.dirty_since = self.dirty_since or now
        self.audit({"op": "write", "path": rel, "chars": len(content), "actor": actor, "chat": chat, "reason": reason})

    def delete(self, rel: str, *, actor: str, reason: str = "") -> None:
        with self.lock:
            self.client.rm(self.uri(rel), recursive=True)
        self.audit({"op": "delete", "path": rel, "actor": actor, "reason": reason})

    def audit(self, entry: dict[str, Any]) -> None:
        entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **entry}
        log.info("memory %s %s (%s)", entry.get("op"), entry.get("path"), entry.get("actor"))
        if not self.audit_path:
            return
        try:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as err:  # noqa: BLE001
            log.warning("audit log write failed: %s", err)

    def audit_tail(self, n: int = 50) -> list[dict[str, Any]]:
        if not self.audit_path or not self.audit_path.exists():
            return []
        lines = self.audit_path.read_text(encoding="utf-8").splitlines()[-n:]
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    # ------------------------------------------------------------- records
    def remember(self, kind: str, text: str, *, topic: str | None = None, chat: str | None = None,
                 source: str = "stated", supersedes: str | None = None, actor: str = "agent") -> dict[str, Any]:
        kind = kind.lower().rstrip("s") if kind.lower() not in KINDS else kind.lower()
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        text = " ".join(text.split()).strip()
        if not text:
            raise ValueError("memory text is empty")
        rel = self.path_for(kind, topic, text)
        existing = self.read(rel) or ""
        lines = existing.splitlines() if existing else []
        if not lines:
            title = {"profile": "Profile", "preference": f"Preference: {topic}", "procedure": f"How to help: {topic}",
                     "entity": f"Entity: {topic}", "event": f"Event: {topic or slugify(text)}"}[kind]
            lines = [f"# {title}", ""]
        chat_id = (chat or "")[:8] or None
        new_rec = Record(text=text, date=today(), chat=chat_id, source=source)
        # Exact duplicate → no-op
        for line in lines:
            rec = Record.parse(line)
            if rec and not rec.superseded and rec.text.lower() == text.lower():
                return {"path": rel, "status": "unchanged"}
        # Mark superseded lines
        changed = False
        if supersedes:
            needle = supersedes.lower()
            for i, line in enumerate(lines):
                rec = Record.parse(line)
                if rec and not rec.superseded and needle in rec.text.lower():
                    rec.superseded = today()
                    lines[i] = rec.render()
                    changed = True
        lines.append(new_rec.render())
        self.write(rel, "\n".join(lines).rstrip() + "\n", actor=actor, chat=chat, reason=f"remember {kind}" + (" (supersedes)" if changed else ""))
        return {"path": rel, "status": "added", "superseded": changed}

    def forget(self, text: str, *, chat: str | None = None, actor: str = "agent") -> dict[str, Any]:
        """Soft-delete: mark every active record containing `text` as superseded."""
        needle = " ".join(text.split()).lower()
        touched: list[str] = []
        for f in self.list_files():
            if f["kind"] in ("reflection", "other"):
                continue
            content = self.read(f["rel"])
            if not content or needle not in content.lower():
                continue
            lines = content.splitlines()
            hit = False
            for i, line in enumerate(lines):
                rec = Record.parse(line)
                if rec and not rec.superseded and needle in rec.text.lower():
                    rec.superseded = today()
                    lines[i] = rec.render()
                    hit = True
            if hit:
                self.write(f["rel"], "\n".join(lines).rstrip() + "\n", actor=actor, chat=chat, reason="forget")
                touched.append(f["rel"])
        return {"superseded_in": touched}

    # ------------------------------------------------------------- reading
    def active_lines(self, content: str, *, include_superseded: bool = False) -> list[str]:
        out = []
        for line in content.splitlines():
            rec = Record.parse(line)
            if rec is None:
                if line.strip() and not line.startswith("#"):
                    out.append(line.strip())
                continue
            if rec.superseded and not include_superseded:
                continue
            out.append(rec.render() if include_superseded else f"- {rec.text} ({rec.date})")
        return out

    def core_block(self, budget: int = 5000) -> str:
        """Always-loaded memory: index, profile, preferences, procedures, recent events."""
        files = self.list_files()
        by_rel = {f["rel"]: f for f in files}
        parts: list[str] = []
        used = 0

        def add(title: str, body: str) -> None:
            nonlocal used
            chunk = f"### {title}\n{body.strip()}\n"
            if used + len(chunk) > budget:
                chunk = chunk[: max(0, budget - used)]
            if chunk:
                parts.append(chunk)
                used += len(chunk)

        if "_index.md" in by_rel:
            idx = self.read("_index.md")
            if idx:
                add("Memory index (what exists; read_file /memories/<path> for details)", idx[:1200])
        for rel in ["profile.md"] + sorted(r for r in by_rel if r.split("/")[0] in ALWAYS_LOADED_DIRS):
            if used >= budget:
                break
            content = self.read(rel)
            if content:
                lines = self.active_lines(content)
                if lines:
                    add(rel, "\n".join(lines))
        # recent episodic memory (last 5 events by date in filename)
        events = sorted((r for r in by_rel if r.startswith("events/")), reverse=True)[:5]
        ev_lines = []
        for rel in events:
            content = self.read(rel)
            if content:
                ev_lines.extend(self.active_lines(content)[:2])
        if ev_lines and used < budget:
            add("Recent events", "\n".join(ev_lines))
        if not parts:
            return "No memories about the user yet."
        return "\n".join(parts)

    # ------------------------------------------------------------- retrieval
    @staticmethod
    def keywords(query: str) -> list[str]:
        words = re.findall(r"[\w][\w'-]{2,}", query, flags=re.UNICODE)
        stop = {"the", "and", "for", "with", "what", "where", "when", "who", "how", "did", "does", "you", "your", "about",
                "this", "that", "have", "has", "was", "were", "are", "can", "could", "would", "should", "please", "tell",
                "bana", "benim", "nedir", "neresi", "hangi", "için", "ile", "gibi", "mi", "mı", "mu", "mü"}
        out = []
        for w in words:
            if w.lower() in stop:
                continue
            if w[0].isupper() or any(ch.isdigit() for ch in w) or len(w) >= 5:
                out.append(w)
        return out[:6]

    def _vector(self, query: str, limit: int) -> list[dict[str, Any]]:
        try:
            with self.lock:
                res = self.client.find(query, target_uri=self.root, limit=limit)
        except Exception as err:  # noqa: BLE001
            log.warning("vector recall failed: %s", err)
            return []
        hits = []
        for key in ("memories", "resources", "skills"):
            hits.extend(h for h in (res or {}).get(key, []) if isinstance(h, dict) and h.get("uri"))
        return hits

    def _grep(self, pattern: str) -> list[str]:
        try:
            with self.lock:
                res = self.client.grep(self.root, pattern, case_insensitive=True, node_limit=100)
        except Exception as err:  # noqa: BLE001
            log.debug("grep recall failed: %s", err)
            return []
        uris: list[str] = []
        items = res
        if isinstance(res, dict):
            items = res.get("matches") or res.get("results") or res.get("result") or res.get("files") or []
        for it in items if isinstance(items, list) else []:
            u = it.get("uri") or it.get("path") if isinstance(it, dict) else (it if isinstance(it, str) else None)
            if u and str(u).startswith("viking://"):
                uris.append(str(u))
        return uris

    def recall(self, query: str, *, limit: int = 6, kinds: tuple[str, ...] | None = None, exclude: set[str] | None = None) -> list[Hit]:
        """Hybrid retrieval: vector + keyword, reranked by score, recency and kind."""
        exclude = exclude or set()
        cands: dict[str, Hit] = {}
        for h in self._vector(query, limit * 2):
            rel = self.rel(h["uri"])
            if rel.split("/")[-1] in SKIP_FILES or h["uri"] in exclude:
                continue
            cands[h["uri"]] = Hit(uri=h["uri"], rel=rel, kind=self.kind_of(rel), text="", score=float(h.get("score") or 0.5), why=["semantic"])
        for kw in self.keywords(query):
            for uri in self._grep(re.escape(kw)):
                rel = self.rel(uri)
                if rel.split("/")[-1] in SKIP_FILES or uri in exclude:
                    continue
                if uri in cands:
                    cands[uri].score += 0.15
                    cands[uri].why.append(f"keyword:{kw}")
                else:
                    cands[uri] = Hit(uri=uri, rel=rel, kind=self.kind_of(rel), text="", score=0.55, why=[f"keyword:{kw}"])
        if kinds:
            cands = {u: h for u, h in cands.items() if h.kind in kinds}
        # recency boost for dated files (events/reflections) and kind weights
        for h in cands.values():
            m = re.search(r"(\d{4}-\d{2}-\d{2})", h.rel)
            if m:
                try:
                    age_days = (date.today() - date.fromisoformat(m.group(1))).days
                    h.score += max(0.0, 0.2 - 0.01 * age_days)
                except ValueError:
                    pass
            h.score += {"profile": 0.1, "preference": 0.1, "procedure": 0.05, "entity": 0.05, "event": 0.0, "reflection": -0.05}.get(h.kind, 0)
        ranked = sorted(cands.values(), key=lambda h: h.score, reverse=True)[:limit]
        for h in ranked:
            content = self.read(h.rel) or ""
            h.text = "\n".join(self.active_lines(content))[:900]
        return [h for h in ranked if h.text]

    # ------------------------------------------------------------- consolidation
    CONSOLIDATION_PROMPT = """You are the memory curator for a personal assistant. You receive ALL memory files about one user.
Rewrite them into a clean, deduplicated, consistent set. Rules:
- Keep the file layout: profile.md, preferences/<topic>.md, procedures/<topic>.md, entities/<name>.md, events/<date>-<slug>.md, reflections/<date>.md.
- Keep every record line in the exact format `- [YYYY-MM-DD · chat abcd1234 · stated] text` (chat part optional; source is stated|inferred|imported; keep dates and chat ids from the originals).
- Merge duplicates into one line (keep the earliest date). When two active lines contradict, keep the newer one active and tag the older one with ` · superseded YYYY-MM-DD` inside its brackets. Never invent facts.
- Move events older than {retention_days} days into a short reflection file `reflections/{today}.md` (2-6 bullets of durable takeaways) and delete those event files.
- Write `_index.md`: one line per file, `- path — one-line summary`.
- Never store secrets (passwords, API keys, card numbers); drop them if present.
Return ONLY JSON: {{"files": {{"<path>": "<full new content>", ...}}, "delete": ["<path>", ...], "notes": "<one sentence about what changed>"}}.
Include in "files" only files whose content changes, plus `_index.md`."""

    def consolidate(self, llm_call: Callable[[str, str], str], *, retention_days: int = 30, max_chars: int = 60000, actor: str = "consolidation") -> dict[str, Any]:
        files = self.list_files()
        if not files:
            self.last_consolidation = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "changed": 0, "notes": "no memories"}
            self.dirty_since = None
            return self.last_consolidation
        dump: list[str] = []
        total = 0
        for f in sorted(files, key=lambda x: x["rel"]):
            content = self.read(f["rel"]) or ""
            chunk = f"=== {f['rel']} ===\n{content.strip()}\n"
            if total + len(chunk) > max_chars:
                break
            dump.append(chunk)
            total += len(chunk)
        system = self.CONSOLIDATION_PROMPT.format(retention_days=retention_days, today=today())
        raw = llm_call(system, "\n".join(dump))
        plan = _parse_json(raw)
        changed = 0
        for rel, content in (plan.get("files") or {}).items():
            rel = str(rel).strip("/")
            if not _safe_rel(rel) or not isinstance(content, str):
                continue
            if (self.read(rel) or "").strip() == content.strip():
                continue
            self.write(rel, content.rstrip() + "\n", actor=actor, reason="consolidation")
            changed += 1
        deleted = 0
        for rel in plan.get("delete") or []:
            rel = str(rel).strip("/")
            if _safe_rel(rel) and rel != "profile.md" and rel != "_index.md":
                try:
                    self.delete(rel, actor=actor, reason="consolidation prune")
                    deleted += 1
                except Exception as err:  # noqa: BLE001
                    log.warning("prune failed for %s: %s", rel, err)
        self.last_consolidation = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "changed": changed, "deleted": deleted, "notes": str(plan.get("notes") or "")[:300]}
        self.dirty_since = None
        log.info("consolidation done: %s", self.last_consolidation)
        return self.last_consolidation


def _safe_rel(rel: str) -> bool:
    if not rel or ".." in rel or rel.startswith("/") or not rel.endswith(".md"):
        return False
    top = rel.split("/")[0]
    return rel in ("profile.md", "_index.md") or top in ("preferences", "procedures", "entities", "events", "reflections")


def _parse_json(raw: str) -> dict[str, Any]:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("consolidation model returned no JSON object")
    return json.loads(text[start:end + 1])
