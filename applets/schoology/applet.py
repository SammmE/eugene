from __future__ import annotations

from typing import Any

from eugene.core import AppletBase, FieldSpec
from eugene.models import ToolDefinition


class SchoologyApplet(AppletBase):
    name = "schoology"
    description = "Read Schoology feed and event data via schoolopy."
    load = "lazy"
    inject = "selective"
    can_disable = True

    class Config:
        fields = {
            "domain": FieldSpec(
                default="https://www.schoology.com",
                description="Base Schoology domain, for example https://schoology.schoolname.com.",
            ),
            "consumer_key": FieldSpec(
                default="",
                description="Schoology API consumer key. Prefer SCHOOLOGY_CONSUMER_KEY in .env.",
            ),
            "consumer_secret": FieldSpec(
                default="",
                description="Schoology API consumer secret. Prefer SCHOOLOGY_CONSUMER_SECRET in .env.",
            ),
            "default_limit": FieldSpec(
                default=10,
                description="Default maximum number of items returned by Schoology list tools.",
            ),
        }

    async def on_load(self) -> None:
        self.logger.info("Schoology applet loaded")

    def get_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="get_schoology_me",
                description="Fetch the authenticated Schoology user profile.",
                input_schema={"type": "object", "properties": {}},
                applet_name=self.name,
            ),
            ToolDefinition(
                name="get_schoology_feed",
                description="Fetch the authenticated user's Schoology feed entries using two-legged API auth.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Maximum feed items to return.",
                        }
                    },
                },
                applet_name=self.name,
            ),
            ToolDefinition(
                name="get_schoology_sections",
                description="Fetch Schoology sections for the authenticated user or for a specific user ID.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "user_id": {
                            "type": "integer",
                            "description": "Optional Schoology user ID. Defaults to the authenticated user.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum sections to return.",
                        },
                    },
                },
                applet_name=self.name,
            ),
            ToolDefinition(
                name="get_schoology_assignments",
                description="Fetch assignments for a Schoology section.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "section_id": {
                            "type": "integer",
                            "description": "The Schoology section ID.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum assignments to return.",
                        },
                    },
                    "required": ["section_id"],
                },
                applet_name=self.name,
            ),
            ToolDefinition(
                name="get_schoology_events",
                description="Fetch Schoology events, optionally scoped to a district, school, user, section, or group.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "realm": {
                            "type": "string",
                            "enum": ["district", "school", "user", "section", "group"],
                            "description": "Optional realm type for a scoped event lookup.",
                        },
                        "realm_id": {
                            "type": "integer",
                            "description": "Realm ID required when realm is provided.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum events to return.",
                        },
                    },
                },
                applet_name=self.name,
            ),
            ToolDefinition(
                name="get_schoology_event",
                description="Fetch a specific Schoology event by event ID, optionally scoped to a realm.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "event_id": {
                            "type": "integer",
                            "description": "The Schoology event ID.",
                        },
                        "realm": {
                            "type": "string",
                            "enum": ["district", "school", "user", "section", "group"],
                            "description": "Optional realm type for a scoped event lookup.",
                        },
                        "realm_id": {
                            "type": "integer",
                            "description": "Realm ID required when realm is provided.",
                        },
                    },
                    "required": ["event_id"],
                },
                applet_name=self.name,
            ),
            ToolDefinition(
                name="create_schoology_event",
                description="Create a Schoology event. Provide an event object and optionally scope it to a district, school, user, section, or group.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "event": {
                            "type": "object",
                            "description": "Raw Schoology event payload fields accepted by schoolopy.Event.",
                        },
                        "realm": {
                            "type": "string",
                            "enum": ["district", "school", "user", "section", "group"],
                            "description": "Optional realm type for a scoped event creation.",
                        },
                        "realm_id": {
                            "type": "integer",
                            "description": "Realm ID required when realm is provided.",
                        },
                    },
                    "required": ["event"],
                },
                applet_name=self.name,
            ),
        ]

    async def handle_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if name == "get_schoology_me":
            return self._normalize_item(self._get_client().get_me())
        if name == "get_schoology_feed":
            return self._get_feed(arguments)
        if name == "get_schoology_sections":
            return self._get_sections(arguments)
        if name == "get_schoology_assignments":
            return self._get_assignments(arguments)
        if name == "get_schoology_events":
            return self._get_events(arguments)
        if name == "get_schoology_event":
            return self._get_event(arguments)
        if name == "create_schoology_event":
            return self._create_event(arguments)
        raise ValueError(f"Unknown tool: {name}")

    def _get_feed(self, arguments: dict[str, Any]) -> Any:
        client = self._get_client()
        limit = self._limit(arguments.get("limit"))
        feed = client.get_feed()
        return self._normalize_collection(feed, limit)

    def _get_sections(self, arguments: dict[str, Any]) -> Any:
        client = self._get_client()
        limit = self._limit(arguments.get("limit"))
        user_id = arguments.get("user_id")
        sections = client.get_sections(user_id=int(user_id)) if user_id is not None else client.get_sections()
        return self._normalize_collection(sections, limit)

    def _get_assignments(self, arguments: dict[str, Any]) -> Any:
        client = self._get_client()
        section_id = int(arguments["section_id"])
        limit = self._limit(arguments.get("limit"))
        assignments = client.get_assignments(section_id)
        return self._normalize_collection(assignments, limit)

    def _get_events(self, arguments: dict[str, Any]) -> Any:
        client = self._get_client()
        scope = self._scope_kwargs(arguments)
        limit = self._limit(arguments.get("limit"))
        events = client.get_events(**scope)
        return self._normalize_collection(events, limit)

    def _get_event(self, arguments: dict[str, Any]) -> Any:
        client = self._get_client()
        event_id = int(arguments["event_id"])
        scope = self._scope_kwargs(arguments)
        event = client.get_event(event_id, **scope)
        return self._normalize_item(event)

    def _create_event(self, arguments: dict[str, Any]) -> Any:
        client = self._get_client()
        scope = self._scope_kwargs(arguments)

        try:
            import schoolopy  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "The schoolopy package is not installed. Add it to the environment before using the Schoology applet."
            ) from exc

        raw_event = arguments["event"]
        if not isinstance(raw_event, dict):
            raise RuntimeError("event must be a JSON object.")
        event = schoolopy.Event(raw_event)
        created = client.create_event(event, **scope)
        return self._normalize_item(created)

    def _get_client(self) -> Any:
        try:
            import schoolopy  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "The schoolopy package is not installed. Add it to the environment before using the Schoology applet."
            ) from exc

        key = str(self.config.get("consumer_key", "")).strip()
        secret = str(self.config.get("consumer_secret", "")).strip()
        domain = str(self.config.get("domain", "https://www.schoology.com")).strip()
        if not key or not secret:
            raise RuntimeError(
                "Schoology credentials are not configured. Set SCHOOLOGY_CONSUMER_KEY and SCHOOLOGY_CONSUMER_SECRET in .env or configure the applet."
            )

        auth_kwargs: dict[str, Any] = {}
        if domain:
            auth_kwargs["domain"] = domain
        auth = schoolopy.Auth(key, secret, **auth_kwargs)
        return schoolopy.Schoology(auth)

    def _scope_kwargs(self, arguments: dict[str, Any]) -> dict[str, int]:
        realm = arguments.get("realm")
        if realm is None:
            return {}

        realm_id_raw = arguments.get("realm_id")
        if realm_id_raw is None:
            raise RuntimeError("realm_id is required when realm is provided.")
        realm_name = str(realm).strip()
        if realm_name not in {"district", "school", "user", "section", "group"}:
            raise RuntimeError(f"Unsupported realm: {realm_name}")
        return {f"{realm_name}_id": int(realm_id_raw)}

    def _limit(self, requested: Any) -> int:
        fallback = int(self.config.get("default_limit", 10) or 10)
        value = fallback if requested in (None, "") else int(requested)
        return max(1, min(value, 100))

    def _normalize_collection(self, items: Any, limit: int) -> list[Any]:
        if items is None:
            return []
        if isinstance(items, list):
            return [self._normalize_item(item) for item in items[:limit]]

        normalized: list[Any] = []
        for index, item in enumerate(items):
            if index >= limit:
                break
            normalized.append(self._normalize_item(item))
        return normalized

    def _normalize_item(self, item: Any) -> Any:
        if item is None:
            return None
        if isinstance(item, (str, int, float, bool)):
            return item
        if isinstance(item, dict):
            return {str(key): self._normalize_item(value) for key, value in item.items()}
        if isinstance(item, (list, tuple, set)):
            return [self._normalize_item(value) for value in item]

        if hasattr(item, "__dict__"):
            result: dict[str, Any] = {}
            for key, value in vars(item).items():
                if key.startswith("_"):
                    continue
                result[str(key)] = self._normalize_item(value)
            if result:
                return result

        return repr(item)
