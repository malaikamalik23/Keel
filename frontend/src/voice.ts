// Voice in the browser, with no new service or key: the Web Speech API for dictation and speechSynthesis for reading
// replies aloud. Dictation only fills the message box; you still press Send, so a misheard word never runs a command.
// Privacy: in Chrome and Edge, dictation audio is sent to Google's / Microsoft's speech service; the UI says so.

export interface Recognition {
  lang: string; interimResults: boolean; continuous: boolean;
  onresult: ((e: { resultIndex: number; results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal: boolean }> }) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void; stop(): void; abort(): void;
}
type RecognitionCtor = new () => Recognition;

export function recognitionCtor(): RecognitionCtor | null {
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export const canSpeak = () => typeof window !== "undefined" && "speechSynthesis" in window && typeof SpeechSynthesisUtterance !== "undefined";

export const LANGS = [
  { code: "en-US", label: "English" },
  { code: "ur-PK", label: "اردو (Urdu)" },
] as const;

export function voiceError(code: string): string {
  switch (code) {
    case "not-allowed":
    case "service-not-allowed": return "Microphone access was blocked. Allow it for this site in the browser's address bar, then try again.";
    case "no-speech": return "Didn't hear anything. Try again a little closer to the microphone.";
    case "audio-capture": return "No microphone was found.";
    case "network": return "Dictation needs an internet connection in this browser.";
    case "language-not-supported": return "This browser can't take dictation in that language.";
    case "aborted": return "";
    default: return `Dictation stopped (${code}).`;
  }
}

/** What to say for a reply: links and markup are read as words a person would say, not character by character. */
export function speakable(text: string): string {
  return text
    .replace(/https?:\/\/\S+/gi, "a link")
    .replace(/\[(?:page ~?\d+|link removed)\]/gi, " ")
    .replace(/[*_#`>|~]+/g, " ")
    .replace(/\s+/g, " ")
    .replace(/ ([.,!?;:])/g, "$1")
    .trim()
    .slice(0, 2000);
}

/** Read text aloud, stopping whatever is being read. Urdu script gets an Urdu voice when the system has one. */
export function speak(text: string, opts: { lang?: string; onEnd?: () => void } = {}): boolean {
  if (!canSpeak()) return false;
  const say = speakable(text);
  if (!say) return false;
  const synth = window.speechSynthesis;
  synth.cancel();
  const u = new SpeechSynthesisUtterance(say);
  const lang = /[؀-ۿ]/.test(say) ? "ur-PK" : opts.lang ?? "en-US";
  u.lang = lang;
  const voice = synth.getVoices().find((v) => v.lang.toLowerCase().startsWith(lang.slice(0, 2).toLowerCase()));
  if (voice) u.voice = voice;
  u.onend = () => opts.onEnd?.();
  u.onerror = () => opts.onEnd?.();
  synth.speak(u);
  return true;
}

export function stopSpeaking() {
  if (canSpeak()) window.speechSynthesis.cancel();
}

// Per-browser preferences (a convenience only: if storage is blocked, the defaults apply).
export function loadPref(key: string, fallback: string): string {
  try { return localStorage.getItem(`keel.voice.${key}`) ?? fallback; } catch { return fallback; }
}
export function savePref(key: string, value: string) {
  try { localStorage.setItem(`keel.voice.${key}`, value); } catch { /* storage blocked: keep it for this page only */ }
}
