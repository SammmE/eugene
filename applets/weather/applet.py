from __future__ import annotations
from typing import Any
import requests
from eugene.core import AppletBase, FieldSpec
from eugene.models import ToolDefinition

class WeatherApplet(AppletBase):
    name = "weather"
    description = "Provides current local weather forecast. Injects selectively into context."
    load = "lazy"
    inject = "selective"
    can_disable = True

    class Config:
        fields = {
            "default_latitude": FieldSpec(default="40.7128", description="Default Latitude (e.g. 40.7128 for NY)"),
            "default_longitude": FieldSpec(default="-74.0060", description="Default Longitude (e.g. -74.0060 for NY)"),
        }

    async def on_load(self) -> None:
        self.logger.info("Weather applet loaded")

    def get_context_injection(self) -> str:
        lat = self.config.get("default_latitude", "40.7128")
        lon = self.config.get("default_longitude", "-74.0060")
        try:
            url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true"
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                current = data.get("current_weather", {})
                temp = current.get("temperature")
                wind = current.get("windspeed")
                return f"[Context injection successful -> Weather: The current local temperature at lat {lat}, lon {lon} is {temp}°C with {wind}km/h wind.]"
        except Exception as e:
            self.logger.warning(f"Failed to fetch weather for context injection: {e}")
        return "[Weather: Location weather unavailable.]"

    def get_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="get_current_weather",
                description="Get the current weather for a specific location using latitude and longitude.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "latitude": {"type": "number"},
                        "longitude": {"type": "number"}
                    },
                    "required": ["latitude", "longitude"]
                },
                applet_name=self.name,
            )
        ]

    async def handle_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if name == "get_current_weather":
            lat = arguments.get("latitude")
            lon = arguments.get("longitude")
            try:
                url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true"
                resp = requests.get(url, timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("current_weather", "No current weather data available.")
                return f"API returned {resp.status_code}"
            except Exception as e:
                return f"Error fetching weather: {e}"
        raise ValueError(f"Unknown tool: {name}")
