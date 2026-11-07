"""The single gate every tool call passes through: validate -> permission check -> run -> audit."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import timedelta

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import PendingAction, utcnow
from app.errors import ConflictError, NotFoundError
from app.security.audit import record_audit
from app.security.permissions import Decision, PermissionPolicy
from app.security.untrusted import wrap_untrusted
from app.tools.base import Tool, ToolContext, ToolError, ToolRegistry

logger = logging.getLogger(__name__)


@dataclass
class ToolOutcome:
    status: str  # "ok" | "error" | "pending_confirmation"
    result: dict
    permission: str = "unknown"
    pending_action_id: int | None = None


def _format_validation_error(exc: ValidationError) -> str:
    return "Invalid input: " + "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())


class ToolExecutor:
    def __init__(self, registry: ToolRegistry, policy: PermissionPolicy, pending_action_ttl_hours: float | None = 24.0):
        self.registry = registry
        self.policy = policy
        # None/0 disables expiry entirely; pending actions then live forever, as before.
        self.pending_action_ttl_hours = pending_action_ttl_hours or None

    # ---- called by the agent ----
    def run(self, ctx: ToolContext, name: str, raw_input: dict | None) -> ToolOutcome:
        tool = self.registry.get(name)
        if tool is None:
            return self._error(ctx, name, "unknown", raw_input, f"Unknown tool '{name}'.")
        try:
            params = tool.input_model.model_validate(raw_input or {})
        except ValidationError as exc:
            return self._error(ctx, name, tool.permission.value, raw_input, _format_validation_error(exc))

        decision = self.policy.decide(tool, tainted=ctx.tainted)
        if decision is Decision.ALLOW and ctx.tainted and tool.taint_gate is not None and not tool.taint_gate(ctx, params):
            decision = Decision.REQUIRE_CONFIRMATION
        if decision is Decision.REQUIRE_CONFIRMATION:
            return self._queue(ctx, tool, params)
        return self._execute(ctx, tool, params, actor="agent")

    # ---- called by the user (API / CLI), never by the model ----
    def confirm(self, ctx: ToolContext, pending_id: int) -> ToolOutcome:
        pa = self._get_pending(ctx, pending_id)
        tool = self.registry.get(pa.tool_name)
        if tool is None:
            raise NotFoundError(f"Tool '{pa.tool_name}' no longer exists.")
        try:
            params = tool.input_model.model_validate(pa.tool_input)
        except ValidationError as exc:
            pa.status, pa.resolved_at = "failed", utcnow()
            ctx.db.commit()
            return ToolOutcome("error", {"error": _format_validation_error(exc)}, tool.permission.value)

        # Stale-target check. SQLite reuses the id of a deleted row, so "delete task #1" approved later could hit a
        # *different* task #1. The summary stored when the request was queued names the target; if it no longer
        # matches what that id refers to now (or the item is gone), refuse rather than act on the wrong thing.
        if tool.summarize is not None:
            try:
                fresh: str | None = tool.summarize(ctx, params)
            except ToolError:
                fresh = None
            if fresh != pa.summary:
                pa.status, pa.resolved_at = "failed", utcnow()
                record_audit(
                    ctx.db, actor="user", tool_name=pa.tool_name, permission=tool.permission.value, params=pa.tool_input,
                    status="error", detail="Refused: the target changed since this request was queued.",
                    conversation_id=pa.conversation_id, pending_action_id=pa.id,
                )
                ctx.db.commit()
                now = f'it now refers to: {fresh}' if fresh else "it no longer exists"
                return ToolOutcome(
                    "error",
                    {"error": f'Nothing was done. This request was "{pa.summary}", but {now}. Ask again if you still want it.'},
                    tool.permission.value,
                )

        # Mark confirmed in the same transaction as the action itself, so it can never run twice.
        pa.status, pa.resolved_at = "confirmed", utcnow()
        outcome = self._execute(ctx, tool, params, actor="user", pending_action_id=pa.id)
        if outcome.status != "ok":  # _execute rolled back, including the status change above
            pa = ctx.db.get(PendingAction, pending_id)
            pa.status, pa.resolved_at = "failed", utcnow()
            ctx.db.commit()
        return outcome

    def run_as_user(self, ctx: ToolContext, name: str, raw_input: dict | None) -> ToolOutcome:
        """Run a tool because the *user* asked (REST edit/delete). Same validation and audit trail, but the
        confirmation gate is skipped: the human is the one acting. Never reachable from the model."""
        tool = self.registry.get(name)
        if tool is None:
            return self._error(ctx, name, "unknown", raw_input, f"Unknown tool '{name}'.")
        try:
            params = tool.input_model.model_validate(raw_input or {})
        except ValidationError as exc:
            return self._error(ctx, name, tool.permission.value, raw_input, _format_validation_error(exc))
        return self._execute(ctx, tool, params, actor="user")

    def reject(self, ctx: ToolContext, pending_id: int) -> PendingAction:
        pa = self._get_pending(ctx, pending_id)
        pa.status, pa.resolved_at = "rejected", utcnow()
        record_audit(
            ctx.db, actor="user", tool_name=pa.tool_name, permission="n/a", params=pa.tool_input,
            status="rejected", conversation_id=pa.conversation_id, pending_action_id=pa.id,
        )
        ctx.db.commit()
        return pa

    # ---- expiry (called by the agent each turn, and by the pending-actions listing endpoint) ----
    def _is_stale(self, pa: PendingAction) -> bool:
        if self.pending_action_ttl_hours is None:
            return False
        return utcnow() - pa.created_at > timedelta(hours=self.pending_action_ttl_hours)

    def _mark_expired(self, db: Session, pa: PendingAction) -> None:
        pa.status, pa.resolved_at = "expired", utcnow()
        record_audit(
            db, actor="system", tool_name=pa.tool_name, permission="n/a", params=pa.tool_input,
            status="expired", detail=f"Expired after {self.pending_action_ttl_hours}h without a response.",
            conversation_id=pa.conversation_id, pending_action_id=pa.id,
        )

    def expire_stale(self, db: Session) -> int:
        """Mark pending actions older than the TTL as expired. Returns how many were expired."""
        if self.pending_action_ttl_hours is None:
            return 0
        cutoff = utcnow() - timedelta(hours=self.pending_action_ttl_hours)
        stale = db.scalars(
            select(PendingAction).where(PendingAction.status == "pending", PendingAction.created_at < cutoff)
        ).all()
        for pa in stale:
            self._mark_expired(db, pa)
        if stale:
            db.commit()
        return len(stale)

    # ---- internals ----
    def _get_pending(self, ctx: ToolContext, pending_id: int) -> PendingAction:
        pa = ctx.db.get(PendingAction, pending_id)
        if pa is None:
            raise NotFoundError(f"No pending action with id {pending_id}.")
        if pa.status == "pending" and self._is_stale(pa):
            self._mark_expired(ctx.db, pa)
            ctx.db.commit()
        if pa.status != "pending":
            raise ConflictError(f"Pending action {pending_id} is already {pa.status}.")
        return pa

    def _queue(self, ctx: ToolContext, tool: Tool, params: BaseModel) -> ToolOutcome:
        dump = params.model_dump(mode="json")
        try:
            summary = tool.summarize(ctx, params) if tool.summarize else f"{tool.name} {json.dumps(dump)}"
        except ToolError as exc:
            return self._error(ctx, tool.name, tool.permission.value, dump, str(exc))

        pending = ctx.db.scalars(
            select(PendingAction).where(PendingAction.status == "pending", PendingAction.tool_name == tool.name)
        )
        pa = next((p for p in pending if p.tool_input == dump), None)  # don't queue the same action twice
        if pa is None:
            pa = PendingAction(
                conversation_id=ctx.conversation_id, tool_name=tool.name, tool_input=dump, summary=summary
            )
            ctx.db.add(pa)
            ctx.db.flush()
        record_audit(
            ctx.db, actor="agent", tool_name=tool.name, permission=tool.permission.value, params=dump,
            status="pending_confirmation", conversation_id=ctx.conversation_id, pending_action_id=pa.id,
        )
        ctx.db.commit()
        return ToolOutcome(
            "pending_confirmation",
            {
                "status": "pending_confirmation",
                "pending_action_id": pa.id,
                "summary": summary,
                "message": "NOT executed. The user must approve this action in the app before anything happens.",
            },
            tool.permission.value,
            pa.id,
        )

    def _execute(
        self, ctx: ToolContext, tool: Tool, params: BaseModel, *, actor: str, pending_action_id: int | None = None
    ) -> ToolOutcome:
        dump = params.model_dump(mode="json")
        audit = dict(
            actor=actor, tool_name=tool.name, permission=tool.permission.value, params=dump,
            conversation_id=ctx.conversation_id, pending_action_id=pending_action_id,
        )
        try:
            result = tool.handler(ctx, params)
            if tool.returns_untrusted:
                ctx.tainted = True
                result = {"untrusted_data": wrap_untrusted(json.dumps(result, default=str, ensure_ascii=False), tool.name)}
            record_audit(ctx.db, status="ok", **audit)
            ctx.db.commit()  # effect + audit row commit together
            return ToolOutcome("ok", result, tool.permission.value)
        except ToolError as exc:
            return self._failed(ctx, audit, str(exc), str(exc))
        except Exception as exc:  # noqa: BLE001 - a tool bug must not crash the agent loop
            logger.exception("Tool %s crashed", tool.name)
            return self._failed(ctx, audit, "Internal error while running the tool.", repr(exc))

    def _failed(self, ctx: ToolContext, audit: dict, message: str, detail: str) -> ToolOutcome:
        ctx.db.rollback()
        record_audit(ctx.db, status="error", detail=detail, **audit)
        ctx.db.commit()
        return ToolOutcome("error", {"error": message}, audit["permission"])

    def _error(self, ctx: ToolContext, name: str, permission: str, params, message: str) -> ToolOutcome:
        record_audit(
            ctx.db, actor="agent", tool_name=name, permission=permission,
            params=params if isinstance(params, dict) else None, status="error", detail=message,
            conversation_id=ctx.conversation_id,
        )
        ctx.db.commit()
        return ToolOutcome("error", {"error": message}, permission)
