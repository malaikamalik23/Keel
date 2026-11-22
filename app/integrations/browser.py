"""Keel's own browser: a separate Chromium (never the user's browser or profile) that opens pages, fills forms and
downloads documents, and stops before anything consequential.

* It runs on one dedicated thread (Playwright objects belong to the thread that made them), with a fresh private
  profile each time Keel starts: no saved logins, cookies or history carry over.
* Every request the page makes, including scripts and images, goes through a guard that refuses anything that isn't
  plain http(s) on the public internet: a page can't reach Keel's own API, the router or cloud metadata.
* A snapshot is what the model sees: the page's visible text (hidden text is left out) and its links, buttons and
  fields, each with a short ref like "e12". The tools decide what is safe to click (app/tools/browser.py).
"""
from __future__ import annotations

import atexit
import concurrent.futures
import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from app.integrations.safe_http import UnsafeURLError, default_resolver, public_addresses, validate_url

logger = logging.getLogger(__name__)
MAX_TEXT = 8000
MAX_ELEMENTS = 150
CALL_TIMEOUT = 90

# Collects visible interactive elements and tags each with data-keel-ref so a later call can find it again.
SNAPSHOT_JS = r"""
() => {
  const visible = (el) => {
    const s = getComputedStyle(el); const r = el.getBoundingClientRect();
    return s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0' && r.width > 0 && r.height > 0;
  };
  const labelOf = (el) => {
    if (el.labels && el.labels.length) return el.labels[0].innerText;
    const aria = el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('title');
    if (aria) return aria;
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) return l.innerText; }
    return '';
  };
  document.querySelectorAll('[data-keel-ref]').forEach(e => e.removeAttribute('data-keel-ref'));
  const sel = 'a[href], button, input, select, textarea, [role=button], [role=link], [role=checkbox], [role=tab]';
  const out = []; let n = 0;
  for (const el of document.querySelectorAll(sel)) {
    if (out.length >= %MAX%) break;
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (type === 'hidden' || !visible(el)) continue;
    const ref = 'e' + (++n); el.setAttribute('data-keel-ref', ref);
    const tag = el.tagName.toLowerCase();
    const form = el.form || el.closest('form');
    out.push({
      ref, tag, type, role: el.getAttribute('role') || '',
      text: (el.innerText || el.value || '').trim().slice(0, 120),
      label: (labelOf(el) || '').trim().slice(0, 120),
      name: (el.getAttribute('name') || el.id || '').slice(0, 80),
      autocomplete: (el.getAttribute('autocomplete') || '').toLowerCase(),
      href: tag === 'a' ? el.href : '',
      in_form: !!form, form_method: form ? (form.getAttribute('method') || 'get').toLowerCase() : '',
      required: !!el.required, checked: !!el.checked,
      value: (tag === 'input' || tag === 'textarea') && type !== 'password' ? String(el.value || '').slice(0, 200) : '',
      options: tag === 'select' ? Array.from(el.options).slice(0, 30).map(o => o.text.trim().slice(0, 60)) : [],
    });
  }
  const captcha = !!document.querySelector('iframe[src*="recaptcha"], iframe[src*="hcaptcha"], iframe[src*="turnstile"], .g-recaptcha, .h-captcha, .cf-turnstile');
  return {title: document.title, url: location.href, text: document.body ? document.body.innerText : '', elements: out, captcha};
}
""".replace("%MAX%", str(MAX_ELEMENTS))


class BrowserError(Exception):
    pass


@dataclass
class Snapshot:
    url: str
    title: str
    text: str
    elements: list[dict] = field(default_factory=list)
    captcha: bool = False

    def element(self, ref: str) -> dict:
        for e in self.elements:
            if e["ref"] == ref:
                return e
        raise BrowserError(f"No element {ref} on this page. Take a new snapshot with browser_read.")

    def as_dict(self) -> dict:
        d = {"url": self.url, "title": self.title, "text": self.text, "elements": self.elements}
        if self.captcha:
            d["captcha"] = "This page has a CAPTCHA. Keel doesn't solve those: the user has to do it."
        return d


