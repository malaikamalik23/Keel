import { useState } from "react";
import { api } from "../api";
import { useAction, useLoad } from "../hooks";
import { Icon } from "../icons";
import { useToast } from "../toast";
import type { PersonRow, PersonSummary } from "../types";
import { Badge, Card, Empty, ErrorNote, Loading, fmtDate } from "../ui";

const EVERY = [{ d: 0, label: "no reminder" }, { d: 7, label: "every week" }, { d: 14, label: "every 2 weeks" }, { d: 30, label: "every month" }, { d: 90, label: "every 3 months" }];
const initials = (name: string) => name.replace(/^(dr|mr|mrs|ms|prof)\.?\s+/i, "").split(/\s+/).slice(0, 2).map((w) => w[0]?.toUpperCase() ?? "").join("");
const hue = (name: string) => [...name].reduce((h, ch) => (h * 31 + ch.charCodeAt(0)) % 360, 7);
const lastTalked = (days: number | null) => (days === null ? "no contact logged" : days === 0 ? "talked today" : days === 1 ? "talked yesterday" : `last talked ${days} days ago`);

function Avatar({ name }: { name: string }) {
  return <span className="avatar" style={{ background: `hsl(${hue(name)} 55% 45%)` }} aria-hidden="true">{initials(name) || "?"}</span>;
}

