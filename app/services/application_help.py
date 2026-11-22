"""Application help computed by code, not guessed by the model: eligibility against the user's own profile, which
checklist documents already exist in the notes or drafts, and which profile fact fits each field of an online form.

Everything here only reads. Facts come from the user's saved memories (profile topics such as "cgpa", "nationality"),
never from the posting. A requirement Keel can't check is said to be unclear, not met.
"""
from __future__ import annotations

import re
from datetime import date

from app.db.models import ApplicationDraft, Document, Memory, Opportunity

PROFILE_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("full name", "name"), "email": ("email", "e-mail"), "phone": ("phone", "mobile", "whatsapp", "contact number"),
    "nationality": ("nationality", "citizenship", "citizen"), "date_of_birth": ("date of birth", "dob", "birth date", "birthday"),
    "degree": ("degree", "qualification", "education"), "field": ("field of study", "major", "discipline", "subject"),
    "university": ("university", "institution", "college", "school"), "cgpa": ("cgpa", "gpa", "grade point"),
    "graduation": ("graduation", "graduating", "graduate in"), "ielts": ("ielts",), "toefl": ("toefl",), "pte": ("pte",),
    "duolingo": ("duolingo",), "experience": ("work experience", "years of experience", "experience"),
    "address": ("address",), "city": ("city",), "country": ("country of residence", "country"), "gender": ("gender",),
    "linkedin": ("linkedin",), "github": ("github",),
}
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
DEGREE_RANK = [("phd", 3, r"\b(ph\.?\s?d|doctora\w*)"), ("master", 2, r"\b(master'?s?|ms|msc|m\.s|mphil|mba)\b"),
               ("bachelor", 1, r"\b(bachelor'?s?|bs|bsc|b\.s|be|beng|undergraduate)\b")]
COUNTRIES = ("Pakistan", "India", "Bangladesh", "Nepal", "Sri Lanka", "Afghanistan", "Nigeria", "Kenya", "Egypt", "Iran",
             "Indonesia", "Turkey", "China", "United States", "United Kingdom", "Germany", "Canada", "Australia")
DEMONYMS = {"pakistani": "Pakistan", "indian": "India", "bangladeshi": "Bangladesh", "nepali": "Nepal", "sri lankan": "Sri Lanka",
            "afghan": "Afghanistan", "nigerian": "Nigeria", "kenyan": "Kenya", "egyptian": "Egypt", "iranian": "Iran",
            "indonesian": "Indonesia", "turkish": "Turkey", "chinese": "China", "american": "United States", "british": "United Kingdom",
            "german": "Germany", "canadian": "Canada", "australian": "Australia"}


def profile_facts(memories: list[Memory]) -> dict[str, dict]:
    """Canonical profile key -> {"value", "memory_id"}: a memory's topic first, else its wording ("My CGPA is 3.4")."""
    out: dict[str, dict] = {}
    for m in sorted(memories, key=lambda m: (m.topic is None, -m.id)):
        topic = (m.topic or "").lower()
        text = m.content.lower()
        for key, aliases in PROFILE_ALIASES.items():
            if key in out:
                continue
            if topic and any(topic == a or topic.startswith(a) for a in aliases + (key,)):
                out[key] = {"value": m.content.strip()[:200], "memory_id": m.id}
            elif not topic and key in ("cgpa", "ielts", "toefl", "pte", "nationality") and any(a in text for a in aliases):
                out[key] = {"value": m.content.strip()[:200], "memory_id": m.id}
    return out


def _num(text: str | None) -> float | None:
    m = _NUMBER.search(text or "")
    return float(m.group(0)) if m else None


def _degree_rank(text: str) -> tuple[str, int] | None:
    for name, rank, rx in DEGREE_RANK:
        if re.search(rx, text, re.IGNORECASE):
            return name, rank
    return None


