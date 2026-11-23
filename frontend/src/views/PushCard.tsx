import { useState } from "react";
import { api } from "../api";
import { useLoad } from "../hooks";
import { Card, ErrorNote } from "../ui";

type PushInfo = { enabled: boolean; public_key?: string; devices: { id: number; label: string | null; created_at: string; last_ok_at: string | null }[] };

function keyBytes(b64: string): ArrayBuffer {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out.buffer;
}

function deviceLabel(): string {
  const ua = navigator.userAgent;
  const os = /iPhone/.test(ua) ? "iPhone" : /iPad/.test(ua) ? "iPad" : /Android/.test(ua) ? "Android phone" : /Windows/.test(ua) ? "Windows" : /Mac/.test(ua) ? "Mac" : "This device";
  const browser = /Edg\//.test(ua) ? "Edge" : /Firefox\//.test(ua) ? "Firefox" : /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : "browser";
  return `${os} · ${browser}`;
}

/** Turn notifications on for this device, see which devices get them, send a test. */
export default function PushCard() {
  const { data, reload } = useLoad<PushInfo>("/api/push");
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (!data) return null;

  const supported = typeof window !== "undefined" && window.isSecureContext && "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
  const act = async (fn: () => Promise<void>) => {
    setBusy(true);
    try { await fn(); setError(null); await reload(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  const turnOn = () => act(async () => {
    const permission = await Notification.requestPermission();
    if (permission !== "granted") throw new Error("Notifications are blocked for this site in your browser's settings.");
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(data.public_key ?? "") });
    await api.post("/api/push/subscribe", { subscription: sub.toJSON(), label: deviceLabel() });
    setNote("Notifications are on for this device.");
  });
  const test = () => act(async () => {
    const r = await api.post<{ sent_to: number }>("/api/push/test");
    setNote(`Test sent to ${r.sent_to} device(s).`);
  });

  return (
    <Card title="Notifications on your devices">
      {!data.enabled ? <p className="small muted">Push notifications are off (PUSH_ENABLED=false in .env).</p> : <>
        <p className="small muted">Reminders, check-ins and nudges also arrive here as notifications, on every device you turn them on for. Each message is encrypted for that device; the browser's push service passes it on without being able to read it.</p>
        {supported
          ? <button disabled={busy} onClick={() => void turnOn()}>Turn on notifications on this device</button>
          : <p className="small">This browser can't receive them here: notifications need https (or Keel on this computer). On a phone, open Keel through Tailscale's https address (see docs/INSTALL.md); on an iPhone, add Keel to the Home Screen first.</p>}
        {data.devices.length > 0 && <>
          <ul className="svc-details small">
            {data.devices.map((d) => <li key={d.id}>{d.label ?? "A device"} <span className="muted">· since {d.created_at.slice(0, 10)}{d.last_ok_at ? ` · last delivered ${d.last_ok_at.slice(0, 16).replace("T", " ")}` : ""}</span>{" "}
              <button className="ghost small" aria-label={`Stop notifications to ${d.label ?? "this device"}`} onClick={() => void act(() => api.del(`/api/push/devices/${d.id}`))}>Remove</button></li>)}
          </ul>
          <button className="ghost" disabled={busy} onClick={() => void test()}>Send a test notification</button>
        </>}
        {note && <p role="status" className="small ok-text">{note}</p>}
        <ErrorNote error={error} />
      </>}
    </Card>
  );
}
