import { useEffect, useState } from "react";
import { api } from "../api";
import { useLoad } from "../hooks";
import type { ServiceRow } from "../types";
import { Badge, Card, ErrorNote, Loading } from "../ui";

export type Profile = {
  state: "new" | "done" | "skipped";
  name: string; address_as: string;
  goals: string[]; responsibilities: string[]; preferences: string[];
  settings: Record<string, string | number>;
  overridden: string[];
};

const STEPS = ["You", "Your day", "Goals", "Preferences", "Reminders", "Services"] as const;
const lines = (s: string) => s.split("\n").map((l) => l.trim()).filter(Boolean);
const browserZone = () => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone; } catch { return ""; } };

/** First-run setup, and later "edit my profile". Every step can be skipped; nothing is required. */
export default function Onboarding({ onDone }: { onDone: () => void }) {
  const { data, error, loading } = useLoad<Profile>("/api/profile");
  const services = useLoad<ServiceRow[]>("/api/services");
  const [step, setStep] = useState(0);
  const [form, setForm] = useState({ name: "", address_as: "", goals: "", responsibilities: "", preferences: "" });
  const [s, setS] = useState<Record<string, string | number>>({});
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    if (!data) return;
    setForm({ name: data.name, address_as: data.address_as, goals: data.goals.join("\n"),
              responsibilities: data.responsibilities.join("\n"), preferences: data.preferences.join("\n") });
    const zone = browserZone();
    setS({ ...data.settings, ...(data.state === "new" && data.settings.agent_timezone === "UTC" && zone ? { agent_timezone: zone } : {}) });
  }, [data]);

  if (!data) return <Loading loading={loading} error={error} />;
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const setting = (k: string, number = false) => (e: { target: { value: string } }) =>
    setS((x) => ({ ...x, [k]: number ? Number(e.target.value) : e.target.value }));

  const finish = async (state: "done" | "skipped") => {
    setSaving(true);
    try {
      const changed = Object.fromEntries(Object.entries(s).filter(([k, v]) => data.settings[k] !== v));
      await api.post("/api/profile", {
        state, settings: changed,
        answers: state === "skipped" ? {} : { name: form.name, address_as: form.address_as, goals: lines(form.goals),
          responsibilities: lines(form.responsibilities), preferences: lines(form.preferences) },
      });
      onDone();
    } catch (e) { setProblem(e instanceof Error ? e.message : String(e)); }
    finally { setSaving(false); }
  };

  const field = (label: string, input: JSX.Element, hint?: string) => (
    <label className="ob-field">{label}{input}{hint && <span className="small muted">{hint}</span>}</label>
  );

  return (
    <div className="onboarding">
      <h1>{data.state === "new" ? "Welcome to Keel" : "Your profile"}</h1>
      <p className="muted">{data.state === "new"
        ? "A few questions so Keel can plan around your real day. Skip anything you like; you can change all of it later under Profile."
        : "Change anything and save. Answers become memories you can see in the Memory tab; nothing is sent anywhere."}</p>
      <ol className="ob-steps" aria-label="Steps">
        {STEPS.map((t, i) => <li key={t} className={i === step ? "active" : i < step ? "done" : ""}><button className="ghost small" onClick={() => setStep(i)}>{t}</button></li>)}
      </ol>
      <ErrorNote error={problem} />
      <Card title={STEPS[step]}>
        {step === 0 && <>
          {field("Your name", <input value={form.name} onChange={set("name")} maxLength={80} autoComplete="name" />)}
          {field("What should Keel call you?", <input value={form.address_as} onChange={set("address_as")} maxLength={80} />, "e.g. a first name or nickname")}
        </>}
        {step === 1 && <>
          {field("Time zone", <input value={String(s.agent_timezone ?? "")} onChange={setting("agent_timezone")} placeholder="Asia/Karachi" />,
            "Keel's 'today', reminders and check-ins use it. Your browser says: " + (browserZone() || "unknown"))}
          <div className="ob-row">
            {field("Work starts", <input type="time" value={String(s.workday_start ?? "")} onChange={setting("workday_start")} />)}
            {field("Work ends", <input type="time" value={String(s.workday_end ?? "")} onChange={setting("workday_end")} />)}
          </div>
          <p className="small muted">The planner only puts work between these times, around your calendar.</p>
        </>}
        {step === 2 && <>
          {field("Main goals (one per line)", <textarea rows={4} value={form.goals} onChange={set("goals")} placeholder={"Finish my FYP\nGet a fully funded master's"} />,
            "Each becomes a goal; ask Keel in Chat to break one into milestones.")}
          {field("Responsibilities (one per line)", <textarea rows={3} value={form.responsibilities} onChange={set("responsibilities")} placeholder={"Teaching assistant for Data Structures\nHelping at home on weekends"} />)}
        </>}
        {step === 3 && field("Preferences (one per line)", <textarea rows={5} value={form.preferences} onChange={set("preferences")}
          placeholder={"I focus best late at night\nKeep messages short\nNo plans on Friday afternoons"} />, "Keel takes these into account when it plans and writes to you.")}
        {step === 4 && <>
          <div className="ob-row">
            {field("Morning check-in", <input type="time" value={String(s.morning_checkin_time ?? "")} onChange={setting("morning_checkin_time")} />)}
            {field("Evening check-in", <input type="time" value={String(s.evening_checkin_time ?? "")} onChange={setting("evening_checkin_time")} />)}
          </div>
          <div className="ob-row">
            {field("Quiet from", <input type="time" value={String(s.quiet_hours_start ?? "")} onChange={setting("quiet_hours_start")} />)}
            {field("Quiet until", <input type="time" value={String(s.quiet_hours_end ?? "")} onChange={setting("quiet_hours_end")} />)}
          </div>
          {field("At most this many messages a day", <input type="number" min={0} max={20} value={Number(s.notification_max_per_day ?? 5)} onChange={setting("notification_max_per_day", true)} />,
            "Reminders you asked for always come; check-ins and nudges share this budget.")}
          <p className="small muted">Reminders and check-ins come by email (set SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD and EMAIL_TO in .env), and only while the scheduler runs (SCHEDULER_ENABLED=true).</p>
        </>}
        {step === 5 && <>
          <p className="small muted">All optional: Keel works without any of them. Connections are set up in .env (see docs/INSTALL.md); this shows where each stands.</p>
          <ul className="svc-details small">
            {(services.data ?? []).map((r) => <li key={r.id}>{r.name} <Badge tone={r.status === "connected" ? "ok" : ""}>{r.status.replace(/_/g, " ")}</Badge>
              {r.status !== "connected" && <span className="muted"> · {r.setup}</span>}</li>)}
          </ul>
        </>}
      </Card>
      <div className="ob-actions">
        {step > 0 && <button className="ghost" onClick={() => setStep(step - 1)}>Back</button>}
        {step < STEPS.length - 1 && <button className="ghost" onClick={() => setStep(step + 1)}>Skip this step</button>}
        {step < STEPS.length - 1 ? <button onClick={() => setStep(step + 1)}>Next</button>
          : <button disabled={saving} onClick={() => void finish("done")}>{saving ? "Saving…" : "Save and finish"}</button>}
        {data.state === "new" && <button className="ghost" disabled={saving} onClick={() => void finish("skipped")}>Skip setup for now</button>}
        {data.state !== "new" && step < STEPS.length - 1 && <button className="ghost" disabled={saving} onClick={() => void finish("done")}>Save</button>}
      </div>
    </div>
  );
}