def _age(dob_text: str, today: date) -> int | None:
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", dob_text) or re.search(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", dob_text)
    if not m:
        return None
    a, b, c = (int(x) for x in m.groups())
    y, mo, d = (a, b, c) if a > 31 else (c, b, a)
    try:
        born = date(y, mo, d)
    except ValueError:
        return None
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def requirement_lines(text: str) -> list[str]:
    parts = re.split(r"\n+|(?<=[.;])\s+|\s+[-•*]\s+", text or "")
    return [p.strip(" -•*\t") for p in parts if len(p.strip(" -•*\t")) > 3][:40]


def check_requirement(line: str, facts: dict[str, dict], today: date) -> dict:
    low = line.lower()

    def result(kind, status, needs, you=None, why=""):
        return {"requirement": line[:240], "kind": kind, "needs": needs, "you": you, "status": status, "why": why}

    if m := re.search(r"\b(?:c?gpa)\b[^\d]{0,30}(\d(?:\.\d{1,2})?)(?:\s*/\s*(\d(?:\.\d)?)|\s+(?:out )?of\s+(\d(?:\.\d)?))?", low):
        need, scale = float(m.group(1)), float(m.group(2) or m.group(3) or 4)
        fact = facts.get("cgpa")
        if not fact:
            return result("cgpa", "unknown", f"CGPA {need:g}/{scale:g}", why="Your CGPA isn't saved.")
        have = _num(fact["value"])
        own = re.search(r"(?:/|out of)\s*(\d(?:\.\d)?)", fact["value"].lower())
        if have is None or have > scale or (own and float(own.group(1)) != scale):
            return result("cgpa", "check", f"CGPA {need:g}/{scale:g}", fact["value"],
                          "Your CGPA is on a different scale: check the posting's conversion rule.")
        return result("cgpa", "met" if have >= need else "not_met", f"CGPA {need:g}/{scale:g}", f"{have:g}")
    for test, rx in (("ielts", r"\bielts\b[^\d]{0,30}(\d(?:\.\d)?)"), ("toefl", r"\btoefl\b[^\d]{0,30}(\d{2,3})"),
                     ("pte", r"\bpte\b[^\d]{0,30}(\d{2})"), ("duolingo", r"\bduolingo\b[^\d]{0,30}(\d{2,3})")):
        if m := re.search(rx, low):
            need = float(m.group(1))
            fact = facts.get(test)
            if not fact:
                others = [t for t in ("ielts", "toefl", "pte", "duolingo") if t in facts and t != test]
                why = f"No {test.upper()} score saved" + (f"; you have {', '.join(o.upper() for o in others)}: check if it is accepted instead." if others else ".")
                return result(test, "unknown", f"{test.upper()} {need:g}", why=why)
            have = _num(fact["value"])
            return result(test, "check" if have is None else "met" if have >= need else "not_met", f"{test.upper()} {need:g}", fact["value"])
    if m := re.search(r"(?:under|below|younger than|not (?:be )?(?:older|more) than|maximum age(?: of| is)?|at most|up to)\s*(\d{2})\s*(?:years|yrs)?", low):
        if "age" in low or "old" in low or "years of age" in low:
            limit = int(m.group(1))
            fact = facts.get("date_of_birth")
            age = _age(fact["value"], today) if fact else None
            if age is None:
                return result("age", "unknown", f"age at most {limit}", why="Your date of birth isn't saved.")
            return result("age", "met" if age <= limit else "not_met", f"age at most {limit}", f"{age}")
    if m := re.search(r"(\d+)\+?\s*years?\s*(?:of\s+)?(?:\w+\s+){0,2}experience", low):
        need = int(m.group(1))
        fact = facts.get("experience")
        have = _num(fact["value"]) if fact else None
        if have is None:
            return result("experience", "unknown", f"{need}+ years' experience", fact["value"] if fact else None, "Your years of work experience aren't saved.")
        return result("experience", "met" if have >= need else "not_met", f"{need}+ years' experience", f"{have:g} years")
    wanted = {DEMONYMS[d] for d in DEMONYMS if re.search(rf"\b{d}s?\b", low)} | {c for c in COUNTRIES if c.lower() in low}
    if wanted and re.search(r"citizen|national|passport|resident|domicile|from\b|eligible countr", low):
        fact = facts.get("nationality")
        if not fact:
            return result("nationality", "unknown", " or ".join(sorted(wanted)), why="Your nationality isn't saved.")
        mine = fact["value"].lower()
        hit = any(c.lower() in mine or any(d in mine for d, cc in DEMONYMS.items() if cc == c) for c in wanted)
        if re.search(r"\b(not|except|excluding|ineligible)\b", low):
            return result("nationality", "check", " or ".join(sorted(wanted)), fact["value"], "The posting excludes some countries: read it yourself.")
        return result("nationality", "met" if hit else "not_met", " or ".join(sorted(wanted)), fact["value"])
    if (deg := _degree_rank(line)) and re.search(r"\b(degree|hold|holder|completed|graduate|must have|required)\b", low):
        fact = facts.get("degree")
        if not fact:
            return result("degree", "unknown", f"a {deg[0]}'s degree", why="Your degree isn't saved.")
        have = _degree_rank(fact["value"])
        if have is None:
            return result("degree", "check", f"a {deg[0]}'s degree", fact["value"], "Couldn't tell your degree level.")
        final = re.search(r"final[- ]year|expected|in progress|currently enrolled", fact["value"].lower())
        status = "met" if have[1] >= deg[1] and not final else "check" if have[1] >= deg[1] else "not_met"
        return result("degree", status, f"a {deg[0]}'s degree", fact["value"],
                      "You haven't finished it yet: check whether final-year students may apply." if status == "check" else "")
    return result("other", "check", line[:120], why="Not something Keel can check: read it against your situation.")


