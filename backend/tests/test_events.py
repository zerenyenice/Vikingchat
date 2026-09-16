from langchain_core.messages import AIMessage, ToolMessage

from app.agent.events import events_from_update, stream_depth, text_of


def test_text_of_handles_content_blocks() -> None:
    assert text_of("plain") == "plain"
    assert text_of([{"type": "text", "text": "a"}, {"type": "tool_use", "id": "x"}, {"type": "text", "text": "b"}]) == "ab"
    assert text_of(None) == ""


def test_stream_depth() -> None:
    assert stream_depth({}) == 0
    assert stream_depth({"langgraph_checkpoint_ns": "model:abc"}) == 0
    assert stream_depth({"langgraph_checkpoint_ns": "tools:1|model:2"}) == 1


def test_events_from_update() -> None:
    ai = AIMessage(content="", tool_calls=[{"id": "c1", "name": "viking_find", "args": {"query": "q"}}])
    tool = ToolMessage(content="found 2 items", tool_call_id="c1", name="viking_find")
    events = events_from_update({"model": {"messages": [ai]}, "tools": {"messages": [tool]}})
    assert [e["type"] for e in events] == ["tool_call", "tool_result"]
    assert events[0]["name"] == "viking_find" and events[0]["args"] == {"query": "q"}
    assert events[1]["content"] == "found 2 items"
