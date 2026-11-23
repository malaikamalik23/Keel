import { useCallback, useEffect, useState } from "react";
import { ApiError, api, getApiKey, setApiKey } from "./api";
import Activity from "./views/Activity";
import Applications from "./views/Applications";
import CommandBar from "./CommandBar";
import { Icon, PAGE_ICON } from "./icons";
import Calendar from "./views/Calendar";
import Documents from "./views/Documents";
import Services from "./views/Services";
import Workflows from "./views/Workflows";
import Approvals from "./views/Approvals";
import ErrorBoundary from "./ErrorBoundary";
import Chat from "./views/Chat";
import Commitments from "./views/Commitments";
import Goals from "./views/Goals";
import Inbox from "./views/Inbox";
import Insights from "./views/Insights";
import MemoryView from "./views/MemoryView";
import Onboarding, { type Profile } from "./views/Onboarding";
import Projects from "./views/Projects";
import Today from "./views/Today";
import type { PendingAction } from "./types";

// Seven sections, each holding the pages that belong to it. Every page stays one click away.
const SECTIONS = [
  { name: "Home", pages: ["Today"] },
  { name: "Assistant", pages: ["Chat", "Inbox"] },
  { name: "My Life", pages: ["Calendar", "Goals", "Projects", "Commitments", "Applications"] },
  { name: "Memory", pages: ["Memory", "Documents"] },
  { name: "Automations", pages: ["Workflows", "Approvals", "Activity"] },
  { name: "Insights", pages: ["Insights"] },
  { name: "Settings", pages: ["Services"] },
] as const;
type Tab = (typeof SECTIONS)[number]["pages"][number];

const PAGES: readonly string[] = SECTIONS.flatMap((s) => s.pages);
const startPage = (): Tab => {  // the installed app's shortcuts open a page directly: /?page=Inbox
  try { const p = new URLSearchParams(window.location.search).get("page"); return (p && PAGES.includes(p) ? p : "Today") as Tab; }
  catch { return "Today"; }
};

export default function App() {
  const [tab, setTab] = useState<Tab>(startPage);
  const [pending, setPending] = useState(0);
  const [needsKey, setNeedsKey] = useState(false);
  const [keyInput, setKeyInput] = useState(getApiKey());
  const [version, setVersion] = useState(0); // bump to remount views after the agent changes data
  const [profileOpen, setProfileOpen] = useState(false);

  useEffect(() => {  // first run: open the setup questions once (skippable); later they're under "Profile"
    api.get<Profile>("/api/profile").then((p) => { if (p?.state === "new") setProfileOpen(true); }).catch(() => undefined);
  }, []);

  const refreshPending = useCallback(async () => {
    try { setPending((await api.get<PendingAction[]>("/api/pending-actions")).length); setNeedsKey(false); }
    catch (e) { if (e instanceof ApiError && e.status === 401) setNeedsKey(true); }
  }, []);
  useEffect(() => { void refreshPending(); }, [refreshPending]);
  const changed = useCallback(() => { setVersion((v) => v + 1); void refreshPending(); }, [refreshPending]);
  const go = useCallback((page: string) => { if (page === "Profile") setProfileOpen(true); else if (PAGES.includes(page)) setTab(page as Tab); }, []);
  const ask = useCallback((text: string) => {
    setTab("Chat");
    setTimeout(() => window.dispatchEvent(new CustomEvent("keel:ask", { detail: text })), 0);
  }, []);

  if (needsKey) {
    return (
      <main className="gate">
        <h1>API key required</h1>
        <p className="muted">This server has API_KEY set. Enter it to continue (kept for this browser tab only).</p>
        <form onSubmit={(e) => { e.preventDefault(); setApiKey(keyInput); setNeedsKey(false); void refreshPending(); }}>
          <input type="password" name="api_key" autoComplete="off" value={keyInput} onChange={(e) => setKeyInput(e.target.value)} aria-label="API key" />
          <button type="submit">Continue</button>
        </form>
      </main>
    );
  }

  if (profileOpen) {
    return <main className="gate wide"><Onboarding onDone={() => { setProfileOpen(false); changed(); }} /></main>;
  }

  return (
    <div className="app">
      <CommandBar pages={PAGES} go={go} ask={ask} onCaptured={changed} />
      <nav aria-label="Keel">
        <strong><span className="logo"><Icon name="sparkle" size={15} /></span>Keel</strong>
        <button className="search-btn" onClick={() => window.dispatchEvent(new Event("keel:command"))} aria-label="Search or capture (Ctrl+K)">
          <Icon name="search" size={16} /> Search <kbd>Ctrl K</kbd>
        </button>
        {SECTIONS.map((s) => (
          <div key={s.name} className="nav-section" role="group" aria-label={s.name}>
            <span className="nav-heading">{s.name}{s.name === "Automations" && pending > 0 && tab !== "Approvals" ? " •" : ""}</span>
            {s.pages.map((t) => (
              <button key={t} className={t === tab ? "active" : ""} aria-current={t === tab ? "page" : undefined} onClick={() => setTab(t)}>
                <Icon name={PAGE_ICON[t]} />{t}{t === "Approvals" && pending > 0 && <span className="pill">{pending}</span>}
              </button>
            ))}
            {s.name === "Settings" && <button onClick={() => setProfileOpen(true)}><Icon name="user" />Profile</button>}
          </div>
        ))}
      </nav>
      {/* Chat stays mounted (just hidden) so the conversation survives tab switches and data refreshes. */}
      <main hidden={tab !== "Chat"} className="chat-pane"><ErrorBoundary><Chat onChange={changed} /></ErrorBoundary></main>
      <main hidden={tab === "Chat"} key={`${tab}-${version}`}>
        {tab !== "Today" && <div className="page-title"><h1><Icon name={PAGE_ICON[tab]} size={22} /> {tab}</h1></div>}
        <ErrorBoundary>
        {tab === "Today" && <Today go={go} ask={ask} />}
        {tab === "Inbox" && <Inbox onChange={changed} />}
        {tab === "Calendar" && <Calendar />}
        {tab === "Goals" && <Goals ask={ask} />}
        {tab === "Projects" && <Projects ask={ask} />}
        {tab === "Commitments" && <Commitments />}
        {tab === "Applications" && <Applications />}
        {tab === "Documents" && <Documents ask={ask} />}
        {tab === "Workflows" && <Workflows />}
        {tab === "Memory" && <MemoryView />}
        {tab === "Insights" && <Insights />}
        {tab === "Activity" && <Activity />}
        {tab === "Services" && <Services />}
        {tab === "Approvals" && <Approvals onChange={changed} />}
        </ErrorBoundary>
      </main>
    </div>
  );
}
