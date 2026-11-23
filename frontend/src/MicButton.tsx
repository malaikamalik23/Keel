import { useEffect, useRef, useState } from "react";
import { Icon } from "./icons";
import { loadPref, recognitionCtor, voiceError, type Recognition } from "./voice";

/** A microphone button for any text box: speech fills the box, and you still press Enter or a button to use it, so a
 * misheard word never runs anything. Hidden when the browser has no speech recognition. In Chrome and Edge the audio
 * goes to Google's or Microsoft's speech service (the tooltip says so). Uses the language picked in Chat. */
export default function MicButton({ value, onChange, onError, label = "Speak instead of typing" }: {
  value: string; onChange: (text: string) => void; onError?: (msg: string) => void; label?: string;
}) {
  const Ctor = recognitionCtor();
  const [listening, setListening] = useState(false);
  const rec = useRef<Recognition | null>(null);
  const before = useRef("");
  useEffect(() => () => rec.current?.abort(), []);
  if (!Ctor) return null;

  const toggle = () => {
    if (listening) { rec.current?.stop(); return; }
    const r = new Ctor();
    r.lang = loadPref("lang", "en-US"); r.interimResults = true; r.continuous = false;
    before.current = value.trim() ? value.trim() + " " : "";
    r.onresult = (e) => {
      let heard = "";
      for (let i = 0; i < e.results.length; i++) heard += e.results[i][0].transcript;
      onChange(before.current + heard.trim());
    };
    r.onerror = (e) => { const msg = voiceError(e.error); if (msg) onError?.(msg); };
    r.onend = () => { setListening(false); rec.current = null; };
    rec.current = r;
    try { r.start(); setListening(true); } catch { rec.current = null; onError?.("Dictation couldn't start. Try again."); }
  };

  return (
    <button type="button" className={listening ? "mic on" : "ghost mic"} aria-pressed={listening}
      aria-label={listening ? "Stop dictation" : "Start dictation"}
      title={listening ? "Stop" : `${label}. In Chrome and Edge the audio is sent to Google's or Microsoft's speech service to turn it into text.`}
      onClick={toggle}>
      {listening ? <span className="mic-pulse" aria-hidden="true" /> : null}<Icon name="mic" size={16} />{listening ? " Stop" : ""}
    </button>
  );
}
