import { useState } from "react";
import { api } from "../api";
import { useLoad } from "../hooks";
import type { ServiceCheck, ServiceRow } from "../types";
import { Badge, Card, ErrorNote, Loading } from "../ui";

const STATUS: Record<ServiceRow["status"], [string, string]> = {
  connected: ["connected", "ok"], partial: ["incomplete", "warn"], not_set_up: ["not set up", ""], off: ["off", ""],
};

export default function Services() {
  const { data, error, loading } = useLoad<ServiceRow[]>("/api/services");
  const [checks, setChecks] = useState<Record<string, ServiceCheck | "running">>({});
  const [actionError, setActionError] = useState<string | null>(null);

  const test = async (id: string) => {
    setChecks((c) => ({ ...c, [id]: "running" }));
    try { const r = await api.post<ServiceCheck>(`/api/services/${id}/test`); setChecks((c) => ({ ...c, [id]: r })); setActionError(null); }
    catch (e) { setChecks((c) => { const { [id]: _, ...rest } = c; return rest; }); setActionError(e instanceof Error ? e.message : String(e)); }
  };

  return (
    <div>
      <p className="small muted">What Keel is connected to and what each connection may do. Keys and passwords are never shown here; they live in your .env file. "Check" makes one read-only request: nothing is sent or changed.</p>
      <ErrorNote error={actionError} />
      <Loading loading={loading} error={error} />
      <div className="grid">
        {data?.map((s) => {
          const [text, tone] = STATUS[s.status];
          const check = checks[s.id];
          return (
            <Card key={s.id} title={s.name} right={<Badge tone={tone}>{text}</Badge>}>
              {s.details.length > 0 && <ul className="small svc-details">{s.details.map((d) => <li key={d}>{d}</li>)}</ul>}
              <p className="small muted">{s.access}</p>
              {(s.status === "not_set_up" || s.status === "partial" || s.status === "off") && <p className="small">To set up: <code>{s.setup}</code></p>}
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
    </div>
  );
}
