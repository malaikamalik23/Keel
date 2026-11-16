"""Regenerate docs/screenshots/*.png from made-up demo data in a throwaway database (never data/agent.db).

    python -m scripts.screenshots          # needs the built dashboard and: python -m playwright install chromium
"""
import sys
import tempfile
import threading
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import uvicorn
from playwright.sync_api import sync_playwright

from app.api.main import create_app
from app.db.models import Commitment, Task, TaskEvent, WorkSession
from app.tools.base import ToolContext
from tests.conftest import make_settings
from tests.fakes import ScriptedLLM

out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "docs" / "screenshots"
db_file = Path(tempfile.mkdtemp(prefix="keel-shots-")) / "demo.db"
settings = make_settings(agent_timezone="Asia/Karachi").model_copy(update={"database_url": f"sqlite:///{db_file.as_posix()}", "frontend_dist": "frontend/dist"})
app = create_app(settings, ScriptedLLM([]))
c = app.state.container
today = date.today()
def D(n):
    return (today + timedelta(days=n)).isoformat()

with c.session_factory() as db:
    ctx = ToolContext(db=db, tz=ZoneInfo("Asia/Karachi"))
    def user(tool, **kw):
        o = c.executor.run_as_user(ctx, tool, kw)
        if o.status != "ok":
            raise SystemExit(f"Seeding failed at {tool}: {o.result}")
        return o.result
    user("create_goal", title="Get good at web penetration testing", category="skills", horizon="long", priority="high", target_date=D(120))
    user("add_milestones", goal_id=1, titles=["Finish PortSwigger SQL injection labs", "Write up three HackTheBox machines", "Build a small vulnerable app and break it"])
    user("create_goal", title="Submit final-year project report", category="education", horizon="short", priority="high", target_date=D(30))
    user("create_project", name="FYP: explainable intrusion detection", purpose="Final-year project", priority="high", deadline=D(30), goal_id=2)
    tasks = [
        dict(title="Write FYP introduction", due_date=D(0), priority="high", estimated_minutes=90),
        dict(title="PortSwigger: blind SQL injection lab", due_date=D(0), priority="medium", estimated_minutes=60),
        dict(title="Email Dr. Khan about the dataset", due_date=D(0), priority="high", estimated_minutes=15),
        dict(title="GRE quant practice set 4", due_date=D(1), priority="medium", estimated_minutes=75),
        dict(title="Literature review: SHAP for IDS", due_date=D(3), priority="medium", estimated_minutes=120),
        dict(title="Fix evaluation script bug", due_date=D(-2), priority="high", estimated_minutes=45),
        dict(title="Draft methodology section", due_date=D(5), priority="medium", estimated_minutes=120),
        dict(title="Read two papers on federated IDS", due_date=D(-1), priority="low", estimated_minutes=60),
        dict(title="Set up HackTheBox VPN", priority="low", estimated_minutes=30),
        dict(title="Clean the dataset labels", priority="medium", estimated_minutes=60),
    ]
    for t in tasks:
        user("create_task", **t)
    user("update_task", task_id=4, due_date=D(2), reason="Set 3 took longer than planned")
    user("update_task", task_id=4, due_date=D(3), reason="Had a lab report due")
    user("update_task", task_id=5, due_date=D(4), reason="Waiting for the supervisor's reading list")
    user("update_task", task_id=6, due_date=D(0), reason="Could not reproduce the bug")
    for tid in (8, 9, 10):
        user("complete_task", task_id=tid)
    user("propose_commitment", text="Send the FYP introduction draft to Dr. Khan", deadline=D(1), importance="high", confirmed=True)
    user("propose_commitment", text="Finish GRE quant set 4", deadline=D(3), importance="medium", confirmed=True)
    user("propose_commitment", text="Review Ayesha's poster before Friday", deadline=D(2), importance="medium", confirmed=True)
    user("propose_commitment", text="Publish the HackTheBox write-up", deadline=D(-1), importance="low", confirmed=True)
    user("update_commitment", commitment_id=2, deadline=D(4))
    user("save_memory", content="Works best in the evening, after 7 pm", kind="preference", importance="high")
    user("save_memory", content="Supervisor is Dr. Khan; meetings are on Thursdays", kind="context")
    user("create_daily_plan", capacity_minutes=240)
    for minutes, tid, note in [(50, 1, "Outline and first two paragraphs"), (40, 2, "Got the boolean-based payload working"), (25, 6, "Narrowed it to the label encoder")]:
        user("log_work_session", minutes=minutes, task_id=tid, note=note)
    user("create_reminder", text="Email Dr. Khan about the dataset", remind_at=(datetime.now(ZoneInfo("Asia/Karachi")) + timedelta(hours=3)).isoformat())
    # two requests waiting for approval, exactly as the agent would queue them
    agent_ctx = ToolContext(db=db, tz=ZoneInfo("Asia/Karachi"), tainted=True)
    c.executor.run(agent_ctx, "delete_task", {"task_id": 7})
    c.executor.run(agent_ctx, "save_memory", {"content": "Prefers plans with at most three major tasks", "kind": "preference"})
    # spread the history over the past week so trends and streaks have something to show
    now = datetime.now(UTC).replace(tzinfo=None)
    for i, t in enumerate(db.query(Task).filter(Task.completed_at.isnot(None)).all()):
        t.completed_at = now - timedelta(days=i + 1, hours=2)
    for i, e in enumerate(db.query(TaskEvent).order_by(TaskEvent.id).all()):
        if e.type in ("postponed", "completed"):
            e.at = now - timedelta(days=(i % 5) + 1)
    for d in range(1, 7):
        for minutes, tid in ((45 + 10 * (d % 3), 1), (30 + 5 * d, 2)):
            start = now - timedelta(days=d, hours=3)
            db.add(WorkSession(task_id=tid, minutes=minutes, started_at=start, ended_at=start + timedelta(minutes=minutes), note="Focused session"))
    done = db.get(Commitment, 4)
    done.status, done.completed_at = "done", now - timedelta(days=1)
    db.commit()

server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8771, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
time.sleep(2)
errors = []
with sync_playwright() as p:
    b = p.chromium.launch()
    for scheme in ("light", "dark"):
        page = b.new_page(viewport={"width": 1280, "height": 860}, color_scheme=scheme, device_scale_factor=2)
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto("http://127.0.0.1:8771/")
        page.wait_for_selector("text=Today")
        for tab in ("Today", "Commitments", "Insights", "Approvals"):
            page.get_by_role("button", name=tab).first.click()
            page.wait_for_timeout(900)
            page.screenshot(path=str(out / f"{tab.lower()}-{scheme}.png"), full_page=False)
        page.close()
    b.close()
server.should_exit = True
print("console errors:", errors)
