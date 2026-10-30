"""Calendar through macOS Calendar.app (any account Calendar has, Google included).

Off the Mac, or when Calendar automation is refused, `create_event` writes an .ics file into
hq/os/calendar/ and says so, so nothing is lost.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from .base import Connector, spec

DEFAULT_CALENDAR = "Infinite Arch"


def _parse(value: str) -> tuple[datetime, bool]:
    """(datetime, all_day) from an ISO date or datetime."""
    text = str(value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return datetime.combine(date.fromisoformat(text), datetime.min.time()), True
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None), False
    except ValueError as exc:
        raise ValueError(f"Dates are ISO, like 2026-12-15 or 2026-12-15T18:00: got {text!r}") from exc


def _q(text: str) -> str:
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def applescript_date(var: str, moment: datetime) -> str:
    """Build an AppleScript date from components on a copy of the current date: locale proof."""
    return "\n".join([
        f"set {var} to current date",
        f"set day of {var} to 1",
        f"set year of {var} to {moment.year}",
        f"set month of {var} to {moment.month}",
        f"set day of {var} to {moment.day}",
        f"set hours of {var} to {moment.hour}",
        f"set minutes of {var} to {moment.minute}",
        f"set seconds of {var} to 0",
    ])


def create_script(title: str, start: datetime, end: datetime, all_day: bool, calendar: str, notes: str = "", url: str = "") -> str:
    props = [f"summary:{_q(title)}", "start date:startDate", "end date:endDate"]
    if all_day:
        props.append("allday event:true")
    if notes:
        props.append(f"description:{_q(notes)}")
    if url:
        props.append(f"url:{_q(url)}")
    day_start = datetime.combine(start.date(), datetime.min.time())
    return "\n".join([
        applescript_date("startDate", start),
        applescript_date("endDate", end),
        applescript_date("dayStart", day_start),
        "set dayEnd to dayStart + (1 * days)",
        'tell application "Calendar"',
        f"  if not (exists calendar {_q(calendar)}) then make new calendar with properties {{name:{_q(calendar)}}}",
        f"  set cal to calendar {_q(calendar)}",
        f"  set found to (every event of cal whose summary is {_q(title)} and start date is greater than or equal to dayStart and start date is less than dayEnd)",
        '  if (count of found) > 0 then return "exists"',
        f"  make new event at end of events of cal with properties {{{', '.join(props)}}}",
        '  return "created"',
        "end tell",
    ])


def events_script(start: datetime, end: datetime, calendar: str | None) -> str:
    target = f"{{calendar {_q(calendar)}}}" if calendar else "calendars"
    return "\n".join([
        applescript_date("rangeStart", start),
        applescript_date("rangeEnd", end),
        'set out to ""',
        'tell application "Calendar"',
        f"  repeat with cal in {target}",
        "    repeat with ev in (every event of cal whose start date is greater than or equal to rangeStart and start date is less than rangeEnd)",
        '      set out to out & (name of cal) & tab & (summary of ev) & tab & ((start date of ev) as «class isot» as string) & linefeed',
        "    end repeat",
        "  end repeat",
        "end tell",
        "return out",
    ])


def ics(title: str, start: datetime, end: datetime, all_day: bool, notes: str = "", url: str = "") -> str:
    uid = hashlib.sha1(f"{title}|{start.isoformat()}".encode("utf-8")).hexdigest() + "@theinfinitearch"
    def esc(text: str) -> str:
        return str(text).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
    if all_day:
        when = [f"DTSTART;VALUE=DATE:{start:%Y%m%d}", f"DTEND;VALUE=DATE:{(end if end.date() > start.date() else start + timedelta(days=1)):%Y%m%d}"]
    else:
        when = [f"DTSTART:{start:%Y%m%dT%H%M%S}", f"DTEND:{end:%Y%m%dT%H%M%S}"]
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//THE INFINITE ARCH//Agentic OS//EN", "BEGIN:VEVENT",
             f"UID:{uid}", f"DTSTAMP:{datetime.utcnow():%Y%m%dT%H%M%SZ}", *when, f"SUMMARY:{esc(title)}"]
    if notes:
        lines.append(f"DESCRIPTION:{esc(notes)}")
    if url:
        lines.append(f"URL:{url}")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"


class CalendarConnector(Connector):
    id = "calendar"
    name = "Calendar"
    description = "Your Mac's Calendar (Google and iCloud calendars included): read events, add deadlines and show dates."
    ACTIONS = {"create_event"}

    def _calendar(self, name: str | None = None) -> str:
        return str(name or self.config.get("name") or DEFAULT_CALENDAR)

    def status(self) -> dict:
        if sys.platform != "darwin":
            return {"ok": True, "needs": [], "detail": "Not on a Mac: new events are saved as .ics files in hq/os/calendar/ to import."}
        return {"ok": True, "needs": [], "detail": f"Calendar.app, calendar \"{self._calendar()}\". macOS asks once to let the OS control Calendar."}

    def tools(self) -> list[dict]:
        return [
            spec("list_calendars", "The calendars on this Mac."),
            spec("events", "Events between two ISO dates.", {"start": {"type": "string"}, "end": {"type": "string"}, "calendar": {"type": "string"}}, ["start", "end"]),
            spec("create_event", "Add an event (a deadline, a show, a pickup). ISO dates; a date alone makes an all-day event. Same title on the same day is not added twice.",
                 {"title": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"}, "all_day": {"type": "boolean"},
                  "calendar": {"type": "string"}, "notes": {"type": "string"}, "url": {"type": "string"}}, ["title", "start"]),
        ]

    runner = staticmethod(lambda script: subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=60))

    def _run(self, script: str) -> str | None:
        if sys.platform != "darwin":
            return None
        try:
            result = self.runner(script)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return result.stdout.strip() if result.returncode == 0 else None

    def tool_list_calendars(self):
        out = self._run('tell application "Calendar" to get name of every calendar')
        return out if out is not None else "Calendar is not reachable from here (not a Mac, or automation refused)."

    def tool_events(self, start: str, end: str, calendar: str | None = None):
        s, _ = _parse(start)
        e, _ = _parse(end)
        out = self._run(events_script(s, e, calendar))
        if out is None:
            return "Calendar is not reachable from here (not a Mac, or automation refused)."
        rows = [line.split("\t") for line in out.splitlines() if line.strip()]
        return [{"calendar": r[0], "title": r[1], "start": r[2] if len(r) > 2 else ""} for r in rows] or "No events."

    def tool_create_event(self, title: str, start: str, end: str | None = None, all_day: bool | None = None,
                          calendar: str | None = None, notes: str = "", url: str = ""):
        title = " ".join(str(title).replace("\u2014", ", ").split())[:200]
        s, date_only = _parse(start)
        all_day = date_only if all_day is None else bool(all_day)
        e = _parse(end)[0] if end else (s + timedelta(days=1) if all_day else s + timedelta(hours=1))
        cal = self._calendar(calendar)
        out = self._run(create_script(title, s, e, all_day, cal, notes, url))
        if out == "exists":
            return f"Already on {cal}: {title} ({s:%b %d})."
        if out == "created":
            return f"Added to {cal}: {title} ({s:%b %d}{'' if all_day else f', {s:%H:%M}'})."
        folder = self.domain / "hq" / "os" / "calendar"
        folder.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^a-z0-9]+", "-", f"{s:%Y-%m-%d}-{title}".lower()).strip("-")[:80] + ".ics"
        (folder / name).write_text(ics(title, s, e, all_day, notes, url), encoding="utf-8")
        return f"Calendar was not reachable, so I saved hq/os/calendar/{name} to import (double-click it on the Mac)."
