"""Tool groups and lean tool selection.

Why this exists: every LLM request carries the definitions of every tool. With 40+ tools that is ~6,000 tokens of fixed
overhead per request (about 7,000 with the system prompt), which is slow, costs money on every provider, and on
Groq's free tier (8,000 tokens/minute) makes the agent unusable. In the LEAN profile (default) each request carries
only the core task tools plus the groups the current message plausibly needs:

1. the core group ("tasks") is always sent;
2. keyword rules add groups the message obviously needs ("remind me..." -> reminders);
3. groups used recently in the conversation stay on, so short follow-ups ("yes", "move it to Monday") keep working;
4. anything missed is one `load_tools(groups=[...])` call away: the system prompt lists the unloaded groups.

Nothing here weakens security: permissions, confirmation and taint are enforced by the executor for every tool call,
whether or not the tool was advertised in that request.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from app.tools.base import Permission, Tool, ToolContext, ToolError

if TYPE_CHECKING:
    from app.tools.base import ToolRegistry

CORE = "tasks"
LOAD_TOOLS = "load_tools"

GROUP_INFO: dict[str, str] = {
    "tasks": "tasks: create, list, update, complete, delete (always loaded)",
    "plan": "plan: build/show today's plan, skip plan items, log time worked",
    "accountability": "accountability: what slipped or was avoided, weekly review, history of a task",
    "commitments": "commitments: promises with deadlines (propose, confirm, list, update, complete)",
    "goals": "goals: goals, milestones and projects",
    "memory": "memory: save, search, review, edit or forget facts, preferences and decisions about the user",
    "reminders": "reminders: schedule, list or cancel a reminder",
    "calendar": "calendar: read calendar events and free time",
    "web": "web: search the web, read a web page",
    "github": "github: repositories, READMEs, issues, recent commits",
    "notes": "notes: search and read the user's own notes and papers",
}

TOOL_GROUP: dict[str, str] = {
    **dict.fromkeys(["get_current_datetime", "create_task", "list_tasks", "update_task", "complete_task", "delete_task"], "tasks"),
    **dict.fromkeys(["create_daily_plan", "get_daily_plan", "update_plan_item", "log_work_session", "list_work_sessions"], "plan"),
    **dict.fromkeys(["accountability_report", "task_history"], "accountability"),
    **dict.fromkeys(["propose_commitment", "confirm_commitment", "list_commitments", "update_commitment", "complete_commitment"], "commitments"),
    **dict.fromkeys(["create_goal", "list_goals", "update_goal", "add_milestones", "milestone_to_tasks", "create_project", "list_projects", "update_project"], "goals"),
    **dict.fromkeys(["save_memory", "search_memories", "review_memories", "update_memory", "delete_memory"], "memory"),
    **dict.fromkeys(["create_reminder", "list_reminders", "cancel_reminder"], "reminders"),
    "read_calendar": "calendar",
    **dict.fromkeys(["web_fetch", "web_search"], "web"),
    **dict.fromkeys(["github_list_repos", "github_get_readme", "github_list_issues", "github_recent_commits"], "github"),
    **dict.fromkeys(["notes_search", "read_note"], "notes"),
}


def group_of(tool_name: str) -> str:
    """Unknown (newly added) tools fall into the core group so they are never silently hidden; a test forces an
    explicit assignment anyway."""
    return CORE if tool_name == LOAD_TOOLS else TOOL_GROUP.get(tool_name, CORE)


def _rx(*alts: str) -> re.Pattern[str]:
    return re.compile(r"(?:" + "|".join(alts) + r")", re.IGNORECASE)


# Deliberately generous: a false positive costs ~1k tokens, a false negative costs one extra load_tools call.
KEYWORDS: dict[str, re.Pattern[str]] = {
    "plan": _rx(r"\bplan(s|ning|ned)?\b", r"\bschedul\w*", r"\bagenda\b", r"\btimetable\b", r"\bworked\b", r"\bworking on\b", r"\bspent\b",
                r"\blog(ged)?\b", r"\bsessions?\b", r"\b\d+ ?(mins?|minutes|hrs?|hours?) (on|of|for)\b", r"\bskip\b", r"\bfocus(ed)? (time|session)\b", r"\bwhat should i (do|work on)\b",
                r"\bhow much (time )?(did|have) i (work|stud)\w*", r"\bghant[ae]\w*", r"\bparh(a|i|ai)\b", r"\bkaam kiya\b"),  # Roman Urdu: hours, studied, worked
    "accountability": _rx(r"\bavoid\w*", r"\bslip\w*", r"\bpostpon\w*", r"\bprocrastinat\w*", r"\bput(ting)? (it )?off\b", r"\breview\b",
                          r"\bhow (am i|did i|was my|is my)\b", r"\bmy week\b", r"\bbehind\b", r"\bhistory\b", r"\bwhy (does|do|is|did)\b",
                          r"\bprogress\b", r"\bstuck\b", r"\bon track\b", r"\bkeep (on )?(delaying|pushing)\b", r"\boverwhelm\w*",
                          r"\bconsisten\w*", r"\blately\b", r"\bhow much (time )?(did|have) i (work|stud)\w*",
                          r"\bkaisa raha\b", r"\bkaisi rahi\b", r"\bhaft[ae]\b"),  # Roman Urdu: how did it go, week
    "commitments": _rx(r"\bpromis\w*", r"\bcommitment\w*", r"\bi'?ll\b", r"\bi will\b", r"\bi'?m going to\b", r"\bgoing to (finish|do|submit|send|complete)\b",
                       r"\bby (mon|tues|wednes|thurs|fri|satur|sun)day\b", r"\bdeadline\b", r"\bi owe\b", r"\bi committed\b"),
    "goals": _rx(r"\bgoals?\b", r"\bambition\w*", r"\bmilestone\w*", r"\bprojects?\b", r"\bwant to (learn|get good|become|master|build|improve|be)\b",
                 r"\bget good at\b", r"\blong[- ]term\b", r"\broadmap\b", r"\bcurriculum\b", r"\bdecompose\b", r"\bbreak (it|this|that|my \w+) (down|into)\b",
                 r"\bseekh\w*", r"\bsikh\w*"),  # Roman Urdu: learn
    "memory": _rx(r"\bremember\b", r"\bforget\b", r"\bmemor(y|ies)\b", r"\bprefer\w*", r"\bi (like|love|hate|usually|always|never)\b", r"\babout me\b",
                  r"\bhow i (like|study|work|learn)\b", r"\bi (study|work|focus|learn|sleep|concentrate)( best| better| well)?\b", r"\bkeep in mind\b", r"\bfrom now on\b", r"\bdo you know\b",
                  r"\bfyi\b", r"\bmy (supervisor|advisor|mentor|teacher|manager|boss|roommate|sister|brother|mother|father) is\b", r"\b(tips|advice) (for|on)\b",
                  r"\byaad rakh\w*"),  # Roman Urdu: remember
    "reminders": _rx(r"\bremind\w*", r"\balarm\b", r"\bnotify me\b", r"\bping me\b", r"\bnudge me\b", r"\bwake me\b", r"\balert me\b", r"\bdon'?t let me forget\b",
                     r"\byaad dila\w*"),  # Roman Urdu: remind me
    "calendar": _rx(r"\bcalendar\b", r"\bmeetings?\b", r"\bappointments?\b", r"\bfree time\b", r"\bbusy\b", r"\bavailabilit\w*", r"\bam i free\b", r"\bany(thing| plans)? on (mon|tues|wednes|thurs|fri|satur|sun)day\b", r"\bnext (week|few days)\b", r"\bwhat'?s on (my )?(day|week|schedule)\b"),
    "web": _rx(r"\bsearch\b", r"\bgoogle\b", r"\blook(ing)? up\b", r"\bonline\b", r"\bweb\b", r"\binternet\b", r"\blatest\b", r"\bnews\b", r"\bpapers?\b",
               r"\barticles?\b", r"\barxiv\b", r"https?://", r"\bwww\.", r"\bresearch\b", r"\bsurvey\b", r"\bwikipedia\b", r"\bsources?\b", r"\btechniques?\b", r"\bliterature\b", r"\bwhat'?s new\b", r"\bstate of the art\b", r"\bsota\b"),
    "github": _rx(r"\bgithub\b", r"\brepos?\b", r"\brepositor\w*", r"\bissues?\b", r"\bpull requests?\b", r"\bprs?\b", r"\breadme\b", r"\bcommits?\b", r"\bpushed?\b",
                  r"\b(in|on|of|from|at) [A-Za-z0-9][\w-]*/[A-Za-z0-9][\w.-]*"),  # "in torvalds/linux": an owner/repo name
    "notes": _rx(r"\bnotes?\b", r"\bwrote\b", r"\bwritten\b", r"\bmy (files?|documents?|papers|drafts?|docs)\b", r"\bpdf\b", r"\bwhat did i write\b", r"\bjournal\b", r"\bobsidian\b", r"\bfolder\b", r"\bi (said|mentioned|noted|saved)\b"),
}


def select_groups(message: str, recent: set[str], available: set[str]) -> set[str]:
    """Groups to advertise for this message: core + keyword matches + recently used, limited to what exists."""
    chosen = {CORE} | set(recent)
    for group, rx in KEYWORDS.items():
        if rx.search(message):
            chosen.add(group)
    return chosen & available


def recent_groups(messages: list[dict], limit: int = 12) -> set[str]:
    """Groups of tools the model called in the last `limit` messages (keeps follow-ups like 'yes' working)."""
    found: set[str] = set()
    for m in messages[-limit:]:
        if m.get("role") != "assistant" or not isinstance(m.get("content"), list):
            continue
        for block in m["content"]:
            if block.get("type") == "tool_use":
                found.add(group_of(block["name"]))
    found.discard(CORE)
    return found


def not_loaded(active: set[str], available: set[str]) -> dict[str, str]:
    return {g: GROUP_INFO[g] for g in sorted(available - active) if g in GROUP_INFO}


# ---------------------------------------------------------------------------------- the load_tools meta-tool
class LoadToolsInput(BaseModel):
    groups: list[str] = Field(min_length=1, max_length=6, description="Names of tool groups to load, taken from the 'More tools' list in the system prompt.")


def register_load_tools(registry: ToolRegistry) -> None:
    def load_tools(ctx: ToolContext, p: LoadToolsInput) -> dict:
        available = {group_of(t.name) for t in registry.all()}
        unknown = [g for g in p.groups if g not in available]
        if unknown:
            raise ToolError(f"Unknown tool group(s): {', '.join(unknown)}. Valid groups: {', '.join(sorted(available - {CORE}))}.")
        ctx.active_groups |= set(p.groups)
        names = [t.name for t in registry.all() if group_of(t.name) in p.groups]
        return {"loaded": sorted(set(p.groups)), "tools_now_available": names, "note": "Call the tool you need now."}

    registry.register(Tool(
        name=LOAD_TOOLS,
        description="Load more tools into this conversation. Some tool groups are not loaded by default to save space; the system prompt lists them. Call this first, then call the tool you need.",
        permission=Permission.READ, input_model=LoadToolsInput, handler=load_tools,
    ))
