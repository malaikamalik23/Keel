import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { Icon, PAGE_ICON } from "./icons";
import MicButton from "./MicButton";
import { useToast } from "./toast";

type Command = { id: string; label: string; hint: string; icon: string; run: () => void | Promise<void> };

/** Ctrl+K (or ⌘K): jump to any page, capture something into the inbox, or ask Keel, without leaving the keyboard. */
export default function CommandBar({ pages, go, ask, onCaptured }: {
  pages: readonly string[]; go: (page: string) => void; ask: (text: string) => void; onCaptured?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const toast = useToast();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setOpen((o) => !o); }
      else if (e.key === "Escape") setOpen(false);
    };
    const onOpen = () => setOpen(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener("keel:command", onOpen);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("keel:command", onOpen); };
  }, []);
  useEffect(() => { if (open) { setQ(""); setSel(0); setTimeout(() => input.current?.focus(), 0); } }, [open]);

  const commands = useMemo<Command[]>(() => {
    const text = q.trim();
    const typed: Command[] = text ? [
      { id: "capture", label: `Capture “${text}”`, hint: "Inbox: Keel suggests where it belongs", icon: "inbox", run: async () => {
        await api.post("/api/inbox", { text }); toast("Captured to the inbox", { action: { label: "Open inbox", run: () => go("Inbox") } }); onCaptured?.();
      } },
      { id: "ask", label: `Ask Keel: “${text}”`, hint: "Chat", icon: "chat", run: () => ask(text) },
    ] : [];
    const words = text.toLowerCase();
    const nav: Command[] = [...pages, "Profile"].filter((p) => !words || p.toLowerCase().includes(words))
      .map((p) => ({ id: `go-${p}`, label: `Go to ${p}`, hint: "Page", icon: PAGE_ICON[p] ?? "sparkle", run: () => go(p) }));
    return [...typed, ...nav];
  }, [q, pages, go, ask, toast, onCaptured]);

  if (!open) return null;
  const pick = async (c: Command | undefined) => {
    if (!c) return;
    setOpen(false);
    try { await c.run(); } catch (e) { toast(e instanceof Error ? e.message : String(e), { tone: "bad" }); }
  };

  return (
    <div className="cmd-backdrop" onMouseDown={() => setOpen(false)}>
      <div className="cmd" role="dialog" aria-label="Command bar" onMouseDown={(e) => e.stopPropagation()}>
        <div className="cmd-input"><Icon name="search" />
          <input ref={input} value={q} placeholder="Type to capture, ask Keel, or jump to a page…" aria-label="Command"
            onChange={(e) => { setQ(e.target.value); setSel(0); }}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(s + 1, commands.length - 1)); }
              else if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)); }
              else if (e.key === "Enter") { e.preventDefault(); void pick(commands[sel]); }
            }} />
          <MicButton value={q} onChange={(t) => { setQ(t); setSel(0); input.current?.focus(); }} onError={(m) => toast(m, { tone: "bad" })} label="Say it: capture or ask by voice" />
          <kbd>Esc</kbd>
        </div>
        <ul className="cmd-list" role="listbox">
          {commands.map((c, i) => (
            <li key={c.id} role="option" aria-selected={i === sel} className={i === sel ? "sel" : ""}
              onMouseEnter={() => setSel(i)} onClick={() => void pick(c)}>
              <Icon name={c.icon} /><span>{c.label}</span><span className="muted small">{c.hint}</span>
            </li>
          ))}
          {commands.length === 0 && <li className="muted">Nothing matches.</li>}
        </ul>
      </div>
    </div>
  );
}
