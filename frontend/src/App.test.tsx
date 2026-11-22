import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

const weekStart = (i: number) => new Date(Date.UTC(2026, 9, 5 + i * 7)).toISOString().slice(0, 10);
const week = (i: number) => ({ week_start: weekStart(i), created: i, completed: i, postponed: 1, planned_items: 2, planned_done: 1, minutes_worked: 30 * i });
const FIXTURES: Record<string, unknown> = {
  "/api/pending-actions": [{ id: 3, tool_name: "delete_task", summary: "Permanently delete task #1: “Write intro”", status: "pending", created_at: "2026-11-07T10:00:00" }],
  "/api/plan": { date: "2026-11-07", exists: true, capacity_minutes: 240, usable_minutes: 204, planned_minutes: 90, items: [{ item_id: 1, task_id: 1, title: "Write FYP intro", minutes: 60, major: true, status: "planned", start: "09:00", end: "10:00" }, { item_id: 2, task_id: null, title: "Read paper", minutes: 30, major: false, status: "done" }] },
  "/api/tasks": [{ id: 1, title: "Write FYP intro", description: null, status: "todo", priority: "high", due_date: "2026-11-01", estimated_minutes: 60, postponement_count: 2 }],
  "/api/commitments": [{ id: 1, text: "Finish the GRE set", deadline: "2026-11-09", importance: "high", status: "open", postponement_count: 1, completed_at: null }],
  "/api/accountability": { window: { start: "2026-11-01", end: "2026-11-07", days: 7 }, tasks: { created: 3, completed: 1, open_now: 2, overdue: 1, overdue_titles: ["Write FYP intro"] }, plan_adherence: { planned_items: 4, done: 2, skipped: 1, rate: 0.5 }, most_postponed: [{ task_id: 1, title: "Write FYP intro", postponement_count: 3, reasons_given: ["too big"] }], avoided_tasks: [], overdue_commitments: [], neglected_goals: [{ goal_id: 1, title: "Pentesting", days_since_activity: 21 }], work: { total_minutes: 120, sessions: 3, minutes_by_project: {} } },
  "/api/goals": [{ id: 1, title: "Web pentesting", description: "Get good", horizon: "long", category: "skills", status: "active", priority: "high", target_date: null, milestones: [{ id: 1, title: "HTTP basics", position: 0, status: "done", target_date: null }, { id: 2, title: "Auth", position: 1, status: "todo", target_date: null }] }],
  "/api/projects": [{ id: 1, name: "FYP", description: null, purpose: "Graduate", status: "active", priority: "high", deadline: "2027-03-01", goal_id: null, repo: "malaikamalik23/Keel", progress: { tasks_total: 4, done: 1, open: 3, percent: 25 }, open_blockers: 1 }],
  "/api/projects/1/report": { project: { id: 1, name: "FYP", status: "active", purpose: "Graduate", deadline: "2027-03-01", repo: "malaikamalik23/Keel" }, progress: { tasks_total: 4, done: 1, open: 3, percent: 25 }, done_in_period: ["Lit review"], overdue: [], stuck: [{ id: 2, title: "Ethics form", waiting_on: "Dr. Khan" }], minutes_worked: 90, open_blockers: [{ id: 5, kind: "blocker", text: "No GPU quota", date: "2026-11-18", resolved: false, days_open: 3 }], resolved_blockers: [], decisions: [{ id: 6, kind: "decision", text: "Use PyTorch", why: "Lab code", date: "2026-11-19" }], updates: [], links: [{ id: 1, kind: "url", target: "javascript:alert(1)", title: "Bad link" }, { id: 2, kind: "note", target: "fyp/proposal.md", title: "Proposal" }] },
  "/api/memories": [
    { id: 1, kind: "preference", category: "general", topic: null, content: "Focuses best in the morning.", importance: "high", source: "chat", created_at: "2026-11-01T00:00:00", updated_at: "2026-11-01T00:00:00", confirmed_at: "2026-11-01T00:00:00", last_used_at: null, needs_review: false },
    { id: 2, kind: "fact", category: "academic", topic: "supervisor", content: "Dr. Khan", importance: "medium", source: "user", created_at: "2025-01-01T00:00:00", updated_at: "2025-01-01T00:00:00", confirmed_at: "2025-01-01T00:00:00", last_used_at: null, needs_review: true },
  ],
  "/api/briefing": { date: "2026-11-18", weekday: "Wednesday", next_action: { id: 1, title: "Write FYP intro", priority: "high" }, plan: { exists: true, items_left: 1, items_total: 2 }, due_today: [], overdue: [], coming_up: [{ id: 4, title: "GRE set 4", priority: "medium", due: "2026-11-20" }], commitments_due_soon: [], follow_ups_due: [{ title: "Revise chapter 2", waiting_on: "Dr. Khan", since: "2026-11-18" }], waiting_count: 1, blocked_count: 0, slipping: [], calendar: [{ title: "Supervisor meeting", time: "10:00-10:30", minutes: 30 }], text: "", important_dates: [{ title: "GRE exam", date: "2026-11-21", days_left: 3 }], calendar_warnings: ["Tomorrow: 1 pair(s) of calendar events overlap. You can't be in both."] },
  "/api/opportunities": [{ id: 1, kind: "internship", title: "Google STEP", organization: "Google", url: "javascript:alert(1)", location: null, deadline: "2026-12-15", status: "preparing", documents: [{ name: "CV", status: "ready" }, { name: "Recommendation letter", status: "requested", from: "Dr. Khan" }], missing: ["Recommendation letter (Dr. Khan)"], next_step: null, next_step_date: null, applied_on: null, days_left: 24, eligibility: null, notes: null, drafts: [{ id: 3, kind: "cover_letter", title: "Cover letter v1", content: "Dear team, [ADD: your CGPA]" }] }],
  "/api/documents": [{ id: 1, path: "uploads/cfp.docx", size: 12000, chunk_count: 4, indexed_at: "2026-11-21T10:00:00", pages: 3, pages_estimated: true, hidden_skipped: 0 }, { id: 2, path: "papers/fl.pdf", size: 400000, chunk_count: 30, indexed_at: "2026-11-21T10:00:00", pages: 12, pages_estimated: false, hidden_skipped: 0 }],
  "/api/document-links": { "uploads/cfp.docx": [{ id: 4, to: "opportunity", title: "DAAD EPOS", note: "call for papers" }, { id: "p2", to: "project", title: "FYP", note: null }] },
  "/api/documents/upload": { id: 3, path: "uploads/brief.docx", size: 9000, chunk_count: 2, indexed_at: "2026-11-21T10:00:00", pages: 1, pages_estimated: false, hidden_skipped: 1 },
  "/api/workflows": [{ id: 1, name: "Interview: prepare", enabled: true, trigger: { type: "event", event: "opportunity_status", status: "interview" }, steps: [{ tool: "create_task", args: {} }, { tool: "delete_task", args: { task_id: 1 }, approve: true }], description: "When an application moves to 'interview': 1. create_task {} 2. delete_task {\"task_id\": 1} [waits for your approval]", runs: [{ id: 9, workflow_id: 1, status: "waiting_approval", step: 2, event: {}, attempts: 0, error: null, started: "2026-11-21T10:00", finished: null, log: [{ step: 0, state: "started", at: "t" }, { step: 0, state: "ok", at: "t" }] }, { id: 8, workflow_id: 1, status: "failed", step: 1, event: {}, attempts: 3, error: "No task with id 5.", started: "2026-11-20T10:00", finished: "2026-11-20T10:40", log: [] }] }],
  "/api/workflow-templates": [{ key: "weekly-review", name: "Sunday evening: week in numbers", trigger: { type: "schedule" }, steps: [{ notify: "hi" }], description: "When every sun at 20:00: 1. tell you: hi" }, { key: "interview-prep", name: "Interview: prepare", trigger: {}, steps: [], description: "x" }],
  "/api/opportunities/1/eligibility": { verdict: "Nothing checked fails, but some can't be confirmed yet.", counts: { met: 1, not_met: 0, unknown: 1, check: 0 },
    checks: [{ requirement: "Minimum CGPA 3.0 out of 4.0.", kind: "cgpa", needs: "CGPA 3/4", you: "3.4", status: "met", why: "" },
             { requirement: "IELTS 6.5 overall.", kind: "ielts", needs: "IELTS 6.5", you: null, status: "unknown", why: "No IELTS score saved." }],
    ask_user: ["Save your ielts (save_memory with topic 'ielts')"] },
  "/api/opportunities/1/readiness": { documents: [{ name: "CV", status: "ready", files_in_notes: ["cv/Malaika_CV.pdf"], drafts: [], next: "Done." },
    { name: "Recommendation letter", status: "requested", from: "Dr. Khan", files_in_notes: [], drafts: [], next: "Waiting for Dr. Khan." }], still_needed: ["Recommendation letter"], ready: false },
  "/api/calendar": { start: "2026-11-23", end: "2026-11-29", today: "2026-11-24", calendar: "ok", calendar_error: null, events_truncated: false, workday: ["09:00", "21:00"],
    overdue: [{ id: 2, title: "Old form", due: "2026-11-20" }],
    days: ["2026-11-23", "2026-11-24", "2026-11-25", "2026-11-26", "2026-11-27", "2026-11-28", "2026-11-29"].map((date, i) => ({
      date, weekday: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][i], is_today: i === 1, busy_minutes: i === 1 ? 90 : 0, free_minutes: i === 0 ? null : 565, work_due_minutes: 0,
      warnings: i === 1 ? ["Double-booked: 1 pair(s) of events overlap."] : [],
      items: i === 1 ? [{ kind: "event", title: "Supervisor <b>meeting</b>", all_day: false, start: "10:00", end: "11:00", location: "Room 4" }, { kind: "block", title: "Write intro", start: "14:00", end: "15:00", status: "planned", task_id: 1 }, { kind: "task", id: 1, title: "Write chapter 2", status: "todo" }]
        : i === 3 ? [{ kind: "application", id: 1, title: "DAAD scholarship", status: "interested", type: "scholarship" }] : [],
    })) },
  "/api/services": [
    { id: "model", name: "AI model", status: "connected", details: ["Groq · openai/gpt-oss-120b"], access: "Receives each chat message", setup: "GROQ_API_KEY", testable: false },
    { id: "calendar", name: "Calendar", status: "connected", details: ["iCal feed from calendar.google.com"], access: "Read-only.", setup: "CALENDAR_ICS_URL", testable: true },
    { id: "browser", name: "Keel's browser", status: "off", details: [], access: "Opens sites.", setup: "BROWSER_ENABLED=true", testable: false },
  ],
  "/api/backups": { enabled: true, folder: "data/backups", backups: [{ name: "agent-20261123-031500.keelbak", size: 52224, created: "2026-11-23T03:15:00+00:00", encrypted: true }] },
  "/api/profile": { state: "done", name: "Malaika", address_as: "Mali", goals: ["Finish my FYP"], responsibilities: [], preferences: ["Keep messages short"],
    settings: { agent_timezone: "Asia/Karachi", workday_start: "09:00", workday_end: "21:00", quiet_hours_start: "22:00", quiet_hours_end: "07:00",
      morning_checkin_time: "08:00", evening_checkin_time: "20:00", notification_max_per_day: 5, weekly_checkin_weekday: 6 }, overridden: [] },
  "/api/inbox": { new: [
      { id: 7, text: "remind me at 6pm to call the bank", status: "new", filed_as: null, created_at: "2026-11-09T09:30:00",
        suggestion: { kind: "reminder", fields: { text: "Call the bank", remind_at: "2026-11-09T18:00" }, why: "Has a time (at 6pm), so a reminder.", alternatives: ["task", "memory", "note"] } }],
    recent: [{ id: 3, text: "Renew my passport", status: "filed", filed_as: "task #12", suggestion: null, created_at: "2026-11-08T10:00:00" }],
    projects: [], notes: true },
  "/api/retention": { categories: [{ id: "conversations", label: "Chat conversations", min_days: 7, note: "Whole conversations", days: null }, { id: "audit_log", label: "Audit log", min_days: 30, note: "Every tool call", days: 90 }], last_run: null },
  "/api/retention/preview": { would_delete: { audit_log: 12 } },
  "/api/retention/apply": { deleted: { audit_log: 12 } },
  "/api/services/calendar/test": { service: "calendar", ok: false, detail: "CalendarError: Calendar server returned HTTP 404.", checked_at: "2026-11-24T10:00:00" },
  "/api/memories/review": { stale: [{ id: 2, kind: "fact", category: "academic", topic: "supervisor", content: "Dr. Khan", importance: "medium", source: "user", created_at: "2025-01-01T00:00:00", updated_at: "2025-01-01T00:00:00", confirmed_at: "2025-01-01T00:00:00", last_used_at: null, needs_review: true }], conflicts: [] },
  "/api/analytics": { weeks: Array.from({ length: 8 }, (_, i) => week(i)), streak: { current_days: 4, longest_days: 9, window_days: 90 }, commitments: { done: 3, dropped: 1, overdue: 1, kept_rate: 0.6 }, estimates: { tasks_measured: 5, median_actual_vs_estimate: 1.4 }, postponement: { total: 6, avg_before_completion: 1.5, top_reasons: [{ reason: "too big", count: 3 }] }, completions_by_weekday: { Mon: 1, Tue: 2, Wed: 0, Thu: 3, Fri: 1, Sat: 0, Sun: 0 } },
  "/api/reminders": [{ id: 1, text: "Email Dr. Khan", fire_at: "2026-11-08T04:00:00", status: "pending", linked_type: null, linked_id: null, sent_at: null }],
  "/api/checkins": [{ id: 1, kind: "morning", checkin_date: "2026-11-07", summary: "Good morning.", user_response: "On it", created_at: "2026-11-07T03:00:00" }],
  "/api/notifications": [{ id: 1, kind: "morning", priority: "normal", channel: "log", text: "hi", status: "sent", sent_at: "2026-11-07T03:00:00" }],
  "/api/audit-log": [{ id: 1, created_at: "2026-11-07T03:00:00", actor: "agent", tool_name: "create_task", permission: "write", status: "ok", detail: null }],
};

