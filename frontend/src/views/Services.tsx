import { useState } from "react";
import { api } from "../api";
import { useAction, useLoad } from "../hooks";
import type { ServiceCheck, ServiceRow } from "../types";
import { Badge, Card, ErrorNote, Loading } from "../ui";

const STATUS: Record<ServiceRow["status"], [string, string]> = {
  connected: ["connected", "ok"], partial: ["incomplete", "warn"], not_set_up: ["not set up", ""], off: ["off", ""],
};

export default function Services() {
  const { data, error, loading, reload } = useLoad<ServiceRow[]>("/api/services");
  const [checks, setChecks] = useState<Record<string, ServiceCheck | "running">>({});
  const [actionError, setActionError] = useState<string | null>(null);

  const test = async (id: string) => {
    setChecks((c) => ({ ...c, [id]: "running" }));
    try { const r = await api.post<ServiceCheck>(`/api/services/${id}/test`); setChecks((c) => ({ ...c, [id]: r })); setActionError(null); }
    catch (e) { setChecks((c) => { const { [id]: _, ...rest } = c; return rest; }); setActionError(e instanceof Error ? e.message : String(e)); }
  };

  const flip = async (id: string, on: boolean) => {
    try { await api.put(`/api/switches/${id}`, { on }); setActionError(null); await reload(); }
    catch (e) { setActionError(e instanceof Error ? e.message : String(e)); }
  };

  return (
    <div>
      <p className="small muted">What Keel is connected to and what each connection may do. Keys and passwords are never shown here; they live in your .env file. "Check" makes one read-only request: nothing is sent or changed. Switching something off takes effect at once, for chat, workflows and approvals already waiting; only you can switch it back on, here.</p>
      <ErrorNote error={actionError} />
      <Loading loading={loading} error={error} />
      <div className="grid">
        {data?.map((s) => {
          const [text, tone] = STATUS[s.status];
          const check = checks[s.id];
          return (
            <Card key={s.id} title={s.name} right={<>
              {s.switch && (
                <label className="svc-switch small">
                  <input type="checkbox" checked={s.switch.on} aria-label={`${s.switch.label} on`} onChange={(e) => void flip(s.switch!.id, e.target.checked)} />
                  {s.switch.on ? "On" : "Switched off"}
                </label>
              )}
              <Badge tone={s.switch && !s.switch.on ? "warn" : tone}>{s.switch && !s.switch.on ? "paused" : text}</Badge>
            </>}>
              {s.details.length > 0 && <ul className="small svc-details">{s.details.map((d) => <li key={d}>{d}</li>)}</ul>}
              <p className="small muted">{s.access}</p>
              {(s.status === "not_set_up" || s.status === "partial" || s.status === "off") && <p className="small">To set up: <code>{s.setup}</code></p>}
              {s.id === "backups" && <Backups />}
              {s.testable && (
                <div className="svc-check">
                  <button className="ghost" disabled={check === "running"} onClick={() => void test(s.id)}>{check === "running" ? "Checking…" : "Check connection"}</button>
                  {check && check !== "running" && (
                    <span role="status" className={`small ${check.ok ? "ok-text" : check.ok === false ? "error" : "muted"}`}>
                      {check.ok ? "Works" : check.ok === false ? "Failed" : "Not set up"}: {check.detail}
                    </span>
                  )}
                </div>
              )}
            </Card>
          );
        })}
      </div>
      <Retention />
    </div>
  );
}

type RetentionData = { categories: { id: string; label: string; min_days: number; note: string; days: number | null }[]; last_run: string | null };
const CHOICES = [null, 7, 30, 90, 180, 365];

function Retention() {
  const { data, reload } = useLoad<RetentionData>("/api/retention");
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);
  if (!data) return null;

  const save = async (id: string, days: number | null) => {
    try { await api.put("/api/retention", { [id]: days }); setError(null); await reload(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };
  const deleteNow = async () => {
    try {
      const { would_delete } = await api.post<{ would_delete: Record<string, number> }>("/api/retention/preview");
      const items = data.categories.filter((c) => would_delete[c.id]).map((c) => `${would_delete[c.id]} × ${c.label}`);
      if (items.length === 0) { setResult("Nothing is older than your rules."); return; }
      if (!window.confirm(`Permanently delete ${items.join(", ")}? This can't be undone (older backups still have them).`)) return;
      const { deleted } = await api.post<{ deleted: Record<string, number> }>("/api/retention/apply");
      setResult(`Deleted ${Object.values(deleted).reduce((a, b) => a + b, 0)} item(s).`);
      setError(null);
      await reload();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };
  const anyRule = data.categories.some((c) => c.days !== null);

  return (
    <Card title="Data retention">
      <p className="small muted">How long Keel keeps its logs and history. Everything is kept until you choose otherwise. What you made yourself (tasks, goals, projects, promises, memories, applications, notes) is never removed by these rules. With the scheduler on they run once a day.</p>
      <ErrorNote error={error} />
      <table className="retention">
        <tbody>
          {data.categories.map((c) => (
            <tr key={c.id}>
              <td><label htmlFor={`ret-${c.id}`}>{c.label}</label><div className="small muted">{c.note}</div></td>
              <td>
                <select id={`ret-${c.id}`} value={c.days ?? ""} onChange={(e) => void save(c.id, e.target.value === "" ? null : Number(e.target.value))}>
                  {CHOICES.filter((d) => d === null || d >= c.min_days || d === c.days).map((d) => (
                    <option key={d ?? "forever"} value={d ?? ""}>{d === null ? "Keep forever" : `${d} days`}</option>
                  ))}
                  {c.days !== null && !CHOICES.includes(c.days) && <option value={c.days}>{c.days} days</option>}
                </select>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="svc-check">
        <button className="ghost danger" disabled={!anyRule} onClick={() => void deleteNow()}>Delete expired now…</button>
        {result && <span role="status" className="small muted">{result}</span>}
        {data.last_run && <span className="small muted">Last run {new Date(data.last_run + "Z").toLocaleString()}</span>}
      </div>
    </Card>
  );
}

type BackupList = { enabled: boolean; folder?: string; backups: { name: string; size: number; created: string; encrypted: boolean }[] };

function Backups() {
  const { data, reload } = useLoad<BackupList>("/api/backups");
  const [busy, setBusy] = useState(false);
  const { run, error } = useAction(reload);
  const now = async () => { setBusy(true); try { await run(() => api.post("/api/backups")); } finally { setBusy(false); } };
  if (!data) return null;
  const latest = data.backups.slice(0, 3);
  return (
    <div className="small">
      {data.enabled && <button className="ghost" disabled={busy} onClick={() => void now()}>{busy ? "Backing up…" : "Back up now"}</button>}
      <ErrorNote error={error} />
      {latest.length > 0 ? (
        <ul className="svc-details">
          {latest.map((b) => (
            <li key={b.name}>{b.name} · {Math.max(1, Math.round(b.size / 1024))} KB{b.encrypted ? "" : " · not encrypted"}</li>
          ))}
        </ul>
      ) : <p className="muted">No backups yet{data.folder ? ` in ${data.folder}` : ""}.</p>}
      <p className="muted">To restore, stop Keel and run <code>python -m scripts.backup restore FILE</code>.</p>
    </div>
  );
}
