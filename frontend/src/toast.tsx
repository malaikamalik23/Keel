import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";

type Toast = { id: number; text: string; tone: "ok" | "bad" | "info"; action?: { label: string; run: () => void | Promise<void> } };
type Show = (text: string, opts?: { tone?: Toast["tone"]; action?: Toast["action"] }) => void;

const ToastContext = createContext<Show>(() => undefined);

/** Small confirmations at the bottom of the screen: every click gets visible feedback, some with Undo. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const next = useRef(1);
  const dismiss = useCallback((id: number) => setToasts((t) => t.filter((x) => x.id !== id)), []);
  const show = useCallback<Show>((text, opts) => {
    const id = next.current++;
    setToasts((t) => [...t.slice(-2), { id, text, tone: opts?.tone ?? "ok", action: opts?.action }]);
    setTimeout(() => dismiss(id), opts?.action ? 6000 : 3500);
  }, [dismiss]);
  return (
    <ToastContext.Provider value={show}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.tone}`}>
            <span>{t.text}</span>
            {t.action && <button className="ghost small" onClick={() => { dismiss(t.id); void t.action!.run(); }}>{t.action.label}</button>}
            <button className="toast-x" aria-label="Dismiss" onClick={() => dismiss(t.id)}>×</button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);
