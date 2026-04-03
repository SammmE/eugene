from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel

from eugene.core import AppletBase
from eugene.models import ToolDefinition



class Question(BaseModel):
    """A single question that can be asked to the user."""
    text: str
    choices: list[str] | None = None  # If set, UI shows multiple-choice buttons


class PromptRequest(BaseModel):
    questions: list[Question]


class PromptResponse(BaseModel):
    request_id: str
    answers: list[str]


class UserPromptApplet(AppletBase):
    name = "user_prompt"
    description = (
        "Prompt the user for answers to one or more questions. "
        "On web, shows an interactive step-by-step modal dialog with optional multiple-choice. "
        "On Discord/Telegram, sends the questions as a plain text message."
    )
    load = "eager"
    inject = "never"
    can_disable = True

    # Pending futures: request_id -> Future[list[str]]
    _pending: dict[str, asyncio.Future[list[str]]]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._pending = {}

    async def on_load(self) -> None:
        self.logger.info("UserPrompt applet loaded")

    # ── Tools ─────────────────────────────────────────────────────────────────

    def get_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="ask_user",
                description=(
                    "Ask the user one or more questions and wait for their answers. "
                    "Each question may optionally include a list of choices. "
                    "Returns a summary of what was asked and answered."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "questions": {
                            "type": "array",
                            "description": "List of questions to ask the user.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "text": {
                                        "type": "string",
                                        "description": "The question text.",
                                    },
                                    "choices": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "description": "Optional list of answer choices for multiple-choice.",
                                    },
                                },
                                "required": ["text"],
                            },
                        },
                    },
                    "required": ["questions"],
                },
                applet_name=self.name,
            )
        ]

    async def handle_tool(self, name: str, arguments: dict[str, Any]) -> str:
        if name != "ask_user":
            raise ValueError(f"Unknown tool: {name}")

        session_id: str = arguments.get("_runtime_session_id", "")
        channel: str = arguments.get("_runtime_source_channel", "web")
        raw_questions: list[dict[str, Any]] = arguments.get("questions", [])
        questions = [Question(**q) for q in raw_questions]

        if not questions:
            return "No questions were provided."

        if channel == "web":
            return await self._ask_web(session_id, questions)
        else:
            return await self._ask_text_channel(channel, session_id, questions)

    # ── Web channel: interactive modal ────────────────────────────────────────

    async def _ask_web(self, session_id: str, questions: list[Question]) -> str:
        import uuid

        request_id = str(uuid.uuid4())
        loop = asyncio.get_event_loop()
        future: asyncio.Future[list[str]] = loop.create_future()
        self._pending[request_id] = future

        # Send the prompt request to the frontend over WebSocket
        websocket = self.services.channels.web_sessions.get(session_id)
        if websocket is None:
            self._pending.pop(request_id, None)
            return "No active web session found; cannot prompt the user."

        payload = {
            "type": "user_prompt.request",
            "request_id": request_id,
            "questions": [
                {"text": q.text, "choices": q.choices or []}
                for q in questions
            ],
        }
        try:
            await websocket.send_json(payload)
        except Exception as exc:
            self._pending.pop(request_id, None)
            return f"Failed to send prompt to the web UI: {exc}"

        # Wait for the frontend to POST the answers (timeout: 5 minutes)
        try:
            answers: list[str] = await asyncio.wait_for(future, timeout=300)
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            return "User prompt timed out (5 minutes). No answers were received."
        finally:
            self._pending.pop(request_id, None)

        return self._format_qa(questions, answers)

    # ── Non-web channels: plain text ──────────────────────────────────────────

    async def _ask_text_channel(self, channel: str, session_id: str, questions: list[Question]) -> str:
        lines = ["I have a few questions for you:"]
        for i, q in enumerate(questions, 1):
            line = f"{i}. {q.text}"
            if q.choices:
                choices_str = " / ".join(q.choices)
                line += f"  (Options: {choices_str})"
            lines.append(line)
        lines.append("\nPlease reply with your answers, one per line.")
        message_text = "\n".join(lines)
        await self.services.channels.deliver(message_text, channel, session_id)
        return f"Questions sent to {channel}. Awaiting user reply."

    # ── REST routes ───────────────────────────────────────────────────────────

    def get_routes(self) -> list[tuple[str, APIRouter]]:
        router = APIRouter()
        applet = self

        @router.post("/respond")
        async def respond(
            body: PromptResponse,
            request: Request,
            x_api_key: Annotated[str | None, Header()] = None,
        ) -> dict[str, str]:
            """Called by the frontend when the user has answered all questions."""
            from fastapi import HTTPException as _HTTPException
            app_state = request.app.state.app_state
            if x_api_key != app_state.services.config.api_key:
                raise _HTTPException(status_code=401, detail="Invalid API key")
            future = applet._pending.get(body.request_id)
            if future is None:
                raise _HTTPException(status_code=404, detail="No pending prompt with that request_id.")
            if future.done():
                raise _HTTPException(status_code=409, detail="Prompt already answered.")
            future.set_result(body.answers)
            return {"status": "ok"}

        return [("", router)]


    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _format_qa(questions: list[Question], answers: list[str]) -> str:
        lines = []
        for i, q in enumerate(questions):
            answer = answers[i] if i < len(answers) else "(skipped)"
            lines.append(f"Q: {q.text}\nA: {answer}")
        return "\n\n".join(lines)
