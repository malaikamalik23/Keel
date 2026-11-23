import { useState } from "react";
import { api } from "../api";
import { useAction, useLoad } from "../hooks";
import { Icon } from "../icons";
import { useToast } from "../toast";
import type { PersonSummary, Recommendations, Suggestion } from "../types";
import { Badge, Card, Empty, ErrorNote, Loading } from "../ui";

const ENERGY = [
  { key: "low", label: "Tired", icon: "clock" },
  { key: "ok", label: "Okay", icon: "check" },
  { key: "high", label: "Fresh", icon: "bolt" },
] as const;
const TIME = [{ m: 0, label: "Till my next thing" }, { m: 15, label: "15 min" }, { m: 30, label: "30 min" }, { m: 60, label: "1 hour" }, { m: 120, label: "2 hours" }];
const duration = (m: number) => { const h = Math.floor(m / 60), r = m % 60; return !h ? `${r} min` : !r ? `${h} h` : `${h} h ${r} min`; };
const KIND_ICON: Record<Suggestion["kind"], string> = { task: "check", follow_up: "chat", promise: "promise", person: "people" };

function load(key: string, fallback: string) { try { return sessionStorage.getItem(`keel.now.${key}`) ?? fallback; } catch { return fallback; } }
function save(key: string, value: string) { try { sessionStorage.setItem(`keel.now.${key}`, value); } catch { /* storage blocked */ } }

/** "What should I do now?": pick how you feel and how long you have; Keel ranks what fits, with its reasons. */
export default function WhatNow({ ask, onChange }: { ask?: (text: string) => void; onChange?: () => Promise<void> | void }) {
  const [energy, setEnergy] = useState(() => load("energy", ""));
  const [minutes, setMinutes] = useState(() => Number(load("minutes", "0")) || 0);
  const query = new URLSearchParams({ ...(energy ? { energy } : {}), ...(minutes ? { minutes: String(minutes) } : {}) }).toString();
  const { data, error, loading, reload } = useLoad<Recommendations>(`/api/recommendations${query ? `?${query}` : ""}`);
  const { run, error: actionError } = useAction(async () => { await reload(); await onChange?.(); });
  const toast = useToast();

  const pickEnergy = (e: string) => { const v = energy === e ? "" : e; setEnergy(v); save("energy", v); };
  const pickTime = (m: number) => { setMinutes(m); save("minutes", String(m)); };

  const act = (s: Suggestion) => {
    if (s.kind === "task") return run(async () => {
      await api.post(`/api/tasks/${s.id}/complete`);
      toast(`Done: ${s.title}`, { action: { label: "Undo", run: () => run(() => api.patch(`/api/tasks/${s.id}`, { status: "todo" })) } });
    });
    if (s.kind === "promise") return run(async () => {
      await api.patch(`/api/commitments/${s.id}`, { status: "done" });
      toast(`Promise kept: ${s.title}`, { action: { label: "Undo", run: () => run(() => api.patch(`/api/commitments/${s.id}`, { status: "open" })) } });
    });
    if (s.kind === "person") return run(async () => {
      const p = await api.post<PersonSummary>(`/api/people/${s.id}/contacts`, {});
      const logged = p.interactions.length ? Math.max(...p.interactions.map((i) => i.id)) : undefined;
      toast(`Logged: talked to ${p.name} today`, logged ? { action: { label: "Undo", run: () => run(() => api.del(`/api/person-contacts/${logged}`)) } } : undefined);
    });
    ask?.(`Help me follow up: ${s.title}. Draft a short, polite message.`);
  };
  const doneLabel = (s: Suggestion) => (s.kind === "task" ? "Done" : s.kind === "promise" ? "Kept" : s.kind === "person" ? "Talked today" : "Draft it");

  const freeLabel = data?.busy_now ? `In a meeting until ${data.busy_until}` : data?.free_until && !minutes ? `Free until ${data.free_until}` : null;

  return (
    <Card title="What now?" right={freeLabel ? <Badge tone={data?.busy_now ? "warn" : ""}>{freeLabel}</Badge> : undefined}>
      <div className="now-pickers">
        <span className="seg" role="group" aria-label="How do you feel">
          {ENERGY.map((e) => (
            <button key={e.key} className={energy === e.key ? "" : "ghost"} aria-pressed={energy === e.key} onClick={() => pickEnergy(e.key)}>
              <Icon name={e.icon} size={14} /> {e.label}
            </button>
          ))}
        </span>
        <span className="seg" role="group" aria-label="How much time">
          {TIME.map((t) => <button key={t.m} className={minutes === t.m ? "" : "ghost"} aria-pressed={minutes === t.m} onClick={() => pickTime(t.m)}>{t.label}</button>)}
        </span>
      </div>
      <ErrorNote error={actionError} />
      <Loading loading={loading && !data} error={error} />
      {data && !(data.suggestions?.length) && <Empty>{data.note ?? "Nothing to suggest right now."}</Empty>}
      {data && (data.suggestions?.length ?? 0) > 0 && (
        <ol className="now-list">
          {data.suggestions.map((s, i) => (
            <li key={`${s.kind}-${s.id}`} className={i === 0 ? "first" : ""}>
              <span className={`now-icon k-${s.kind}`} aria-hidden="true"><Icon name={KIND_ICON[s.kind]} size={15} /></span>
              <span className="now-main">
                <span className="now-title">{s.title}{s.minutes ? <span className="muted small"> · ~{duration(s.minutes)}</span> : null}</span>
                <span className="now-why small">{s.reasons.join(" · ")}</span>
              </span>
              <span className="actions">
                {s.action === "break_down" && ask && <button className="ghost small" onClick={() => ask(`Break “${s.title}” into steps of 10–20 minutes so I can start it now`)}>Break it down</button>}
                {s.action === "start" && ask && <button className="ghost small" onClick={() => ask(`I have ${minutes || data.minutes} minutes for “${s.title}”. What exactly should I do in that time?`)}>Start it</button>}
                <button className={i === 0 ? "small" : "ghost small"} aria-label={`${doneLabel(s)}: ${s.title}`} onClick={() => void act(s)}>{doneLabel(s)}</button>
              </span>
            </li>
          ))}
        </ol>
      )}
      {data?.note && (data.suggestions?.length ?? 0) > 0 && <p className="small muted">{data.note}</p>}
    </Card>
  );
}