function Detail({ id, onChange, ask }: { id: number; onChange: () => Promise<void>; ask?: (text: string) => void }) {
  const { data: p, error, loading, reload } = useLoad<PersonSummary>(`/api/people/${id}`);
  const { run, error: actionError } = useAction(async () => { await reload(); await onChange(); });
  const toast = useToast();
  const [note, setNote] = useState("");
  const [when, setWhen] = useState("");
  const [notes, setNotes] = useState<string | null>(null);
  if (!p) return <Loading loading={loading} error={error} />;
  const patch = (body: object, msg: string) => run(async () => { await api.patch(`/api/people/${id}`, body); toast(msg); });

  return (
    <div className="small proj-detail">
      {(p.promises.length > 0 || p.waiting_on_them.length > 0 || p.tasks.length > 0) && (
        <ul className="list">
          {p.promises.map((c) => (
            <li key={`c${c.id}`}><span><Icon name="promise" size={14} /> You promised: {c.text} {c.deadline && <Badge tone={c.overdue ? "bad" : ""}>{c.overdue ? "overdue" : "by"} {fmtDate(c.deadline)}</Badge>}</span></li>
          ))}
          {p.waiting_on_them.map((t) => (
            <li key={`w${t.id}`}><span><Icon name="clock" size={14} /> Waiting on them: {t.title} {t.follow_up_date && <Badge tone={t.follow_up_due ? "warn" : ""}>follow up {fmtDate(t.follow_up_date)}</Badge>}</span></li>
          ))}
          {p.tasks.map((t) => (
            <li key={`t${t.id}`}><span><Icon name="check" size={14} /> To do: {t.title} {t.due && <Badge>{fmtDate(t.due)}</Badge>}</span></li>
          ))}
        </ul>
      )}
      {p.memories.length > 0 && <p className="muted">Keel also remembers: {p.memories.map((m) => m.content).join(" · ")}</p>}

      <p className="muted">Notes</p>
      {notes === null ? (
        <p className="person-notes" onClick={() => setNotes(p.notes ?? "")} role="button" tabIndex={0} aria-label="Edit notes"
          onKeyDown={(e) => { if (e.key === "Enter") setNotes(p.notes ?? ""); }}>{p.notes || <span className="muted">Add notes: how you know them, what they care about…</span>}</p>
      ) : (
        <form onSubmit={(e) => { e.preventDefault(); void patch({ notes }, "Notes saved").then(() => setNotes(null)); }}>
          <textarea rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={2000} aria-label={`Notes about ${p.name}`} autoFocus />
          <div className="toolbar"><button type="submit">Save</button><button type="button" className="ghost" onClick={() => setNotes(null)}>Cancel</button></div>
        </form>
      )}
      <div className="toolbar">
        <label>Keep in touch{" "}
          <select value={p.keep_in_touch_days ?? 0} aria-label={`Keep in touch with ${p.name}`}
            onChange={(e) => { const d = Number(e.target.value); void patch({ keep_in_touch_days: d || null }, d ? `Keel will nudge you ${EVERY.find((x) => x.d === d)?.label ?? `every ${d} days`}` : "No more nudges"); }}>
            {[...EVERY, ...(p.keep_in_touch_days && !EVERY.some((x) => x.d === p.keep_in_touch_days) ? [{ d: p.keep_in_touch_days, label: `every ${p.keep_in_touch_days} days` }] : [])]
              .map((x) => <option key={x.d} value={x.d}>{x.label}</option>)}
          </select>
        </label>
        {p.email && <a href={`mailto:${p.email}`}>{p.email}</a>}
      </div>

      <p className="muted">Contact log</p>
      <form className="toolbar" onSubmit={(e) => {
        e.preventDefault();
        void run(async () => {
          const after = await api.post<PersonSummary>(`/api/people/${id}/contacts`, { note: note.trim(), ...(when ? { on_date: when } : {}) });
          const logged = after.interactions.length ? Math.max(...after.interactions.map((i) => i.id)) : undefined;
          setNote(""); setWhen("");
          toast(`Logged a contact with ${p.name}`, logged ? { action: { label: "Undo", run: () => run(() => api.del(`/api/person-contacts/${logged}`)) } } : undefined);
        });
      }}>
        <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="What did you talk about?" aria-label={`What you talked about with ${p.name}`} maxLength={500} />
        <input type="date" value={when} onChange={(e) => setWhen(e.target.value)} aria-label="When" max={new Date().toLocaleDateString("en-CA")} />
        <button type="submit" className="ghost">Log</button>
      </form>
      {p.interactions.length === 0 ? <p className="muted">Nothing logged yet.</p> : (
        <ul className="proj-timeline">
          {p.interactions.map((i) => (
            <li key={i.id}><Icon name="chat" size={14} /><span>{fmtDate(i.date)}{i.text ? `: ${i.text}` : ""}</span>
              <button className="ghost small" aria-label={`Remove the contact on ${i.date}`} onClick={() => void run(async () => { await api.del(`/api/person-contacts/${i.id}`); toast("Contact removed", { tone: "info" }); })}>×</button></li>
          ))}
        </ul>
      )}
      <div className="ob-actions">
        {ask && <button className="ghost small" onClick={() => ask(`Brief me on ${p.name} before I talk to them: what I promised, what I'm waiting on, and what we last discussed.`)}><Icon name="sparkle" size={14} /> Brief me before we talk</button>}
        <button className="ghost danger small" onClick={() => window.confirm(`Forget ${p.name} and their contact log? Promises and tasks that mention them stay.`) && void run(async () => { await api.del(`/api/people/${id}`); toast(`Forgot ${p.name}`, { tone: "info" }); })}>Forget</button>
      </div>
      <ErrorNote error={actionError} />
    </div>
  );
}

