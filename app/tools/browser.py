"""Browser tools: Keel's own Chromium opens sites, reads them, clicks around, fills forms, attaches the user's documents
and downloads files, and stops before anything consequential.

Rules (enforced here, not by the prompt):
* Page text is untrusted (fenced, taints the turn), like any web page.
* Opening a URL the user didn't give (and no search returned) needs the user's approval, as for web_fetch.
* Clicking links, tabs and menus is free. A click that would submit a form (POST), or whose label reads like a
  consequential action (submit, apply, pay, delete, sign in, register, confirm...), is refused: only browser_submit
  does that, and it always waits for the user to approve the exact site, button and values.
* Typing into a page or attaching a file always waits for the user's approval: a page's scripts can read what is
  typed, so it is disclosure, and the user sees each value (or file) and the site first. Passwords, payment details,
  one-time codes and ID numbers are never typed by Keel at all: the user does those.
* CAPTCHAs are never solved: the user does them.
"""
from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from app.integrations.browser import BrowserError, BrowserSession
from app.security.untrusted import normalize_url
from app.tools.base import Permission, Tool, ToolContext, ToolError, ToolRegistry

CONSEQUENTIAL = re.compile(
    r"\b(submit|apply|send|pay(ment)?|buy|purchase|order|check ?out|confirm|delete|remove|cancel|unsubscribe|subscribe|"
    r"sign ?up|register|create (an )?account|donate|transfer|withdraw|book|reserve|accept|agree|finish|complete|"
    r"log ?in|sign ?in|log ?out|sign ?out|save|upload|publish|post|vote|bid|enrol+|join)\b", re.IGNORECASE)
SENSITIVE = re.compile(
    r"pass(word|code|phrase)|\bpin\b|\botp\b|one[- ]time|verification code|security code|card|cvv|cvc|expir|iban|swift|"
    r"account (number|no)|routing|sort code|\bssn\b|social security|passport|cnic|national id|\bnic\b|tax (id|number)|\bntn\b|bank",
    re.IGNORECASE)
FILLABLE = ("input", "textarea", "select")


def describe(el: dict) -> str:
    words = el.get("label") or el.get("text") or el.get("name") or el["tag"]
    return f"{el['ref']} (“{words[:60]}”)"


def consequential(el: dict) -> str | None:
    """Why clicking this element would do something that needs the user, or None if it's just navigation."""
    tag, kind = el["tag"], el.get("type", "")
    if (el.get("href") or "").lower().startswith("javascript:"):
        return "it runs the page's own code"
    posts = el.get("in_form") and el.get("form_method") == "post"
    if tag == "input" and kind in ("submit", "image") or tag == "button" and kind in ("", "submit") and el.get("in_form"):
        if posts:
            return "it sends a form to the site"
    words = " ".join(str(el.get(k, "")) for k in ("text", "label", "name"))
    m = CONSEQUENTIAL.search(words)
    if m:
        return f"it reads like '{m.group(0).lower()}'"
    if posts and tag == "input" and kind in ("submit", "image"):
        return "it sends a form to the site"
    return None


def sensitive(el: dict) -> str | None:
    if el.get("type") == "password" or el.get("autocomplete", "").startswith(("cc-", "current-password", "new-password", "one-time-code")):
        return "a password, payment or one-time-code field"
    m = SENSITIVE.search(" ".join(str(el.get(k, "")) for k in ("label", "name", "autocomplete")))
    return f"a '{m.group(0).lower()}' field" if m else None


class OpenInput(BaseModel):
    url: str = Field(max_length=2000, description="Full http(s) URL.")


class Empty(BaseModel):
    pass


class RefInput(BaseModel):
    ref: str = Field(max_length=8, description="An element ref from the latest page snapshot, like e12.")


class FieldValue(BaseModel):
    ref: str = Field(max_length=8)
    value: str = Field(max_length=5000, description="Text to type; for a dropdown the option's text; for a checkbox yes/no.")


class FillInput(BaseModel):
    fields: list[FieldValue] = Field(min_length=1, max_length=30)


class AttachInput(BaseModel):
    ref: str = Field(max_length=8, description="The file-upload field.")
    path: str = Field(max_length=500, description="An indexed document in the notes folder, e.g. 'cv.pdf'.")


