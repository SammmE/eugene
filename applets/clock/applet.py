from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from eugene.core import AppletBase, FieldSpec


class ClockApplet(AppletBase):
    name = "clock"
    description = "Current time and date context."
    load = "eager"
    inject = "always"
    can_disable = False

    class Config:
        fields = {
            "timezone": FieldSpec(default="America/Detroit", description="Canonical system timezone."),
            "format": FieldSpec(default="12hr", description="Hour format.", options=["12hr", "24hr"]),
        }

    def get_context_injection(self) -> str:
        now = datetime.now(ZoneInfo(self.config["timezone"]))
        fmt = "%Y-%m-%d %I:%M %p %Z" if self.config["format"] == "12hr" else "%Y-%m-%d %H:%M %Z"
        return f"Current time: {now.strftime(fmt)}"
