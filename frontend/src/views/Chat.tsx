import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { ToolCall } from "../types";
import { Badge, ErrorNote } from "../ui";
import { LANGS, canSpeak, loadPref, recognitionCtor, savePref, speak, stopSpeaking, voiceError, type Recognition } from "../voice";

interface Turn { role: "user" | "agent"; text: string; tools?: ToolCall[]; pending?: number[]; }

export default function Chat({ onChange }: { onChange: () => void }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [conv, setConv] = useState<number | null>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [resolved, setResolved] = useState<Record<number, string>>({});
  const end = useRef<HTMLDivElement>(null);
  const field = useRef<HTMLInputElement>(null);
  const count = useRef(0);  // turns on screen, so a new reply knows its own index
  // Braces matter: an effect's return value is treated as its cleanup function, and current Chrome's scrollIntoView() returns a Promise.
  useEffect(() => { count.current = turns.length; end.current?.scrollIntoView({ behavior: "smooth" }); }, [turns]);

  // ---- voice
  const Ctor = recognitionCtor();
  const speechOut = canSpeak();
  const [lang, setLang] = useState(() => loadPref("lang", "en-US"));
  const [readAloud, setReadAloud] = useState(() => loadPref("readAloud", "off") === "on");
  const [listening, setListening] = useState(false);
  const [speaking, setSpeaking] = useState<number | null>(null);  // index of the turn being read
  const rec = useRef<Recognition | null>(null);
  const before = useRef("");  // what was typed before dictation started, kept in front of the spoken words
  useEffect(() => () => { rec.current?.abort(); stopSpeaking(); }, []);

  const quiet = () => { stopSpeaking(); setSpeaking(null); };

  const listen = () => {
    if (!Ctor) return;
    if (listening) { rec.current?.stop(); return; }
    quiet(); setError(null);
    const r = new Ctor();
    r.lang = lang; r.interimResults = true; r.continuous = false;
    before.current = input.trim() ? input.trim() + " " : "";
    r.onresult = (e) => {
      let heard = "";
      for (let i = 0; i < e.results.length; i++) heard += e.results[i][0].transcript;
      setInput(before.current + heard.trim());
    };
    r.onerror = (e) => { const msg = voiceError(e.error); if (msg) setError(msg); };
    r.onend = () => { setListening(false); rec.current = null; field.current?.focus(); };
    rec.current = r;
    try { r.start(); setListening(true); } catch { rec.current = null; setError("Dictation couldn't start. Try again."); }
  };

  const read = (i: number, text: string) => {
    if (speaking === i) { quiet(); return; }
    if (speak(text, { lang, onEnd: () => setSpeaking((s) => (s === i ? null : s)) })) setSpeaking(i);
  };

  const send = async (text?: string) => {
    const message = (text ?? input).trim();
    if (!message || busy) return;
    rec.current?.abort();
    quiet();
    setInput(""); setBusy(true); setError(null);
    setTurns((t) => [...t, { role: "user", text: message }]);
    try {
      const r = await api.chat(message, conv);
      setConv(r.conversation_id);
      const index = count.current;  // the user's message is on screen by now; the reply goes right after it
      setTurns((t) => [...t, { role: "agent", text: r.reply, tools: r.tool_calls, pending: r.pending_action_ids }]);
      if (readAloud) read(index, r.reply);
      onChange();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };

  // "Ask Keel" from the command bar (Ctrl+K) or the Home page sends straight into this conversation.
  const sendRef = useRef(send);
  sendRef.current = send;
  useEffect(() => {
    const onAsk = (e: Event) => { const text = (e as CustomEvent<string>).detail; if (text) void sendRef.current(text); };
    window.addEventListener("keel:ask", onAsk);
    return () => window.removeEventListener("keel:ask", onAsk);
  }, []);

  const resolve = async (id: number, action: "confirm" | "reject") => {
    try { await api.post(`/api/pending-actions/${id}/${action}`); setResolved((r) => ({ ...r, [id]: action === "confirm" ? "approved" : "rejected" })); onChange(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };

  return (
    <div className="chat">
      <div className="messages">
        {turns.length === 0 && <p className="muted">Try: “Plan my day, I have 4 hours” · “I'll finish the GRE quant set by Friday” · “What have I been avoiding?”</p>}
        {turns.map((t, i) => (
          <div key={i} className={`msg ${t.role}`}>
            <div className="bubble">{t.text}</div>
            {t.tools && t.tools.length > 0 && <div className="tools">{t.tools.map((c, j) => <Badge key={j} tone={c.status === "ok" ? "ok" : c.status === "error" ? "bad" : "warn"}>{c.name}</Badge>)}</div>}
            {t.role === "agent" && speechOut && t.text && (
              <button type="button" className="ghost speak" aria-pressed={speaking === i} onClick={() => read(i, t.text)}>{speaking === i ? "Stop reading" : "Read aloud"}</button>
            )}
            {t.pending?.map((id) => (
              <div key={id} className="approval">
                {resolved[id] ? <Badge>{resolved[id]}</Badge> : (<><span>Action #{id} needs your approval</span> <button onClick={() => resolve(id, "confirm")}>Approve</button> <button className="ghost" onClick={() => resolve(id, "reject")}>Reject</button></>)}
              </div>
            ))}
          </div>
        ))}
        {busy && <p className="muted">Thinking…</p>}
        <div ref={end} />
      </div>
      <ErrorNote error={error} />
      <form onSubmit={(e) => { e.preventDefault(); void send(); }} className="composer">
        <input ref={field} name="message" autoComplete="off" value={input} onChange={(e) => setInput(e.target.value)}
          placeholder={listening ? "Listening… speak now" : "Message your agent…"} aria-label="Message" disabled={busy} />
        {Ctor && (
          <button type="button" className={listening ? "mic on" : "ghost mic"} aria-pressed={listening} disabled={busy}
            aria-label={listening ? "Stop dictation" : "Start dictation"} title={listening ? "Stop" : "Speak instead of typing"} onClick={listen}>
            {listening ? "■ Stop" : "🎤"}
          </button>
        )}
        <button type="submit" disabled={busy || !input.trim()}>Send</button>
        <button type="button" className="ghost" onClick={() => { quiet(); setTurns([]); setConv(null); }}>New</button>
      </form>
      {(Ctor || speechOut) && (
        <div className="voice-prefs small muted">
          {speechOut && (
            <label><input type="checkbox" checked={readAloud} onChange={(e) => { setReadAloud(e.target.checked); savePref("readAloud", e.target.checked ? "on" : "off"); if (!e.target.checked) quiet(); }} /> Read replies aloud</label>
          )}
          <label>Voice language{" "}
            <select value={lang} aria-label="Voice language" onChange={(e) => { setLang(e.target.value); savePref("lang", e.target.value); }}>
              {LANGS.map((l) => <option key={l.code} value={l.code}>{l.label}</option>)}
            </select>
          </label>
          {Ctor && <span>Dictation fills the box and you still press Send. Your browser sends the audio to its own speech service (Google in Chrome, Microsoft in Edge).</span>}
        </div>
      )}
    </div>
  );
}
