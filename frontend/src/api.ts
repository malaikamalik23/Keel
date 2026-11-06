import type { ChatResponse } from "./types";

const KEY_STORAGE = "agent_api_key";
export const getApiKey = () => sessionStorage.getItem(KEY_STORAGE) ?? "";
export const setApiKey = (k: string) => (k ? sessionStorage.setItem(KEY_STORAGE, k) : sessionStorage.removeItem(KEY_STORAGE));

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const key = getApiKey();
  if (key) headers["X-API-Key"] = key;
  const res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  if (!res.ok) {
    let detail = res.statusText;
    try { const j = await res.json(); detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* not JSON */ }
    throw new ApiError(res.status, detail);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

export const api = {
  get: <T,>(path: string) => request<T>("GET", path),
  post: <T,>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
  patch: <T,>(path: string, body: unknown) => request<T>("PATCH", path, body),
  del: (path: string) => request<void>("DELETE", path),
  chat: (message: string, conversation_id: number | null) =>
    request<ChatResponse>("POST", "/api/chat", { message, conversation_id }),
};
