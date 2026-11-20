"""Outlook-published calendars (Outlook.com / Microsoft 365 "Publish calendar" -> ICS link).

Outlook writes Windows time-zone names ("Pakistan Standard Time") with its own VTIMEZONE block instead of IANA names
("Asia/Karachi"), and includes a weekly series with an exception. Times must come out the same as from Google.
"""
from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

from app.integrations.calendar import parse_events
from app.services.scheduling import busy_intervals

TZ = ZoneInfo("Asia/Karachi")

OUTLOOK_ICS = "\r\n".join([
    "BEGIN:VCALENDAR",
    "METHOD:PUBLISH",
    "PRODID:Microsoft Exchange Server 2010",
    "VERSION:2.0",
    "X-WR-CALNAME:Calendar",
    "BEGIN:VTIMEZONE",
    "TZID:Pakistan Standard Time",
    "BEGIN:STANDARD",
    "DTSTART:16010101T000000",
    "TZOFFSETFROM:+0500",
    "TZOFFSETTO:+0500",
    "END:STANDARD",
    "BEGIN:DAYLIGHT",
    "DTSTART:16010101T000000",
    "TZOFFSETFROM:+0500",
    "TZOFFSETTO:+0500",
    "END:DAYLIGHT",
    "END:VTIMEZONE",
    "BEGIN:VTIMEZONE",
    "TZID:GMT Standard Time",
    "BEGIN:STANDARD",
    "DTSTART:16010101T020000",
    "TZOFFSETFROM:+0100",
    "TZOFFSETTO:+0000",
    "RRULE:FREQ=YEARLY;INTERVAL=1;BYDAY=-1SU;BYMONTH=10",
    "END:STANDARD",
    "BEGIN:DAYLIGHT",
    "DTSTART:16010101T010000",
    "TZOFFSETFROM:+0000",
    "TZOFFSETTO:+0100",
    "RRULE:FREQ=YEARLY;INTERVAL=1;BYDAY=-1SU;BYMONTH=3",
    "END:DAYLIGHT",
    "END:VTIMEZONE",
    # a one-off meeting at 10:00 Karachi time
    "BEGIN:VEVENT",
    "UID:040000008200E00074C5B7101A82E00800000000A1B2C3D4",
    "SUMMARY:Supervisor meeting",
    "DTSTART;TZID=Pakistan Standard Time:20261118T100000",
    "DTEND;TZID=Pakistan Standard Time:20261118T110000",
    "END:VEVENT",
    # a call set up from London: 09:00 GMT is 14:00 in Karachi
    "BEGIN:VEVENT",
    "UID:040000008200E00074C5B7101A82E00800000000E5F6A7B8",
    "SUMMARY:Call with London",
    "DTSTART;TZID=GMT Standard Time:20261118T090000",
    "DTEND;TZID=GMT Standard Time:20261118T093000",
    "END:VEVENT",
    # a weekly lab on Wednesdays 16:00-17:00, one occurrence moved to 18:00
    "BEGIN:VEVENT",
    "UID:040000008200E00074C5B7101A82E0080000000011223344",
    "SUMMARY:Lab",
    "RRULE:FREQ=WEEKLY;BYDAY=WE;COUNT=10",
    "DTSTART;TZID=Pakistan Standard Time:20261111T160000",
    "DTEND;TZID=Pakistan Standard Time:20261111T170000",
    "END:VEVENT",
    "BEGIN:VEVENT",
    "UID:040000008200E00074C5B7101A82E0080000000011223344",
    "RECURRENCE-ID;TZID=Pakistan Standard Time:20261125T160000",
    "SUMMARY:Lab (moved)",
    "DTSTART;TZID=Pakistan Standard Time:20261125T180000",
    "DTEND;TZID=Pakistan Standard Time:20261125T190000",
    "END:VEVENT",
    # an all-day event (Outlook style: date values)
    "BEGIN:VEVENT",
    "UID:040000008200E00074C5B7101A82E00800000000AAAAAAAA",
    "SUMMARY:Holiday",
    "DTSTART;VALUE=DATE:20261118",
    "DTEND;VALUE=DATE:20261119",
    "END:VEVENT",
    "END:VCALENDAR",
]) + "\r\n"


def _times(day):
    return [(e["start"][11:16], e["end"][11:16], e["all_day"]) for e in parse_events(OUTLOOK_ICS, day, day, TZ)]


def test_windows_time_zone_names_give_the_right_local_times():
    assert _times(date(2026, 11, 18)) == [
        ("00:00", "00:00", True),     # the all-day holiday
        ("10:00", "11:00", False),    # Pakistan Standard Time = Asia/Karachi
        ("14:00", "14:30", False),    # 09:00 GMT
        ("16:00", "17:00", False),    # the weekly lab
    ]


def test_a_moved_occurrence_of_a_weekly_series_is_at_its_new_time():
    assert _times(date(2026, 11, 25)) == [("18:00", "19:00", False)]
    assert _times(date(2026, 12, 2)) == [("16:00", "17:00", False)]


def test_the_planner_sees_outlook_meetings_as_busy_time_and_ignores_all_day_events():
    busy = busy_intervals(lambda: OUTLOOK_ICS, date(2026, 11, 18), TZ)
    assert [(b.start.strftime("%H:%M"), b.end.strftime("%H:%M")) for b in busy] == [("10:00", "11:00"), ("14:00", "14:30"), ("16:00", "17:00")]


def test_outlook_event_ids_are_kept_for_linking_tasks():
    events = parse_events(OUTLOOK_ICS, date(2026, 11, 18), date(2026, 11, 18), TZ)
    assert {e["uid"] for e in events if not e["all_day"]} >= {"040000008200E00074C5B7101A82E00800000000A1B2C3D4"}
