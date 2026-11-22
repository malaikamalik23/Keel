"""The dashboard in real Chromium. These catch what jsdom can't: browser-specific behaviour (for example, current
Chrome's scrollIntoView() returns a Promise, which crashed the Chat view when an effect returned it)."""
import re

from tests.fakes import text, tool_call

SCRIPT = [
    tool_call("create_task", {"title": "Write FYP introduction", "estimated_minutes": 90}, id="a"), text("Task added."),
    tool_call("list_tasks", {}, id="b"), tool_call("create_daily_plan", {"capacity_minutes": 240}, id="c"),
    text("**Today's plan**\n\n| Item | Time |\n|---|---|\n| Write FYP introduction | 90 min |"),
    tool_call("delete_task", {"task_id": 1}, id="d"), text("Queued: waiting for your approval."),
    tool_call("create_reminder", {"text": "Email my supervisor", "remind_at": "2099-01-01T09:00"}, id="e"), text("Reminder set."),
]


def say(page, message, expect):
    page.get_by_label("Message").fill(message)
    page.get_by_role("button", name="Send").click()
    page.get_by_text(expect).wait_for(timeout=15000)


def _skip_first_run_setup(page) -> None:
    """A fresh database opens the welcome questions first, as for a real new user; skip them like one would."""
    page.get_by_role("heading", name="Welcome to Keel").wait_for(timeout=10000)
    page.get_by_role("button", name="Skip setup for now").click()


def test_full_user_flow_in_a_real_browser_without_any_console_errors(browser, serve):
    url, _ = serve(SCRIPT, agent_timezone="Asia/Karachi")
    ctx = browser.new_context(timezone_id="Asia/Karachi", viewport={"width": 1280, "height": 800})
    page = ctx.new_page()
    problems: list[str] = []
    page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
    page.on("console", lambda m: problems.append(f"console.{m.type}: {m.text}") if m.type in ("error", "warning") else None)
    page.on("requestfailed", lambda r: problems.append(f"request failed: {r.url}"))

    page.goto(url)
    _skip_first_run_setup(page)
    page.wait_for_selector("nav")

    # every tab renders (no blank page, no crash)
    for tab in ["Goals", "Projects", "Commitments", "Memory", "Chat", "Insights", "Activity", "Approvals", "Today"]:
        page.get_by_role("button", name=re.compile(f"^{tab}")).click()
        assert page.locator("nav").is_visible(), f"{tab} blanked the page"

    # chat: the page must survive the reply, and the conversation must stay on screen
    page.get_by_role("button", name="Chat").click()
    say(page, "Add a task: write the FYP introduction, about 90 minutes", "Task added.")
    assert page.locator("nav").is_visible()
    assert page.get_by_text("Add a task: write the FYP introduction, about 90 minutes").is_visible()
    page.get_by_role("button", name="Today").click()
    page.get_by_text("Write FYP introduction").first.wait_for()
    page.get_by_role("button", name="Chat").click()
    assert page.get_by_text("Task added.").is_visible(), "conversation was lost when switching tabs"

    say(page, "Plan my day, I have 4 hours", "Write FYP introduction | 90 min")
    page.get_by_role("button", name="Today").click()
    page.get_by_text("90 / 204 min").wait_for()

    # delete: queued, with a visible approval box and a badge; nothing deleted until approval
    page.get_by_role("button", name="Chat").click()
    say(page, "Delete the FYP introduction task", "Queued: waiting for your approval.")
    page.get_by_text("needs your approval").wait_for()
    # The badge count comes from a separate request fired after the reply, so wait for it rather than reading it once
    # (reading immediately raced on CI). It still fails if the badge never shows 1.
    page.get_by_role("button", name=re.compile(r"^Approvals\s*1$")).wait_for()
    page.get_by_role("button", name=re.compile("^Approvals")).click()
    page.get_by_role("button", name="Approve").click()
    page.get_by_text("Nothing pending").wait_for()
    page.get_by_role("button", name="Today").click()
    page.get_by_text("No open tasks.").wait_for()
    page.get_by_text(re.compile("Nothing is planned for today")).wait_for()  # the plan item went with the task (no ghost entry)
    assert not page.locator("main:not([hidden])").get_by_text("Write FYP introduction").count()  # (the hidden Chat pane keeps its transcript)

    # reminder: 09:00 Karachi is shown as 9:00 in a Karachi browser
    page.get_by_role("button", name="Chat").click()
    say(page, "Remind me tomorrow at 9am to email my supervisor", "Reminder set.")
    page.get_by_role("button", name="Activity").click()
    visible = page.locator("main:not([hidden])")
    visible.get_by_text("Email my supervisor").wait_for()
    assert re.search(r"\b0?9:00 AM", visible.inner_text()), visible.inner_text()

    assert problems == [], problems
    ctx.close()


def test_dashboard_shows_a_message_instead_of_a_blank_page_when_a_view_crashes(browser, serve):
    url, _ = serve([])
    page = browser.new_page()
    # make the API return the wrong shape for one endpoint, as a buggy server might
    page.route("**/api/accountability*", lambda route: route.fulfill(status=200, content_type="application/json", body="[]"))
    page.goto(url)
    _skip_first_run_setup(page)
    page.get_by_text("Something went wrong in this view").wait_for(timeout=10000)
    page.get_by_role("button", name="Goals").click()  # navigation still works
    page.get_by_text(re.compile("No active goals")).wait_for()
    page.close()