class BrowserSession:
    """One Keel browser tab, used from a single worker thread. `fixtures` (tests only) serves fixed pages for URLs
    instead of the network; `resolver` resolves host names for the public-address guard."""

    def __init__(self, *, headless: bool = True, allowed_domains=(), blocked_domains=(),
                 resolver: Callable[[str], list[str]] = default_resolver, fixtures: dict[str, tuple] | None = None,
                 idle_seconds: float = 900):
        self.headless = headless
        self.allowed, self.blocked = list(allowed_domains), list(blocked_domains)
        self._resolver = resolver
        self.fixtures = fixtures
        self.idle_seconds = idle_seconds
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="keel-browser")
        self._pw = self._browser = self._context = self._page = None
        self._last_used = 0.0
        self.blocked_requests: list[str] = []
        self.requests: list[tuple[str, str, str | None]] = []
        self.last: Snapshot | None = None
        self._lock = threading.Lock()
        atexit.register(self.close)

    # ---- thread plumbing
    def _run(self, fn, *args):
        future = self._pool.submit(fn, *args)
        try:
            return future.result(timeout=CALL_TIMEOUT)
        except concurrent.futures.TimeoutError:
            raise BrowserError("The page took too long.") from None

    def _ensure(self):
        if self._page is not None and time.monotonic() - self._last_used > self.idle_seconds:
            self._shutdown()  # an old session: start clean
        if self._page is None:
            from playwright.sync_api import sync_playwright

            self._pw = sync_playwright().start()
            try:
                self._browser = self._pw.chromium.launch(headless=self.headless)
            except Exception as exc:  # noqa: BLE001 - browser binary missing
                self._pw.stop()
                self._pw = None
                raise BrowserError("Keel's browser isn't installed: run `python -m playwright install chromium`.") from exc
            self._context = self._browser.new_context(accept_downloads=True, java_script_enabled=True)
            self._context.route("**/*", self._guard)
            self._page = self._context.new_page()
            self._page.set_default_timeout(30_000)
        self._last_used = time.monotonic()
        return self._page

    def _guard(self, route):
        url = route.request.url
        if self.fixtures is not None:  # tests: fixed pages, and a log of every request the page made
            self.requests.append((route.request.method, url, route.request.post_data))
            hit = self.fixtures.get(url.split("#")[0].split("?")[0])
            if hit is None:
                self.blocked_requests.append(url)
                return route.fulfill(status=404, body="not in test fixtures")
            status, ctype, body, *headers = hit
            return route.fulfill(status=status, content_type=ctype, body=body, headers=headers[0] if headers else None)
        parts = urlsplit(url)
        if parts.scheme in ("data", "blob", "about"):
            return route.continue_()
        try:
            if parts.scheme not in ("http", "https"):
                raise UnsafeURLError("scheme")
            public_addresses(parts.hostname or "", self._resolver)
        except UnsafeURLError:
            self.blocked_requests.append(url)
            return route.abort("blockedbyclient")
        return route.continue_()

    def _shutdown(self):
        for obj in (self._context, self._browser):
            try:
                if obj is not None:
                    obj.close()
            except Exception:  # noqa: BLE001 - already gone
                logger.debug("browser close failed", exc_info=True)
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:  # noqa: BLE001 - already stopped
            logger.debug("playwright stop failed", exc_info=True)
        self._pw = self._browser = self._context = self._page = None
        self.last = None

    def close(self):
        try:
            self._run(self._shutdown)
        except Exception:  # noqa: BLE001 - shutting down anyway
            logger.debug("browser shutdown failed", exc_info=True)

    # ---- page work (each runs on the worker thread)
    def _snap(self, page) -> Snapshot:
        page.wait_for_load_state("domcontentloaded")
        d = page.evaluate(SNAPSHOT_JS)
        text = re.sub(r"\n{3,}", "\n\n", d.get("text") or "").strip()
        self.last = Snapshot(d["url"], (d.get("title") or "")[:200], text[:MAX_TEXT] + (" [cut]" if len(text) > MAX_TEXT else ""),
                             d.get("elements") or [], bool(d.get("captcha")))
        return self.last

    def open(self, url: str) -> Snapshot:
        if self.fixtures is None:
            try:
                validate_url(url, allowed_domains=self.allowed, blocked_domains=self.blocked, resolver=self._resolver)
            except UnsafeURLError as exc:
                raise BrowserError(str(exc)) from None

        def go():
            page = self._ensure()
            try:
                page.goto(url, wait_until="domcontentloaded")
            except Exception as exc:  # noqa: BLE001 - network errors, aborted by the guard
                raise BrowserError(f"Couldn't open the page ({type(exc).__name__}).") from None
            return self._snap(page)
        with self._lock:
            return self._run(go)

    def snapshot(self) -> Snapshot:
        def snap():
            if self._page is None:
                raise BrowserError("No page is open. Use browser_open first.")
            self._last_used = time.monotonic()
            return self._snap(self._page)
        with self._lock:
            return self._run(snap)

    def _loc(self, ref: str):
        if not re.fullmatch(r"e\d{1,4}", ref):
            raise BrowserError("Element refs look like e12.")
        if self._page is None:
            raise BrowserError("No page is open. Use browser_open first.")
        loc = self._page.locator(f'[data-keel-ref="{ref}"]')
        if loc.count() != 1:
            raise BrowserError(f"No element {ref} on this page any more. Take a new snapshot with browser_read.")
        return loc

    def click(self, ref: str) -> Snapshot:
        def do():
            loc = self._loc(ref)
            self._last_used = time.monotonic()
            try:
                with self._page.expect_navigation(wait_until="domcontentloaded", timeout=8000):
                    loc.click()
            except Exception:  # noqa: BLE001 - many clicks don't navigate (tabs, menus): just settle
                self._page.wait_for_timeout(400)
            return self._snap(self._page)
        with self._lock:
            return self._run(do)

    def fill(self, values: list[tuple[str, str]]) -> Snapshot:
        def do():
            for ref, value in values:
                loc = self._loc(ref)
                tag = loc.evaluate("e => e.tagName.toLowerCase()")
                kind = (loc.get_attribute("type") or "").lower()
                if tag == "select":
                    loc.select_option(label=value)
                elif kind in ("checkbox", "radio"):
                    loc.set_checked(value.strip().lower() in ("true", "yes", "on", "1", "checked"))
                else:
                    loc.fill(value)
            self._last_used = time.monotonic()
            return self._snap(self._page)
        with self._lock:
            return self._run(do)

    def attach(self, ref: str, name: str, data: bytes, mime: str) -> Snapshot:
        def do():
            self._loc(ref).set_input_files(files=[{"name": name, "mimeType": mime, "buffer": data}])
            return self._snap(self._page)
        with self._lock:
            return self._run(do)

    def download(self, ref: str) -> tuple[str, bytes]:
        def do():
            loc = self._loc(ref)
            with self._page.expect_download(timeout=30_000) as info:
                loc.click()
            dl = info.value
            path = dl.path()
            data = open(path, "rb").read() if path else b""  # noqa: PTH123, SIM115 - Playwright's temp file
            return dl.suggested_filename, data
        with self._lock:
            try:
                return self._run(do)
            except BrowserError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise BrowserError(f"That didn't download a file ({type(exc).__name__}).") from None

    def form_values(self, ref: str) -> list[tuple[str, str]]:
        """Label -> value of the fields in the same form as `ref`, as the page holds them now (passwords masked)."""
        def do():
            return self._loc(ref).evaluate(r"""el => {
              const form = el.form || el.closest('form'); if (!form) return [];
              const out = [];
              for (const f of form.querySelectorAll('input, select, textarea')) {
                const t = (f.getAttribute('type') || '').toLowerCase();
                if (['hidden', 'submit', 'button', 'image', 'reset'].includes(t)) continue;
                let label = (f.labels && f.labels.length ? f.labels[0].innerText : (f.getAttribute('aria-label') || f.name || f.id || '')).trim();
                let v = t === 'password' ? (f.value ? '••••' : '') : t === 'file' ? Array.from(f.files || []).map(x => x.name).join(', ')
                        : (t === 'checkbox' || t === 'radio') ? (f.checked ? 'yes' : 'no')
                        : f.tagName === 'SELECT' ? (f.selectedOptions[0] ? f.selectedOptions[0].text : '') : (f.value || '');
                out.push([label.slice(0, 80), String(v).slice(0, 300)]);
              }
              return out;
            }""")
        with self._lock:
            return [tuple(x) for x in self._run(do)]
