import { useCallback, useState } from "react";
import { api } from "../api";
import { useAction, useLoad } from "../hooks";
import { Icon } from "../icons";
import { useToast } from "../toast";
import type { Memory, MemoryReview } from "../types";
import { Badge, Card, Empty, ErrorNote, Loading } from "../ui";

const KINDS = ["preference", "context", "fact", "decision"];
const CATEGORIES = ["personal", "academic", "professional", "project", "general"];
const SOURCE: Record<string, string> = { chat: "saved in chat", approved: "you approved", user: "added by you", unknown: "older memory" };

function Row({ m, onChange, others = [] }: { m: Memory; onChange: () => Promise<void>; others?: Memory[] }) {
  const [editing, setEditing] = useState<string | null>(null);
  const [more, setMore] = useState(false);
  const { run, error } = useAction(onChange);
  const toast = useToast();
  const outdated = m.status === "outdated";
  const patch = (body: object) => run(() => api.patch(`/api/memories/${m.id}`, body));
  const short = m.content.length > 50 ? `${m.content.slice(0, 50)}…` : m.content;
  const setStatus = (s: "current" | "outdated") => run(async () => {
    await api.patch(`/api/memories/${m.id}`, { status: s });
    toast(s === "outdated" ? `Kept as history: ${short}` : `True again: ${short}`, { action: { label: "Undo", run: () => run(() => api.patch(`/api/memories/${m.id}`, { status: s === "outdated" ? "current" : "outdated" })) } });
  });
  const byId = new Map(others.map((o) => [o.id, o]));
  return (
    <li>
      {editing !== null ? (
        <>
          <textarea name="content" value={editing} onChange={(e) => setEditing(e.target.value)} rows={2} aria-label="Edit memory" />
          <span className="actions">
            <button onClick={() => run(async () => { await api.patch(`/api/memories/${m.id}`, { content: editing }); setEditing(null); toast("Memory updated; the old wording is kept as history"); })}>Save</button>
            <button className="ghost" onClick={() => setEditing(null)}>Cancel</button>
          </span>
        </>
      ) : (
        <>
          <span className={outdated ? "muted" : ""}>
            {m.topic && <strong>{m.topic}: </strong>}{outdated ? <s>{m.content}</s> : m.content}{" "}
            <Badge>{m.kind}</Badge> <Badge>{m.category}</Badge>
            {m.importance === "high" && <> <Badge tone="warn">high</Badge></>}
            {outdated && <> <Badge>no longer true</Badge></>}
            {!outdated && m.confidence === "low" && <> <Badge tone="warn">unsure</Badge></>}
            {m.valid_until && <span className="muted small"> · until {m.valid_until}</span>}
            <span className="muted small"> · {SOURCE[m.source] ?? m.source}</span>
            {(m.linked ?? []).length > 0 && <span className="muted small"> · linked to {(m.linked ?? []).map((id) => byId.get(id)?.content.slice(0, 40) ?? `#${id}`).join("; ")}</span>}
          </span>
          <span className="actions">
            {m.needs_review && !outdated && <button onClick={() => run(async () => { await api.patch(`/api/memories/${m.id}`, { confirm: true }); toast(`Confirmed: ${short}`); })}><Icon name="check" size={14} /> Still true</button>}
            {outdated
              ? <button className="ghost" onClick={() => void setStatus("current")}>True again</button>
              : <button className="ghost" onClick={() => void setStatus("outdated")}>No longer true</button>}
            <button className="ghost" onClick={() => setEditing(m.content)}>Edit</button>
            <button className="ghost" aria-expanded={more} onClick={() => setMore(!more)}>More</button>
            <button className="ghost danger" onClick={() => window.confirm("Permanently delete this memory? (\"No longer true\" keeps it as history instead.)") && run(async () => { await api.del(`/api/memories/${m.id}`); toast(`Deleted: ${short}`, { tone: "info" }); })}>Delete</button>
          </span>
          {more && (
            <span className="mem-more small">
              <label>How sure <select value={m.confidence ?? "medium"} onChange={(e) => void patch({ confidence: e.target.value })} aria-label={`How sure: ${m.content}`}>
                {["high", "medium", "low"].map((c) => <option key={c}>{c}</option>)}</select></label>
              <label>True until <input type="date" value={m.valid_until ?? ""} onChange={(e) => void patch({ valid_until: e.target.value || null })} aria-label={`True until: ${m.content}`} /></label>
              <label>Link to <select value="" onChange={(e) => e.target.value && void run(() => api.post(`/api/memories/${m.id}/links`, { other_id: Number(e.target.value) }))} aria-label={`Link: ${m.content}`}>
                <option value="">choose a memory…</option>
                {others.filter((o) => o.id !== m.id && !(m.linked ?? []).includes(o.id)).map((o) => <option key={o.id} value={o.id}>{o.content.slice(0, 60)}</option>)}
              </select></label>
              {(m.linked ?? []).map((id) => <button key={id} className="ghost small" onClick={() => void run(() => api.del(`/api/memories/${m.id}/links/${id}`))}>Unlink {byId.get(id)?.content.slice(0, 30) ?? `#${id}`}</button>)}
            </span>
          )}
        </>
      )}
      <ErrorNote error={error} />
    </li>
  );
}

