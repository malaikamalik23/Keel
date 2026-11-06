export interface Task { id: number; title: string; description: string | null; status: string; priority: string; due_date: string | null; estimated_minutes: number | null; postponement_count: number; }
export interface Milestone { id: number; title: string; position: number; status: string; target_date: string | null; }
export interface Goal { id: number; title: string; description: string | null; horizon: string; category: string; status: string; priority: string; target_date: string | null; milestones: Milestone[]; }
export interface Project { id: number; name: string; description: string | null; purpose: string | null; status: string; priority: string; deadline: string | null; goal_id: number | null; }
export interface Commitment { id: number; text: string; deadline: string | null; importance: string; status: string; postponement_count: number; completed_at: string | null; }
export interface Memory { id: number; kind: string; content: string; importance: string; created_at: string; updated_at: string; }
export interface PendingAction { id: number; tool_name: string; summary: string; status: string; created_at: string; }
export interface AuditEntry { id: number; created_at: string; actor: string; tool_name: string; permission: string; status: string; detail: string | null; }
export interface PlanItem { item_id: number; task_id: number | null; title: string; minutes: number; major: boolean; status: string; }
export interface Plan { date: string; exists: boolean; capacity_minutes?: number; usable_minutes?: number; planned_minutes?: number; items: PlanItem[]; }
export interface ToolCall { name: string; permission: string; status: string; }
export interface ChatResponse { conversation_id: number; reply: string; tool_calls: ToolCall[]; pending_action_ids: number[]; }
export interface Accountability {
  window: { start: string; end: string; days: number };
  tasks: { created: number; completed: number; open_now: number; overdue: number; overdue_titles: string[] };
  plan_adherence: { planned_items: number; done: number; skipped: number; rate: number | null };
  most_postponed: { task_id: number; title: string; postponement_count: number; reasons_given: string[] }[];
  avoided_tasks: { task_id: number; title: string; age_days: number; postponement_count: number }[];
  overdue_commitments: { commitment_id: number; text: string; deadline: string }[];
  neglected_goals: { goal_id: number; title: string; days_since_activity: number }[];
  work: { total_minutes: number; sessions: number; minutes_by_project: Record<string, number> };
}
export interface Reminder { id: number; text: string; fire_at: string; status: string; linked_type: string | null; linked_id: number | null; sent_at: string | null; }
export interface Checkin { id: number; kind: string; checkin_date: string; summary: string; user_response: string | null; created_at: string; }
export interface NotificationRow { id: number; kind: string; priority: string; channel: string; text: string; status: string; sent_at: string; }
