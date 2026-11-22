export interface Task { id: number; title: string; description: string | null; status: string; priority: string; due_date: string | null; estimated_minutes: number | null; postponement_count: number; recurrence?: string | null; blocked_by_id?: number | null; waiting_on?: string | null; follow_up_date?: string | null; event_start?: string | null; }
export interface Milestone { id: number; title: string; position: number; status: string; target_date: string | null; }
export interface Goal { id: number; title: string; description: string | null; horizon: string; category: string; status: string; priority: string; target_date: string | null; milestones: Milestone[]; }
export interface Project { id: number; name: string; description: string | null; purpose: string | null; status: string; priority: string; deadline: string | null; goal_id: number | null; repo?: string | null; progress?: { tasks_total: number; done: number; open: number; percent: number | null } | null; open_blockers: number; }
export interface ProjectEntry { id: number; kind: string; text: string; date: string; why?: string; resolved?: boolean; resolution?: string; days_open?: number; }
export interface ProjectReport {
  project: { id: number; name: string; status: string; purpose: string | null; deadline: string | null; repo: string | null };
  progress: { tasks_total: number; done: number; open: number; percent: number | null };
  done_in_period: string[]; overdue: { id: number; title: string; due: string }[];
  stuck: { id: number; title: string; waiting_for_task?: string; waiting_on?: string }[]; minutes_worked: number;
  open_blockers: ProjectEntry[]; resolved_blockers: ProjectEntry[]; decisions: ProjectEntry[]; updates: ProjectEntry[];
  links: { id: number; kind: string; target: string; title: string | null }[]; days_to_deadline?: number;
}
export interface DocumentRow { id: number; path: string; size: number; chunk_count: number; indexed_at: string; pages: number | null; pages_estimated: boolean; hidden_skipped: number; }
export interface EligibilityCheck { requirement: string; kind: string; needs: string; you: string | null; status: "met" | "not_met" | "unknown" | "check"; why: string; }
export interface Eligibility { verdict: string; counts: Record<string, number>; checks: EligibilityCheck[]; deadline?: { date: string; days_left: number; passed?: boolean }; ask_user?: string[]; }
export interface ReadinessItem { name: string; status: string; from?: string; files_in_notes: string[]; drafts: { id: number; title: string }[]; next: string; }
export interface Readiness { documents: ReadinessItem[]; still_needed: string[]; ready: boolean; note?: string; }
export interface CalendarItem {
  kind: "event" | "block" | "task" | "promise" | "application" | "project" | "reminder" | "date";
  title: string; id?: number; task_id?: number | null; status?: string; priority?: string; type?: string; category?: string;
  start?: string | null; end?: string | null; minutes?: number; all_day?: boolean; ends_other_day?: boolean; location?: string | null;
}
export interface CalendarDay { date: string; weekday: string; is_today: boolean; busy_minutes: number; free_minutes: number | null; work_due_minutes: number; warnings: string[]; items: CalendarItem[]; }
export interface CalendarView { start: string; end: string; today: string; calendar: "ok" | "off" | "error"; calendar_error: string | null; events_truncated: boolean; overdue: { id: number; title: string; due: string }[]; days: CalendarDay[]; workday: [string, string]; }
export interface ServiceRow { id: string; name: string; status: "connected" | "partial" | "not_set_up" | "off"; details: string[]; access: string; setup: string; testable: boolean; switch?: { id: string; label: string; on: boolean }; }
export interface ServiceCheck { service: string; ok: boolean | null; detail: string; checked_at: string; }
export interface WorkflowStep { tool?: string; args?: Record<string, unknown>; notify?: string; approve?: boolean; }
export interface WorkflowRun { id: number; workflow_id: number; status: string; step: number; event: Record<string, unknown>; attempts: number; error: string | null; started: string; finished: string | null; log: { step: number; state: string; at: string; error?: string; attempt?: number }[]; }
export interface WorkflowRow { id: number; name: string; enabled: boolean; trigger: Record<string, unknown>; steps: WorkflowStep[]; description: string; runs: WorkflowRun[]; }
export interface WorkflowTemplate { key: string; name: string; trigger: Record<string, unknown>; steps: WorkflowStep[]; description: string; }
export interface Commitment { id: number; text: string; deadline: string | null; importance: string; status: string; postponement_count: number; completed_at: string | null; }
export interface Memory { id: number; kind: string; category: string; topic: string | null; content: string; importance: string; source: string; created_at: string; updated_at: string; confirmed_at: string | null; last_used_at: string | null; needs_review: boolean; status?: "current" | "outdated"; confidence?: "high" | "medium" | "low"; valid_until?: string | null; replaced_by_id?: number | null; linked?: number[]; }
export interface BriefTask { id: number; title: string; priority: string; due?: string; minutes?: number; }
export interface Briefing {
  date: string; weekday: string; next_action: string | BriefTask | null;
  plan: { exists: boolean; items_left: number; items_total: number };
  due_today: BriefTask[]; overdue: BriefTask[]; coming_up: BriefTask[];
  commitments_due_soon: { text: string; deadline: string; overdue: boolean }[];
  follow_ups_due: { title: string; waiting_on: string; since: string }[];
  waiting_count: number; blocked_count: number; slipping: { title: string; postponed: number }[];
  calendar: { title: string; time: string; minutes: number }[] | null; text: string;
  important_dates?: { title: string; date: string; days_left: number }[]; calendar_warnings?: string[];
}
export interface AppDoc { name: string; status: string; from?: string; }
export interface AppDraft { id: number; kind: string; title: string; content: string; }
export interface Opportunity {
  id: number; kind: string; title: string; organization: string | null; url: string | null; location: string | null;
  deadline: string | null; status: string; documents: AppDoc[]; missing: string[]; next_step: string | null;
  next_step_date: string | null; applied_on: string | null; days_left?: number; eligibility: string | null; notes: string | null;
  drafts: AppDraft[];
}
export interface MemoryReview { stale: Memory[]; conflicts: Memory[][]; }
export interface PendingAction { id: number; tool_name: string; summary: string; status: string; created_at: string; }
export interface AuditEntry { id: number; created_at: string; actor: string; tool_name: string; permission: string; status: string; detail: string | null; }
export interface PlanItem { item_id: number; task_id: number | null; title: string; minutes: number; major: boolean; status: string; start?: string; end?: string; }
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
