import { useState } from "react";
import { useLoad } from "../hooks";
import type { Project } from "../types";
import { Badge, Card, Empty, Loading, fmtDate, isOverdue } from "../ui";

export default function Projects() {
  const [status, setStatus] = useState("active");
  const { data, error, loading } = useLoad<Project[]>(`/api/projects?status=${status}`);
  return (
    <div>
      <div className="toolbar">
        <select name="status" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Project status">
          {["active", "paused", "completed", "archived", "all"].map((s) => <option key={s}>{s}</option>)}
        </select>
      </div>
      <Loading loading={loading} error={error} />
      {data?.length === 0 && <Empty>No {status} projects.</Empty>}
      <div className="grid">
        {data?.map((p) => (
          <Card key={p.id} title={p.name} right={<Badge>{p.priority}</Badge>}>
            {p.purpose && <p><strong>Why:</strong> {p.purpose}</p>}
            {p.description && <p>{p.description}</p>}
            {p.deadline && <Badge tone={isOverdue(p.deadline) ? "bad" : ""}>due {fmtDate(p.deadline)}</Badge>}
          </Card>
        ))}
      </div>
    </div>
  );
}