/** People: who someone is, when you last talked, what you promised them, and who you meant to get back to. */
export default function People({ ask }: { ask?: (text: string) => void }) {
  const { data, error, loading, reload } = useLoad<PersonRow[]>("/api/people");
  const { run, error: actionError } = useAction(reload);
  const toast = useToast();
  const [open, setOpen] = useState<number | null>(null);
  const [q, setQ] = useState("");
  const [name, setName] = useState("");
  const [relation, setRelation] = useState("");
  const [every, setEvery] = useState(0);

  const add = () => run(async () => {
    const p = await api.post<PersonSummary>("/api/people", { name: name.trim(), relation: relation.trim() || undefined, keep_in_touch_days: every || undefined });
    setName(""); setRelation(""); setEvery(0); setOpen(p.id);
    toast(`Saved ${p.name}`);
  });
  const talked = (p: PersonRow) => run(async () => {
    const after = await api.post<PersonSummary>(`/api/people/${p.id}/contacts`, {});
    const logged = after.interactions.length ? Math.max(...after.interactions.map((i) => i.id)) : undefined;
    toast(`Logged: talked to ${p.name} today`, logged ? { action: { label: "Undo", run: () => run(() => api.del(`/api/person-contacts/${logged}`)) } } : undefined);
  });

  const needle = q.trim().toLowerCase();
  const shown = (data ?? []).filter((p) => !needle || `${p.name} ${p.relation ?? ""} ${p.aliases.join(" ")}`.toLowerCase().includes(needle));
  const due = (data ?? []).filter((p) => p.reconnect_due).length;
  const promises = (data ?? []).reduce((n, p) => n + p.promises, 0);

  return (
    <div>
      <Card title="Add someone">
        <form className="toolbar" onSubmit={(e) => { e.preventDefault(); if (name.trim().length >= 2) void add(); }}>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name, e.g. Dr. Khan" aria-label="Name" maxLength={120} />
          <input value={relation} onChange={(e) => setRelation(e.target.value)} placeholder="Who they are: supervisor, friend…" aria-label="Relation" maxLength={60} />
          <select value={every} onChange={(e) => setEvery(Number(e.target.value))} aria-label="Keep in touch">
            {EVERY.map((x) => <option key={x.d} value={x.d}>{x.d ? `keep in touch ${x.label}` : "no reminder"}</option>)}
          </select>
          <button type="submit" disabled={name.trim().length < 2}><Icon name="plus" size={15} /> Add</button>
        </form>
        <p className="small muted">Or just tell Keel in Chat: “Dr. Khan is my supervisor”, “I talked to Sara today about the slides”. Promises and tasks that mention someone by name show up on their card by themselves.</p>
        <ErrorNote error={actionError} />
      </Card>

      {data && data.length > 0 && (
        <div className="toolbar">
          <div className="chips">
            <span className="chip"><Icon name="people" size={14} /><b>{data.length}</b> {data.length === 1 ? "person" : "people"}</span>
            {due > 0 && <span className="chip warn"><Icon name="clock" size={14} /><b>{due}</b> to get back to</span>}
            {promises > 0 && <span className="chip"><Icon name="promise" size={14} /><b>{promises}</b> open {promises === 1 ? "promise" : "promises"}</span>}
          </div>
          <label className="search"><Icon name="search" size={15} /><input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find someone…" aria-label="Find someone" /></label>
        </div>
      )}
      <Loading loading={loading} error={error} />
      {data?.length === 0 && <Empty>No one saved yet. Add the people you work with or want to stay close to.</Empty>}
      {data && data.length > 0 && shown.length === 0 && <Empty>Nobody matches “{q}”.</Empty>}
      <div className="grid">
        {shown.map((p) => (
          <Card key={p.id} title={p.name} right={<Avatar name={p.name} />}>
            <p className="small">
              {p.relation && <Badge>{p.relation}</Badge>}{" "}
              <span className={p.reconnect_due ? "warn-text" : "muted"}>{lastTalked(p.days_since_contact)}{p.reconnect_due && p.keep_in_touch_days ? ` · you wanted every ${p.keep_in_touch_days} days` : ""}</span>
            </p>
            {(p.promises > 0 || p.waiting > 0) && (
              <p className="small">
                {p.promises > 0 && <Badge tone={p.overdue_promises ? "bad" : ""}>{p.promises} {p.promises === 1 ? "promise" : "promises"}{p.overdue_promises ? `, ${p.overdue_promises} overdue` : ""}</Badge>}{" "}
                {p.waiting > 0 && <Badge tone={p.follow_ups_due ? "warn" : ""}>waiting on them ×{p.waiting}</Badge>}
              </p>
            )}
            <div className="ob-actions">
              <button className={p.reconnect_due ? "small" : "ghost small"} onClick={() => void talked(p)}><Icon name="check" size={14} /> Talked today</button>
              <button className="ghost small" aria-expanded={open === p.id} onClick={() => setOpen(open === p.id ? null : p.id)}>{open === p.id ? "Hide" : "Details"}</button>
            </div>
            {open === p.id && <Detail key={`${p.id}-${p.last_contact}-${p.keep_in_touch_days}`} id={p.id} onChange={reload} ask={ask} />}
          </Card>
        ))}
      </div>
    </div>
  );
}
