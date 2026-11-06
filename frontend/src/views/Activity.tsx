import { api } from "../api";
import { useAction, useLoad } from "../hooks";
import type { Checkin, NotificationRow, Reminder } from "../types";
import { Badge, Card, Empty, ErrorNote, Loading } from "../ui";

const utc = (s: string) => new Date(s + "Z").toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

export default function Activity() {
  const reminders = useLoad<Reminder[]>("/api/reminders?status=pending");
  const checkins = useLoad<Checkin[]>("/api/checkins?limit=10");
  const notifications = useLoad<NotificationRow[]>("/api/notifications?limit=20");
  const refresh = async () => { await Promise.all([reminders.reload(), checkins.reload(), notifications.reload()]); };
  const { run, error } = useAction(refresh);

  return (
    <div className="grid">
      <ErrorNote error={error} />
      <Card title="Upcoming reminders">
        <Loading loading={reminders.loading} error={reminders.error} />
        {reminders.data?.length === 0 && <Empty>No reminders. Ask in Chat: “Remind me tomorrow at 9 to email my supervisor.”</Empty>}
        <ul className="list">
          {reminders.data?.map((r) => (
            <li key={r.id}>
              <span>{r.text} <Badge>{utc(r.fire_at)}</Badge></span>
              <button className="ghost" onClick={() => run(() => api.post(`/api/reminders/${r.id}/cancel`))}>Cancel</button>
            </li>
          ))}
        </ul>
      </Card>

      <Card title="Check-ins" right={<button className="ghost" onClick={() => run(() => api.post("/api/scheduler/tick"))}>Run scheduler now</button>}>
        <Loading loading={checkins.loading} error={checkins.error} />
        {checkins.data?.length === 0 && <Empty>No check-ins yet. They arrive at your configured morning/evening times (SCHEDULER_ENABLED=true).</Empty>}
        <ul className="list">
          {checkins.data?.map((c) => (
            <li key={c.id}><span><Badge>{c.kind}</Badge> <Badge>{c.checkin_date}</Badge><br />{c.summary}{c.user_response && <><br /><em className="muted">You: {c.user_response}</em></>}</span></li>
          ))}
        </ul>
      </Card>

      <Card title="Sent notifications">
        <Loading loading={notifications.loading} error={notifications.error} />
        <table>
          <thead><tr><th>When</th><th>Kind</th><th>Via</th><th>Status</th></tr></thead>
          <tbody>
            {notifications.data?.map((n) => (
              <tr key={n.id} title={n.text}>
                <td>{utc(n.sent_at)}</td><td>{n.kind}</td><td>{n.channel}</td>
                <td><Badge tone={n.status === "sent" ? "ok" : "bad"}>{n.status}</Badge></td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
