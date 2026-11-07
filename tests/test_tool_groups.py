import json

import pytest

from app.agent.prompts import build_system_prompt
from app.tools.base import ToolContext
from app.tools.groups import CORE, GROUP_INFO, KEYWORDS, LOAD_TOOLS, TOOL_GROUP, group_of, not_loaded, recent_groups, select_groups
from evals.common import FIXED_NOW, full_registry, load_cases
from tests.conftest import make_settings
from tests.fakes import ScriptedLLM, text, tool_call

REG = full_registry()
AVAILABLE = REG.available_groups()

# Natural phrasings written AFTER the keyword rules. 21/29 matched before the patterns for the misses were added
# (the honest, untuned estimate was 72%); the rest reach their tools through load_tools. Kept as a regression set.
HELDOUT = [
    ("can you schedule out tomorrow for me", "plan"), ("i spent two hours on the thesis today", "plan"), ("i did 90 mins on the report", "plan"),
    ("what's slipping?", "accountability"), ("how's my week going", "accountability"), ("i keep avoiding my thesis", "accountability"),
    ("promise: send the draft to Dr. Khan on Thursday", "commitments"), ("list my promises", "commitments"),
    ("i want to learn rust this year", "goals"), ("ok, set up a roadmap for my thesis", "goals"),
    ("please keep in mind that i'm vegetarian", "memory"), ("i study best at night", "memory"), ("forget what i said about mornings", "memory"),
    ("wake me up at 6am tomorrow", "reminders"), ("cancel that reminder", "reminders"), ("add a reminder for friday 5pm", "reminders"),
    ("when am i free this week", "calendar"), ("do i have anything on thursday", "calendar"), ("what's on my calendar", "calendar"),
    ("find recent literature on federated ids", "web"), ("what's new in llm security", "web"), ("summarize this page: https://example.com/a", "web"),
    ("any open bugs on my repo", "github"), ("show me my latest PRs", "github"),
    ("what's in my folder about honeypots", "notes"), ("what did i say about drift in my drafts", "notes"),
    ("which tasks did i finish this week", "tasks"), ("mark it done", "tasks"), ("delete the old tasks", "tasks"),
]


def tokens(tools, system="") -> int:
    return (len(json.dumps(tools)) + len(system)) // 4  # rough: 4 chars per token


# ------------------------------------------------------------------ structure
def test_every_registered_tool_has_an_explicit_group_and_every_group_is_described():
    names = {t.name for t in REG.all()} - {LOAD_TOOLS}
    assert names - set(TOOL_GROUP) == set(), "assign a group in app/tools/groups.py"
    assert set(TOOL_GROUP) - names == set(), "TOOL_GROUP lists a tool that no longer exists"
    assert set(TOOL_GROUP.values()) == set(GROUP_INFO) and set(KEYWORDS) == set(GROUP_INFO) - {CORE}
    assert group_of("some_future_tool") == CORE and group_of(LOAD_TOOLS) == CORE  # unknown tools are never hidden


def test_specs_filtering_and_the_load_tools_helper():
    full = {s["name"] for s in REG.specs()}
    assert LOAD_TOOLS not in full and len(full) == len(REG.all()) - 1
    core_only = {s["name"] for s in REG.specs({CORE})}
    assert core_only == {n for n, g in TOOL_GROUP.items() if g == CORE} | {LOAD_TOOLS}
    two = {s["name"] for s in REG.specs({CORE, "web"})}
    assert {"web_search", "web_fetch", "create_task", LOAD_TOOLS} <= two and "github_list_repos" not in two
    assert LOAD_TOOLS not in {s["name"] for s in REG.specs(set(AVAILABLE))}  # nothing left to load


def test_lean_requests_are_much_smaller_than_full_ones():
    full = tokens(REG.specs(), build_system_prompt(FIXED_NOW))
    lean = tokens(REG.specs({CORE}), build_system_prompt(FIXED_NOW, more_tools=not_loaded({CORE}, AVAILABLE)))
    assert full > 6500            # the problem: ~7k tokens before the conversation even starts
    assert lean < 3300            # fits several requests per minute inside Groq's free 8k tokens/minute
    assert lean < 0.5 * full


# ------------------------------------------------------------------ selection
def test_select_groups_core_keywords_sticky_and_availability():
    assert select_groups("Hi!", set(), AVAILABLE) == {CORE}
    assert select_groups("Remind me tomorrow at 9", set(), AVAILABLE) == {CORE, "reminders"}
    assert {"web", "github"} <= select_groups("search github for papers", set(), AVAILABLE)
    assert select_groups("yes", {"commitments"}, AVAILABLE) == {CORE, "commitments"}  # follow-ups keep their tools
    assert select_groups("remind me", set(), {CORE, "memory"}) == {CORE}  # a group that isn't configured is never selected


def test_recent_groups_reads_tool_calls_from_history():
    hist = [
        {"role": "user", "content": [{"type": "text", "text": "remind me"}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "1", "name": "create_reminder", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "{}"}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "2", "name": "create_task", "input": {}}]},
    ]
    assert recent_groups(hist) == {"reminders"}  # core is always on anyway
    assert recent_groups(hist, limit=1) == set()  # only the create_task call is in range
    assert recent_groups([{"role": "assistant", "content": "plain text"}]) == set()


