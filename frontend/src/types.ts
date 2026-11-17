export interface Task { id: number; title: string; description: string | null; status: string; priority: string; due_date: string | null; estimated_minutes: number | null; postponement_count: number; }
export interface Milestone { id: number; title: string; position: number; status: string; target_date: string | null; }
export interface Goal { id: number; title: string; description: string | null; horizon: string; category: string; status: string; priority: string; target_date: string | null; milestones: Milestone[]; }
export interface Project { id: number; name: string; description: string | null; purpose: string | null; status: string; priority: string; deadline: string | null; goal_id: number | null; }
export interface Commitment { id: number; text: string; deadline: string | null; importance: string; status: string; postponement_count: number; completed_at: string | null; }
export interface Memory { id: number; kind: string; category: string; topic: string | null; content: string; importance: string; source: string; created_at: string; updated_at: string; confirmed_at: string | null; last_used_at: string | null; needs_review: boolean; }
export interface MemoryReview { stale: Memory[]; conflicts: Memory[][]; }
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
export interface WeekStat { week_start: string; created: number; completed: number; postponed: number; planned_items: number; planned_done: number; minutes_worked: number; }
export interface Analytics {
  weeks: WeekStat[];
  streak: { current_days: number; longest_days: number; window_days: number };
  commitments: { done: number; dropped: number; overdue: number; kept_rate: number | null };
  estimates: { tasks_measured: number; median_actual_vs_estimate: number | null };
  postponement: { total: number; avg_before_completion: number | null; top_reasons: { reason: string; count: number }[] };
  completions_by_weekday: Record<string, number>;
}
