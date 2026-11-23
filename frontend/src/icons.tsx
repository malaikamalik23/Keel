/** A small hand-made icon set (24×24, stroked), so the dashboard needs no icon package. */
const PATHS: Record<string, string> = {
  home: "M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z",
  chat: "M4 5h16v11H8l-4 4z",
  inbox: "M3 13l3-8h12l3 8v6H3zM3 13h5l1 2h6l1-2h5",
  calendar: "M4 6h16v14H4zM4 10h16M8 3v5M16 3v5",
  goal: "M12 21a9 9 0 1 1 0-18 9 9 0 0 1 0 18zM12 16a4 4 0 1 1 0-8 4 4 0 0 1 0 8zM12 12h.01",
  project: "M3 7h7l2 2h9v10H3z",
  promise: "M7 12l3 3 7-7M12 21a9 9 0 1 1 0-18 9 9 0 0 1 0 18z",
  briefcase: "M4 8h16v11H4zM9 8V5h6v3M4 13h16",
  memory: "M9 4a4 4 0 0 0-4 4v1a3 3 0 0 0 0 6v1a4 4 0 0 0 7 3V4.5A3 3 0 0 0 9 4zM15 4a4 4 0 0 1 4 4v1a3 3 0 0 1 0 6v1a4 4 0 0 1-7 3",
  document: "M6 3h8l4 4v14H6zM14 3v4h4M9 12h6M9 16h6",
  workflow: "M5 6h6v4H5zM13 14h6v4h-6zM8 10v4h5",
  approve: "M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7zM9 12l2 2 4-4",
  activity: "M3 12h4l3-8 4 16 3-8h4",
  insights: "M4 20V10M10 20V4M16 20v-7M22 20H2",
  settings: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19 12l2-1-2-4-2 .5-1.5-1L15 4h-4l-.5 2.5-1.5 1L7 7 5 11l2 1v2l-2 1 2 4 2-.5 1.5 1L11 20h4l.5-2.5 1.5-1 2 .5 2-4-2-1z",
  user: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21a8 8 0 0 1 16 0",
  search: "M11 18a7 7 0 1 1 0-14 7 7 0 0 1 0 14zM21 21l-5-5",
  plus: "M12 5v14M5 12h14",
  check: "M5 12l5 5 9-10",
  clock: "M12 21a9 9 0 1 1 0-18 9 9 0 0 1 0 18zM12 7v5l3 2",
  bolt: "M13 2L4 14h7l-1 8 9-12h-7z",
  alert: "M12 3l10 18H2zM12 10v5M12 18h.01",
  sparkle: "M12 3l2 6 6 2-6 2-2 6-2-6-6-2 6-2z",
};

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 18, className = "" }: { name: IconName | string; size?: number; className?: string }) {
  return (
    <svg className={`icon ${className}`} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={PATHS[name] ?? PATHS.sparkle} />
    </svg>
  );
}

export const PAGE_ICON: Record<string, IconName> = {
  Today: "home", Chat: "chat", Inbox: "inbox", Calendar: "calendar", Goals: "goal", Projects: "project", Commitments: "promise",
  Applications: "briefcase", Memory: "memory", Documents: "document", Workflows: "workflow", Approvals: "approve",
  Activity: "activity", Insights: "insights", Services: "settings", Profile: "user",
};
