import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Message, ToolEvent } from "../lib/api";

function ToolEvents({ events }: { events: ToolEvent[] }) {
  if (!events.length) return null;
  return (
    <div className="tool-events">
      {events.map((ev, i) => (
        <details key={ev.id ? `${ev.type}-${ev.id}` : i} className="tool-event">
          <summary>
            {ev.type === "tool_call" ? "▶ " : "✓ "}
            <code>{ev.name}</code>
            {ev.type === "tool_result" && ev.status && ev.status !== "success" ? ` (${ev.status})` : ""}
          </summary>
          <pre>{ev.type === "tool_call" ? JSON.stringify(ev.args, null, 2) : ev.content}</pre>
        </details>
      ))}
    </div>
  );
}

export default function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === "user";
  const toolEvents = message.toolEvents ?? [];
  const persistedCalls = message.meta?.tool_calls ?? [];
  return (
    <div className={`msg ${message.role} ${message.meta?.error ? "error" : ""}`}>
      <span className="role">{isUser ? "you" : "agent"}</span>
      {!isUser && toolEvents.length > 0 && <ToolEvents events={toolEvents} />}
      {!isUser && toolEvents.length === 0 && persistedCalls.length > 0 && (
        <div className="tool-events">
          <div className="tool-event">
            used tools: {persistedCalls.map((c) => c.name).join(", ")}
          </div>
        </div>
      )}
      <div className="bubble">
        {isUser ? (
          message.content
        ) : (
          <>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content || (message.streaming ? "" : "(no text)")}</ReactMarkdown>
            {message.streaming && <span className="cursor" />}
          </>
        )}
      </div>
    </div>
  );
}
