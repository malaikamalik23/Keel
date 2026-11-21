"""Workflows: "when X happens, do these steps", run by the scheduler with approvals, history, retries and recovery.

Design (why it is safe to let these run unattended):
- A workflow is created only with the user's approval (create_workflow is high impact), so every step is something
  the user signed off on. Runs execute with a clean, untainted context.
- Steps are Keel's own tools plus "notify". Tools that read external content (web, GitHub, notes, calendar) are not
  allowed: nothing a run reads can steer what it does next.
- A step marked `approve` (always, for high-impact tools such as deletes) pauses the run until the user approves
  that step of that run in the dashboard. The model has no tool that approves.
- Triggers are found by looking at the database each tick, not by in-memory events, so anything that happened
  while the app was off is still picked up. A unique (workflow, dedupe_key) row means one run per event or day.
- Every step is logged as started before it runs and ok/error after. A failed step is retried with backoff
  (1, 5, 30 minutes), then the run fails and the user is told. A run found with a step "started" but never
  finished (the app stopped mid-step) re-runs that step if it only reads; if it could have changed data, the
  run waits for the user to check and approve instead of doing it twice.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Opportunity, Project, ProjectEntry, Task, Workflow, WorkflowRun, utcnow
from app.services.timeutil import local_to_naive_utc, naive_utc_to_local
from app.tools.base import Permission, ToolContext, ToolRegistry

MAX_STEPS = 10
MAX_ATTEMPTS = 3
BACKOFF = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30))
WAIT_FOR_QUIET = timedelta(minutes=15)
SCHEDULE_GRACE = timedelta(hours=3)
ACTIVE = ("running", "retrying")
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
EVENTS = ("task_completed", "opportunity_added", "opportunity_status", "blocker_logged")
# Tools a workflow may never use: the workflow tools themselves (no workflows that build or start workflows).
NOT_IN_WORKFLOWS = {"load_tools", "gmail_send", "create_workflow", "run_workflow", "set_workflow_enabled", "list_workflows", "workflow_history"}


# ------------------------------------------------------------------ definitions
class Trigger(BaseModel):
    type: Literal["schedule", "event", "manual"]
    time: str | None = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$", description="schedule: local HH:MM")
    days: list[Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]] | None = Field(None, description="schedule: default every day")
    event: Literal["task_completed", "opportunity_added", "opportunity_status", "blocker_logged"] | None = None
    title_contains: str | None = Field(None, max_length=100, description="event: only when the title contains this")
    status: str | None = Field(None, max_length=20, description="opportunity_status: the new status, e.g. 'interview'")

    @model_validator(mode="after")
    def _complete(self):
        if self.type == "schedule" and not self.time:
            raise ValueError("A schedule trigger needs a time (HH:MM).")
        if self.type == "event" and not self.event:
            raise ValueError(f"An event trigger needs an event: {', '.join(EVENTS)}.")
        if self.event == "opportunity_status" and not self.status:
            raise ValueError("opportunity_status needs the status to watch for.")
        return self


class Step(BaseModel):
    tool: str | None = Field(None, max_length=64)
    args: dict[str, Any] = Field(default_factory=dict)
    notify: str | None = Field(None, max_length=500, description="Send the user this message instead of running a tool.")
    approve: bool = Field(False, description="Pause for the user's approval before this step.")

    @model_validator(mode="after")
    def _one(self):
        if bool(self.tool) == bool(self.notify):
            raise ValueError("Each step is either a tool call or a notify message.")
        return self

    @field_validator("args")
    @classmethod
    def _small(cls, v):
        if len(json.dumps(v, default=str)) > 4000:
            raise ValueError("Step arguments are too large.")
        return v


def validate_definition(registry: ToolRegistry, trigger: dict, steps: list[dict]) -> tuple[Trigger, list[Step]]:
    t = Trigger.model_validate(trigger)
    if not 1 <= len(steps) <= MAX_STEPS:
        raise ValueError(f"A workflow has 1 to {MAX_STEPS} steps.")
    parsed = [Step.model_validate(s) for s in steps]
    for i, s in enumerate(parsed, 1):
        if s.notify:
            continue
        tool = registry.get(s.tool or "")
        if tool is None or s.tool in NOT_IN_WORKFLOWS:
            raise ValueError(f"Step {i}: '{s.tool}' is not a tool workflows can use.")
        if tool.returns_untrusted:
            raise ValueError(f"Step {i}: '{s.tool}' reads external content, which workflows can't use.")
        fields = tool.input_model.model_fields
        unknown = set(s.args) - set(fields)
        if unknown:
            raise ValueError(f"Step {i}: {s.tool} has no argument(s) {', '.join(sorted(unknown))}.")
        missing = sorted(n for n, f in fields.items() if f.is_required() and n not in s.args)
        if missing:
            raise ValueError(f"Step {i}: {s.tool} needs {', '.join(missing)}.")
        if tool.permission is Permission.HIGH_IMPACT:
            s.approve = True  # deletes and other high-impact steps always wait for the user
    return t, parsed


def describe(trigger: dict, steps: list[dict]) -> str:
    t = trigger
    if t["type"] == "schedule":
        when = f"every {'/'.join(t['days']) if t.get('days') else 'day'} at {t['time']}"
    elif t["type"] == "event":
        when = {"task_completed": "a task is completed", "opportunity_added": "an application is added",
                "opportunity_status": f"an application moves to '{t.get('status')}'", "blocker_logged": "a project blocker is logged"}[t["event"]]
        if t.get("title_contains"):
            when += f" (title contains '{t['title_contains']}')"
    else:
        when = "you start it"
    parts = []
    for i, s in enumerate(steps, 1):
        what = f"tell you: {s['notify']}" if s.get("notify") else f"{s['tool']} {json.dumps(s.get('args') or {}, ensure_ascii=False)}"
        parts.append(f"{i}. {what}{' [waits for your approval]' if s.get('approve') else ''}")
    return f"When {when}: " + " ".join(parts)


# ------------------------------------------------------------------ templates: {{today}}, {{today+7}}, {{event.title}}, {{steps.1.task.id}}
_PLACEHOLDER = re.compile(r"\{\{\s*([\w.+-]+)\s*\}\}")


class TemplateError(ValueError):
    pass


def _lookup(name: str, scope: dict) -> Any:
    m = re.fullmatch(r"today([+-]\d{1,3})?", name)
    if m:
        return (scope["today"] + timedelta(days=int(m.group(1) or 0))).isoformat()
    cur: Any = scope
    for part in name.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and 0 < int(part) <= len(cur):
            cur = cur[int(part) - 1]  # steps.1 is the first step
        else:
            raise TemplateError(f"'{{{{{name}}}}}' has no value in this run.")
    return cur


def render(value: Any, scope: dict) -> Any:
    """Fill placeholders. Plain lookups only: there is no expression language to abuse."""
    if isinstance(value, str):
        whole = _PLACEHOLDER.fullmatch(value.strip())
        if whole:
            return _lookup(whole.group(1), scope)  # keeps numbers as numbers (e.g. a task id)
        return _PLACEHOLDER.sub(lambda m: str(_lookup(m.group(1), scope)), value)
    if isinstance(value, dict):
        return {k: render(v, scope) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, scope) for v in value]
    return value


# ------------------------------------------------------------------ triggers
def _matches(t: dict, title: str | None) -> bool:
    want = (t.get("title_contains") or "").casefold()
    return not want or want in (title or "").casefold()


def find_events(db: Session, wf: Workflow, since: datetime) -> list[tuple[str, dict]]:
    """(dedupe_key, event payload) for everything that happened after `since` (naive UTC) and matches."""
    t, out = wf.trigger, []
    ev = t.get("event")
    if ev == "task_completed":
        for x in db.scalars(select(Task).where(Task.status == "done", Task.completed_at > since)):
            if _matches(t, x.title):
                out.append((f"task:{x.id}", {"type": ev, "id": x.id, "title": x.title, "project_id": x.project_id}))
    elif ev == "opportunity_added":
        for o in db.scalars(select(Opportunity).where(Opportunity.created_at > since)):
            if _matches(t, o.title):
                out.append((f"opportunity:{o.id}", {"type": ev, "id": o.id, "title": o.title, "organization": o.organization, "kind": o.kind,
                                                    "deadline": o.deadline.isoformat() if o.deadline else None}))
    elif ev == "opportunity_status":
        for o in db.scalars(select(Opportunity).where(Opportunity.updated_at > since, Opportunity.status == t.get("status"))):
            if _matches(t, o.title):
                out.append((f"opportunity:{o.id}:{o.status}", {"type": ev, "id": o.id, "title": o.title, "status": o.status,
                                                               "organization": o.organization}))
    elif ev == "blocker_logged":
        for e in db.scalars(select(ProjectEntry).where(ProjectEntry.kind == "blocker", ProjectEntry.created_at > since)):
            project = db.get(Project, e.project_id)
            if _matches(t, project.name if project else None):
                out.append((f"blocker:{e.id}", {"type": ev, "id": e.id, "text": e.text[:300], "project": project.name if project else None,
                                                "project_id": e.project_id}))
    return out


def schedule_due(t: dict, now_local: datetime, created_local: datetime | None = None) -> str | None:
    """The dedupe key for today's scheduled run if it is due now (within the grace window), else None.
    A workflow created after today's time first runs tomorrow, not the minute it is made."""
    hh, mm = map(int, t["time"].split(":"))
    at = now_local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if WEEKDAYS[now_local.weekday()] not in (t.get("days") or WEEKDAYS):
        return None
    if created_local is not None and created_local > at:
        return None
    if at <= now_local <= at + SCHEDULE_GRACE:
        return f"schedule:{now_local.date().isoformat()}"
    return None


