import { useState } from "react";
import { api } from "../api";
import { useAction, useLoad } from "../hooks";
import { Icon } from "../icons";
import { useToast } from "../toast";
import WhatNow from "./WhatNow";
import type { Accountability, Briefing, Commitment, PendingAction, Plan, Task } from "../types";
import { Badge, Card, Empty, ErrorNote, Loading, fmtDate, isOverdue } from "../ui";

const greeting = () => { const h = new Date().getHours(); return h < 5 ? "Good night" : h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening"; };
const today = () => new Date().toLocaleDateString("en-CA");

/** Home: what to do next, what needs you, and today's tasks you can tick off right here. */
export default function Today({ go, ask }: { go?: (page: string) => void; ask?: (text: string) => void }) {
  const plan = useLoad<Plan>("/api/plan");
  const tasks = useLoad<Task[]>("/api/tasks?status=open");
  const commitments = useLoad<Commitment[]>("/api/commitments?status=open");
  const acct = useLoad<Accountability>("/api/accountability?window_days=7");
  const brief = useLoad<Briefing>("/api/briefing");
  const profile = useLoad<{ name?: string; address_as?: string }>("/api/profile");
  const pending = useLoad<PendingAction[]>("/api/pending-actions");
  const refresh = async () => { await Promise.all([plan.reload(), tasks.reload(), commitments.reload(), acct.reload(), brief.reload()]); };
  const { run, error } = useAction(refresh);
  const toast = useToast();
  const [leaving, setLeaving] = useState<Set<number>>(new Set());
  const [newTitle, setNewTitle] = useState("");
  const [newDue, setNewDue] = useState("");

  const finish = async (id: number, title: string) => {
    setLeaving((s) => new Set(s).add(id));
    await new Promise((r) => setTimeout(r, 250));  // let the row slide away before the list refreshes
    await run(async () => {
      await api.post(`/api/tasks/${id}/complete`);
      toast(`Done: ${title}`, { action: { label: "Undo", run: () => run(async () => { await api.patch(`/api/tasks/${id}`, { status: "todo" }); toast("Back on your list", { tone: "info" }); }) } });
    });
    setLeaving((s) => { const n = new Set(s); n.delete(id); return n; });
  };
  const add = () => run(async () => {
    const title = newTitle.trim();
    if (!title) return;
    const t = await api.post<Task>("/api/tasks", { title, ...(newDue ? { due_date: newDue } : {}) });
    setNewTitle(""); setNewDue("");
    toast(`Added: ${t.title}`, { action: { label: "Undo", run: () => run(() => api.patch(`/api/tasks/${t.id}`, { status: "cancelled" })) } });
  });
  const skip = (id: number) => {
    // Cancel on this prompt must mean "don't skip" (it used to skip anyway, with no reason). OK with an empty box still skips.
    const reason = window.prompt("Why skip this? (optional, helps spot patterns)");
    if (reason === null) return;
    return run(async () => { await api.patch(`/api/plan-items/${id}`, { status: "skipped", reason: reason.trim() || undefined }); toast("Skipped", { tone: "info" }); });
  };

  const p = plan.data;
  const a = acct.data;
  const b = brief.data;
  const openIds = new Set((tasks.data ?? []).map((t) => t.id)); // a blocker that is done no longer blocks
  const alerts = a ? [
    ...a.tasks.overdue_titles.map((t) => `Overdue task: ${t}`),
    ...a.overdue_commitments.map((c) => `Overdue commitment: ${c.text} (was due ${fmtDate(c.deadline)})`),
    ...a.most_postponed.filter((m) => m.postponement_count >= 2).map((m) => `“${m.title}” postponed ${m.postponement_count}×${m.reasons_given.length ? ` — ${m.reasons_given.join(", ")}` : ""}`),
    ...a.neglected_goals.map((g) => `No progress on goal “${g.title}” for ${g.days_since_activity} days`),
  ] : [];
  const nextTask = b?.next_action && typeof b.next_action !== "string" ? b.next_action : null;
  const next = b?.next_action ? (typeof b.next_action === "string" ? b.next_action : b.next_action.title) : null;
  const name = profile.data?.address_as || profile.data?.name?.split(" ")[0] || "";
  const overdue = (tasks.data ?? []).filter((t) => isOverdue(t.due_date)).length;
  const dueToday = (tasks.data ?? []).filter((t) => t.due_date === today()).length;
  const waiting = Array.isArray(pending.data) ? pending.data.length : 0;

  return (
    <div>
      <div className="hello">
        <h1>{greeting()}{name ? `, ${name}` : ""}</h1>
        <p>{new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })} · press <kbd>Ctrl K</kbd> to capture or ask anything</p>
      </div>
      <ErrorNote error={error} />

      <section className="card hero" aria-label="Up next">
        <span className="eyebrow"><Icon name="bolt" size={14} /> Up next</span>
        <Loading loading={brief.loading} error={brief.error} />
        {b && <h2>{next ? <>Start with <strong>{next}</strong>.</> : "Nothing that can be started right now."}</h2>}
        {b && nextTask?.minutes && <p>About {nextTask.minutes} minutes{nextTask.due ? ` · due ${fmtDate(nextTask.due)}` : ""}</p>}
        <div className="hero-actions">
          {nextTask && openIds.has(nextTask.id) && <button onClick={() => void finish(nextTask.id, nextTask.title)}><Icon name="check" size={16} /> Mark done</button>}
          {ask && !p?.exists && <button className="ghost" onClick={() => ask("Plan my day")}><Icon name="sparkle" size={16} /> Plan my day</button>}
          {ask && <button className="ghost" onClick={() => ask("What should I do next?")}><Icon name="chat" size={16} /> Ask Keel</button>}
        </div>
      </section>

      <div className="chips">
        {overdue > 0 && <span className="chip bad"><Icon name="alert" size={16} /><b>{overdue}</b> overdue</span>}
        {dueToday > 0 && <span className="chip warn"><Icon name="clock" size={16} /><b>{dueToday}</b> due today</span>}
        {waiting > 0 && <button className="chip warn" onClick={() => go?.("Approvals")}><Icon name="approve" size={16} /><b>{waiting}</b> waiting for your approval</button>}
        {a && <span className="chip ok"><Icon name="check" size={16} /><b>{a.tasks.completed}</b> done this week</span>}
        {a && a.work.total_minutes > 0 && <span className="chip"><Icon name="activity" size={16} /><b>{Math.round(a.work.total_minutes / 6) / 10}</b> h logged</span>}
      </div>

      <WhatNow ask={ask} onChange={refresh} />

      <div className="home-grid">
        <div className="stack">
          <Card
            title="Today's plan"
            right={p?.exists ? (
              <span>
                <Badge>{p.planned_minutes} / {p.usable_minutes} min</Badge>{" "}
                {p.items.some((i) => i.start) && <button className="ghost small" onClick={() => run(() => api.download(`/api/plan.ics?date=${p.date}`, `keel-plan-${p.date}.ics`))}>Add to calendar</button>}
              </span>
            ) : undefined}
          >
            <Loading loading={plan.loading} error={plan.error} />
            {p && !p.exists && <Empty>No plan yet. Press “Plan my day” above, or tell Keel how many hours you have.</Empty>}
            {p?.exists && p.items.length === 0 && <Empty>Nothing is planned for today (the plan's tasks were removed). Ask Keel to plan again.</Empty>}
            {p?.exists && p.items.length > 0 && (
              <ul className="timeline">
                {[...p.items].sort((x, y) => (x.start ?? "99:99").localeCompare(y.start ?? "99:99")).map((i) => (
                  <li key={i.item_id} className={i.status !== "planned" ? "done" : ""}>
                    <span className="time">{i.start ? `${i.start}–${i.end}` : "any time"}</span>
                    <span style={{ flex: 1 }}>{i.title} <Badge>{i.minutes} min</Badge> {i.major && <Badge tone="warn">major</Badge>} {i.status !== "planned" && <Badge tone={i.status === "done" ? "ok" : ""}>{i.status}</Badge>}</span>
                    {i.status === "planned" && (
                      <span className="actions">
                        <button className="small" onClick={() => run(async () => {
                          await (i.task_id ? api.post(`/api/tasks/${i.task_id}/complete`) : api.patch(`/api/plan-items/${i.item_id}`, { status: "done" }));
                          toast(`Done: ${i.title}`);
                        })}>Done</button>
                        <button className="ghost small" onClick={() => skip(i.item_id)}>Skip</button>
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card title="Your tasks" right={<Badge>{tasks.data?.length ?? 0} open</Badge>}>
            <form className="quick-add" onSubmit={(e) => { e.preventDefault(); void add(); }}>
              <input value={newTitle} onChange={(e) => setNewTitle(e.target.value)} placeholder="Add a task…" aria-label="New task" maxLength={200} />
              <input type="date" value={newDue} onChange={(e) => setNewDue(e.target.value)} aria-label="Due date" />
              <button type="submit" disabled={!newTitle.trim()} aria-label="Add task"><Icon name="plus" size={16} /></button>
            </form>
            <Loading loading={tasks.loading} error={tasks.error} />
            {tasks.data?.length === 0 && <Empty>No open tasks. Add one above, or capture anything with Ctrl K.</Empty>}
            <ul className="list">
              {tasks.data?.map((t) => (
                <li key={t.id} className={`task-row${leaving.has(t.id) ? " leaving" : ""}`}>
                  <button className={`tick${leaving.has(t.id) ? " on" : ""}`} aria-label={`Done: ${t.title}`} onClick={() => void finish(t.id, t.title)}><Icon name="check" size={14} /></button>
                  <span className="task-main">
                    <span>{t.title}</span>
                    <span className="task-meta">
                      {t.due_date && <Badge tone={isOverdue(t.due_date) ? "bad" : t.due_date === today() ? "warn" : ""}>{fmtDate(t.due_date)}</Badge>}
                      {t.priority === "high" && <Badge tone="warn">high</Badge>}
                      {t.postponement_count > 0 && <Badge tone="warn">postponed {t.postponement_count}×</Badge>}
                      {t.recurrence && <Badge>repeats {t.recurrence}</Badge>}
                      {t.waiting_on && <Badge>waiting on {t.waiting_on}{t.follow_up_date ? `, follow up ${fmtDate(t.follow_up_date)}` : ""}</Badge>}
                      {t.blocked_by_id && openIds.has(t.blocked_by_id) && <Badge>after #{t.blocked_by_id}</Badge>}
                      {t.event_start && <Badge>before meeting {new Date(t.event_start + "Z").toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit" })}</Badge>}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          </Card>
        </div>

        <div className="stack">
          <Card title="Coming up">
            {b && (
              <>
                {(b.calendar_warnings ?? []).map((w, i) => <p key={i} className="small warn-text" role="alert"><Icon name="alert" size={14} /> {w}</p>)}
                {b.calendar && b.calendar.length > 0 && (
                  <ul className="list">{b.calendar.map((e, i) => <li key={i}><span>{e.title}</span> <Badge>{e.time}</Badge></li>)}</ul>
                )}
                {(b.important_dates ?? []).length > 0 && <p className="small">Dates: {(b.important_dates ?? []).map((d) => `${d.title} (${d.days_left === 0 ? "today" : fmtDate(d.date)})`).join(", ")}</p>}
                {b.follow_ups_due.length > 0 && <p className="small">Time to follow up: {b.follow_ups_due.map((f) => `${f.waiting_on} (${f.title})`).join(", ")}</p>}
                {b.coming_up.length > 0 && <p className="small">Coming up: {b.coming_up.map((t) => `${t.title} (${fmtDate(t.due ?? null)})`).join(", ")}</p>}
                {(b.waiting_count > 0 || b.blocked_count > 0) && <p className="muted small">{b.waiting_count} waiting on someone, {b.blocked_count} waiting for another task.</p>}
              </>
            )}
            {commitments.data && commitments.data.length > 0 && (
              <>
                <h2 className="sub small muted">Promises</h2>
                <ul className="list">
                  {commitments.data.slice(0, 5).map((c) => (
                    <li key={c.id}><span>{c.text}</span>{c.deadline && <Badge tone={isOverdue(c.deadline) ? "bad" : ""}>{fmtDate(c.deadline)}</Badge>}</li>
                  ))}
                </ul>
              </>
            )}
            {b && !b.calendar?.length && !commitments.data?.length && !(b.important_dates ?? []).length && <Empty>Nothing on the horizon.</Empty>}
          </Card>

          <Card title="Needs attention">
            <Loading loading={acct.loading} error={acct.error} />
            {a && (alerts.length ? (
              <>
                <ul className="list">{alerts.slice(0, 3).map((t) => <li key={t}><span><Icon name="alert" size={14} className="warn-text" /> {t}</span></li>)}</ul>
                {alerts.length > 3 && (
                  <details className="more"><summary>{alerts.length - 3} more</summary>
                    <ul className="list">{alerts.slice(3, 10).map((t) => <li key={t}>{t}</li>)}</ul>
                  </details>
                )}
              </>
            ) : <Empty>Nothing slipping. Nice.</Empty>)}
            {a && <p className="muted small">Last {a.window.days} days: {a.tasks.completed} completed, {a.work.total_minutes} min logged{a.plan_adherence.rate !== null && `, ${Math.round(a.plan_adherence.rate * 100)}% of planned items done`}.</p>}
          </Card>
        </div>
      </div>
    </div>
  );
}
