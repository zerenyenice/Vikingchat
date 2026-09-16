"""Helpers that turn LangGraph stream chunks into UI-friendly SSE events."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage


def sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


def text_of(content: Any) -> str:
    """Extract plain text from a message content (string or content-block list)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") in (None, "text") and block.get("text"):
                    parts.append(str(block["text"]))
        return "".join(parts)
    return str(content)


def stream_depth(metadata: dict[str, Any] | None) -> int:
    """0 for the main agent, >0 for subagents launched through the `task` tool."""
    ns = (metadata or {}).get("langgraph_checkpoint_ns") or ""
    return ns.count("|")


def truncate(value: Any, limit: int = 1500) -> str:
    text = value if isinstance(value, str) else text_of(value) or json.dumps(value, default=str)
    return text if len(text) <= limit else text[:limit] + f"... [{len(text) - limit} more chars]"


def events_from_update(update: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert a `stream_mode="updates"` chunk into tool_call / tool_result events."""
    events: list[dict[str, Any]] = []
    for node_name, payload in (update or {}).items():
        if not isinstance(payload, dict):
            continue
        messages = payload.get("messages")
        if not messages:
            continue
        if not isinstance(messages, list):
            messages = [messages]
        for message in messages:
            if not isinstance(message, BaseMessage):
                continue
            if isinstance(message, (AIMessage, AIMessageChunk)) and message.tool_calls:
                for call in message.tool_calls:
                    events.append(
                        {
                            "type": "tool_call",
                            "id": call.get("id"),
                            "name": call.get("name"),
                            "args": call.get("args"),
                            "node": node_name,
                        }
                    )
            elif isinstance(message, ToolMessage):
                events.append(
                    {
                        "type": "tool_result",
                        "id": message.tool_call_id,
                        "name": message.name,
                        "status": getattr(message, "status", "success"),
                        "content": truncate(message.content),
                        "node": node_name,
                    }
                )
    return events
