from __future__ import annotations

from enum import Enum

from app.tools.base import Permission, Tool


class Decision(str, Enum):
    ALLOW = "allow"
    REQUIRE_CONFIRMATION = "require_confirmation"


class PermissionPolicy:
    """Decides, in code (not in the prompt), whether a tool call may run right now.

    - READ: always allowed.
    - WRITE: allowed, unless `confirm_writes` is on, or the session is *tainted*
      (untrusted external content is in play), in which case the user must confirm.
    - HIGH_IMPACT: always needs explicit user confirmation.
    """

    def __init__(self, confirm_writes: bool = False, taint_escalation: bool = True):
        self.confirm_writes = confirm_writes
        self.taint_escalation = taint_escalation

    def decide(self, tool: Tool, *, tainted: bool) -> Decision:
        if tool.permission is Permission.HIGH_IMPACT:
            return Decision.REQUIRE_CONFIRMATION
        if tool.permission is Permission.WRITE and (self.confirm_writes or (tainted and self.taint_escalation)):
            return Decision.REQUIRE_CONFIRMATION
        return Decision.ALLOW
