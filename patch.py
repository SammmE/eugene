import sys

path = "c:/Users/sam/Code/eugene/src/eugene/services.py"
with open(path, "r", encoding="utf-8") as f:
    text = f.read()

target = """    def __init__(self, services: ServiceContainer) -> None:
        self.services = services
        self.logger = logger.bind(component="core")
        services.event_bus.subscribe("message.received", self._handle_message_event)
        services.event_bus.subscribe("message.response", self._handle_response_event)"""

replacement = """    def __init__(self, services: ServiceContainer) -> None:
        self.services = services
        self.logger = logger.bind(component="core")
        services.event_bus.subscribe("message.received", self._handle_message_event)
        services.event_bus.subscribe("message.response", self._handle_response_event)
        services.event_bus.subscribe("message.delta", self._handle_stream_event)
        services.event_bus.subscribe("message.tool_call", self._handle_stream_event)
        services.event_bus.subscribe("message.tool_call_delta", self._handle_stream_event)

    async def _handle_stream_event(self, event) -> None:
        channel_name = event.payload.get("channel", "web")
        session_id = event.payload.get("session_id")
        if not session_id:
            return
        
        if channel_name == "web":
            websocket = self.services.channels.web_sessions.get(session_id)
            if websocket:
                payload = dict(event.payload)
                payload["type"] = event.event_type
                try:
                    await websocket.send_json(payload)
                except Exception:
                    pass"""

if target in text:
    text = text.replace(target, replacement)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print("Patched")
else:
    print("Not found")

