import { useState } from "react";
import { api } from "../api";
import { useLoad } from "../hooks";
import type { CalendarDay, CalendarItem, CalendarView } from "../types";
import { Badge, Card, ErrorNote, Loading } from "../ui";

// Dates are plain "YYYY-MM-DD" strings, stepped in UTC so a daylight-saving change can't skip or repeat a day.
const toDate = (iso: string) => new Date(iso + "T00:00:00Z");
const iso = (d: Date) => d.toISOString().slice(0, 10);
const addDays = (s: string, n: number) => { const d = toDate(s); d.setUTCDate(d.getUTCDate() + n); return iso(d); };
const mondayOf = (s: string) => addDays(s, -((toDate(s).getUTCDay() + 6) % 7));
const localToday = () => new Date().toLocaleDateString("en-CA");
const monthStart = (s: string) => s.slice(0, 8) + "01";
const label = (s: string, opts: Intl.DateTimeFormatOptions) => toDate(s).toLocaleDateString(undefined, { ...opts, timeZone: "UTC" });

const KIND: Record<CalendarItem["kind"], string> = {
  event: "Calendar", block: "Plan", task: "Due", promise: "Promise", application: "Deadline", project: "Project due", reminder: "Reminder", date: "Date",
};

export function hours(min: number) {
  const h = Math.floor(min / 60), m = min % 60;
  return h ? (m ? `${h}h ${m}m` : `${h}h`) : `${m}m`;
}

function Item({ it }: { it: CalendarItem }) {
  const time = it.all_day ? "all day" : it.start ? (it.end ? `${it.start}–${it.end}` : it.start) : "";
  const done = it.status === "done" || it.status === "skipped";
  return (
    <li className={`cal-item k-${it.kind}${done ? " done" : ""}`}>
      <span className="cal-kind">{KIND[it.kind]}{time && ` · ${time}`}{it.ends_other_day && " (next day)"}</span>
      <span className="cal-title">{it.title}</span>
      {it.location && <span className="small muted">{it.location}</span>}
      {it.kind === "application" && it.status && <span className="small muted">{it.type} · {it.status}</span>}
    </li>
  );
}

function DayColumn({ day }: { day: CalendarDay }) {
  return (
    <section className={`cal-day${day.is_today ? " today" : ""}`} aria-label={label(day.date, { weekday: "long", month: "long", day: "numeric" })}>
      <header>
        <strong>{day.weekday} {toDate(day.date).getUTCDate()}</strong>
        {day.is_today && <Badge tone="ok">today</Badge>}
      </header>
      <p className="small muted cal-stats">
        {day.busy_minutes > 0 && <>{hours(day.busy_minutes)} in meetings</>}
        {day.busy_minutes > 0 && day.free_minutes !== null && " · "}
        {day.free_minutes !== null && <>{hours(day.free_minutes)} free</>}
      </p>
      {day.warnings.map((w) => <p key={w} className="small warn-text">{w}</p>)}
      {day.items.length ? <ul className="cal-items">{day.items.map((it, i) => <Item key={i} it={it} />)}</ul> : <p className="small muted">Nothing.</p>}
      {day.items.some((i) => i.kind === "block" && i.start) && (
        <button className="ghost small" onClick={() => void api.download(`/api/plan.ics?date=${day.date}`, `keel-plan-${day.date}.ics`)}>Add plan to calendar</button>
      )}
    </section>
  );
}