def eligibility_report(o: Opportunity, memories: list[Memory], today: date) -> dict:
    facts = profile_facts(memories)
    checks = [check_requirement(line, facts, today) for line in requirement_lines(o.eligibility or "")]
    counts = {s: sum(1 for c in checks if c["status"] == s) for s in ("met", "not_met", "unknown", "check")}
    verdict = ("No requirements are saved for this posting: add them from the posting first." if not checks else
               "At least one stated requirement isn't met." if counts["not_met"] else
               "Every requirement Keel could check is met." if not counts["unknown"] and not counts["check"] else
               "Nothing checked fails, but some can't be confirmed yet.")
    out = {"opportunity": {"id": o.id, "title": o.title, "organization": o.organization}, "verdict": verdict, "counts": counts, "checks": checks}
    if o.deadline:
        days = (o.deadline - today).days
        out["deadline"] = {"date": o.deadline.isoformat(), "days_left": days, **({"passed": True} if days < 0 else {})}
    missing = sorted({c["kind"] for c in checks if c["status"] == "unknown"})
    if missing:
        out["ask_user"] = [f"Save your {k.replace('_', ' ')} (save_memory with topic '{k.replace('_', ' ')}')" for k in missing]
    out["rules"] = "Report this honestly, unknowns included. Don't call the user eligible when anything is unknown or not met."
    return out


DOC_KINDS: dict[str, tuple[tuple[str, ...], str | None]] = {
    "cv": (("cv", "resume", "résumé", "curriculum vitae"), "cv"),
    "sop": (("sop", "statement of purpose", "personal statement", "motivation letter", "motivation", "statement"), "sop"),
    "cover letter": (("cover letter",), "cover_letter"),
    "transcript": (("transcript", "marksheet", "mark sheet", "grades", "degree certificate", "certificate"), None),
    "recommendation": (("recommendation", "reference", "lor"), None),
    "passport": (("passport",), None),
    "language test": (("ielts", "toefl", "pte", "duolingo", "english"), None),
    "research proposal": (("research proposal", "proposal"), None),
    "portfolio": (("portfolio",), None),
}


def doc_kind(name: str) -> str | None:
    low = name.lower()
    return next((k for k, (aliases, _) in DOC_KINDS.items() if any(a in low for a in aliases)), None)


