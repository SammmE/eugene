from __future__ import annotations
from typing import Any
import datetime
import uuid
from eugene.core import AppletBase, FieldSpec
from eugene.models import ToolDefinition
import caldav


class CalendarApplet(AppletBase):
    name = "calendar"
    description = "Syncs with CalDAV to list and add events."
    load = "lazy"
    inject = "selective"
    can_disable = True

    class Config:
        fields = {
            "caldav_url": FieldSpec(default="", description="CalDAV Server URL"),
            "caldav_user": FieldSpec(default="", description="CalDAV Username"),
            "caldav_password": FieldSpec(default="", description="CalDAV Password"),
            "preferred_calendar_name": FieldSpec(
                default="",
                description="Optional calendar name to use instead of the first returned calendar.",
            ),
        }

    async def on_load(self) -> None:
        self.logger.info("Calendar applet loaded.")

    def _get_client(self):
        url = self.config.get("caldav_url")
        user = self.config.get("caldav_user")
        password = self.config.get("caldav_password")
        if not url or not user or not password:
            raise ValueError("CalDAV credentials are not fully configured.")
        return caldav.DAVClient(url=url, username=user, password=password)

    def _get_principal(self, client: Any) -> Any:
        principal = client.principal
        return principal() if callable(principal) else principal

    def _get_calendars(self, principal: Any) -> list[Any]:
        getter = getattr(principal, "get_calendars", None)
        if callable(getter):
            return list(getter())

        fallback = getattr(principal, "calendars", None)
        if callable(fallback):
            return list(fallback())

        raise RuntimeError("CalDAV principal does not expose get_calendars().")

    def _pick_calendar(self, calendars: list[Any]) -> Any:
        preferred = str(self.config.get("preferred_calendar_name", "") or "").strip().lower()
        if preferred:
            for calendar in calendars:
                if str(getattr(calendar, "name", "") or "").strip().lower() == preferred:
                    return calendar
        return calendars[0]

    def _format_calendar_error(self, exc: Exception) -> str:
        message = str(exc).strip() or exc.__class__.__name__
        lowered = message.lower()
        if "401" in lowered or "unauthor" in lowered or "forbidden" in lowered:
            return (
                "Calendar connection was rejected by the server. Recheck the CalDAV URL, username, "
                "and password. For Google Calendar, use https://apidata.googleusercontent.com/caldav/v2/ "
                "and a Google App Password, not your normal account password."
            )
        return f"Error accessing calendar: {message}"

    def get_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="list_upcoming_events",
                description="List upcoming calendar events.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "days": {
                            "type": "integer",
                            "description": "Number of days ahead to check",
                            "default": 7,
                        }
                    },
                },
                applet_name=self.name,
            ),
            ToolDefinition(
                name="add_event",
                description="Add a new event to the calendar.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "start_time": {
                            "type": "string",
                            "description": "ISO 8601 formatted start time (e.g. 2026-04-03T15:00:00)",
                        },
                    },
                    "required": ["title", "start_time"],
                },
                applet_name=self.name,
            ),
        ]

    async def handle_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if name == "list_upcoming_events":
            days = arguments.get("days", 7)
            try:
                client = self._get_client()
                principal = self._get_principal(client)
                calendars = self._get_calendars(principal)
                if not calendars:
                    return "No calendars found."
                calendar = self._pick_calendar(calendars)

                start_date = datetime.datetime.now()
                end_date = start_date + datetime.timedelta(days=days)
                events = calendar.date_search(
                    start=start_date, end=end_date, expand=True
                )

                result = []
                for event in events:
                    if hasattr(event.vobject_instance, "vevent"):
                        summary = event.vobject_instance.vevent.summary.value
                        dtstart = event.vobject_instance.vevent.dtstart.value
                        result.append(f"{summary} at {dtstart}")
                return (
                    f"Upcoming events: {result}"
                    if result
                    else "No upcoming events found."
                )
            except ValueError as ve:
                return str(ve)
            except Exception as e:
                return self._format_calendar_error(e)

        elif name == "add_event":
            title = arguments.get("title")
            start_time_str = arguments.get("start_time")
            try:
                start_time = datetime.datetime.fromisoformat(start_time_str)
            except ValueError:
                return "Invalid datetime format. Please use ISO 8601 string."

            try:
                client = self._get_client()
                principal = self._get_principal(client)
                calendars = self._get_calendars(principal)
                if not calendars:
                    return "No calendars found."
                calendar = self._pick_calendar(calendars)

                # Basic vCalendar event creation
                now_str = datetime.datetime.now(datetime.timezone.utc).strftime(
                    "%Y%m%dT%H%M%SZ"
                )
                if start_time.tzinfo is None:
                    start_time = start_time.astimezone()
                start_utc = start_time.astimezone(datetime.timezone.utc)
                start_str = start_utc.strftime("%Y%m%dT%H%M%SZ")

                vcal = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Eugene//Calendar Applet//EN
BEGIN:VEVENT
UID:{uuid.uuid4()}
DTSTAMP:{now_str}
DTSTART:{start_str}
SUMMARY:{title}
END:VEVENT
END:VCALENDAR"""
                calendar.save_event(vcal)
                return f"Added event '{title}' at {start_time}"
            except ValueError as ve:
                return str(ve)
            except Exception as e:
                return self._format_calendar_error(e)

        raise ValueError(f"Unknown tool: {name}")
