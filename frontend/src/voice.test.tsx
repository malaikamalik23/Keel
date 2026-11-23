import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Chat from "./views/Chat";
import { speakable, voiceError } from "./voice";

type Handler = ((e: never) => void) | null;
class FakeRecognition {
  static last: FakeRecognition | null = null;
  lang = ""; interimResults = false; continuous = true; started = false; stopped = false;
  onresult: Handler = null; onerror: Handler = null; onend: (() => void) | null = null;
  constructor() { FakeRecognition.last = this; }
  start() { this.started = true; }
  stop() { this.stopped = true; this.onend?.(); }
  abort() { this.onend?.(); }
  hear(...parts: string[]) {
    const results = parts.map((t, i) => Object.assign([{ transcript: t }], { isFinal: i < parts.length - 1 }));
    (this.onresult as unknown as (e: unknown) => void)?.({ resultIndex: 0, results });
  }
  fail(error: string) { (this.onerror as unknown as (e: unknown) => void)?.({ error }); this.onend?.(); }
}

function stubSynth() {
  const spoken: { text: string; lang: string }[] = [];
  const synth = {
    speak: vi.fn((u: { text: string; lang: string }) => { spoken.push({ text: u.text, lang: u.lang }); }),
    cancel: vi.fn(),
    getVoices: () => [{ lang: "en-US", name: "English" }, { lang: "ur-PK", name: "Urdu" }],
  };
  vi.stubGlobal("speechSynthesis", synth);
  vi.stubGlobal("SpeechSynthesisUtterance", class { lang = ""; voice: unknown = null; onend: (() => void) | null = null; onerror: (() => void) | null = null; constructor(public text: string) {} });
  return { synth, spoken };
}

