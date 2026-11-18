import { api } from "../api";
import { useAction, useLoad } from "../hooks";
import type { Accountability, Briefing, Commitment, Plan, Task } from "../types";
import { Badge, Card, Empty, ErrorNote, Loading, fmtDate, isOverdue } from "../ui";

export default function Today() {
  const plan = useLoad<Plan>("/api/plan");
  const tasks = useLoad<Task[]>("/api/tasks?status=open");
  const commitments = useLoad<Commitment[]>("/api/commitments?status=open");
  const acct = useLoad<Accountability>("/api/accountability?window_days=7");
  const brief = useLoad<Briefing>("/api/briefing");
  const refresh = async () => { await Promise.all([plan.reload(), tasks.reload(), commitments.reload(), acct.reload(), brief.reload()]); };
  const { run, error } = useAction(refresh);

  const skip = (id: number) => {
    // Cancel on this prompt must mean "don't skip" (it used to skip anyway, with no reason). OK with an empty box still skips.
    const reason = window.prompt("Why skip this? (optional, helps spot patterns)");
    if (reason === null) return;
    return run(() => api.patch(`/api/plan-items/${id}`, { status: "skipped", reason: reason.trim() || undefined }));
  };
  const p = plan.data;
  const a = acct.data;
  const openIds = new Set((tasks.data ?? []).map((t) => t.id)); // a blocker that is done no longer blocks
  const alerts = a ? [
    ...a.tasks.overdue_titles.map((t) => `Overdue task: ${t}`),
    ...a.overdue_commitments.map((c) => `Overdue commitment: ${c.text} (was due ${fmtDate(c.deadline)})`),
    ...a.most_postponed.filter((m) => m.postponement_count >= 2).map((m) => `“${m.title}” postponed ${m.postponement_count}×${m.reasons_given.length ? ` — ${m.reasons_given.join(", ")}` : ""}`),
    ...a.neglected_goals.map((g) => `No progress on goal “${g.title}” for ${g.days_since_activity} days`),
  ] : [];

  const b = brief.data;
  const next = b?.next_action ? (typeof b.next_action === "string" ? b.next_action : b.next_action.title) : null;

  return (
    <div className="grid">
      <ErrorNote error={error} />
      <Card title="Briefing">
        <Loading loading={brief.loading} error={brief.error} />
        {b && (
          <>
            <p>{next ? <>Start with <strong>{next}</strong>.</> : "Nothing that can be started right now."}</p>
            {b.follow_ups_due.length > 0 && <p className="small">Time to follow up: {b.follow_ups_due.map((f) => `${f.waiting_on} (${f.title})`).join(", ")}</p>}
            {b.commitments_due_soon.length > 0 && <p className="small">Promises due soon: {b.commitments_due_soon.map((c) => `${c.text} (${fmtDate(c.deadline)})`).join(", ")}</p>}
            {b.coming_up.length > 0 && <p className="small">Coming up: {b.coming_up.map((t) => `${t.title} (${fmtDate(t.due ?? null)})`).join(", ")}</p>}
            {(b.waiting_count > 0 || b.blocked_count > 0) && <p className="muted small">{b.waiting_count} waiting on someone, {b.blocked_count} waiting for another task.</p>}
            {b.calendar && b.calendar.length > 0 && (
              <ul className="list">{b.calendar.map((e, i) => <li key={i}><span>{e.title}</span> <Badge>{e.time}</Badge></li>)}</ul>
            )}
          </>
        )}
      </Card>
      <Card
        title="Today's plan"
        right={p?.exists ? (
          <span>
            <Badge>{p.planned_minutes} / {p.usable_minutes} min</Badge>{" "}
            {p.items.some((i) => i.start) && <button className="ghost" onClick={() => run(() => api.download(`/api/plan.ics?date=${p.date}`, `keel-plan-${p.date}.ics`))}>Add to calendar</button>}
          </span>
        ) : undefined}
      >
        <Loading loading={plan.loading} error={plan.error} />
        {p && !p.exists && <Empty>No plan yet. Ask the agent in Chat: “Plan my day, I have 4 hours.”</Empty>}
        {p?.exists && p.items.length === 0 && <Empty>Nothing is planned for today (the plan's tasks were removed). Ask in Chat to plan again.</Empty>}
        {p?.exists && p.items.length > 0 && (
          <ul className="list">
            {[...p.items].sort((x, y) => (x.start ?? "99:99").localeCompare(y.start ?? "99:99")).map((i) => (
              <li key={i.item_id} className={i.status !== "planned" ? "done" : ""}>
                <span>{i.start && <strong className="small">{i.start}–{i.end} </strong>}{i.title} <Badge>{i.minutes} min</Badge> {i.major && <Badge tone="warn">major</Badge>} {i.status !== "planned" && <Badge tone={i.status === "done" ? "ok" : ""}>{i.status}</Badge>}</span>
                {i.status === "planned" && (
                  <span className="actions">
                    <button onClick={() => run(() => (i.task_id ? api.post(`/api/tasks/${i.task_id}/complete`) : api.patch(`/api/plan-items/${i.item_id}`, { status: "done" })))}>Done</button>
                    <button className="ghost" onClick={() => skip(i.item_id)}>Skip</button>
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Needs attention">
        <Loading loading={acct.loading} error={acct.error} />
        {a && (alerts.length ? <ul className="list">{alerts.slice(0, 8).map((t) => <li key={t}>{t}</li>)}</ul> : <Empty>Nothing slipping. Nice.</Empty>)}
        {a && <p className="muted small">Last {a.window.days} days: {a.tasks.completed} completed, {a.work.total_minutes} min logged{a.plan_adherence.rate !== null && `, ${Math.round(a.plan_adherence.rate * 100)}% of planned items done`}.</p>}
      </Card>

      <Card title="Open tasks">
        <Loading loading={tasks.loading} error={tasks.error} />
        {tasks.data?.length === 0 && <Empty>No open tasks.</Empty>}
        <ul className="list">
          {tasks.data?.map((t) => (
            <li key={t.id}>
              <span>{t.title} {t.due_date && <Badge tone={isOverdue(t.due_date) ? "bad" : ""}>{fmtDate(t.due_date)}</Badge>} {t.priority === "high" && <Badge tone="warn">high</Badge>} {t.postponement_count > 0 && <Badge tone="warn">postponed {t.postponement_count}×</Badge>} {t.recurrence && <Badge>repeats {t.recurrence}</Badge>} {t.waiting_on && <Badge>waiting on {t.waiting_on}{t.follow_up_date ? `, follow up ${fmtDate(t.follow_up_date)}` : ""}</Badge>} {t.blocked_by_id && openIds.has(t.blocked_by_id) && <Badge>after #{t.blocked_by_id}</Badge>}</span>
              <button onClick={() => run(() => api.post(`/api/tasks/${t.id}/complete`))}>Done</button>
            </li>
          ))}
        </ul>
      </Card>

      <Card title="Commitments">
        <Loading loading={commitments.loading} error={commitments.error} />
        {commitments.data?.length === 0 && <Empty>No open commitments.</Empty>}
        <ul className="list">
          {commitments.data?.slice(0, 6).map((c) => (
            <li key={c.id}><span>{c.text} {c.deadline && <Badge tone={isOverdue(c.deadline) ? "bad" : ""}>{fmtDate(c.deadline)}</Badge>}</span></li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
