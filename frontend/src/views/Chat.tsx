import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { ToolCall } from "../types";
import { Badge, ErrorNote } from "../ui";

interface Turn { role: "user" | "agent"; text: string; tools?: ToolCall[]; pending?: number[]; }

export default function Chat({ onChange }: { onChange: () => void }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [conv, setConv] = useState<number | null>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [resolved, setResolved] = useState<Record<number, string>>({});
  const end = useRef<HTMLDivElement>(null);
  // Braces matter: an effect's return value is treated as its cleanup function, and current Chrome's scrollIntoView() returns a Promise.
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth" }); }, [turns]);

  const send = async () => {
    const message = input.trim();
    if (!message || busy) return;
    setInput(""); setBusy(true); setError(null);
    setTurns((t) => [...t, { role: "user", text: message }]);
    try {
      const r = await api.chat(message, conv);
      setConv(r.conversation_id);
      setTurns((t) => [...t, { role: "agent", text: r.reply, tools: r.tool_calls, pending: r.pending_action_ids }]);
      onChange();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };

  const resolve = async (id: number, action: "confirm" | "reject") => {
    try { await api.post(`/api/pending-actions/${id}/${action}`); setResolved((r) => ({ ...r, [id]: action === "confirm" ? "approved" : "rejected" })); onChange(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };

  return (
    <div className="chat">
      <div className="messages">
        {turns.length === 0 && <p className="muted">Try: “Plan my day, I have 4 hours” · “I'll finish the GRE quant set by Friday” · “What have I been avoiding?”</p>}
        {turns.map((t, i) => (
          <div key={i} className={`msg ${t.role}`}>
            <div className="bubble">{t.text}</div>
            {t.tools && t.tools.length > 0 && <div className="tools">{t.tools.map((c, j) => <Badge key={j} tone={c.status === "ok" ? "ok" : c.status === "error" ? "bad" : "warn"}>{c.name}</Badge>)}</div>}
            {t.pending?.map((id) => (
              <div key={id} className="approval">
                {resolved[id] ? <Badge>{resolved[id]}</Badge> : (<><span>Action #{id} needs your approval</span> <button onClick={() => resolve(id, "confirm")}>Approve</button> <button className="ghost" onClick={() => resolve(id, "reject")}>Reject</button></>)}
              </div>
            ))}
          </div>
        ))}
        {busy && <p className="muted">Thinking…</p>}
        <div ref={end} />
      </div>
      <ErrorNote error={error} />
      <form onSubmit={(e) => { e.preventDefault(); void send(); }} className="composer">
        <input name="message" autoComplete="off" value={input} onChange={(e) => setInput(e.target.value)} placeholder="Message your agent…" aria-label="Message" disabled={busy} />
        <button type="submit" disabled={busy || !input.trim()}>Send</button>
        <button type="button" className="ghost" onClick={() => { setTurns([]); setConv(null); }}>New</button>
      </form>
    </div>
  );
}
