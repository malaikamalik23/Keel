// Node 25+ defines its own global `localStorage` (and `sessionStorage`), which is undefined unless Node is
// started with --localstorage-file. Vitest does not overwrite globals that already exist, so on new Node
// versions the tests would see Node's empty getter instead of jsdom's working Storage. Point both back at
// jsdom so storage assertions (e.g. "the API key is never written to localStorage") are actually tested.
const dom = (globalThis as { jsdom?: { window: Window } }).jsdom;
if (dom) {
  for (const name of ["localStorage", "sessionStorage"] as const) {
    if (!globalThis[name]) {
      Object.defineProperty(globalThis, name, { value: dom.window[name], configurable: true, writable: true });
    }
  }
}
