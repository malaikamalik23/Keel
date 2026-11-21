import { useState } from "react";
import { api } from "../api";
import { useAction, useLoad } from "../hooks";
import type { Opportunity } from "../types";
import { Badge, Card, Empty, ErrorNote, Loading, fmtDate } from "../ui";

const STATUSES = ["interested", "preparing", "applied", "interview", "offer", "accepted", "rejected", "withdrawn"];
const KINDS = ["job", "internship", "scholarship", "program", "other"];
const NEXT_DOC_STATUS: Record<string, string> = { needed: "drafting", drafting: "ready", ready: "needed", requested: "received", received: "requested" };

function safeLink(url: string | null): string | null {
  // Only plain http(s) links become clickable; anything else is shown as text.
  return url && /^https?:\/\//i.test(url) ? url : null;
}

function OpportunityCard({ o, onChange }: { o: Opportunity; onChange: () => Promise<void> }) {
  const { run, error } = useAction(onChange);
  const [open, setOpen] = useState<number | null>(null);
  const link = safeLink(o.url);
  const setDoc = (name: string, status: string) => run(() => api.patch(`/api/opportunities/${o.id}`, { documents: [{ name, status }] }));
  return (
    <Card
      title={`${o.title}${o.organization ? ` · ${o.organization}` : ""}`}
      right={
        <select value={o.status} aria-label={`Status of ${o.title}`} onChange={(e) => run(() => api.patch(`/api/opportunities/${o.id}`, { status: e.target.value }))}>
          {STATUSES.map((s) => <option key={s}>{s}</option>)}
        </select>
      }
    >
      <p className="small">
        <Badge>{o.kind}</Badge>{" "}
        {o.deadline && <Badge tone={o.days_left !== undefined && o.days_left <= 3 ? "bad" : ""}>closes {fmtDate(o.deadline)}{o.days_left !== undefined ? ` (${o.days_left}d)` : ""}</Badge>}{" "}
        {o.applied_on && <Badge tone="ok">applied {fmtDate(o.applied_on)}</Badge>}{" "}
        {link && <a href={link} target="_blank" rel="noopener noreferrer">posting</a>}
      </p>
      {o.next_step && <p className="small">Next: {o.next_step}{o.next_step_date ? ` (${fmtDate(o.next_step_date)})` : ""}</p>}
      {o.documents.length > 0 && (
        <ul className="list">
          {o.documents.map((d) => (
            <li key={d.name}>
              <span>{d.name}{d.from ? ` (${d.from})` : ""}</span>
              <button className={["ready", "received"].includes(d.status) ? "ghost" : ""} title="Click to change" onClick={() => setDoc(d.name, NEXT_DOC_STATUS[d.status] ?? "needed")}>{d.status}</button>
            </li>
          ))}
        </ul>
      )}
      {o.drafts.length > 0 && (
        <>
          <p className="muted small">Drafts</p>
          <ul className="list">
            {o.drafts.map((d) => (
              <li key={d.id} style={{ display: "block" }}>
                <span><Badge>{d.kind.replace("_", " ")}</Badge> {d.title}</span>{" "}
                <span className="actions">
                  <button className="ghost" onClick={() => setOpen(open === d.id ? null : d.id)}>{open === d.id ? "Hide" : "Show"}</button>
                  <button className="ghost" onClick={() => void navigator.clipboard?.writeText(d.content)}>Copy</button>
                  <button className="ghost danger" onClick={() => window.confirm("Delete this draft?") && run(() => api.del(`/api/drafts/${d.id}`))}>Delete</button>
                </span>
                {open === d.id && <pre className="draft">{d.content}</pre>}
              </li>
            ))}
          </ul>
        </>
      )}
      <ErrorNote error={error} />
    </Card>
  );
}

function AddOpportunity({ onAdded }: { onAdded: () => Promise<void> }) {
  const [title, setTitle] = useState("");
  const [org, setOrg] = useState("");
  const [kind, setKind] = useState("job");
  const [deadline, setDeadline] = useState("");
  const [url, setUrl] = useState("");
  const [docs, setDocs] = useState("");
  const { run, error } = useAction(onAdded);
  const add = () => run(async () => {
    await api.post("/api/opportunities", {
      title, kind, organization: org || undefined, deadline: deadline || undefined, url: url || undefined,
      documents: docs.split(",").map((d) => d.trim()).filter(Boolean),
    });
    setTitle(""); setOrg(""); setDeadline(""); setUrl(""); setDocs("");
  });
  return (
    <Card title="Track an application">
      <form onSubmit={(e) => { e.preventDefault(); if (title.trim()) void add(); }}>
        <div className="composer wrap">
          <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Title, e.g. Data Science Intern" aria-label="Application title" />
          <input value={org} onChange={(e) => setOrg(e.target.value)} placeholder="Organization" aria-label="Organization" />
          <select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Kind">{KINDS.map((k) => <option key={k}>{k}</option>)}</select>
          <input type="date" value={deadline} onChange={(e) => setDeadline(e.target.value)} aria-label="Deadline" />
          <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="Link to the posting" aria-label="Posting link" />
          <input value={docs} onChange={(e) => setDocs(e.target.value)} placeholder="Documents: CV, SOP, 2 recommendation letters" aria-label="Required documents" />
          <button type="submit" disabled={!title.trim()}>Add</button>
        </div>
      </form>
      <ErrorNote error={error} />
    </Card>
  );
}

export default function Applications() {
  const [show, setShow] = useState("active");
  const list = useLoad<Opportunity[]>(`/api/opportunities?status=${show}`);
  return (
    <div className="stack">
      <p className="muted small">Keel tracks and prepares; you submit. Ask in Chat to draft a cover letter or SOP from your profile: anything it doesn't know is left as [ADD: ...] for you.</p>
      <AddOpportunity onAdded={list.reload} />
      <div className="toolbar">
        <select value={show} onChange={(e) => setShow(e.target.value)} aria-label="Which applications">
          <option value="active">active</option>
          <option value="all">all, including closed</option>
        </select>
      </div>
      <Loading loading={list.loading} error={list.error} />
      {list.data?.length === 0 && <Empty>No applications tracked yet.</Empty>}
      {list.data?.map((o) => <OpportunityCard key={o.id} o={o} onChange={list.reload} />)}
    </div>
  );
}
