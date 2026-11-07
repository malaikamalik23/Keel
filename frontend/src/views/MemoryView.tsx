import { useState } from "react";
import { api } from "../api";
import { useAction, useLoad } from "../hooks";
import type { Memory } from "../types";
import { Badge, Card, Empty, ErrorNote, Loading } from "../ui";

export default function MemoryView() {
  const [kind, setKind] = useState("all");
  const { data, error, loading, reload } = useLoad<Memory[]>(`/api/memories?kind=${kind}`);
  const { run, error: actionError } = useAction(reload);
  const [editing, setEditing] = useState<{ id: number; text: string } | null>(null);

  return (
    <div>
      <div className="toolbar">
        <select name="kind" value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Memory kind">{["all", "preference", "context", "fact"].map((k) => <option key={k}>{k}</option>)}</select>
      </div>
      <p className="muted small">Everything the agent remembers about you. Edit or delete anything — changes are audited.</p>
      <ErrorNote error={actionError} />
      <Loading loading={loading} error={error} />
      {data?.length === 0 && <Empty>No memories yet.</Empty>}
      <Card>
        <ul className="list">
          {data?.map((m) => (
            <li key={m.id}>
              {editing?.id === m.id ? (
                <>
                  <textarea name="content" value={editing.text} onChange={(e) => setEditing({ id: m.id, text: e.target.value })} rows={2} aria-label="Edit memory" />
                  <span className="actions">
                    <button onClick={() => run(async () => { await api.patch(`/api/memories/${m.id}`, { content: editing.text }); setEditing(null); })}>Save</button>
                    <button className="ghost" onClick={() => setEditing(null)}>Cancel</button>
                  </span>
                </>
              ) : (
                <>
                  <span>{m.content} <Badge>{m.kind}</Badge> {m.importance === "high" && <Badge tone="warn">high</Badge>}</span>
                  <span className="actions">
                    <button className="ghost" onClick={() => setEditing({ id: m.id, text: m.content })}>Edit</button>
                    <button className="ghost danger" onClick={() => window.confirm("Permanently delete this memory?") && run(() => api.del(`/api/memories/${m.id}`))}>Delete</button>
                  </span>
                </>
              )}
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