function stubChat(reply = "Done. See https://example.com/x for **details**.") {
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${input}`);
    return new Response(JSON.stringify({ conversation_id: 1, reply, tool_calls: [], pending_action_ids: [] }), { status: 200 });
  }));
  return calls;
}

beforeEach(() => { Element.prototype.scrollIntoView = vi.fn(); localStorage.clear(); FakeRecognition.last = null; });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); delete (window as { webkitSpeechRecognition?: unknown }).webkitSpeechRecognition; });

describe("voice", () => {
  it("reads replies as speech, not as markup and URLs", () => {
    expect(speakable("Done. See https://example.com/x for **details** [page ~3].")).toBe("Done. See a link for details.");
    expect(voiceError("not-allowed")).toMatch(/Microphone access was blocked/);
    expect(voiceError("aborted")).toBe("");
  });

  it("dictation fills the box but never sends on its own", async () => {
    (window as { webkitSpeechRecognition?: unknown }).webkitSpeechRecognition = FakeRecognition;
    const calls = stubChat();
    const user = userEvent.setup();
    render(<Chat onChange={() => {}} />);
    await user.type(screen.getByLabelText("Message"), "Remind me:");
    await user.click(screen.getByRole("button", { name: "Start dictation" }));
    const rec = FakeRecognition.last!;
    expect(rec.started && rec.lang === "en-US" && rec.interimResults).toBe(true);
    expect(screen.getByRole("button", { name: "Stop dictation" })).toBeTruthy();
    act(() => rec.hear("call the bank ", "tomorrow at 10"));
    expect((screen.getByLabelText("Message") as HTMLInputElement).value).toBe("Remind me: call the bank tomorrow at 10");
    await user.click(screen.getByRole("button", { name: "Stop dictation" }));
    expect(rec.stopped).toBe(true);
    expect(screen.getByRole("button", { name: "Start dictation" })).toBeTruthy();
    expect(calls).toEqual([]);  // nothing sent until you press Send
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(calls).toEqual(["POST /api/chat"]));
  });

  it("uses the chosen language and explains a blocked microphone", async () => {
    (window as { webkitSpeechRecognition?: unknown }).webkitSpeechRecognition = FakeRecognition;
    stubChat();
    const user = userEvent.setup();
    render(<Chat onChange={() => {}} />);
    await user.selectOptions(screen.getByLabelText("Voice language"), "ur-PK");
    expect(localStorage.getItem("keel.voice.lang")).toBe("ur-PK");
    await user.click(screen.getByRole("button", { name: "Start dictation" }));
    expect(FakeRecognition.last!.lang).toBe("ur-PK");
    act(() => FakeRecognition.last!.fail("not-allowed"));
    expect(screen.getByRole("alert").textContent).toMatch(/Microphone access was blocked/);
    expect(screen.getByRole("button", { name: "Start dictation" })).toBeTruthy();
  });

  it("reads replies aloud when switched on, and stops on request", async () => {
    const { synth, spoken } = stubSynth();
    stubChat();
    const user = userEvent.setup();
    render(<Chat onChange={() => {}} />);
    expect(screen.queryByRole("button", { name: "Start dictation" })).toBeNull();  // no dictation in this browser
    await user.click(screen.getByLabelText("Read replies aloud"));
    expect(localStorage.getItem("keel.voice.readAloud")).toBe("on");
    await user.type(screen.getByLabelText("Message"), "plan my day");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(spoken).toEqual([{ text: "Done. See a link for details.", lang: "en-US" }]));
    await user.click(screen.getByRole("button", { name: "Stop reading" }));
    expect(synth.cancel).toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Read aloud" }));
    expect(spoken).toHaveLength(2);
  });

  it("speaks Urdu script with an Urdu voice and stays silent unless asked", async () => {
    const { spoken } = stubSynth();
    stubChat("کل صبح دس بجے بینک کو فون کریں");
    const user = userEvent.setup();
    render(<Chat onChange={() => {}} />);
    await user.type(screen.getByLabelText("Message"), "yaad dilana");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText(/بینک/)).toBeTruthy();
    expect(spoken).toEqual([]);  // read-aloud is off by default
    await user.click(screen.getByRole("button", { name: "Read aloud" }));
    expect(spoken[0].lang).toBe("ur-PK");
  });

  it("shows no voice controls in a browser without speech support", () => {
    stubChat();
    render(<Chat onChange={() => {}} />);
    expect(screen.queryByRole("button", { name: "Start dictation" })).toBeNull();
    expect(screen.queryByLabelText("Read replies aloud")).toBeNull();
  });
});

describe("MicButton (Ctrl+K and Inbox)", () => {
  it("fills the box with what was heard, keeps what was typed, and never submits by itself", async () => {
    vi.stubGlobal("webkitSpeechRecognition", FakeRecognition);
    const { default: MicButton } = await import("./MicButton");
    const seen: string[] = [];
    const user = userEvent.setup();
    render(<MicButton value="call" onChange={(t) => seen.push(t)} />);
    await user.click(screen.getByRole("button", { name: "Start dictation" }));
    act(() => FakeRecognition.last!.hear("the bank ", "at six"));
    expect(seen[seen.length - 1]).toBe("call the bank at six");
    await user.click(screen.getByRole("button", { name: "Stop dictation" }));
    expect(FakeRecognition.last!.stopped).toBe(true);
    expect(screen.getByRole("button", { name: "Start dictation" })).toBeTruthy();
  });

  it("explains a blocked microphone and hides itself when the browser has no dictation", async () => {
    vi.stubGlobal("webkitSpeechRecognition", FakeRecognition);
    const { default: MicButton } = await import("./MicButton");
    const errs: string[] = [];
    const user = userEvent.setup();
    const { unmount } = render(<MicButton value="" onChange={() => undefined} onError={(m) => errs.push(m)} />);
    await user.click(screen.getByRole("button", { name: "Start dictation" }));
    act(() => FakeRecognition.last!.fail("not-allowed"));
    expect(errs[0]).toMatch(/Microphone access was blocked/);
    unmount();
    vi.stubGlobal("webkitSpeechRecognition", undefined);
    render(<MicButton value="" onChange={() => undefined} />);
    expect(screen.queryByRole("button", { name: "Start dictation" })).toBeNull();
  });
});
