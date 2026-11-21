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
  "/api/documents/upload": { id: 3, path: "uploads/brief.docx", size: 9000, chunk_count: 2, indexed_at: "2026-11-21T10:00:00", pages: 1, pages_estimated: false, hidden_skipped: 1 },
  "/api/workflows": [{ id: 1, name: "Interview: prepare", enabled: true, trigger: { type: "event", event: "opportunity_status", status: "interview" }, steps: [{ tool: "create_task", args: {} }, { tool: "delete_task", args: { task_id: 1 }, approve: true }], description: "When an application moves to 'interview': 1. create_task {} 2. delete_task {\"task_id\": 1} [waits for your approval]", runs: [{ id: 9, workflow_id: 1, status: "waiting_approval", step: 2, event: {}, attempts: 0, error: null, started: "2026-11-21T10:00", finished: null, log: [{ step: 0, state: "started", at: "t" }, { step: 0, state: "ok", at: "t" }] }, { id: 8, workflow_id: 1, status: "failed", step: 1, event: {}, attempts: 3, error: "No task with id 5.", started: "2026-11-20T10:00", finished: "2026-11-20T10:40", log: [] }] }],
  "/api/workflow-templates": [{ key: "weekly-review", name: "Sunday evening: week in numbers", trigger: { type: "schedule" }, steps: [{ notify: "hi" }], description: "When every sun at 20:00: 1. tell you: hi" }, { key: "interview-prep", name: "Interview: prepare", trigger: {}, steps: [], description: "x" }],
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
