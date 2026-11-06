import { useState } from "react";
import { useLoad } from "../hooks";
import type { Goal } from "../types";
import { Badge, Card, Empty, Loading, fmtDate } from "../ui";

export default function Goals() {
  const [status, setStatus] = useState("active");
  const { data, error, loading } = useLoad<Goal[]>(`/api/goals?status=${status}`);
  return (
    <div>
      <div className="toolbar">
        <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Goal status">
          {["active", "paused", "achieved", "dropped", "all"].map((s) => <option key={s}>{s}</option>)}
        </select>
      </div>
      <Loading loading={loading} error={error} />
      {data?.length === 0 && <Empty>No {status} goals. Tell the agent an ambition and it will break it into milestones.</Empty>}
      <div className="grid">
        {data?.map((g) => {
          const done = g.milestones.filter((m) => m.status === "done").length;
          return (
            <Card key={g.id} title={g.title} right={<Badge>{g.category}</Badge>}>
              {g.description && <p>{g.description}</p>}
              <p className="muted small">{g.horizon}-term · priority {g.priority}{g.target_date && ` · target ${fmtDate(g.target_date)}`}</p>
              {g.milestones.length > 0 && (
                <>
                  <progress value={done} max={g.milestones.length} />
                  <ol className="list">{g.milestones.map((m) => <li key={m.id} className={m.status === "done" ? "done" : ""}><span>{m.title}</span><Badge>{m.status}</Badge></li>)}</ol>
                </>
              )}
            </Card>
          );
        })}
      </div>
    </div>
  );
}
