import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import ErrorBoundary from "./ErrorBoundary";
import { ToastProvider } from "./toast";
import "./styles.css";

createRoot(document.getElementById("root")!).render(<StrictMode><ErrorBoundary><ToastProvider><App /></ToastProvider></ErrorBoundary></StrictMode>);

// Installable app and push notifications: only in the built dashboard, and only where the browser allows it (https, or
// http on this computer). The service worker never caches /api, so no personal data is stored by it.
if (import.meta.env.PROD && "serviceWorker" in navigator && window.isSecureContext) {
  window.addEventListener("load", () => { navigator.serviceWorker.register("/sw.js").catch(() => undefined); });
}
