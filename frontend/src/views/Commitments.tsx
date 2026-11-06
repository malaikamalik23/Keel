import { useState } from "react";
import { api } from "../api";
import { useAction, useLoad } from "../hooks";
import type { Commitment } from "../types";
import { Badge, Card, Empty, ErrorNote, Loading, fmtDate, isOverdue } from "../ui";

const STATUSES = ["open", "proposed", "done", "dropped", "all"];

export default function Commitments() {
  const [status, setStatus] = useState("open");
  const { data, error, loading, reload } = useLoad<Commitment[]>(`/api/commitments?status=${status}`);
  const { run, error: actionError } = useAction(reload);
  return (
    <div>
      <div className="toolbar">
        <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Commitment status">{STATUSES.map((s) => <option key={s}>{s}</option>)}</select>
      </div>
      <ErrorNote error={actionError} />
      <Loading loading={loading} error={error} />
      {data?.length === 0 && <Empty>Nothing here.</Empty>}
      <Card>
        <ul className="list">
          {data?.map((c) => (
            <li key={c.id} className={c.status === "done" || c.status === "dropped" ? "done" : ""}>
              <span>
                {c.text} <Badge>{c.status}</Badge> {c.importance === "high" && <Badge tone="warn">high</Badge>}
                {c.deadline && <Badge tone={c.status === "open" && isOverdue(c.deadline) ? "bad" : ""}>{fmtDate(c.deadline)}</Badge>}
                {c.postponement_count > 0 && <Badge tone="warn">postponed {c.postponement_count}×</Badge>}
              </span>
              <span className="actions">
                {c.status === "proposed" && <button onClick={() => run(() => api.patch(`/api/commitments/${c.id}`, { status: "open" }))}>Confirm</button>}
                {(c.status === "open" || c.status === "proposed") && (
                  <>
                    <input type="date" aria-label="New deadline" value={c.deadline ?? ""} onChange={(e) => e.target.value && run(() => api.patch(`/api/commitments/${c.id}`, { deadline: e.target.value }))} />
                    <button onClick={() => run(() => api.patch(`/api/commitments/${c.id}`, { status: "done" }))}>Done</button>
                    <button className="ghost" onClick={() => run(() => api.patch(`/api/commitments/${c.id}`, { status: "dropped" }))}>Drop</button>
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
