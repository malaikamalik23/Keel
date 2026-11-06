from datetime import date, datetime
from typing import Any

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
