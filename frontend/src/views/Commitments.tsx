import { useState } from "react";
import { api } from "../api";
import { useAction, useLoad } from "../hooks";
import { Icon } from "../icons";
import { useToast } from "../toast";
import type { Commitment } from "../types";
import { Badge, Card, Empty, ErrorNote, Loading, fmtDate, isOverdue } from "../ui";

const STATUSES = ["open", "proposed", "done", "dropped", "all"];

export default function Commitments() {
  const [status, setStatus] = useState("open");
  const { data, error, loading, reload } = useLoad<Commitment[]>(`/api/commitments?status=${status}`);
  const { run, error: actionError } = useAction(reload);
  const toast = useToast();
  const keep = (id: number, text: string) => run(async () => {
    await api.patch(`/api/commitments/${id}`, { status: "done" });
    toast(`Promise kept: ${text}`, { action: { label: "Undo", run: () => run(() => api.patch(`/api/commitments/${id}`, { status: "open" })) } });
  });
  return (
    <div>
      <div className="toolbar">
        <select name="status" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Commitment status">{STATUSES.map((s) => <option key={s}>{s}</option>)}</select>
      </div>
      <ErrorNote error={actionError} />
      <Loading loading={loading} error={error} />
      {data?.length === 0 && <Empty>Nothing here.</Empty>}
      <Card>
        <ul className="list">
          {data?.map((c) => (
            <li key={c.id} className={`task-row${c.status === "done" || c.status === "dropped" ? " done" : ""}`}>
              {c.status === "open" && <button className="tick" aria-label={`Kept: ${c.text}`} onClick={() => void keep(c.id, c.text)}><Icon name="check" size={14} /></button>}
              <span className="task-main">
                {c.text} <Badge>{c.status}</Badge> {c.importance === "high" && <Badge tone="warn">high</Badge>}
                {c.deadline && <Badge tone={c.status === "open" && isOverdue(c.deadline) ? "bad" : ""}>{fmtDate(c.deadline)}</Badge>}
                {c.postponement_count > 0 && <Badge tone="warn">postponed {c.postponement_count}×</Badge>}
              </span>
              <span className="actions">
                {c.status === "proposed" && <button onClick={() => run(() => api.patch(`/api/commitments/${c.id}`, { status: "open" }))}>Confirm</button>}
                {(c.status === "open" || c.status === "proposed") && (
                  <>
                    <input type="date" name="deadline" aria-label="New deadline" value={c.deadline ?? ""} onChange={(e) => e.target.value && run(() => api.patch(`/api/commitments/${c.id}`, { deadline: e.target.value }))} />
                    {c.status === "proposed" && <button className="ghost small" onClick={() => void keep(c.id, c.text)}>Done</button>}
                    <button className="ghost small" onClick={() => run(async () => { await api.patch(`/api/commitments/${c.id}`, { status: "dropped" }); toast(`Dropped: ${c.text}`, { tone: "info" }); })}>Drop</button>
                  </>
                )}
              </span>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
