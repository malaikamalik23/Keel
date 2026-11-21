import { useState } from "react";
import { api } from "../api";
import { useAction, useLoad } from "../hooks";
import type { Project, ProjectReport } from "../types";
import { Badge, Card, Empty, ErrorNote, Loading, fmtDate, isOverdue } from "../ui";

const safeLink = (url: string) => (/^https?:\/\//i.test(url) ? url : null);
const REPO = /^[A-Za-z0-9][A-Za-z0-9-]{0,38}\/[A-Za-z0-9_.-]{1,100}$/;

function Detail({ id, onChange }: { id: number; onChange: () => Promise<void> }) {
  const { data: r, error, loading, reload } = useLoad<ProjectReport>(`/api/projects/${id}/report`);
  const { run, error: actionError } = useAction(async () => { await reload(); await onChange(); });
  const [kind, setKind] = useState("update");
  const [text, setText] = useState("");
  const [why, setWhy] = useState("");
  const [url, setUrl] = useState("");
  if (!r) return <Loading loading={loading} error={error} />;
  const add = () => run(async () => {
    await api.post(`/api/projects/${id}/entries`, { kind, text, ...(kind === "decision" && why ? { why } : {}) });
    setText(""); setWhy("");
  });
  return (
    <div className="small">
      <p className="muted">Last 7 days: {r.done_in_period.length} task(s) done, {r.minutes_worked} min worked.</p>
      {r.overdue.length > 0 && <p><Badge tone="bad">overdue</Badge> {r.overdue.map((t) => t.title).join(", ")}</p>}
      {r.stuck.length > 0 && <p><Badge>waiting</Badge> {r.stuck.map((t) => `${t.title} (${t.waiting_for_task ?? t.waiting_on})`).join(", ")}</p>}
      {r.open_blockers.length > 0 && (
        <>
          <p className="muted">Blockers</p>
          <ul className="list">
            {r.open_blockers.map((b) => (
              <li key={b.id}>
                <span>{b.text} <span className="muted">({b.days_open}d)</span></span>
                <button className="ghost" onClick={() => run(() => api.post(`/api/project-entries/${b.id}/resolve`, {}))}>Resolved</button>
              </li>
            ))}
          </ul>
        </>
      )}
      {r.decisions.length > 0 && (
        <>
          <p className="muted">Decisions</p>
          <ul className="list">
            {r.decisions.map((d) => <li key={d.id}><span>{fmtDate(d.date)}: {d.text}{d.why ? ` (why: ${d.why})` : ""}</span></li>)}
          </ul>
        </>
      )}
      {r.updates.length > 0 && (
        <>
          <p className="muted">Updates</p>
          <ul className="list">{r.updates.map((u) => <li key={u.id}><span>{fmtDate(u.date)}: {u.text}</span></li>)}</ul>
        </>
      )}
      {(r.links.length > 0 || r.project.repo) && (
        <>
          <p className="muted">Documents</p>
          <ul className="list">
            {r.project.repo && REPO.test(r.project.repo) && (
              <li><a href={`https://github.com/${r.project.repo}/pulls`} target="_blank" rel="noopener noreferrer">{r.project.repo} pull requests</a></li>
            )}
            {r.links.map((l) => {
              const href = l.kind === "url" ? safeLink(l.target) : null;
              return (
                <li key={l.id}>
                  <span>{href ? <a href={href} target="_blank" rel="noopener noreferrer">{l.title ?? l.target}</a> : `${l.title ?? l.target}${l.kind === "note" ? " (note)" : ""}`}</span>
                  <button className="ghost danger" onClick={() => run(() => api.del(`/api/project-links/${l.id}`))}>Remove</button>
                </li>
              );
            })}
          </ul>
        </>
      )}
      <div className="toolbar">
        <select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Log entry kind">
          <option>update</option><option>blocker</option><option>decision</option>
        </select>
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="What happened?" aria-label="Log entry" maxLength={2000} />
        {kind === "decision" && <input value={why} onChange={(e) => setWhy(e.target.value)} placeholder="Why?" aria-label="Decision reason" maxLength={2000} />}
        <button disabled={!text.trim()} onClick={add}>Log</button>
      </div>
      <div className="toolbar">
        <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://… document link" aria-label="Document link" maxLength={500} />
        <button disabled={!url.trim()} onClick={() => run(async () => { await api.post(`/api/projects/${id}/links`, { url: url.trim() }); setUrl(""); })}>Attach</button>
      </div>
      <ErrorNote error={actionError} />
    </div>
  );
}

export default function Projects() {
  const [status, setStatus] = useState("active");
  const [open, setOpen] = useState<number | null>(null);
  const { data, error, loading, reload } = useLoad<Project[]>(`/api/projects?status=${status}`);
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
            {p.progress && p.progress.tasks_total > 0 && (
              <p className="small">
                <meter min={0} max={p.progress.tasks_total} value={p.progress.done} aria-label={`${p.name} progress`} />{" "}
                {p.progress.done}/{p.progress.tasks_total} tasks done
              </p>
            )}
            <p className="small">
              {p.deadline && <Badge tone={isOverdue(p.deadline) ? "bad" : ""}>due {fmtDate(p.deadline)}</Badge>}{" "}
              {p.open_blockers > 0 && <Badge tone="bad">{p.open_blockers} blocker(s)</Badge>}{" "}
              <button className="ghost" onClick={() => setOpen(open === p.id ? null : p.id)}>{open === p.id ? "Hide" : "Details"}</button>
            </p>
            {open === p.id && <Detail id={p.id} onChange={reload} />}
          </Card>
        ))}
      </div>
    </div>
  );
}
