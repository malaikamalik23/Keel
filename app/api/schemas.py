from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: int | None = None


class ToolCallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    name: str
    permission: str
    status: str
    input: dict[str, Any]
    result: dict[str, Any]


class ChatResponse(BaseModel):
    conversation_id: int
    reply: str
    tool_calls: list[ToolCallOut]
    pending_action_ids: list[int]


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    description: str | None
    status: str
    priority: str
    due_date: date | None
    estimated_minutes: int | None
    postponement_count: int
    created_at: datetime
    completed_at: datetime | None


class MilestoneOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    position: int
    status: str
    target_date: date | None


class GoalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    description: str | None
    horizon: str
    category: str
    status: str
    priority: str
    target_date: date | None
    milestones: list[MilestoneOut]


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    description: str | None
    purpose: str | None
    status: str
    priority: str
    deadline: date | None
    goal_id: int | None


class CommitmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    text: str
    deadline: date | None
    importance: str
    status: str
    project_id: int | None
    task_id: int | None
    postponement_count: int
    created_at: datetime
    completed_at: datetime | None


class MemoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    kind: str
    content: str
    importance: str
    created_at: datetime
    updated_at: datetime


class MemoryUpdate(BaseModel):
    content: str | None = Field(None, min_length=1, max_length=1000)
    kind: Literal["preference", "context", "fact"] | None = None
    importance: Literal["low", "medium", "high"] | None = None


class CommitmentUpdate(BaseModel):
    text: str | None = Field(None, min_length=1, max_length=300)
    deadline: date | None = None
    importance: Literal["low", "medium", "high"] | None = None
    status: Literal["open", "dropped", "done"] | None = None


class PendingActionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    tool_name: str
    summary: str
    status: str
    created_at: datetime
    resolved_at: datetime | None


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    actor: str
    tool_name: str
    permission: str
    status: str
    detail: str | None
    params: Any


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    role: str
    content: Any
    created_at: datetime


class ConfirmResponse(BaseModel):
    status: str
    result: dict[str, Any]


class TaskEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    task_id: int | None
    task_title: str
    type: str
    old_value: str | None
    new_value: str | None
    reason: str | None
    at: datetime


class PlanItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    task_id: int | None
    title: str
    planned_minutes: int
    is_major: bool
    status: str


class PlanOut(BaseModel):
    date: date
    exists: bool
    capacity_minutes: int | None = None
    usable_minutes: int | None = None
    planned_minutes: int | None = None
    items: list[dict[str, Any]] = []


class WorkSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    task_id: int | None
    project_id: int | None
    started_at: datetime
    minutes: int
    note: str | None


class PlanItemUpdate(BaseModel):
    status: Literal["planned", "done", "skipped"]
    reason: str | None = Field(None, max_length=500)


class ReminderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    text: str
    fire_at: datetime  # naive UTC
    status: str
    linked_type: str | None
    linked_id: int | None
    sent_at: datetime | None


class CheckinOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    kind: str
    checkin_date: date
    summary: str
    user_response: str | None
    created_at: datetime


class CheckinResponseIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    kind: str
    priority: str
    channel: str
    text: str
    status: str
    sent_at: datetime


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    path: str
    size: int
    chunk_count: int
    indexed_at: datetime