def readiness(o: Opportunity, documents: list[Document], drafts: list[ApplicationDraft]) -> dict:
    items = []
    for d in o.documents or []:
        kind = doc_kind(d["name"])
        aliases, draft_kind = DOC_KINDS.get(kind, ((), None)) if kind else ((), None)
        files = [doc.path for doc in documents if kind and any(a in doc.path.lower().replace("_", " ").replace("-", " ") for a in aliases)][:5]
        mine = [{"id": x.id, "title": x.title} for x in drafts if draft_kind and x.kind == draft_kind and x.opportunity_id in (o.id, None)][:5]
        status = d.get("status", "needed")
        item = {"name": d["name"], "status": status, "files_in_notes": files, "drafts": mine}
        if d.get("from"):
            item["from"] = d["from"]
        if status in ("ready", "received"):
            item["next"] = "Done."
        elif kind == "recommendation":
            item["next"] = (f"Waiting for {d.get('from', 'the referee')}." if status == "requested"
                            else "Ask a referee and mark it requested (update_opportunity).")
        elif files or mine:
            item["next"] = "Something already exists: check it fits this application, then mark it ready."
        else:
            item["next"] = "Nothing found yet: write or upload it." + (" Keel can draft it (prepare_application)." if draft_kind else "")
        items.append(item)
    left = [i["name"] for i in items if i["status"] not in ("ready", "received")]
    return {"opportunity": {"id": o.id, "title": o.title}, "documents": items, "still_needed": left,
            "ready": not left and bool(items),
            **({} if items else {"note": "This posting has no document checklist yet: add the required documents with update_opportunity."})}


FORM_TEXT_DRAFTS = (("sop", r"statement|purpose|motivation|why (do )?you|essay|personal"), ("cover_letter", r"cover letter"))


def fill_plan(elements: list[dict], facts: dict[str, dict], drafts: list[ApplicationDraft], sensitive) -> dict:
    """For each fillable field on the page: the profile fact or draft that fits, or why the user must fill it."""
    plan, left = [], []
    for el in elements:
        if el["tag"] not in ("input", "textarea", "select") or el.get("type") in ("submit", "button", "image", "reset", "hidden", "file"):
            continue
        label = (el.get("label") or el.get("name") or "").strip()
        low = label.lower()
        if why := sensitive(el):
            left.append({"ref": el["ref"], "label": label, "why": f"{why}: the user types it"})
            continue
        key = next((k for k, aliases in PROFILE_ALIASES.items() if any(a in low for a in aliases)), None)
        if el["tag"] == "input" and el.get("type") == "email":
            key = "email"
        if key and key in facts:
            value = facts[key]["value"]
            if el["tag"] == "select" and el.get("options"):
                match = next((o for o in el["options"] if o and (o.lower() in value.lower() or value.lower() in o.lower())), None)
                if match is None:
                    left.append({"ref": el["ref"], "label": label, "why": f"none of its options matches your saved {key}"})
                    continue
                value = match
            plan.append({"ref": el["ref"], "label": label, "value": value, "from": f"memory #{facts[key]['memory_id']} ({key})"})
            continue
        if el["tag"] == "textarea":
            for kind, rx in FORM_TEXT_DRAFTS:
                draft = next((d for d in drafts if d.kind == kind), None) if re.search(rx, low) else None
                if draft:
                    plan.append({"ref": el["ref"], "label": label, "value": draft.content[:5000], "from": f"draft #{draft.id} ({draft.title})"})
                    break
            else:
                left.append({"ref": el["ref"], "label": label, "why": "no saved draft fits: write one first or ask the user"})
            continue
        left.append({"ref": el["ref"], "label": label, "why": f"no saved {key.replace('_', ' ') if key else 'fact'} fits"})
    gaps = [p for p in plan if "[ADD:" in p["value"]]
    return {"proposed": plan, "left_for_user": left,
            **({"warning": f"{len(gaps)} proposed value(s) still contain [ADD: ...] gaps: fill those before typing them in."} if gaps else {}),
            "next": "Show the user this plan. If they agree, call browser_fill with the proposed values (they approve it). "
                    "Never invent a value for a field left for the user."}
