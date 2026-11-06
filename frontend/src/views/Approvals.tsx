import { api } from "../api";
import { useAction, useLoad } from "../hooks";
import type { AuditEntry, PendingAction } from "../types";
import { Badge, Card, Empty, ErrorNote, Loading } from "../ui";

export default function Approvals({ onChange }: { onChange: () => void }) {
  const pending = useLoad<PendingAction[]>("/api/pending-actions");
  const audit = useLoad<AuditEntry[]>("/api/audit-log?limit=40");
  const refresh = async () => { await Promise.all([pending.reload(), audit.reload()]); onChange(); };
  const { run, error } = useAction(refresh);
  return (
    <div className="grid">
      <Card title="Waiting for your approval">
        <ErrorNote error={error} />
        <Loading loading={pending.loading} error={pending.error} />
        {pending.data?.length === 0 && <Empty>Nothing pending. The agent can't do anything destructive without you.</Empty>}
        <ul className="list">
          {pending.data?.map((p) => (
            <li key={p.id}>
              <span>{p.summary}</span>
              <span className="actions">
                <button onClick={() => run(() => api.post(`/api/pending-actions/${p.id}/confirm`))}>Approve</button>
                <button className="ghost" onClick={() => run(() => api.post(`/api/pending-actions/${p.id}/reject`))}>Reject</button>
              </span>
            </li>
          ))}
        </ul>
      </Card>
      <Card title="Audit log">
        <Loading loading={audit.loading} error={audit.error} />
        <table>
          <thead><tr><th>When</th><th>Actor</th><th>Tool</th><th>Result</th></tr></thead>
          <tbody>
            {audit.data?.map((a) => (
              <tr key={a.id}>
                <td>{new Date(a.created_at + "Z").toLocaleString()}</td><td>{a.actor}</td><td>{a.tool_name}</td>
                <td><Badge tone={a.status === "ok" ? "ok" : a.status === "error" ? "bad" : "warn"}>{a.status}</Badge></td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
