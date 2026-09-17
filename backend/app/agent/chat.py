"""Run one chat turn and stream it as server-sent events."""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from ..db import Database
from .events import events_from_update, sse, stream_depth, text_of
from .factory import AgentHandle

log = logging.getLogger(__name__)


def run_config(session_id: str, user_id: str) -> dict[str, Any]:
    return {
        "configurable": {"thread_id": session_id, "session_id": session_id, "peer_id": user_id},
        "recursion_limit": 200,
    }


def run_input(session_id: str, user_id: str, content: str) -> dict[str, Any]:
    """Graph input: the new message plus the identifiers OpenViking's middleware needs."""
    return {"messages": [HumanMessage(content=content)], "session_id": session_id, "peer_id": user_id}


async def stream_turn(
    handle: AgentHandle,
    db: Database,
    session: dict[str, Any],
    user: dict[str, Any],
    content: str,
) -> AsyncIterator[str]:
    """Persist the user message, run the agent and yield SSE frames."""
    session_id = session["id"]
    user_msg = db.add_message(session_id, "user", content)
    if len(db.list_messages(session_id)) == 1 and session["title"] in ("New chat", "New skill session"):
        db.touch_session(session_id, title=content.strip()[:60] or session["title"])
    else:
        db.touch_session(session_id)
    yield sse({"type": "user_message", "message": user_msg})

    config = run_config(session_id, user["id"])
    answer_parts: list[str] = []
    tool_events: list[dict[str, Any]] = []
    try:
        async for mode, chunk in handle.graph.astream(
            run_input(session_id, user["id"], content),
            config=config,
            stream_mode=["messages", "updates"],
        ):
            if mode == "messages":
                message, metadata = chunk
                if isinstance(message, AIMessageChunk) and stream_depth(metadata) == 0:
                    text = text_of(message.content)
                    if text:
                        answer_parts.append(text)
                        yield sse({"type": "token", "text": text})
            elif mode == "updates":
                for event in events_from_update(chunk):
                    tool_events.append(event)
                    yield sse(event)
    except Exception as exc:  # surface provider / OpenViking errors to the UI
        log.exception("Agent turn failed for session %s", session_id)
        error_text = f"The agent run failed: {exc}"
        db.add_message(session_id, "assistant", error_text, {"error": True})
        yield sse({"type": "error", "message": str(exc)})
        return

    final_text = "".join(answer_parts).strip()
    if not final_text:
        final_text = await _last_ai_text(handle, config)
    meta = {
        "tool_calls": [
            {"name": e.get("name"), "args": e.get("args")} for e in tool_events if e["type"] == "tool_call"
        ]
    }
    assistant_msg = db.add_message(session_id, "assistant", final_text, meta)
    db.touch_session(session_id)
    yield sse({"type": "done", "message": assistant_msg})


async def _last_ai_text(handle: AgentHandle, config: dict[str, Any]) -> str:
    try:
        state = await handle.graph.aget_state(config)
    except Exception:  # pragma: no cover
        return ""
    messages = (state.values or {}).get("messages") or []
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            text = text_of(message.content).strip()
            if text:
                return text
    return ""
