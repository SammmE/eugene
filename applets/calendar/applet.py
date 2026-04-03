from __future__ import annotations
from typing import Any
import datetime
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
                principal = client.principal()
                calendars = principal.calendars()
                if not calendars:
                    return "No calendars found."
                calendar = calendars[0]  # Use first calendar

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
                return f"Error accessing calendar: {e}"

        elif name == "add_event":
            title = arguments.get("title")
            start_time_str = arguments.get("start_time")
            try:
                start_time = datetime.datetime.fromisoformat(start_time_str)
            except ValueError:
                return "Invalid datetime format. Please use ISO 8601 string."

            try:
                client = self._get_client()
                principal = client.principal()
                calendars = principal.calendars()
                if not calendars:
                    return "No calendars found."
                calendar = calendars[0]

                # Basic vCalendar event creation
                now_str = datetime.datetime.now(datetime.timezone.utc).strftime(
                    "%Y%m%dT%H%M%SZ"
                )
                start_str = start_time.strftime("%Y%m%dT%H%M%SZ")

                vcal = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Eugene//Calendar Applet//EN
BEGIN:VEVENT
UID:{datetime.datetime.now().timestamp()}
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
                return f"Error adding event: {e}"

        raise ValueError(f"Unknown tool: {name}")
