import { useCallback, useEffect, useState } from "react";
import { ApiError, api, getApiKey, setApiKey } from "./api";
import Activity from "./views/Activity";
import Applications from "./views/Applications";
import Calendar from "./views/Calendar";
import Documents from "./views/Documents";
import Services from "./views/Services";
import Workflows from "./views/Workflows";
import Approvals from "./views/Approvals";
import ErrorBoundary from "./ErrorBoundary";
import Chat from "./views/Chat";
import Commitments from "./views/Commitments";
import Goals from "./views/Goals";
import Insights from "./views/Insights";
import MemoryView from "./views/MemoryView";
import Projects from "./views/Projects";
import Today from "./views/Today";
import type { PendingAction } from "./types";

const TABS = ["Today", "Calendar", "Goals", "Projects", "Commitments", "Applications", "Documents", "Workflows", "Memory", "Chat", "Insights", "Activity", "Services", "Approvals"] as const;
type Tab = (typeof TABS)[number];

export default function App() {
  const [tab, setTab] = useState<Tab>("Today");
  const [pending, setPending] = useState(0);
  const [needsKey, setNeedsKey] = useState(false);
  const [keyInput, setKeyInput] = useState(getApiKey());
  const [version, setVersion] = useState(0); // bump to remount views after the agent changes data

  const refreshPending = useCallback(async () => {
    try { setPending((await api.get<PendingAction[]>("/api/pending-actions")).length); setNeedsKey(false); }
    catch (e) { if (e instanceof ApiError && e.status === 401) setNeedsKey(true); }
  }, []);
  useEffect(() => { void refreshPending(); }, [refreshPending]);
  const changed = useCallback(() => { setVersion((v) => v + 1); void refreshPending(); }, [refreshPending]);

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

  return (
    <div className="app">
      <nav>
        <strong>Keel</strong>
        {TABS.map((t) => (
          <button key={t} className={t === tab ? "active" : ""} onClick={() => setTab(t)}>
            {t}{t === "Approvals" && pending > 0 && <span className="pill">{pending}</span>}
          </button>
        ))}
      </nav>
      {/* Chat stays mounted (just hidden) so the conversation survives tab switches and data refreshes. */}
      <main hidden={tab !== "Chat"} className="chat-pane"><ErrorBoundary><Chat onChange={changed} /></ErrorBoundary></main>
      <main hidden={tab === "Chat"} key={`${tab}-${version}`}>
        <ErrorBoundary>
        {tab === "Today" && <Today />}
        {tab === "Calendar" && <Calendar />}
        {tab === "Goals" && <Goals />}
        {tab === "Projects" && <Projects />}
        {tab === "Commitments" && <Commitments />}
        {tab === "Applications" && <Applications />}
        {tab === "Documents" && <Documents />}
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
