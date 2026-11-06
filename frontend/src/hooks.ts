import { useCallback, useEffect, useState } from "react";
import { api } from "./api";

export function useLoad<T>(path: string) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const reload = useCallback(async () => {
    try {
      setData(await api.get<T>(path));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [path]);
  useEffect(() => { setLoading(true); void reload(); }, [reload]);
  return { data, error, loading, reload };
}

/** Run a mutation, then refresh; surfaces failures instead of swallowing them. */
export function useAction(after: () => void | Promise<void>) {
  const [error, setError] = useState<string | null>(null);
  const run = useCallback(async (fn: () => Promise<unknown>) => {
    try { await fn(); setError(null); await after(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  }, [after]);
  return { run, error };
}
