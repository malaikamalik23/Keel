import type { ReactNode } from "react";

export const Card = ({ title, children, right }: { title?: string; children: ReactNode; right?: ReactNode }) => (
  <section className="card">
    {(title || right) && <header><h2>{title}</h2>{right}</header>}
    {children}
  </section>
);
export const Badge = ({ children, tone = "" }: { children: ReactNode; tone?: string }) => <span className={`badge ${tone}`}>{children}</span>;
export const Empty = ({ children }: { children: ReactNode }) => <p className="muted">{children}</p>;
export const ErrorNote = ({ error }: { error: string | null }) => (error ? <p className="error" role="alert">{error}</p> : null);
export const Loading = ({ loading, error }: { loading: boolean; error: string | null }) =>
  error ? <ErrorNote error={error} /> : loading ? <p className="muted">Loading…</p> : null;
export const fmtDate = (d: string | null) => (d ? new Date(d + "T00:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "no date");
export const isOverdue = (d: string | null) => !!d && d < new Date().toLocaleDateString("en-CA");