function AddMemory({ onAdded }: { onAdded: () => Promise<void> }) {
  const [content, setContent] = useState("");
  const [kind, setKind] = useState("fact");
  const [category, setCategory] = useState("general");
  const [topic, setTopic] = useState("");
  const { run, error } = useAction(onAdded);
  const toast = useToast();
  const add = () => run(async () => {
    await api.post("/api/memories", { content, kind, category, ...(topic.trim() ? { topic } : {}) });
    toast("Memory saved");
    setContent(""); setTopic("");
  });
  return (
    <Card title="Add a memory">
      <form onSubmit={(e) => { e.preventDefault(); if (content.trim()) void add(); }}>
        <textarea name="new-memory" value={content} onChange={(e) => setContent(e.target.value)} rows={2} placeholder="e.g. My supervisor is Dr. Khan" aria-label="New memory" />
        <div className="composer wrap">
          <select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="New memory kind">{KINDS.map((k) => <option key={k}>{k}</option>)}</select>
          <select value={category} onChange={(e) => setCategory(e.target.value)} aria-label="New memory category">{CATEGORIES.map((k) => <option key={k}>{k}</option>)}</select>
          <input value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="profile topic (optional), e.g. supervisor" aria-label="Profile topic" />
          <button type="submit" disabled={!content.trim()}>Add</button>
        </div>
      </form>
      <ErrorNote error={error} />
    </Card>
  );
}

export default function MemoryView() {
  const [kind, setKind] = useState("all");
  const [category, setCategory] = useState("all");
  const [status, setStatus] = useState("current");
  const [q, setQ] = useState("");
  const list = useLoad<Memory[]>(`/api/memories?kind=${kind}&category=${category}&status=${status}`);
  const all = useLoad<Memory[]>("/api/memories");
  const review = useLoad<MemoryReview>("/api/memories/review");
  const { reload: reloadList } = list;
  const { reload: reloadAllMemories } = all;
  const { reload: reloadReview } = review;
  const reloadAll = useCallback(async () => {
    await Promise.all([reloadList(), reloadAllMemories(), reloadReview()]);
  }, [reloadList, reloadAllMemories, reloadReview]);

  const topics = (all.data ?? []).filter((m) => m.topic);
  const stale = review.data?.stale ?? [];
  const conflicts = review.data?.conflicts ?? [];
  const needle = q.trim().toLowerCase();
  const shown = (list.data ?? []).filter((m) => !needle || `${m.topic ?? ""} ${m.content} ${m.kind} ${m.category}`.toLowerCase().includes(needle));
  const current = (all.data ?? []).filter((m) => m.status !== "outdated").length;
  const toReview = stale.length + conflicts.length;

  return (
    <div className="stack">
      <p className="muted small">Everything the agent remembers about you. Edit, confirm or delete anything; changes are audited. "No longer true" keeps a fact as history (a past job, an old address) without Keel treating it as current; editing keeps the old wording the same way.</p>

      <div className="chips">
        <span className="chip"><Icon name="memory" size={14} /><b>{current}</b> {current === 1 ? "thing" : "things"} Keel knows</span>
        <span className="chip"><Icon name="user" size={14} /><b>{topics.length}</b> profile {topics.length === 1 ? "fact" : "facts"}</span>
        {toReview > 0 && <button className="chip warn" onClick={() => document.getElementById("needs-review")?.scrollIntoView({ behavior: "smooth" })}><Icon name="alert" size={14} /><b>{toReview}</b> to review</button>}
      </div>

      <Card title="Profile">
        {topics.length === 0 ? (
          <Empty>No profile facts yet. Tell the agent things like your university or supervisor, or add one below with a topic.</Empty>
        ) : (
          <ul className="list">{topics.map((m) => <Row key={m.id} m={m} onChange={reloadAll} others={all.data ?? []} />)}</ul>
        )}
      </Card>

      {(stale.length > 0 || conflicts.length > 0) && (
        <div id="needs-review">
        <Card title="Needs review">
          {conflicts.map((group) => (
            <div key={group[0].id}>
              <p className="small">Two answers for <strong>{group[0].topic}</strong>. Keep the right one and delete the other:</p>
              <ul className="list">{group.map((m) => <Row key={m.id} m={m} onChange={reloadAll} />)}</ul>
            </div>
          ))}
          {stale.length > 0 && <p className="small">Not confirmed for over six months, marked unsure, or past its end date. Still true?</p>}
          <ul className="list">{stale.map((m) => <Row key={m.id} m={m} onChange={reloadAll} others={all.data ?? []} />)}</ul>
        </Card>
        </div>
      )}

      <AddMemory onAdded={reloadAll} />

      <Card
        title="All memories"
        right={
          <span>
            <select name="kind" value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Memory kind">{["all", ...KINDS].map((k) => <option key={k} value={k}>{k === "all" ? "every kind" : k}</option>)}</select>{" "}
            <select name="category" value={category} onChange={(e) => setCategory(e.target.value)} aria-label="Memory category">{["all", ...CATEGORIES].map((k) => <option key={k} value={k}>{k === "all" ? "every category" : k}</option>)}</select>{" "}
            <select name="status" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Memory status">
              <option value="current">still true</option><option value="outdated">no longer true (history)</option><option value="all">all</option></select>
          </span>
        }
      >
        <label className="search"><Icon name="search" size={15} /><input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search memories…" aria-label="Search memories" /></label>
        <Loading loading={list.loading} error={list.error} />
        {list.data && shown.length === 0 && <Empty>{needle ? `Nothing matches “${q}”.` : "No memories match."}</Empty>}
        <ul className="list">{shown.map((m) => <Row key={m.id} m={m} onChange={reloadAll} others={all.data ?? []} />)}</ul>
      </Card>
    </div>
  );
}
