import { useLoad } from "../hooks";
import type { Analytics } from "../types";
import { Card, Empty, Loading } from "../ui";

function Bars({ values, labels, unit = "" }: { values: number[]; labels: string[]; unit?: string }) {
  const max = Math.max(...values, 1);
  return (
    <div className="bars" role="img" aria-label={labels.map((l, i) => `${l}: ${values[i]}${unit}`).join(", ")}>
      {values.map((v, i) => (
        <div key={labels[i]} className="bar-col" title={`${labels[i]}: ${v}${unit}`}>
          <span className="bar-value">{v || ""}</span>
          <div className="bar" style={{ height: `${(v / max) * 100}%` }} />
          <span className="bar-label">{labels[i]}</span>
        </div>
      ))}
    </div>
  );
}

const pct = (n: number | null) => (n === null ? "—" : `${Math.round(n * 100)}%`);

export default function Insights() {
  const { data: a, error, loading } = useLoad<Analytics>("/api/analytics?weeks=8");
  if (!a) return <Loading loading={loading} error={error} />;
  const weekLabels = a.weeks.map((w) => new Date(w.week_start + "T00:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" }));
  const noData = a.weeks.every((w) => w.created + w.completed + w.minutes_worked === 0);

  return (
    <div className="grid">
      {noData && <Card><Empty>No history yet. Insights appear once you've created and completed some tasks through the agent.</Empty></Card>}
      <Card title="Tasks completed per week"><Bars values={a.weeks.map((w) => w.completed)} labels={weekLabels} /></Card>
      <Card title="Minutes worked per week"><Bars values={a.weeks.map((w) => w.minutes_worked)} labels={weekLabels} unit=" min" /></Card>
      <Card title="Tasks postponed per week"><Bars values={a.weeks.map((w) => w.postponed)} labels={weekLabels} /></Card>
      <Card title="Completions by weekday"><Bars values={Object.values(a.completions_by_weekday)} labels={Object.keys(a.completions_by_weekday)} /></Card>

      <Card title="Consistency">
        <p className="stat">{a.streak.current_days}<span> day streak</span></p>
        <p className="muted small">Longest in the last {a.streak.window_days} days: {a.streak.longest_days}. A day counts when you completed a task or logged work.</p>
        <p className="stat">{pct(a.commitments.kept_rate)}<span> promises kept</span></p>
        <p className="muted small">{a.commitments.done} kept · {a.commitments.dropped} dropped · {a.commitments.overdue} overdue</p>
      </Card>

      <Card title="Planning accuracy">
        {a.estimates.median_actual_vs_estimate === null ? <Empty>Log work against tasks with estimates to see how accurate your estimates are.</Empty> : (
          <>
            <p className="stat">{a.estimates.median_actual_vs_estimate}×<span> actual vs estimated</span></p>
            <p className="muted small">Median over {a.estimates.tasks_measured} finished tasks. Above 1× means things take longer than you plan.</p>
          </>
        )}
        <h2 className="sub">Why things slip</h2>
        {a.postponement.top_reasons.length === 0 ? <Empty>No postponement reasons recorded yet.</Empty> : (
          <ul className="list">{a.postponement.top_reasons.map((r) => <li key={r.reason}><span>{r.reason}</span><span className="muted">{r.count}×</span></li>)}</ul>
        )}
        {a.postponement.avg_before_completion !== null && <p className="muted small">Finished tasks were postponed {a.postponement.avg_before_completion}× on average.</p>}
      </Card>
    </div>
  );
}
