"""Terminal chat with the agent.  Run:  python -m app.cli"""
from __future__ import annotations

import sys

from sqlalchemy import select

from app.container import build_container
from app.db.models import AuditLog, Conversation, PendingAction, Task
from app.errors import AppError
from app.tools.base import ToolContext
from app.tools.planning import get_plan, plan_dict

HELP = """Commands:
  /tasks            list open tasks
  /plan             show today's plan
  /pending          list actions waiting for your approval
  /confirm <id>     approve a pending action
  /reject <id>      reject a pending action
  /audit            show the last 15 audited actions
  /new              start a new conversation
  /quit             exit
Anything else is sent to the agent."""


def main() -> int:
    c = build_container()
    with c.session_factory() as db:
        last = db.scalars(select(Conversation).order_by(Conversation.id.desc())).first()
        conversation_id = last.id if last else None
        ctx = ToolContext(db=db, tz=c.agent.tz)
        print(f"Personal agent (model: {c.settings.agent_model}). Type /help for commands.")

        while True:
            try:
                line = input("\nyou> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not line:
                continue
            try:
                if line in ("/quit", "/exit"):
                    return 0
                elif line == "/help":
                    print(HELP)
                elif line == "/new":
                    conversation_id = None
                    print("Started a new conversation.")
                elif line == "/tasks":
                    for t in db.scalars(select(Task).where(Task.status.in_(("todo", "in_progress"))).order_by(Task.id)):
                        print(f"  #{t.id} [{t.priority}] {t.title}  due={t.due_date or '-'}  postponed={t.postponement_count}")
                elif line == "/plan":
                    out = plan_dict(get_plan(db, ctx.today()), ctx.today())
                    if not out["exists"]:
                        print("  (no plan for today -- ask the agent to plan your day)")
                    else:
                        print(f"  {out['planned_minutes']} of {out['usable_minutes']} usable minutes planned")
                        for i in out["items"]:
                            print(f"  [{i['status']:7}] #{i['item_id']} {i['title']} ({i['minutes']} min{', major' if i['major'] else ''})")
                elif line == "/pending":
                    rows = db.scalars(select(PendingAction).where(PendingAction.status == "pending")).all()
                    print("\n".join(f"  #{p.id} {p.summary}" for p in rows) or "  (nothing pending)")
                elif line.startswith("/confirm "):
                    out = c.executor.confirm(ctx, int(line.split()[1]))
                    print(f"  {out.status}: {out.result}")
                elif line.startswith("/reject "):
                    c.executor.reject(ctx, int(line.split()[1]))
                    print("  rejected")
                elif line == "/audit":
                    for a in db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(15)):
                        print(f"  {a.created_at:%m-%d %H:%M} {a.actor:5} {a.tool_name:22} {a.status}")
                else:
                    res = c.agent.chat(db, line, conversation_id)
                    conversation_id = res.conversation_id
                    for t in res.tool_calls:
                        print(f"  [tool] {t.name} ({t.permission}) -> {t.status}")
                    print(f"\nagent> {res.reply}")
                    for pid in res.pending_action_ids:
                        print(f"  (waiting for your approval: /confirm {pid} or /reject {pid})")
            except AppError as exc:
                print(f"  error: {exc}")
            except (ValueError, IndexError):
                print("  usage error, try /help")


if __name__ == "__main__":
    sys.exit(main())
