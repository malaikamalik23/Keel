import { useState } from "react";
import { api } from "../api";
import { useAction, useLoad } from "../hooks";
import { Icon } from "../icons";
import { useToast } from "../toast";
import type { WorkflowRow, WorkflowRun, WorkflowStep, WorkflowTemplate } from "../types";
import { Badge, Card, Empty, ErrorNote, Loading } from "../ui";

const when = (iso: string) => iso.slice(0, 16).replace("T", " ");
const TONE: Record<string, string> = { done: "ok", failed: "bad", waiting_approval: "warn", retrying: "", running: "", cancelled: "" };

function stepLabel(wf: WorkflowRow, i: number) {
  const s = wf.steps[i];
  if (!s) return "";
  return s.notify ? `message: ${s.notify}` : `${s.tool}`;
}

const pretty = (tool: string) => tool.replace(/_/g, " ");
const triggerLabel = (t: Record<string, unknown>) => {
  if (t.type === "schedule") {
    const days = Array.isArray(t.days) && t.days.length ? (t.days as string[]).join(", ") : "day";
    return `Every ${days}${t.time ? ` at ${String(t.time)}` : ""}`;
  }
  const on = String(t.event ?? t.type ?? "");
  return on ? `When ${pretty(on)}${t.status ? ` is ${String(t.status)}` : ""}` : "When triggered";
};

/** The rule drawn as a little flow: trigger → step → step. */
function Flow({ trigger, steps }: { trigger: Record<string, unknown>; steps: WorkflowStep[] }) {
  return (
    <ol className="flow" aria-label="Steps">
      <li className="flow-step trigger"><Icon name="bolt" size={13} /> {triggerLabel(trigger)}</li>
      {steps.map((s, i) => (
        <li key={i} className={`flow-step${s.approve ? " needs-ok" : ""}`}>
          <Icon name={s.notify ? "chat" : s.approve ? "approve" : "workflow"} size={13} /> {s.notify ? "Message you" : pretty(s.tool ?? "step")}
          {s.approve && <span className="small"> · asks first</span>}
        </li>
      ))}
    </ol>
  );
}

function Run({ wf, run, act }: { wf: WorkflowRow; run: WorkflowRun; act: (path: string, msg: string) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <li style={{ display: "block" }}>
      <span>
        <Badge tone={TONE[run.status] ?? ""}>{run.status.replace("_", " ")}</Badge>{" "}
        {when(run.started)}{run.status === "waiting_approval" && <> · step {run.step}: {stepLabel(wf, run.step - 1)}</>}
        {run.error && run.status !== "done" && <span className="muted"> · {run.error}</span>}
      </span>{" "}
      <span className="actions">
        {run.status === "waiting_approval" && <button onClick={() => act(`/api/workflow-runs/${run.id}/approve`, `Approved step ${run.step} of ${wf.name}`)}>Approve step {run.step}</button>}
        {["waiting_approval", "running", "retrying"].includes(run.status) && <button className="ghost" onClick={() => act(`/api/workflow-runs/${run.id}/cancel`, `Cancelled a run of ${wf.name}`)}>Cancel</button>}
        {run.status === "failed" && <button className="ghost" onClick={() => act(`/api/workflow-runs/${run.id}/retry`, `Retrying ${wf.name}`)}>Retry</button>}
        <button className="ghost" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? "Hide log" : "Log"}</button>
      </span>
      {open && (
        <ul className="list small">
          {run.log.map((e, i) => (
            <li key={i}><span>step {e.step + 1}: {e.state}{e.attempt ? ` (try ${e.attempt})` : ""}{e.error ? ` · ${e.error}` : ""}</span></li>
          ))}
        </ul>
      )}
    </li>
  );
}

export default function Workflows() {
  const { data, error, loading, reload } = useLoad<WorkflowRow[]>("/api/workflows");
  const templates = useLoad<WorkflowTemplate[]>("/api/workflow-templates");
  const { run, error: actionError } = useAction(reload);
  const toast = useToast();
  const act = (path: string, msg: string) => run(async () => { await api.post(path); toast(msg); });
  const used = new Set(data?.map((w) => w.name));
  const toggle = (wf: WorkflowRow, on: boolean) => run(async () => {
    await api.patch(`/api/workflows/${wf.id}`, { enabled: on });
    toast(`${wf.name} is ${on ? "on" : "off"}`, { tone: on ? "ok" : "info", action: { label: "Undo", run: () => run(() => api.patch(`/api/workflows/${wf.id}`, { enabled: !on })) } });
  });
  const waiting = data?.flatMap((w) => w.runs).filter((r) => r.status === "waiting_approval").length ?? 0;
  const failed = data?.flatMap((w) => w.runs).filter((r) => r.status === "failed").length ?? 0;

  return (
    <div>
      <p className="small muted">Workflows are standing rules: when something happens, Keel runs the steps. Steps use Keel's own tools or send you a message; they never read web pages or documents. A step marked for approval waits for you here. Runs need the scheduler (SCHEDULER_ENABLED=true).</p>
      {data && data.length > 0 && (
        <div className="chips">
          <span className="chip"><Icon name="workflow" size={14} /><b>{data.filter((w) => w.enabled).length}</b> of {data.length} on</span>
          {waiting > 0 && <span className="chip warn"><Icon name="approve" size={14} /><b>{waiting}</b> waiting for you</span>}
          {failed > 0 && <span className="chip bad"><Icon name="alert" size={14} /><b>{failed}</b> failed</span>}
        </div>
      )}
      <ErrorNote error={actionError} />
      <Loading loading={loading} error={error} />
      {data?.length === 0 && <Empty>No workflows yet. Start from a template below, or describe one in Chat (“whenever I add a scholarship, make a task to gather the documents”).</Empty>}
      <div className="grid">
        {data?.map((wf) => (
          <Card key={wf.id} title={wf.name} right={
            <label className="switch small">
              <input type="checkbox" role="switch" checked={wf.enabled} aria-label={`${wf.name} enabled`} onChange={(e) => void toggle(wf, e.target.checked)} />
              <span className="switch-track" aria-hidden="true" /> {wf.enabled ? "on" : "off"}
            </label>
          }>
            <p className="small">{wf.description}</p>
            <Flow trigger={wf.trigger} steps={wf.steps} />
            <div className="toolbar">
              <button className="ghost small" onClick={() => act(`/api/workflows/${wf.id}/run`, `Started ${wf.name}`)}><Icon name="bolt" size={14} /> Run now</button>
              <button className="ghost danger small" onClick={() => window.confirm(`Delete the workflow “${wf.name}” and its history?`) && run(async () => { await api.del(`/api/workflows/${wf.id}`); toast(`Deleted ${wf.name}`, { tone: "info" }); })}>Delete</button>
            </div>
            {wf.runs.length > 0 ? (
              <ul className="list">{wf.runs.map((r) => <Run key={r.id} wf={wf} run={r} act={act} />)}</ul>
            ) : <p className="muted small">No runs yet.</p>}
          </Card>
        ))}
      </div>
      {templates.data && (
        <Card title="Templates">
          <div className="grid templates">
            {templates.data.map((t) => (
              <div key={t.key} className="template">
                <strong>{t.name}</strong>
                <p className="small muted">{t.description}</p>
                <Flow trigger={t.trigger} steps={t.steps} />
                <button disabled={used.has(t.name)} onClick={() => run(async () => { await api.post("/api/workflows", { name: t.name, trigger: t.trigger, steps: t.steps }); toast(`Added ${t.name}`); })}>
                  {used.has(t.name) ? "Added" : "Add"}
                </button>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