function mockFetch(overrides: Record<string, unknown> = {}, status = 200) {
  const calls: { method: string; path: string }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const path = String(input).split("?")[0];
    calls.push({ method: init?.method ?? "GET", path });
    const body = path in overrides ? overrides[path] : path in FIXTURES ? FIXTURES[path] : {};
    return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
  }));
  return calls;
}

let errors: unknown[][];
beforeEach(() => { Element.prototype.scrollIntoView = vi.fn(() => Promise.resolve()) as unknown as typeof Element.prototype.scrollIntoView; /* current Chrome returns a Promise */ errors = []; vi.spyOn(console, "error").mockImplementation((...a) => { errors.push(a); }); sessionStorage.clear(); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("dashboard smoke tests", () => {
  it("renders the Today view with plan, alerts, tasks and commitments", async () => {
    mockFetch();
    render(<App />);
    expect(await screen.findByText("Keel")).toBeTruthy();
    expect(await screen.findByText(/Today's plan/)).toBeTruthy();
    expect((await screen.findAllByText(/Write FYP intro/)).length).toBeGreaterThan(0);
    expect(await screen.findByText(/Overdue task: Write FYP intro/)).toBeTruthy();
    expect(await screen.findByText(/postponed 3×/)).toBeTruthy();
    expect(await screen.findByText("Finish the GRE set")).toBeTruthy();
    expect(await screen.findByText(/Time to follow up: Dr. Khan \(Revise chapter 2\)/)).toBeTruthy();
    expect(await screen.findByText("Supervisor meeting")).toBeTruthy();
    expect(await screen.findByText(/09:00–10:00/)).toBeTruthy();
    expect(await screen.findByText(/Dates: GRE exam/)).toBeTruthy();
    expect(await screen.findByText(/Tomorrow: 1 pair\(s\) of calendar events overlap/)).toBeTruthy();
    expect(await screen.findByRole("button", { name: "Add to calendar" })).toBeTruthy();
    expect(errors).toEqual([]);
  });

  it.each([
    ["Goals", /Web pentesting/], ["Projects", /Graduate/], ["Commitments", /Finish the GRE set/], ["Memory", /Focuses best in the morning/],
    ["Applications", /Google STEP/], ["Insights", /day streak/], ["Activity", /Email Dr\. Khan/], ["Approvals", /Permanently delete task #1/],
  ] as const)("renders the %s tab without errors", async (tab, text) => {
    mockFetch();
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: new RegExp(`^${tab}`) }));
    expect(await screen.findByText(text)).toBeTruthy();
    expect(errors).toEqual([]);
  });

  it("Memory tab shows the profile and old memories to review, and can confirm and add memories", async () => {
    const calls = mockFetch();
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Memory" }));
    expect(await screen.findByText(/Needs review/)).toBeTruthy();
    expect((await screen.findAllByText("supervisor:")).length).toBeGreaterThan(0);
    await user.click((await screen.findAllByRole("button", { name: "Still true" }))[0]);
    await waitFor(() => expect(calls).toContainEqual({ method: "PATCH", path: "/api/memories/2" }));
    await user.type(screen.getByLabelText("New memory"), "Chose NUST for the fees");
    await user.selectOptions(screen.getByLabelText("New memory kind"), "decision");
    await user.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(calls).toContainEqual({ method: "POST", path: "/api/memories" }));
    expect(errors).toEqual([]);
  });

  it("Applications never turn a non-http link into a clickable one", async () => {
    mockFetch();
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Applications" }));
    expect(await screen.findByText(/Google STEP/)).toBeTruthy();
    expect(screen.queryByRole("link", { name: "posting" })).toBeNull();  // its url was javascript:alert(1)
    expect(document.querySelector('a[href^="javascript"]')).toBeNull();
    expect(await screen.findByText("Recommendation letter (Dr. Khan)")).toBeTruthy();
    expect(errors).toEqual([]);
  });

  it("Projects show progress, blockers and decisions, and resolving a blocker calls the API", async () => {
    const calls = mockFetch();
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /^Projects/ }));
    expect(await screen.findByText("1/4 tasks done")).toBeTruthy();
    expect(screen.getByText("1 blocker(s)")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Details" }));
    expect(await screen.findByText(/No GPU quota/)).toBeTruthy();
    expect(screen.getByText(/Use PyTorch \(why: Lab code\)/)).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Bad link" })).toBeNull();  // javascript: stays text
    expect(document.querySelector('a[href^="javascript"]')).toBeNull();
    expect(screen.getByRole("link", { name: "malaikamalik23/Keel pull requests" }).getAttribute("href")).toBe("https://github.com/malaikamalik23/Keel/pulls");
    await user.click(screen.getByRole("button", { name: "Resolved" }));
    await waitFor(() => expect(calls).toContainEqual({ method: "POST", path: "/api/project-entries/5/resolve" }));
    expect(errors).toEqual([]);
  });

  it("Documents tab uploads a file as the raw body and only offers delete for uploads", async () => {
    const calls = mockFetch();
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Documents" }));
    expect(await screen.findByText(/~3 pages \(estimated\)/)).toBeTruthy();
    expect(screen.getByText(/12 pages/)).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "Delete" })).toHaveLength(1);  // papers/fl.pdf is the user's own file
    await user.upload(screen.getByLabelText("Document file"), new File(["PK"], "brief.docx"));
    await waitFor(() => expect(calls).toContainEqual({ method: "POST", path: "/api/documents/upload" }));
    expect(await screen.findByText(/Added uploads\/brief.docx \(1 piece\(s\) of hidden text left out\)/)).toBeTruthy();
    const sent = (fetch as unknown as { mock: { calls: [string, RequestInit][] } }).mock.calls.find(([u]) => String(u).startsWith("/api/documents/upload"))!;
    expect(sent[0]).toBe("/api/documents/upload?name=brief.docx");
    expect((sent[1].headers as Record<string, string>)["Content-Type"]).toBe("application/octet-stream");
    expect(errors).toEqual([]);
  });

  it("Workflows tab approves a paused step, retries a failed run and adds a template", async () => {
    const calls = mockFetch();
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Workflows" }));
    expect(await screen.findByText(/step 2: delete_task/)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Approve step 2" }));
    await waitFor(() => expect(calls).toContainEqual({ method: "POST", path: "/api/workflow-runs/9/approve" }));
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(calls).toContainEqual({ method: "POST", path: "/api/workflow-runs/8/retry" }));
    expect(screen.getByRole("button", { name: "Added" })).toBeTruthy();  // the interview template is already in use
    await user.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(calls).toContainEqual({ method: "POST", path: "/api/workflows" }));
    expect(errors).toEqual([]);
  });

  it("Applications tab checks eligibility and readiness on request", async () => {
    const calls = mockFetch();
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /^Applications/ }));
    await user.click(await screen.findByRole("button", { name: "Check eligibility" }));
    expect(await screen.findByText(/Nothing checked fails/)).toBeTruthy();
    expect(screen.getByText("unknown")).toBeTruthy();
    expect(screen.getByText(/tell Keel: Save your ielts\./)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "What's ready?" }));
    expect(await screen.findByText(/Waiting for Dr\. Khan\./)).toBeTruthy();
    expect(calls.some((c) => c.path === "/api/opportunities/1/readiness")).toBe(true);
    expect(errors).toEqual([]);
  });

  it("Documents tab shows what each document is linked to and unlinks one", async () => {
    const calls = mockFetch();
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /^Documents/ }));
    expect(await screen.findByText(/DAAD EPOS/)).toBeTruthy();
    expect(screen.getByText("opportunity")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Unlink uploads/cfp.docx from FYP" }));
    await waitFor(() => expect(calls).toContainEqual({ method: "DELETE", path: "/api/document-links/p2" }));
    expect(errors).toEqual([]);
  });

  it("Calendar tab shows a week of events and Keel's dates, switches to month and moves between weeks", async () => {
    const calls = mockFetch();
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Calendar" }));
    expect(await screen.findByText("Supervisor <b>meeting</b>")).toBeTruthy();  // invite text is shown as text, never as HTML
    expect(screen.getByText("Calendar · 10:00–11:00")).toBeTruthy();
    expect(screen.getByText("Plan · 14:00–15:00")).toBeTruthy();
    expect(screen.getByText("DAAD scholarship")).toBeTruthy();
    expect(screen.getByText(/Double-booked/)).toBeTruthy();
    expect(screen.getByText(/Overdue: Old form/)).toBeTruthy();
    expect(screen.getByText(/1h 30m in meetings · 9h 25m free/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Add plan to calendar" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Next week" }));
    await waitFor(() => expect(calls.filter((c) => c.path === "/api/calendar").length).toBeGreaterThan(1));
    await user.click(screen.getByRole("button", { name: "Month" }));
    expect(await screen.findAllByRole("gridcell")).toHaveLength(7);
    await user.click(screen.getByRole("gridcell", { name: /3 item\(s\)/ }));
    expect(await screen.findAllByText("Write chapter 2")).toHaveLength(2);  // the cell chip and the day opened below
    expect(errors).toEqual([]);
  });

  it("Calendar tab says when no calendar is connected", async () => {
    mockFetch({ "/api/calendar": { ...(FIXTURES["/api/calendar"] as object), calendar: "off", overdue: [] } });
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Calendar" }));
    expect(await screen.findByText(/No calendar connected/)).toBeTruthy();
    expect(errors).toEqual([]);
  });

  it("Services tab lists connections without secrets and runs a check", async () => {
    const calls = mockFetch();
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Services" }));
    expect(await screen.findByText("Groq · openai/gpt-oss-120b")).toBeTruthy();
    expect(screen.getByText("BROWSER_ENABLED=true")).toBeTruthy();  // setup hint for what isn't connected
    expect(screen.getAllByRole("button", { name: "Check connection" })).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "Check connection" }));
    await waitFor(() => expect(calls).toContainEqual({ method: "POST", path: "/api/services/calendar/test" }));
    expect((await screen.findByRole("status")).textContent).toMatch(/Failed: CalendarError: Calendar server returned HTTP 404/);
    expect(errors).toEqual([]);
  });

  it("Services tab lists encrypted backups and takes one on request", async () => {
    const rows = [{ id: "backups", name: "Encrypted backups", status: "connected", details: ["One encrypted file (AES-256-GCM) with the database"], access: "Writes to your backup folder only.", setup: "BACKUP_PASSPHRASE", testable: false }];
    const calls = mockFetch({ "/api/services": rows });
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Services" }));
    expect(await screen.findByText(/agent-20261123-031500.keelbak · 51 KB/)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Back up now" }));
    await waitFor(() => expect(calls).toContainEqual({ method: "POST", path: "/api/backups" }));
    expect(errors).toEqual([]);
  });

  it("Services tab switches an integration off and sets a retention rule", async () => {
    const rows = [{ id: "gmail", name: "Gmail", status: "connected", details: [], access: "Reads mail.", setup: "GMAIL_CLIENT_ID", testable: false, switch: { id: "gmail", label: "Gmail", on: true } }];
    const calls = mockFetch({ "/api/services": rows });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Services" }));
    await user.click(await screen.findByRole("checkbox", { name: "Gmail on" }));
    await waitFor(() => expect(calls).toContainEqual({ method: "PUT", path: "/api/switches/gmail" }));
    expect(screen.queryByRole("option", { name: "7 days" }, )).toBeTruthy();  // conversations may be 7 days
    await user.selectOptions(screen.getByLabelText("Chat conversations"), "30");
    await waitFor(() => expect(calls).toContainEqual({ method: "PUT", path: "/api/retention" }));
    await user.click(screen.getByRole("button", { name: "Delete expired now…" }));
    await waitFor(() => expect(calls).toContainEqual({ method: "POST", path: "/api/retention/apply" }));
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining("12 × Audit log"));
    expect(errors).toEqual([]);
  });

  it("first run opens the setup questions, every step can be skipped, and the answers are saved", async () => {
    const fresh = { ...(FIXTURES["/api/profile"] as object), state: "new", name: "", address_as: "", goals: [], preferences: [] };
    const sent: unknown[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
      const path = String(input).split("?")[0];
      if (path === "/api/profile" && init?.method === "POST") sent.push(JSON.parse(String(init.body)));
      const body = path === "/api/profile" ? fresh : path in FIXTURES ? FIXTURES[path] : {};
      return new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
    }));
    const user = userEvent.setup();
    render(<App />);
    expect(await screen.findByText("Welcome to Keel")).toBeTruthy();
    await user.type(screen.getByLabelText("Your name"), "Malaika");
    await user.click(screen.getByRole("button", { name: "Next" }));
    await user.click(screen.getByRole("button", { name: "Skip this step" }));  // time zone and hours left as they are
    await user.type(screen.getByLabelText(/^Main goals/), "Finish my FYP{enter}Get a scholarship");
    for (let i = 0; i < 3; i++) await user.click(screen.getByRole("button", { name: "Next" }));
    await user.click(screen.getByRole("button", { name: "Save and finish" }));
    await waitFor(() => expect(sent).toHaveLength(1));
    const body = sent[0] as { state: string; answers: { name: string; goals: string[] }; settings: object };
    expect(body.state).toBe("done");
    expect(body.answers.name).toBe("Malaika");
    expect(body.answers.goals).toEqual(["Finish my FYP", "Get a scholarship"]);
    expect(body.settings).toEqual({});  // nothing changed, so nothing overrides .env
    expect(await screen.findByRole("button", { name: "Profile" })).toBeTruthy();
    expect(errors).toEqual([]);
  });

  it("Inbox captures anything, shows Keel's suggestion and why, and files only when asked, as the type you choose", async () => {
    const posts: { path: string; body: unknown }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
      const path = String(input).split("?")[0];
      if (init?.method === "POST") posts.push({ path, body: init.body ? JSON.parse(String(init.body)) : null });
      const body = path in FIXTURES ? FIXTURES[path] : {};
      return new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
    }));
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Inbox" }));
    expect(await screen.findByText("Has a time (at 6pm), so a reminder.")).toBeTruthy();
    expect(screen.getByText(/task #12/)).toBeTruthy();
    await user.type(screen.getByLabelText("Capture"), "Renew my passport{enter}");
    await waitFor(() => expect(posts).toContainEqual({ path: "/api/inbox", body: { text: "Renew my passport" } }));
    expect(posts.some((p) => p.path.includes("/file"))).toBe(false);  // capturing files nothing
    await user.selectOptions(screen.getByLabelText("Type for: remind me at 6pm to call the bank"), "task");
    await user.click(screen.getByRole("button", { name: "File as task" }));
    await waitFor(() => expect(posts).toContainEqual({ path: "/api/inbox/7/file", body: { kind: "task", fields: { title: "Call the bank", due_date: "2026-11-09" } } }));
    expect(errors).toEqual([]);
  });

  it("Memory tab marks a fact no longer true instead of deleting it, and filters to history", async () => {
    const mem = (id: number, content: string, extra = {}) => ({ id, kind: "fact", category: "professional", topic: null, content, importance: "medium", source: "chat",
      created_at: "2026-01-01T00:00:00", updated_at: "2026-01-01T00:00:00", confirmed_at: "2026-01-01T00:00:00", last_used_at: null, needs_review: false, status: "current", confidence: "medium", linked: [], ...extra });
    const calls = mockFetch({
      "/api/memories": [mem(1, "Works at Arbisoft", { linked: [2] }), mem(2, "Team lead is Sara", { confidence: "low" })],
      "/api/memories/review": { stale: [], conflicts: [] },
    });
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Memory" }));
    expect((await screen.findAllByText(/linked to Team lead is Sara/)).length).toBeGreaterThan(0);
    expect(screen.getAllByText("unsure").length).toBeGreaterThan(0);
    await user.click(screen.getAllByRole("button", { name: "No longer true" })[0]);
    await waitFor(() => expect(calls).toContainEqual({ method: "PATCH", path: "/api/memories/1" }));
    await user.selectOptions(screen.getByLabelText("Memory status"), "outdated");
    await waitFor(() => expect(calls.some((c) => c.path === "/api/memories")).toBe(true));
    expect(errors).toEqual([]);
  });

  it("shows a badge with the number of pending approvals", async () => {
    mockFetch();
    render(<App />);
    await waitFor(() => expect(screen.getByRole("button", { name: /Approvals/ }).textContent).toContain("1"));
  });

  it("shows helpful empty states instead of crashing on a fresh database", async () => {
    mockFetch({ "/api/plan": { date: "2026-11-07", exists: false, items: [] }, "/api/tasks": [], "/api/commitments": [], "/api/goals": [], "/api/pending-actions": [],
      "/api/analytics": { ...(FIXTURES["/api/analytics"] as object), weeks: Array.from({ length: 8 }, (_, i) => ({ week_start: weekStart(i), created: 0, completed: 0, postponed: 0, planned_items: 0, planned_done: 0, minutes_worked: 0 })), estimates: { tasks_measured: 0, median_actual_vs_estimate: null }, commitments: { done: 0, dropped: 0, overdue: 0, kept_rate: null }, postponement: { total: 0, avg_before_completion: null, top_reasons: [] } } });
    const user = userEvent.setup();
    render(<App />);
    expect(await screen.findByText(/No plan yet/)).toBeTruthy();
    expect(await screen.findByText(/No open tasks/)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: /^Goals/ }));
    expect(await screen.findByText(/No active goals/)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: /^Insights/ }));
    expect(await screen.findByText(/No history yet/)).toBeTruthy();
    expect(errors).toEqual([]);
  });

  it("approving a pending action calls the confirm endpoint and refreshes", async () => {
    const calls = mockFetch();
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /^Approvals/ }));
    await user.click(await screen.findByRole("button", { name: "Approve" }));
    await waitFor(() => expect(calls).toContainEqual({ method: "POST", path: "/api/pending-actions/3/confirm" }));
  });

  it("Skip on a plan item does nothing when the reason prompt is cancelled, and skips when it is answered", async () => {
    const calls = mockFetch();
    const prompt = vi.spyOn(window, "prompt").mockReturnValueOnce(null).mockReturnValueOnce("");
    const user = userEvent.setup();
    render(<App />);
    const skipButton = await screen.findByRole("button", { name: "Skip" });
    await user.click(skipButton);  // Cancel
    expect(prompt).toHaveBeenCalledTimes(1);
    expect(calls.filter((c) => c.method === "PATCH")).toEqual([]);
    await user.click(skipButton);  // OK with no reason
    await waitFor(() => expect(calls).toContainEqual({ method: "PATCH", path: "/api/plan-items/1" }));
  });

  it("asks for the API key on 401 and sends it afterwards", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
      const key = (init?.headers as Record<string, string> | undefined)?.["X-API-Key"];
      if (key !== "secret") return new Response(JSON.stringify({ detail: "no" }), { status: 401 });
      return new Response(JSON.stringify(FIXTURES[String(input).split("?")[0]] ?? {}), { status: 200 });
    }));
    const user = userEvent.setup();
    render(<App />);
    expect(await screen.findByText(/API key required/)).toBeTruthy();
    await user.type(screen.getByLabelText("API key"), "secret");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByText("Keel")).toBeTruthy();
    expect(sessionStorage.getItem("agent_api_key")).toBe("secret");  // tab-scoped, never localStorage
    expect(localStorage.getItem("agent_api_key")).toBeNull();
  });

  it("renders model output as plain text, never as HTML", async () => {
    mockFetch({ "/api/chat": { conversation_id: 1, reply: "<img src=x onerror=alert(1)> ![p](https://evil.test/p.png)", tool_calls: [], pending_action_ids: [] } });
    const user = userEvent.setup();
    const { container } = render(<App />);
    await user.click(await screen.findByRole("button", { name: /^Chat/ }));
    await user.type(screen.getByLabelText("Message"), "hi");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText(/onerror=alert\(1\)/)).toBeTruthy();
    expect(container.querySelector("img")).toBeNull();
  });

  it("keeps the conversation after the reply and across tab switches", async () => {
    mockFetch({ "/api/chat": { conversation_id: 1, reply: "Added your task.", tool_calls: [{ name: "create_task", permission: "write", status: "ok" }], pending_action_ids: [] } });
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /^Chat/ }));
    await user.type(screen.getByLabelText("Message"), "add a task");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("Added your task.")).toBeTruthy();
    expect(screen.getByText("add a task")).toBeTruthy();      // not wiped by the post-reply data refresh
    await user.click(screen.getByRole("button", { name: /^Goals/ }));
    await user.click(screen.getByRole("button", { name: /^Chat/ }));
    expect(screen.getByText("Added your task.")).toBeTruthy(); // still there after switching tabs
  });

  it("a malformed API response breaks one view, not the whole dashboard", async () => {
    mockFetch({ "/api/accountability": [] });  // wrong shape on purpose
    const user = userEvent.setup();
    render(<App />);
    expect(await screen.findByText(/Something went wrong in this view/)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: /^Goals/ }));  // navigation still works
    expect(await screen.findByText(/Web pentesting/)).toBeTruthy();
  });
});