def test_keyword_coverage_on_the_routing_dataset_and_heldout_phrasings():
    cases = [c for c in load_cases() if c["expect"]]
    missed = [c["id"] for c in cases if not any(group_of(t) in select_groups(c["prompt"], set(), AVAILABLE) for t in c["expect"])]
    assert len(missed) <= 2, missed
    held_missed = [p for p, g in HELDOUT if g not in select_groups(p, set(), AVAILABLE)]
    assert len(held_missed) <= 2, held_missed


@pytest.mark.parametrize("msg", ["Hi!", "Thanks, that's helpful", "Add a task: submit the OS assignment, due 15 Nov, about 2 hours", "Mark the report task as high priority", "Write me a haiku about deadlines"])
def test_plain_messages_load_only_the_core_group(msg):
    assert select_groups(msg, set(), AVAILABLE) == {CORE}


# ------------------------------------------------------------------ the agent loop
def agent_with(make_container, script, **settings):
    llm = ScriptedLLM(script)
    c = make_container(llm=llm, **settings)
    return c, llm


def names(call):
    return {t["name"] for t in call["tools"]}


def test_lean_profile_sends_only_what_the_message_needs(make_container):
    c, llm = agent_with(make_container, [text("ok"), text("ok")])
    with c.session_factory() as db:
        c.agent.chat(db, "add a task: read chapter 3")
        c.agent.chat(db, "remind me tomorrow at 9 to call mum", 1)
    first, second = llm.calls
    assert "create_task" in names(first) and "create_reminder" not in names(first) and LOAD_TOOLS in names(first)
    assert "More tools (not loaded yet)" in first["system"] and "reminders:" in first["system"]
    assert "create_reminder" in names(second) and "reminders:" not in second["system"]  # loaded now, so not listed as missing


def test_load_tools_makes_the_requested_group_available_on_the_next_step(make_container):
    c, llm = agent_with(make_container, [
        tool_call(LOAD_TOOLS, {"groups": ["reminders"]}, id="l"),
        tool_call("create_reminder", {"text": "Call mum", "remind_at": "2099-01-01T09:00"}, id="r"),
        text("Done."),
    ])
    with c.session_factory() as db:
        res = c.agent.chat(db, "hello there")  # nothing in this message selects the reminders group
    assert "create_reminder" not in names(llm.calls[0])
    assert "create_reminder" in names(llm.calls[1]) and "reminders:" not in llm.calls[1]["system"]
    assert [(t.name, t.status) for t in res.tool_calls] == [(LOAD_TOOLS, "ok"), ("create_reminder", "ok")]


def test_follow_ups_keep_the_tools_used_earlier_in_the_conversation(make_container):
    c, llm = agent_with(make_container, [
        tool_call("propose_commitment", {"text": "Finish the GRE set", "deadline": "2099-01-01"}, id="p"), text("Shall I save that?"),
        text("Saved."),
    ])
    with c.session_factory() as db:
        c.agent.chat(db, "I'll finish the GRE set by Friday")
        c.agent.chat(db, "yes", 1)  # no keyword, but commitments were just used
    assert "confirm_commitment" in names(llm.calls[-1])


def test_full_profile_sends_every_tool_and_no_catalog(make_container):
    c, llm = agent_with(make_container, [text("ok")], tool_profile="full")
    with c.session_factory() as db:
        c.agent.chat(db, "hi")
    assert LOAD_TOOLS not in names(llm.calls[0]) and "More tools" not in llm.calls[0]["system"]
    assert names(llm.calls[0]) == {t.name for t in c.registry.all()} - {LOAD_TOOLS}


def test_load_tools_rejects_unknown_groups_and_changes_nothing_else(make_container):
    c, _ = agent_with(make_container, [])
    with c.session_factory() as db:
        ctx = ToolContext(db=db, tz=c.agent.tz)
        bad = c.executor.run(ctx, LOAD_TOOLS, {"groups": ["admin"]})
        assert bad.status == "error" and "Valid groups" in bad.result["error"] and ctx.active_groups == set()
        ok = c.executor.run(ctx, LOAD_TOOLS, {"groups": ["memory"]})
        assert ok.status == "ok" and ctx.active_groups == {"memory"} and "save_memory" in ok.result["tools_now_available"]


def test_hiding_a_tool_never_weakens_enforcement(make_container):
    """The model can still *try* a tool that wasn't advertised; permissions, confirmation and taint apply regardless."""
    c, llm = agent_with(make_container, [
        tool_call("delete_memory", {"memory_id": 1}, id="d"), tool_call("create_reminder", {"text": "x", "remind_at": "2099-01-01T09:00"}, id="r"), text("ok"),
    ])
    from app.db.models import Memory
    with c.session_factory() as db:
        db.add(Memory(content="Prefers evenings.", kind="preference"))
        db.commit()
        res = c.agent.chat(db, "hello")
        assert "delete_memory" not in names(llm.calls[0])
        assert res.tool_calls[0].status == "pending_confirmation"  # HIGH_IMPACT is still gated
        assert db.query(Memory).count() == 1
    assert make_settings().tool_profile == "lean"  # lean is the default
