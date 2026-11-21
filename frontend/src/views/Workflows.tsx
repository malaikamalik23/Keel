import { useState } from "react";
import { api } from "../api";
import { useAction, useLoad } from "../hooks";
import type { WorkflowRow, WorkflowRun, WorkflowTemplate } from "../types";
import { Badge, Card, Empty, ErrorNote, Loading } from "../ui";

const when = (iso: string) => iso.slice(0, 16).replace("T", " ");
const TONE: Record<string, string> = { done: "ok", failed: "bad", waiting_approval: "bad", retrying: "", running: "", cancelled: "" };

function stepLabel(wf: WorkflowRow, i: number) {
  const s = wf.steps[i];
  if (!s) return "";
  return s.notify ? `message: ${s.notify}` : `${s.tool}`;
}

function Run({ wf, run, act }: { wf: WorkflowRow; run: WorkflowRun; act: (path: string) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <li style={{ display: "block" }}>
      <span>
        <Badge tone={TONE[run.status] ?? ""}>{run.status.replace("_", " ")}</Badge>{" "}
        {when(run.started)}{run.status === "waiting_approval" && <> · step {run.step}: {stepLabel(wf, run.step - 1)}</>}
        {run.error && run.status !== "done" && <span className="muted"> · {run.error}</span>}
      </span>{" "}
      <span className="actions">
        {run.status === "waiting_approval" && <button onClick={() => act(`/api/workflow-runs/${run.id}/approve`)}>Approve step {run.step}</button>}
        {["waiting_approval", "running", "retrying"].includes(run.status) && <button className="ghost" onClick={() => act(`/api/workflow-runs/${run.id}/cancel`)}>Cancel</button>}
        {run.status === "failed" && <button className="ghost" onClick={() => act(`/api/workflow-runs/${run.id}/retry`)}>Retry</button>}
        <button className="ghost" onClick={() => setOpen(!open)}>{open ? "Hide log" : "Log"}</button>
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
  const act = (path: string) => run(() => api.post(path));
  const used = new Set(data?.map((w) => w.name));

  return (
    <div>
      <p className="small muted">Workflows are standing rules: when something happens, Keel runs the steps. Steps use Keel's own tools or send you a message; they never read web pages or documents. A step marked for approval waits for you here. Runs need the scheduler (SCHEDULER_ENABLED=true).</p>
      <ErrorNote error={actionError} />
      <Loading loading={loading} error={error} />
      {data?.length === 0 && <Empty>No workflows yet. Start from a template below, or describe one in Chat (“whenever I add a scholarship, make a task to gather the documents”).</Empty>}
      <div className="grid">
        {data?.map((wf) => (
          <Card key={wf.id} title={wf.name} right={
            <label className="small"><input type="checkbox" checked={wf.enabled} aria-label={`${wf.name} enabled`}
              onChange={(e) => run(() => api.patch(`/api/workflows/${wf.id}`, { enabled: e.target.checked }))} /> on</label>
          }>
            <p className="small">{wf.description}</p>
            <div className="toolbar">
              <button className="ghost" onClick={() => act(`/api/workflows/${wf.id}/run`)}>Run now</button>
              <button className="ghost danger" onClick={() => window.confirm(`Delete the workflow “${wf.name}” and its history?`) && run(() => api.del(`/api/workflows/${wf.id}`))}>Delete</button>
            </div>
            {wf.runs.length > 0 ? (
              <ul className="list">{wf.runs.map((r) => <Run key={r.id} wf={wf} run={r} act={act} />)}</ul>
            ) : <p className="muted small">No runs yet.</p>}
          </Card>
        ))}
      </div>
      {templates.data && (
        <Card title="Templates">
          <ul className="list">
            {templates.data.map((t) => (
              <li key={t.key}>
                <span><strong>{t.name}</strong><br /><span className="small muted">{t.description}</span></span>
                <button disabled={used.has(t.name)} onClick={() => run(() => api.post("/api/workflows", { name: t.name, trigger: t.trigger, steps: t.steps }))}>
                  {used.has(t.name) ? "Added" : "Add"}
                </button>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