# ------------------------------------------------------------------ running
@dataclass
class Engine:
    executor: Any  # ToolExecutor
    notifications: Any  # NotificationService
    tz: ZoneInfo

    # -- starting runs
    def start(self, db: Session, wf: Workflow, dedupe_key: str, event: dict) -> WorkflowRun | None:
        run = WorkflowRun(workflow_id=wf.id, dedupe_key=dedupe_key[:200], event=event, status="running", log=[])
        db.add(run)
        try:
            db.commit()
        except IntegrityError:  # already started for this event/day
            db.rollback()
            return None
        return run

    def detect(self, db: Session, now_local: datetime) -> int:
        started = 0
        now_utc = local_to_naive_utc(now_local, self.tz)
        for wf in db.scalars(select(Workflow).where(Workflow.enabled.is_(True))):
            t = wf.trigger
            if t["type"] == "schedule":
                key = schedule_due(t, now_local, naive_utc_to_local(wf.created_at, self.tz))
                if key and self.start(db, wf, key, {"type": "schedule", "date": now_local.date().isoformat()}):
                    started += 1
            elif t["type"] == "event":
                for key, event in find_events(db, wf, wf.checked_at):
                    if self.start(db, wf, key, event):
                        started += 1
                wf.checked_at = now_utc
                db.commit()
        return started

    # -- advancing runs
    def tick(self, db: Session, now_local: datetime) -> dict:
        report = {"started": self.detect(db, now_local), "steps": 0}
        now_utc = local_to_naive_utc(now_local, self.tz)
        runs = db.scalars(select(WorkflowRun).where(WorkflowRun.status.in_(ACTIVE)).order_by(WorkflowRun.id)).all()
        for run in runs:
            if run.next_attempt_at is None or run.next_attempt_at <= now_utc:
                report["steps"] += self.advance(db, run, now_local)
        return report

    def _log(self, db: Session, run: WorkflowRun, entry: dict) -> None:
        run.log = [*run.log, {**entry, "at": utcnow().isoformat(timespec="seconds")}]
        db.commit()

    def _notify(self, db: Session, run: WorkflowRun, text: str, *, key: str, priority: str = "normal", now_local: datetime) -> str | None:
        res = self.notifications.send(db, kind="workflow", text=text, priority=priority, dedupe_key=key, now_local=now_local)
        return None if res.sent or res.reason == "duplicate" else res.reason

    def advance(self, db: Session, run: WorkflowRun, now_local: datetime) -> int:
        """Run steps until the workflow finishes, pauses for approval, or a step fails. Returns steps run."""
        wf = db.get(Workflow, run.workflow_id)
        if wf is None:
            return 0
        steps, done = wf.steps, 0
        scope = {"today": now_local.date(), "event": run.event,
                 "steps": [e.get("result") for e in run.log if e.get("state") == "ok"]}
        while run.status in ACTIVE and run.step_index < len(steps):
            i, step = run.step_index, steps[run.step_index]
            last = run.log[-1] if run.log else None
            if last and last.get("step") == i and last.get("state") == "started" and run.approved_step != i:
                # The app stopped in the middle of this step. Re-running a read is harmless; a write might repeat.
                tool = self.executor.registry.get(step.get("tool") or "")
                if step.get("tool") and (tool is None or tool.permission is not Permission.READ):
                    self._log(db, run, {"step": i, "state": "interrupted"})
                    return done + self._pause(db, run, i, now_local, why="was interrupted (the app stopped mid-step); check it ran or not, then approve to run it")
            if step.get("approve") and run.approved_step != i:
                return done + self._pause(db, run, i, now_local, why="needs your approval")
            self._log(db, run, {"step": i, "state": "started"})
            ok, result, error = self._run_step(db, run, i, step, scope, now_local)
            if ok is None:  # a message held back by quiet hours or the daily budget: try again later, not a failure
                run.log = run.log[:-1]
                run.next_attempt_at = local_to_naive_utc(now_local + WAIT_FOR_QUIET, self.tz)
                db.commit()
                return done
            if not ok:
                return done + self._fail_step(db, run, i, error or "failed", now_local)
            self._log(db, run, {"step": i, "state": "ok", "result": result})
            scope["steps"].append(result)
            run.step_index, run.attempts, run.approved_step, run.next_attempt_at, run.error = i + 1, 0, None, None, None
            db.commit()
            done += 1
        if run.status in ACTIVE and run.step_index >= len(steps):
            run.status, run.finished_at = "done", utcnow()
            db.commit()
        return done

    def _run_step(self, db, run, i, step, scope, now_local) -> tuple[bool | None, Any, str | None]:
        try:
            if step.get("notify"):
                text = str(render(step["notify"], scope))[:1000]
                blocked = self._notify(db, run, f"{text}", key=f"workflow:{run.id}:{i}", now_local=now_local)
                if blocked in ("quiet_hours", "daily_budget_reached", "retry_backoff"):
                    return None, None, None
                if blocked:
                    return False, None, f"message not sent ({blocked})"
                return True, {"sent": text}, None
            args = render(step.get("args") or {}, scope)
        except TemplateError as exc:
            return False, None, str(exc)
        ctx = ToolContext(db=db, tz=self.tz, tainted=False)
        out = self.executor.run_as_workflow(ctx, step["tool"], args, approved=run.approved_step == i)
        if out.status != "ok":
            return False, None, str(out.result.get("error", out.status))[:500]
        return True, _small(out.result), None

    def _pause(self, db: Session, run: WorkflowRun, i: int, now_local: datetime, why: str) -> int:
        wf = db.get(Workflow, run.workflow_id)
        run.status = "waiting_approval"
        db.commit()
        step = wf.steps[i]
        what = f"send: {step['notify']}" if step.get("notify") else f"{step['tool']}"
        self._notify(db, run, f"Workflow “{wf.name}” step {i + 1} ({what}) {why}. Open Workflows in the dashboard to approve or cancel.",
                     key=f"workflow-approve:{run.id}:{i}", priority="high", now_local=now_local)
        return 0

    def _fail_step(self, db: Session, run: WorkflowRun, i: int, error: str, now_local: datetime) -> int:
        run.attempts += 1
        self._log(db, run, {"step": i, "state": "error", "error": error, "attempt": run.attempts})
        run.error = error
        if run.attempts < MAX_ATTEMPTS:
            run.status = "retrying"
            run.next_attempt_at = local_to_naive_utc(now_local + BACKOFF[run.attempts - 1], self.tz)
        else:
            run.status, run.finished_at = "failed", utcnow()
            wf = db.get(Workflow, run.workflow_id)
            self._notify(db, run, f"Workflow “{wf.name}” stopped at step {i + 1} after {MAX_ATTEMPTS} tries: {error}. "
                                  "You can retry it from Workflows in the dashboard.",
                         key=f"workflow-failed:{run.id}", priority="high", now_local=now_local)
        db.commit()
        return 0

    # -- the user's decisions (API only)
    def approve(self, db: Session, run: WorkflowRun, now_local: datetime) -> WorkflowRun:
        if run.status != "waiting_approval":
            raise ValueError("This run is not waiting for approval.")
        run.status, run.approved_step, run.next_attempt_at = "running", run.step_index, None
        db.commit()
        self.advance(db, run, now_local)
        return run

    def cancel(self, db: Session, run: WorkflowRun) -> WorkflowRun:
        if run.status in ("done", "failed", "cancelled"):
            raise ValueError("This run has already finished.")
        run.status, run.finished_at = "cancelled", utcnow()
        self._log(db, run, {"step": run.step_index, "state": "cancelled"})
        return run

    def retry(self, db: Session, run: WorkflowRun, now_local: datetime) -> WorkflowRun:
        if run.status != "failed":
            raise ValueError("Only a failed run can be retried.")
        run.status, run.attempts, run.next_attempt_at, run.finished_at = "running", 0, None, None
        db.commit()
        self.advance(db, run, now_local)
        return run


