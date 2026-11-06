from sqlalchemy.orm import Session

from app.db.models import AuditLog


def record_audit(
    db: Session,
    *,
    actor: str,
    tool_name: str,
    permission: str,
    params: dict | None,
    status: str,
    detail: str | None = None,
    conversation_id: int | None = None,
    pending_action_id: int | None = None,
) -> None:
    """Add an audit row to the session. The caller commits, so the row lands atomically with the action."""
    db.add(
        AuditLog(
            actor=actor,
            tool_name=tool_name,
            permission=permission,
            params=params,
            status=status,
            detail=detail,
            conversation_id=conversation_id,
            pending_action_id=pending_action_id,
        )
    )
