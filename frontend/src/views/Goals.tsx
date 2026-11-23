import { useState } from "react";
import { api } from "../api";
import { useAction, useLoad } from "../hooks";
import { Icon } from "../icons";
import { useToast } from "../toast";
import type { Goal } from "../types";
import { Badge, Card, Empty, ErrorNote, Loading, fmtDate } from "../ui";

/** Goals with their milestones: tick a milestone off, mark a goal achieved or paused, or ask Keel to break a step into tasks. */
export default function Goals({ ask }: { ask?: (text: string) => void }) {
  const [status, setStatus] = useState("active");
  const { data, error, loading, reload } = useLoad<Goal[]>(`/api/goals?status=${status}`);
  const { run, error: actionError } = useAction(reload);
  const toast = useToast();

  const tick = (goal: Goal, id: number, title: string, done: boolean) => run(async () => {
    await api.patch(`/api/milestones/${id}`, { status: done ? "todo" : "done" });
    const left = goal.milestones.filter((m) => m.status !== "done").length - (done ? -1 : 1);
    toast(done ? `Reopened: ${title}` : left === 0 ? `All milestones done for “${goal.title}”. Mark the goal achieved?` : `Milestone done: ${title}`,
      done || left !== 0 ? undefined : { action: { label: "Mark achieved", run: () => run(() => api.patch(`/api/goals/${goal.id}`, { status: "achieved" })) } });
  });
  const setGoal = (g: Goal, s: string, msg: string) => run(async () => {
    await api.patch(`/api/goals/${g.id}`, { status: s });
    toast(msg, { action: { label: "Undo", run: () => run(() => api.patch(`/api/goals/${g.id}`, { status: g.status })) } });
  });

  return (
    <div>
      <div className="toolbar">
        <select name="status" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Goal status">
          {["active", "paused", "achieved", "dropped", "all"].map((s) => <option key={s}>{s}</option>)}
        </select>
      </div>
      <ErrorNote error={actionError} />
      <Loading loading={loading} error={error} />
      {data?.length === 0 && <Empty>No {status} goals. Tell Keel an ambition (“I want to get good at web pentesting”) and it will break it into milestones.</Empty>}
      <div className="grid">
        {data?.map((g) => {
          const done = g.milestones.filter((m) => m.status === "done").length;
          const pct = g.milestones.length ? Math.round((done / g.milestones.length) * 100) : 0;
          const nextStep = g.milestones.find((m) => m.status !== "done");
          return (
            <Card key={g.id} title={g.title} right={<span><Badge>{g.category}</Badge> {g.status !== "active" && <Badge tone={g.status === "achieved" ? "ok" : ""}>{g.status}</Badge>}</span>}>
              {g.description && <p>{g.description}</p>}
              <p className="muted small">{g.horizon}-term · priority {g.priority}{g.target_date && ` · target ${fmtDate(g.target_date)}`}</p>
              {g.milestones.length > 0 && (
                <>
                  <div className="progress" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label={`${g.title} progress`}>
                    <span style={{ width: `${pct}%` }} />
                  </div>
                  <p className="small muted">{done} of {g.milestones.length} milestones · {pct}%</p>
                  <ul className="list">
                    {g.milestones.map((m) => (
                      <li key={m.id} className={`task-row${m.status === "done" ? " done" : ""}`}>
                        <button className={`tick${m.status === "done" ? " on" : ""}`} aria-label={`${m.status === "done" ? "Reopen" : "Done"}: ${m.title}`}
                          onClick={() => void tick(g, m.id, m.title, m.status === "done")}><Icon name="check" size={14} /></button>
                        <span className="task-main"><span>{m.title}</span>{m.target_date && <span className="task-meta"><Badge>{fmtDate(m.target_date)}</Badge></span>}</span>
                      </li>
                    ))}
                  </ul>
                </>
              )}
              <div className="ob-actions">
                {ask && nextStep && <button className="ghost small" onClick={() => ask(`Break the milestone “${nextStep.title}” of my goal “${g.title}” into concrete tasks`)}><Icon name="sparkle" size={14} /> Break next step into tasks</button>}
                {g.status === "active" && <button className="ghost small" onClick={() => void setGoal(g, "achieved", `Achieved: ${g.title}`)}>Mark achieved</button>}
                {g.status === "active" && <button className="ghost small" onClick={() => void setGoal(g, "paused", `Paused: ${g.title}`)}>Pause</button>}
                {g.status !== "active" && <button className="ghost small" onClick={() => void setGoal(g, "active", `Active again: ${g.title}`)}>Make active</button>}
              </div>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
