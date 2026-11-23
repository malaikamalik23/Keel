import { useState } from "react";
import { api } from "../api";
import { useAction, useLoad } from "../hooks";
import { Icon } from "../icons";
import { useToast } from "../toast";
import type { Project, ProjectReport } from "../types";
import { Badge, Card, Empty, ErrorNote, Loading, fmtDate, isOverdue } from "../ui";

const safeLink = (url: string) => (/^https?:\/\//i.test(url) ? url : null);
const REPO = /^[A-Za-z0-9][A-Za-z0-9-]{0,38}\/[A-Za-z0-9_.-]{1,100}$/;
const KIND_ICON: Record<string, string> = { update: "activity", blocker: "alert", decision: "check" };

function Detail({ id, onChange }: { id: number; onChange: () => Promise<void> }) {
  const { data: r, error, loading, reload } = useLoad<ProjectReport>(`/api/projects/${id}/report`);
  const { run, error: actionError } = useAction(async () => { await reload(); await onChange(); });
  const toast = useToast();
  const [kind, setKind] = useState("update");
  const [text, setText] = useState("");
  const [why, setWhy] = useState("");
  const [url, setUrl] = useState("");
  if (!r) return <Loading loading={loading} error={error} />;
  const add = () => run(async () => {
    await api.post(`/api/projects/${id}/entries`, { kind, text, ...(kind === "decision" && why ? { why } : {}) });
    toast(kind === "blocker" ? "Blocker logged" : kind === "decision" ? "Decision recorded" : "Update logged");
    setText(""); setWhy("");
  });
  const timeline = [...r.decisions.map((d) => ({ ...d, k: "decision" })), ...r.updates.map((u) => ({ ...u, k: "update" }))]
    .sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));
  return (
    <div className="small proj-detail">
      <div className="chips">
        <span className="chip ok"><Icon name="check" size={14} /><b>{r.done_in_period.length}</b> done in 7 days</span>
        <span className="chip"><Icon name="clock" size={14} /><b>{r.minutes_worked}</b> min worked</span>
        {r.overdue.length > 0 && <span className="chip bad"><Icon name="alert" size={14} /><b>{r.overdue.length}</b> overdue</span>}
      </div>
      {r.overdue.length > 0 && <p><Badge tone="bad">overdue</Badge> {r.overdue.map((t) => t.title).join(", ")}</p>}
      {r.stuck.length > 0 && <p><Badge>waiting</Badge> {r.stuck.map((t) => `${t.title} (${t.waiting_for_task ?? t.waiting_on})`).join(", ")}</p>}
      {r.open_blockers.length > 0 && (
        <>
          <p className="muted">Blockers</p>
          <ul className="list">
            {r.open_blockers.map((b) => (
              <li key={b.id} className="task-row">
                <button className="tick" aria-label="Resolved" title="Mark resolved"
                  onClick={() => run(async () => { await api.post(`/api/project-entries/${b.id}/resolve`, {}); toast(`Blocker resolved: ${b.text}`); })}><Icon name="check" size={14} /></button>
                <span className="task-main"><span>{b.text}</span>{b.days_open != null && <span className="task-meta"><Badge tone={b.days_open > 7 ? "bad" : ""}>{b.days_open}d open</Badge></span>}</span>
              </li>
            ))}
          </ul>
        </>
      )}
      {timeline.length > 0 && (
        <>
          <p className="muted">Timeline</p>
          <ul className="proj-timeline">
            {timeline.map((e) => (
              <li key={`${e.k}-${e.id}`} className={`tl-${e.k}`}>
                <Icon name={KIND_ICON[e.k]} size={14} />
                <span>{fmtDate(e.date)}: {e.text}{e.why ? ` (why: ${e.why})` : ""}</span>
              </li>
            ))}
          </ul>
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
                  <button className="ghost danger small" onClick={() => run(async () => { await api.del(`/api/project-links/${l.id}`); toast(`Removed ${l.title ?? l.target}`, { tone: "info" }); })}>Remove</button>
                </li>
              );
            })}
          </ul>
        </>
      )}
      <form className="toolbar" onSubmit={(e) => { e.preventDefault(); if (text.trim()) void add(); }}>
        <span className="seg" role="group" aria-label="Log entry kind">
          {["update", "blocker", "decision"].map((k) => (
            <button type="button" key={k} className={kind === k ? "" : "ghost"} aria-pressed={kind === k} onClick={() => setKind(k)}>
              <Icon name={KIND_ICON[k]} size={13} /> {k}
            </button>
          ))}
        </span>
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder={kind === "blocker" ? "What's in the way?" : kind === "decision" ? "What did you decide?" : "What happened?"} aria-label="Log entry" maxLength={2000} />
        {kind === "decision" && <input value={why} onChange={(e) => setWhy(e.target.value)} placeholder="Why?" aria-label="Decision reason" maxLength={2000} />}
        <button type="submit" disabled={!text.trim()}>Log</button>
      </form>
      <form className="toolbar" onSubmit={(e) => { e.preventDefault(); if (url.trim()) void run(async () => { await api.post(`/api/projects/${id}/links`, { url: url.trim() }); toast("Link attached"); setUrl(""); }); }}>
        <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://… document link" aria-label="Document link" maxLength={500} />
        <button type="submit" className="ghost" disabled={!url.trim()}>Attach</button>
      </form>
      <ErrorNote error={actionError} />
    </div>
  );
}