function Month({ data, month, onPick, picked }: { data: CalendarView; month: string; onPick: (d: string) => void; picked: string | null }) {
  return (
    <div className="cal-month" role="grid" aria-label={label(month, { month: "long", year: "numeric" })}>
      {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => <div key={d} className="cal-head small muted" role="columnheader">{d}</div>)}
      {data.days.map((d) => {
        const other = d.date.slice(0, 7) !== month.slice(0, 7);
        return (
          <button key={d.date} role="gridcell" aria-label={`${label(d.date, { month: "long", day: "numeric" })}: ${d.items.length} item(s)`}
            className={`cal-cell${d.is_today ? " today" : ""}${other ? " other" : ""}${picked === d.date ? " picked" : ""}${d.warnings.length ? " warn" : ""}`}
            onClick={() => onPick(d.date)}>
            <span className="cal-num">{toDate(d.date).getUTCDate()}</span>
            {d.items.slice(0, 3).map((it, i) => <span key={i} className={`cal-chip k-${it.kind}`}>{it.start && !it.all_day ? `${it.start} ` : ""}{it.title}</span>)}
            {d.items.length > 3 && <span className="small muted cal-more">+{d.items.length - 3} more</span>}
            {d.items.length > 0 && <span className="cal-count" aria-hidden="true">{d.items.length}</span>}
          </button>
        );
      })}
    </div>
  );
}

export default function Calendar() {
  const [view, setView] = useState<"week" | "month">("week");
  const [anchor, setAnchor] = useState(localToday());
  const [picked, setPicked] = useState<string | null>(null);
  const start = view === "week" ? mondayOf(anchor) : mondayOf(monthStart(anchor));
  const days = view === "week" ? 7 : 42;
  const { data, error, loading } = useLoad<CalendarView>(`/api/calendar?start=${start}&days=${days}`);

  const step = (n: number) => {
    setPicked(null);
    if (view === "week") setAnchor(addDays(anchor, 7 * n));
    else { const d = toDate(monthStart(anchor)); d.setUTCMonth(d.getUTCMonth() + n); setAnchor(iso(d)); }
  };
  const title = view === "week"
    ? `${label(start, { month: "short", day: "numeric" })} – ${label(addDays(start, 6), { month: "short", day: "numeric", year: "numeric" })}`
    : label(monthStart(anchor), { month: "long", year: "numeric" });
  const pickedDay = data?.days.find((d) => d.date === picked);

  return (
    <div>
      <div className="toolbar cal-toolbar">
        <button className="ghost" aria-label={`Previous ${view}`} onClick={() => step(-1)}>‹</button>
        <button className="ghost" onClick={() => { setAnchor(localToday()); setPicked(null); }}>Today</button>
        <button className="ghost" aria-label={`Next ${view}`} onClick={() => step(1)}>›</button>
        <h2>{title}</h2>
        <span className="seg" role="group" aria-label="View">
          {(["week", "month"] as const).map((v) => (
            <button key={v} className={view === v ? "" : "ghost"} aria-pressed={view === v} onClick={() => { setView(v); setPicked(null); }}>{v === "week" ? "Week" : "Month"}</button>
          ))}
        </span>
      </div>
      <Loading loading={loading} error={error} />
      {data && (
        <>
          {data.calendar === "off" && <p className="small muted">No calendar connected, so this shows Keel's own dates only. Connect one on the Services tab (CALENDAR_ICS_URL).</p>}
          {data.calendar === "error" && <ErrorNote error={`Your calendar couldn't be read: ${data.calendar_error ?? "unknown error"}. Keel's own dates are still shown.`} />}
          {data.events_truncated && <p className="small muted">Showing the first 1000 calendar events in this range.</p>}
          {data.overdue.length > 0 && (
            <p className="small warn-text">Overdue: {data.overdue.map((t) => `${t.title} (due ${label(t.due, { month: "short", day: "numeric" })})`).join(", ")}</p>
          )}
          {view === "week" ? (
            <div className="cal-week">{data.days.map((d) => <DayColumn key={d.date} day={d} />)}</div>
          ) : (
            <>
              <Month data={data} month={monthStart(anchor)} onPick={setPicked} picked={picked} />
              {pickedDay && <Card title={label(pickedDay.date, { weekday: "long", month: "long", day: "numeric" })}><DayColumn day={pickedDay} /></Card>}
            </>
          )}
          <p className="small muted">Read-only: Keel never changes your calendar. Working hours {data.workday[0]}–{data.workday[1]}; free time counts only those.</p>
        </>
      )}
    </div>
  );
}
