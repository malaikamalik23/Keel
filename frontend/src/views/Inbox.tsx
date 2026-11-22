import { useState } from "react";
import { api } from "../api";
import { useLoad } from "../hooks";
import { Badge, Card, Empty, ErrorNote, Loading } from "../ui";

type Kind = "task" | "reminder" | "memory" | "note" | "project_update";
type Suggestion = { kind: Kind; fields: Record<string, string | number>; why: string; alternatives: Kind[] };
type Item = { id: number; text: string; status: string; suggestion: Suggestion | null; filed_as: string | null; created_at: string };
type InboxData = { new: Item[]; recent: Item[]; projects: { id: number; name: string }[]; notes: boolean };

const LABEL: Record<Kind, string> = { task: "Task", reminder: "Reminder", memory: "Memory", note: "Note", project_update: "Project update" };

/** Defaults for a type the user switched to, carried over from what they typed and what Keel understood. */
function fieldsFor(kind: Kind, item: Item, data: InboxData): Record<string, string | number> {
  const s = item.suggestion;
  const old = s?.fields ?? {};
  const text = String(old.title ?? old.text ?? old.content ?? item.text);
  if (s && kind === s.kind) return { ...old };
  switch (kind) {
    case "task": return { title: text, due_date: String(old.due_date ?? String(old.remind_at ?? "").slice(0, 10)) };
    case "reminder": return { text, remind_at: String(old.remind_at ?? "") };
    case "memory": return { content: item.text, kind: "fact" };
    case "note": return { title: text.slice(0, 80), content: item.text };
    case "project_update": return { project_id: data.projects[0]?.id ?? "", kind: "update", text: item.text };
  }
}

function Field({ label, children }: { label: string; children: JSX.Element }) {
  return <label className="ob-field small">{label}{children}</label>;
}

function Card1({ item, data, onDone }: { item: Item; data: InboxData; onDone: () => void }) {
  const first = item.suggestion?.kind ?? "task";
  const [kind, setKind] = useState<Kind>(first);
  const [fields, setFields] = useState(() => fieldsFor(first, item, data));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const kinds: Kind[] = [first, ...(item.suggestion?.alternatives ?? [])];
  const set = (k: string) => (e: { target: { value: string } }) => setFields((f) => ({ ...f, [k]: e.target.value }));
  const switchTo = (k: Kind) => { setKind(k); setFields(fieldsFor(k, item, data)); };
  const act = async (path: string, body?: unknown) => {
    setBusy(true);
    try { await api.post(path, body); setError(null); onDone(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };

  return (
    <Card title={item.text} right={<Badge tone="warn">{kind === first ? "suggested" : "changed"}</Badge>}>
      {item.suggestion && kind === first && <p className="small muted">{item.suggestion.why}</p>}
      <div className="ob-row">
        <Field label="File as">
          <select value={kind} onChange={(e) => switchTo(e.target.value as Kind)} aria-label={`Type for: ${item.text}`}>
            {kinds.map((k) => <option key={k} value={k}>{LABEL[k]}</option>)}
          </select>
        </Field>
      </div>
      {kind === "task" && <div className="ob-row">
        <Field label="Title"><input value={String(fields.title ?? "")} onChange={set("title")} maxLength={200} /></Field>
        <Field label="Due"><input type="date" value={String(fields.due_date ?? "")} onChange={set("due_date")} /></Field>
      </div>}
      {kind === "reminder" && <div className="ob-row">
        <Field label="Reminder text"><input value={String(fields.text ?? "")} onChange={set("text")} maxLength={300} /></Field>
        <Field label="When"><input type="datetime-local" value={String(fields.remind_at ?? "")} onChange={set("remind_at")} /></Field>
      </div>}
      {kind === "memory" && <div className="ob-row">
        <Field label="Remember"><input value={String(fields.content ?? "")} onChange={set("content")} maxLength={1000} /></Field>
        <Field label="Kind"><select value={String(fields.kind ?? "fact")} onChange={set("kind")}>
          {["fact", "preference", "decision", "context"].map((k) => <option key={k}>{k}</option>)}</select></Field>
      </div>}
      {kind === "note" && <>
        <Field label="Title"><input value={String(fields.title ?? "")} onChange={set("title")} maxLength={80} /></Field>
        <Field label="Note"><textarea rows={3} value={String(fields.content ?? "")} onChange={set("content")} /></Field>
      </>}
      {kind === "project_update" && <div className="ob-row">
        <Field label="Project"><select value={String(fields.project_id ?? "")} onChange={set("project_id")}>
          {data.projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></Field>
        <Field label="Kind"><select value={String(fields.kind ?? "update")} onChange={set("kind")}>
          {["update", "blocker", "decision"].map((k) => <option key={k}>{k}</option>)}</select></Field>
        <Field label="Text"><input value={String(fields.text ?? "")} onChange={set("text")} maxLength={2000} /></Field>
      </div>}
      <ErrorNote error={error} />
      <div className="ob-actions">
        <button disabled={busy} onClick={() => void act(`/api/inbox/${item.id}/file`, {
          kind, fields: kind === "project_update" ? { ...fields, project_id: Number(fields.project_id) } : fields,
        })}>{`File as ${LABEL[kind].toLowerCase()}`}</button>
        <button className="ghost" disabled={busy} onClick={() => void act(`/api/inbox/${item.id}/dismiss`)}>Dismiss</button>
      </div>
    </Card>
  );
}

/** Type anything; Keel suggests where it belongs; nothing is saved anywhere until you file it. */
export default function Inbox({ onChange }: { onChange?: () => void }) {
  const { data, error, loading, reload } = useLoad<InboxData>("/api/inbox");
  const [text, setText] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const done = async () => { await reload(); onChange?.(); };

  const capture = async () => {
    const value = text.trim();
    if (!value) return;
    setText("");  // clear at once, so the next thing typed is a new capture, not appended to this one
    try { await api.post("/api/inbox", { text: value }); setProblem(null); await reload(); }
    catch (e) { setText((t) => t || value); setProblem(e instanceof Error ? e.message : String(e)); }
  };

  return (
    <div>
      <form className="capture" onSubmit={(e) => { e.preventDefault(); void capture(); }}>
        <input value={text} onChange={(e) => setText(e.target.value)} maxLength={2000} aria-label="Capture"
          placeholder="Anything: “renew my passport”, “remind me at 6pm to call the bank”, “remember my CGPA is 3.6”…" />
        <button type="submit" disabled={!text.trim()}>Capture</button>
      </form>
      <p className="small muted">Keel suggests a type from your words (no AI call, nothing sent anywhere). Nothing is saved until you file it, and you can change the type or any field first.</p>
      <ErrorNote error={problem} />
      <Loading loading={loading} error={error} />
      {data && (data.new.length === 0
        ? <Empty>Inbox empty. Type anything above.</Empty>
        : <div className="grid">{data.new.map((i) => <Card1 key={i.id} item={i} data={data} onDone={() => void done()} />)}</div>)}
      {data && data.recent.length > 0 && (
        <Card title="Recently handled">
          <ul className="svc-details small">
            {data.recent.map((i) => <li key={i.id}>{i.text} <span className="muted">→ {i.status === "filed" ? i.filed_as : "dismissed"}</span></li>)}
          </ul>
        </Card>
      )}
    </div>
  );
}