export default function Projects({ ask }: { ask?: (text: string) => void }) {
  const [status, setStatus] = useState("active");
  const [open, setOpen] = useState<number | null>(null);
  const { data, error, loading, reload } = useLoad<Project[]>(`/api/projects?status=${status}`);
  const blockers = data?.reduce((n, p) => n + p.open_blockers, 0) ?? 0;
  const late = data?.filter((p) => isOverdue(p.deadline)).length ?? 0;
  return (
    <div>
      <div className="toolbar">
        <span className="seg" role="group" aria-label="Project status">
          {["active", "paused", "completed", "archived", "all"].map((s) => (
            <button key={s} className={status === s ? "" : "ghost"} aria-pressed={status === s} onClick={() => { setStatus(s); setOpen(null); }}>{s}</button>
          ))}
        </span>
      </div>
      {data && data.length > 0 && (
        <div className="chips">
          <span className="chip"><Icon name="project" size={14} /><b>{data.length}</b> {status === "all" ? "projects" : status}</span>
          {blockers > 0 && <span className="chip bad"><Icon name="alert" size={14} /><b>{blockers}</b> open blocker{blockers === 1 ? "" : "s"}</span>}
          {late > 0 && <span className="chip warn"><Icon name="clock" size={14} /><b>{late}</b> past deadline</span>}
        </div>
      )}
      <Loading loading={loading} error={error} />
      {data?.length === 0 && <Empty>No {status} projects. Tell Keel about one in Chat (“I'm starting my thesis project, due in March”).</Empty>}
      <div className="grid">
        {data?.map((p) => {
          const total = p.progress?.tasks_total ?? 0;
          const done = p.progress?.done ?? 0;
          const pct = total ? Math.round((done / total) * 100) : 0;
          return (
            <Card key={p.id} title={p.name} right={<Badge tone={p.priority === "high" || p.priority === "urgent" ? "bad" : ""}>{p.priority}</Badge>}>
              {p.purpose && <p><strong>Why:</strong> {p.purpose}</p>}
              {p.description && <p className="muted">{p.description}</p>}
              {total > 0 && (
                <>
                  <div className="progress" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label={`${p.name} progress`}><span style={{ width: `${pct}%` }} /></div>
                  <p className="small muted"><span>{done}/{total} tasks done</span> · {pct}%</p>
                </>
              )}
              <p className="small">
                {p.deadline && <Badge tone={isOverdue(p.deadline) ? "bad" : ""}>due {fmtDate(p.deadline)}</Badge>}{" "}
                {p.open_blockers > 0 && <Badge tone="bad">{p.open_blockers} blocker(s)</Badge>}
              </p>
              <div className="ob-actions">
                <button className="ghost small" aria-expanded={open === p.id} onClick={() => setOpen(open === p.id ? null : p.id)}>{open === p.id ? "Hide" : "Details"}</button>
                {ask && <button className="ghost small" onClick={() => ask(`What should I do next on my project “${p.name}”? Look at its open tasks and blockers.`)}><Icon name="sparkle" size={14} /> Plan next steps</button>}
              </div>
              {open === p.id && <Detail id={p.id} onChange={reload} />}
            </Card>
          );
        })}
      </div>
    </div>
  );
}