def _host(url: str) -> str:
    return urlsplit(url).hostname or "?"


def register(registry: ToolRegistry, session: BrowserSession, *, notes_dir: str | None = None) -> None:
    def _b(fn, *a):
        try:
            return fn(*a)
        except BrowserError as exc:
            raise ToolError(str(exc)) from None

    def _last():
        if session.last is None:
            raise ToolError("No page is open. Use browser_open first.")
        return session.last

    def _el(ref: str) -> dict:
        try:
            return _last().element(ref)
        except BrowserError as exc:
            raise ToolError(str(exc)) from None

    def _view(snap) -> dict:
        d = snap.as_dict()
        d["note"] = ("Clicks that would submit, pay, delete, sign in or register are refused: use browser_submit, which asks "
                     "the user first. After queuing a fill or submit, stop and let the user approve it.")
        return d

    # ---- open / read / click / close
    def browser_open(ctx: ToolContext, p: OpenInput) -> dict:
        return _view(_b(session.open, p.url))

    def browser_read(ctx: ToolContext, p: Empty) -> dict:
        return _view(_b(session.snapshot))

    def browser_click(ctx: ToolContext, p: RefInput) -> dict:
        el = _el(p.ref)
        why = consequential(el)
        if why:
            raise ToolError(f"Not clicking {describe(el)}: {why}. That needs the user: use browser_submit, which shows them "
                            "exactly what would be sent and waits for their approval.")
        return _view(_b(session.click, p.ref))

    def browser_close(ctx: ToolContext, p: Empty) -> dict:
        session.close()
        return {"closed": True}

    # ---- fill
    def _fill_plan(p: FillInput) -> list[tuple[dict, str]]:
        plan = []
        for f in p.fields:
            el = _el(f.ref)
            if el["tag"] not in FILLABLE or el.get("type") in ("submit", "button", "image", "file", "reset"):
                raise ToolError(f"{describe(el)} isn't a field you type into.")
            why = sensitive(el)
            if why:
                raise ToolError(f"{describe(el)} is {why}. Keel never types those: the user fills it in themselves.")
            plan.append((el, f.value))
        return plan

    def browser_fill(ctx: ToolContext, p: FillInput) -> dict:
        plan = _fill_plan(p)
        return _view(_b(session.fill, [(el["ref"], v) for el, v in plan]))

    def summarize_fill(ctx: ToolContext, p: FillInput) -> str:
        snap = _last()
        lines = [f"Type into {len(p.fields)} field(s) on {_host(snap.url)} (“{snap.title[:60]}”). The site can see what is typed:"]
        lines += [f"  {(el.get('label') or el.get('name') or el['ref'])[:60]}: {v}" for el, v in _fill_plan(p)]
        return "\n".join(lines)

    # ---- attach a document
    def _attach_file(ctx: ToolContext, p: AttachInput) -> tuple[dict, str, bytes, str]:
        el = _el(p.ref)
        if el["tag"] != "input" or el.get("type") != "file":
            raise ToolError(f"{describe(el)} isn't a file-upload field.")
        if not notes_dir:
            raise ToolError("Attachments come from the notes folder, and NOTES_DIR isn't set.")
        from app.tools.notes import find_document

        root = Path(notes_dir).expanduser().resolve()
        doc = find_document(ctx, p.path)
        full = (root / doc.path).resolve()
        if not full.is_relative_to(root) or not full.is_file():
            raise ToolError(f"{doc.path} isn't in the notes folder any more.")
        data = full.read_bytes()
        if len(data) > 10_000_000:
            raise ToolError("That file is larger than 10 MB.")
        return el, full.name, data, mimetypes.guess_type(full.name)[0] or "application/octet-stream"

    def browser_attach(ctx: ToolContext, p: AttachInput) -> dict:
        el, name, data, mime = _attach_file(ctx, p)
        return _view(_b(session.attach, el["ref"], name, data, mime))

    def summarize_attach(ctx: ToolContext, p: AttachInput) -> str:
        el, name, data, _ = _attach_file(ctx, p)
        import hashlib

        return (f"Attach {name} ({len(data):,} bytes, fingerprint {hashlib.sha256(data).hexdigest()[:12]}) to the "
                f"“{(el.get('label') or el.get('name') or el['ref'])[:60]}” field on {_host(_last().url)}. The site receives the file.")

    # ---- submit (the one consequential action, always approved)
    def _submit_check(p: RefInput) -> tuple[dict, list[tuple[str, str]]]:
        snap = _last()
        el = _el(p.ref)
        if snap.captcha:
            raise ToolError("This page has a CAPTCHA. Keel doesn't solve those: the user has to finish this step themselves.")
        return el, _b(session.form_values, p.ref)

    def browser_submit(ctx: ToolContext, p: RefInput) -> dict:
        _submit_check(p)
        return _view(_b(session.click, p.ref))

    def summarize_submit(ctx: ToolContext, p: RefInput) -> str:
        el, values = _submit_check(p)
        snap = _last()
        why = consequential(el) or "it may send or change something"
        lines = [f"PRESS “{(el.get('text') or el.get('label') or el['ref'])[:60]}” on {_host(snap.url)} ({snap.url[:150]}): {why}."]
        if values:
            lines.append("The form currently holds:")
            lines += [f"  {label or '(unlabelled)'}: {value}" for label, value in values]
        lines.append("This can't be undone from Keel.")
        return "\n".join(lines)

    # ---- download
    def browser_download(ctx: ToolContext, p: RefInput) -> dict:
        if not notes_dir:
            raise ToolError("Set NOTES_DIR to choose where downloads go.")
        el = _el(p.ref)
        if consequential(el):
            raise ToolError(f"{describe(el)} doesn't look like a plain download link.")
        from app.services.documents import index_directory
        from app.services.uploads import UploadRejected, save_upload

        name, data = _b(session.download, p.ref)
        try:
            rel = save_upload(Path(notes_dir).expanduser(), name or "download", data)
        except UploadRejected as exc:
            raise ToolError(str(exc)) from None
        index_directory(ctx.db, Path(notes_dir).expanduser())
        return {"saved": rel, "from": _host(_last().url),
                "note": "Saved into the notes folder and indexed: read_note and extract_from_note work on it now."}

    U = dict(returns_untrusted=True)
    registry.register(Tool(name="browser_open", permission=Permission.READ, input_model=OpenInput, handler=browser_open, **U,
                           taint_gate=lambda ctx, p: normalize_url(p.url) in ctx.seen_urls,
                           summarize=lambda ctx, p: f"Open {_host(p.url)} in Keel's browser: {p.url[:200]}",
                           description="Open a web page in Keel's own browser (for sites that need clicking or forms; web_fetch is "
                                       "enough to just read one). Returns the visible text and the elements with refs (e3)."))
    registry.register(Tool(name="browser_read", permission=Permission.READ, input_model=Empty, handler=browser_read, **U,
                           description="Snapshot the current page again (text, links, buttons, fields)."))
    registry.register(Tool(name="browser_click", permission=Permission.READ, input_model=RefInput, handler=browser_click, **U,
                           description="Click a link, tab or button that only navigates. To submit, apply, pay or confirm, use browser_submit instead."))
    registry.register(Tool(name="browser_fill", permission=Permission.HIGH_IMPACT, input_model=FillInput, handler=browser_fill, **U,
                           summarize=summarize_fill,
                           description="Type into form fields (the user approves the values). Never passwords, payment or ID numbers."))
    registry.register(Tool(name="browser_attach", permission=Permission.HIGH_IMPACT, input_model=AttachInput, handler=browser_attach, **U,
                           summarize=summarize_attach, description="Attach a document from the notes folder to a file-upload field."))
    registry.register(Tool(name="browser_submit", permission=Permission.HIGH_IMPACT, input_model=RefInput, handler=browser_submit, **U,
                           summarize=summarize_submit,
                           description="Press a button that submits, applies, pays or confirms. The user approves the exact site, "
                                       "button and form values first; only when they asked for it to be sent."))
    registry.register(Tool(name="browser_download", permission=Permission.WRITE, input_model=RefInput, handler=browser_download, **U,
                           description="Download the file a link points to into the notes folder (PDF, Word, text)."))
    registry.register(Tool(name="browser_close", permission=Permission.READ, input_model=Empty, handler=browser_close,
                           description="Close Keel's browser."))