def _small(result: Any) -> Any:
    """Keep a step's result for later steps ({{steps.1.task.id}}) but bounded in size."""
    text = json.dumps(result, default=str, ensure_ascii=False)
    return result if len(text) <= 3000 else {"summary": text[:3000]}


TEMPLATES: list[dict] = [
    {"key": "application-docs", "name": "New application: gather documents",
     "trigger": {"type": "event", "event": "opportunity_added"},
     "steps": [{"tool": "create_task", "args": {"title": "Gather documents for {{event.title}}", "due_date": "{{today+3}}", "priority": "high"}},
               {"notify": "Added a task to gather the documents for {{event.title}} (deadline: {{event.deadline}})."}]},
    {"key": "interview-prep", "name": "Interview: prepare",
     "trigger": {"type": "event", "event": "opportunity_status", "status": "interview"},
     "steps": [{"tool": "create_task", "args": {"title": "Prepare for the {{event.title}} interview", "due_date": "{{today+2}}", "priority": "high"}},
               {"tool": "create_reminder", "args": {"text": "Practise answers for the {{event.title}} interview", "remind_at": "{{today+1}}T18:00"}}]},
    {"key": "blocker-task", "name": "Project blocker: make a task to unblock it",
     "trigger": {"type": "event", "event": "blocker_logged"},
     "steps": [{"tool": "create_task", "args": {"title": "Unblock {{event.project}}: {{event.text}}", "due_date": "{{today+1}}",
                                                "project_id": "{{event.project_id}}"}}]},
    {"key": "weekly-review", "name": "Sunday evening: week in numbers",
     "trigger": {"type": "schedule", "time": "20:00", "days": ["sun"]},
     "steps": [{"tool": "accountability_report", "args": {"window_days": 7}},
               {"notify": "Your week: {{steps.1.tasks.completed}} task(s) done, {{steps.1.tasks.overdue}} overdue. Open Insights for the details."}]},
]


def run_dict(run: WorkflowRun, tz: ZoneInfo) -> dict:
    return {
        "id": run.id, "workflow_id": run.workflow_id, "status": run.status, "step": run.step_index + 1, "event": run.event,
        "attempts": run.attempts, "error": run.error,
        "started": naive_utc_to_local(run.created_at, tz).isoformat(timespec="minutes"),
        "finished": naive_utc_to_local(run.finished_at, tz).isoformat(timespec="minutes") if run.finished_at else None,
        "log": run.log[-30:],
    }


def workflow_dict(wf: Workflow) -> dict:
    return {"id": wf.id, "name": wf.name, "enabled": wf.enabled, "trigger": wf.trigger, "steps": wf.steps,
            "description": describe(wf.trigger, wf.steps)}


def today_local(tz: ZoneInfo) -> date:
    return datetime.now(tz).date()
