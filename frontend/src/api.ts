import type { ChatResponse } from "./types";

const KEY_STORAGE = "agent_api_key";
export const getApiKey = () => sessionStorage.getItem(KEY_STORAGE) ?? "";
export const setApiKey = (k: string) => (k ? sessionStorage.setItem(KEY_STORAGE, k) : sessionStorage.removeItem(KEY_STORAGE));

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function request<T>(method: string, path: string, body?: unknown, raw?: Blob): Promise<T> {
  const headers: Record<string, string> = {};
  if (raw) headers["Content-Type"] = "application/octet-stream";
  else if (body !== undefined) headers["Content-Type"] = "application/json";
  const key = getApiKey();
  if (key) headers["X-API-Key"] = key;
  const res = await fetch(path, { method, headers, body: raw ?? (body === undefined ? undefined : JSON.stringify(body)) });
  if (!res.ok) {
    let detail = res.statusText;
    try { const j = await res.json(); detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* not JSON */ }
    throw new ApiError(res.status, detail);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

/** Download a file from the API (sends the API key like every other request), then save it in the browser. */
async function download(path: string, filename: string): Promise<void> {
  const headers: Record<string, string> = {};
  const key = getApiKey();
  if (key) headers["X-API-Key"] = key;
  const res = await fetch(path, { headers });
  if (!res.ok) throw new ApiError(res.status, res.statusText);
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
}

export const api = {
  download,
  get: <T,>(path: string) => request<T>("GET", path),
  post: <T,>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
  patch: <T,>(path: string, body: unknown) => request<T>("PATCH", path, body),
  put: <T,>(path: string, body: unknown) => request<T>("PUT", path, body),
  del: (path: string) => request<void>("DELETE", path),
  /** Send a file as the raw request body (no multipart parsing needed on the server). */
  upload: <T,>(path: string, file: Blob) => request<T>("POST", path, undefined, file),
  chat: (message: string, conversation_id: number | null) =>
    request<ChatResponse>("POST", "/api/chat", { message, conversation_id }),
};
