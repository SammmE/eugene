from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import signal
import sys
import time
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

import websockets
from rich.align import Align
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from eugene.config import DATA_DIR, ROOT_DIR

if os.name == "nt":  # pragma: no cover
    import msvcrt
else:  # pragma: no cover
    import select
    import termios
    import tty


DEFAULT_WS_BASE = "ws://127.0.0.1:8000"
DEFAULT_SESSION_ID = "termagotchi-terminal"
SAVE_INTERVAL = 10.0
FRAME_INTERVAL = 0.15


@dataclass
class Animation:
    fps: int
    loop: bool
    frames: list[str]


@dataclass
class PetState:
    name: str = "Eugene"
    hunger: float = 100.0
    happiness: float = 100.0
    energy: float = 100.0
    birth_time: float = field(default_factory=time.time)
    last_updated: float = field(default_factory=time.time)
    transient_state: str | None = None
    transient_until: float = 0.0
    idle_message: str | None = None
    idle_message_until: float = 0.0

    @classmethod
    def load(cls, path: Path, name: str) -> "PetState":
        if not path.exists():
            return cls(name=name)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls(name=name)
        payload["name"] = payload.get("name") or name
        state = cls(**{key: value for key, value in payload.items() if key in cls.__dataclass_fields__})
        state.apply_decay()
        return state

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    def apply_decay(self) -> None:
        now = time.time()
        elapsed = max(now - self.last_updated, 0.0)
        if elapsed <= 0:
            return
        hunger_rate = 0.35 * (1.4 if self.hunger < 15 else 1.0)
        happiness_rate = 0.22 * (1.6 if self.hunger < 30 else 1.0) * (0.8 if self.energy < 20 else 1.0)
        energy_rate = 0.14 * (0.8 if self.hunger > 70 and self.happiness > 70 else 1.0)
        self.hunger = max(0.0, self.hunger - elapsed * hunger_rate)
        self.happiness = max(0.0, self.happiness - elapsed * happiness_rate)
        self.energy = max(0.0, self.energy - elapsed * energy_rate)
        self.last_updated = now
        if now >= self.transient_until:
            self.transient_state = None
        if self.idle_message and now >= self.idle_message_until:
            self.idle_message = None

    def current_animation(self) -> str:
        if self.is_dead():
            return "dead"
        if self.transient_state:
            return self.transient_state
        if self.energy <= 10:
            return "sleeping"
        if self.happiness <= 20 or self.hunger <= 15:
            return "sad"
        return "idle"

    def feed(self) -> str:
        if self.is_dead():
            return f"{self.name} can only be remembered now."
        self.hunger = min(100.0, self.hunger + 20.0)
        self.happiness = min(100.0, self.happiness + 5.0)
        self._set_transient("eating", 2.0)
        return f"{self.name} crunches happily."

    def play(self) -> str:
        if self.is_dead():
            return f"{self.name} cannot play."
        if self.energy <= 10:
            return f"{self.name} is too tired to play. Try /sleep."
        self.happiness = min(100.0, self.happiness + 15.0)
        self.energy = max(0.0, self.energy - 10.0)
        self.hunger = max(0.0, self.hunger - 8.0)
        self._set_transient("playing", 2.0)
        return f"{self.name} zooms around the terminal."

    def sleep(self) -> str:
        if self.is_dead():
            return f"{self.name} cannot sleep anymore."
        self.energy = min(100.0, self.energy + 30.0)
        self._set_transient("sleeping", 3.0)
        return f"{self.name} curls up and recharges."

    def rename(self, name: str) -> str:
        clean = name.strip()
        if not clean:
            return "Usage: /name Eugene"
        old = self.name
        self.name = clean
        return f"{old} is now {self.name}."

    def maybe_idle_event(self) -> None:
        now = time.time()
        if self.current_animation() != "idle" or self.idle_message is not None:
            return
        if int(now * 10) % 53 != 0:
            return
        messages = [
            "*stretches and studies the prompt*",
            "*looks ready to help with the next task*",
            "*pads across the terminal and waits*",
            "*purrs like a debugging companion*",
        ]
        self.idle_message = messages[int(now) % len(messages)]
        self.idle_message_until = now + 2.5

    def age_string(self) -> str:
        seconds = max(int(time.time() - self.birth_time), 0)
        days, rem = divmod(seconds, 86400)
        hours, rem = divmod(rem, 3600)
        minutes = rem // 60
        if days:
            return f"{days}d {hours}h"
        if hours:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"

    def is_dead(self) -> bool:
        return self.hunger <= 0 and self.happiness <= 0 and self.energy <= 0

    def _set_transient(self, state: str, seconds: float) -> None:
        self.transient_state = state
        self.transient_until = time.time() + seconds
        self.last_updated = time.time()


class TerminalIO:
    def __init__(self) -> None:
        self._stdin_fd = sys.stdin.fileno() if hasattr(sys.stdin, "fileno") else None
        self._term_settings: list[Any] | None = None

    def enter(self) -> None:
        if os.name != "nt" and self._stdin_fd is not None:  # pragma: no cover
            self._term_settings = termios.tcgetattr(self._stdin_fd)
            tty.setcbreak(self._stdin_fd)

    def exit(self) -> None:
        if os.name != "nt" and self._stdin_fd is not None and self._term_settings is not None:  # pragma: no cover
            termios.tcsetattr(self._stdin_fd, termios.TCSADRAIN, self._term_settings)

    def read_keys(self) -> list[str]:
        if os.name == "nt":  # pragma: no cover
            keys: list[str] = []
            while msvcrt.kbhit():
                char = msvcrt.getwch()
                if char in ("\x00", "\xe0"):
                    if msvcrt.kbhit():
                        msvcrt.getwch()
                    continue
                keys.append(char)
            return keys
        if self._stdin_fd is None:  # pragma: no cover
            return []
        ready, _, _ = select.select([self._stdin_fd], [], [], 0)
        if not ready:
            return []
        return list(os.read(self._stdin_fd, 64).decode("utf-8", errors="ignore"))


class TermagotchiClient:
    def __init__(self, api_key: str, ws_base: str, session_id: str, pet_name: str, save_path: Path, animation_dir: Path) -> None:
        self.api_key = api_key
        self.ws_base = ws_base.rstrip("/")
        self.session_id = session_id
        self.pet = PetState.load(save_path, pet_name)
        self.save_path = save_path
        self.animation_dir = animation_dir
        self.terminal = TerminalIO()
        self.console = Console()
        self.live: Live | None = None
        self.animations = self._load_animations()
        self._history: list[tuple[str, str]] = [("system", "Separate Termagotchi client online. Normal text chats with Eugene.")]
        self._input_buffer = ""
        self._show_help = False
        self._connected = False
        self._thinking = False
        self._streaming_reply = ""
        self._stop = asyncio.Event()
        self._connected_event = asyncio.Event()
        self._websocket: Any | None = None
        self._last_frame_at = 0.0
        self._last_save_at = 0.0

    async def run(self) -> None:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise RuntimeError("Termagotchi client requires an interactive terminal")

        loop = asyncio.get_running_loop()
        for sig in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
            if sig is None:
                continue
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, self._stop.set)

        self.terminal.enter()
        self.live = Live(self._build_renderable(), console=self.console, screen=True, auto_refresh=False)
        self.live.start()
        try:
            await asyncio.gather(
                self._render_loop(),
                self._input_loop(),
                self._socket_loop(),
            )
        finally:
            self.pet.save(self.save_path)
            if self.live is not None:
                self.live.stop()
            self.terminal.exit()

    async def _socket_loop(self) -> None:
        url = f"{self.ws_base}/ws/{self.session_id}?api_key={quote(self.api_key)}"
        retry_delay = 1.0
        while not self._stop.is_set():
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as websocket:
                    self._websocket = websocket
                    self._connected = True
                    self._connected_event.set()
                    retry_delay = 1.0
                    self._push_history("system", f"Connected to Eugene at {self.ws_base}.")
                    async for raw in websocket:
                        payload = json.loads(raw)
                        self._handle_server_event(payload)
                        if self._stop.is_set():
                            break
            except Exception as exc:
                self._connected = False
                self._connected_event.clear()
                self._websocket = None
                self._thinking = False
                self._push_history("system", f"Connection lost: {exc}")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 1.8, 8.0)

    async def _input_loop(self) -> None:
        while not self._stop.is_set():
            for key in self.terminal.read_keys():
                await self._handle_key(key)
            await asyncio.sleep(0.05)

    async def _render_loop(self) -> None:
        while not self._stop.is_set():
            self.pet.apply_decay()
            self.pet.maybe_idle_event()
            self._render()
            if time.time() - self._last_save_at >= SAVE_INTERVAL:
                self.pet.save(self.save_path)
                self._last_save_at = time.time()
            await asyncio.sleep(FRAME_INTERVAL)

    async def _handle_key(self, key: str) -> None:
        if key == "\x03":
            self._stop.set()
            return
        if key in ("\r", "\n"):
            line = self._input_buffer.strip()
            self._input_buffer = ""
            if line:
                await self._handle_line(line)
            return
        if key in ("\x08", "\x7f"):
            self._input_buffer = self._input_buffer[:-1]
            return
        if key == "\t":
            self._input_buffer += "    "
            return
        if key.isprintable():
            self._input_buffer += key

    async def _handle_line(self, line: str) -> None:
        if line.startswith("/"):
            self._run_local_command(line)
            return
        self._push_history("user", line)
        self._thinking = True
        self._streaming_reply = ""
        await self._send({"text": line, "attachments": []})

    async def _send(self, payload: dict[str, Any]) -> None:
        if not self._connected:
            try:
                await asyncio.wait_for(self._connected_event.wait(), timeout=10)
            except asyncio.TimeoutError:
                self._thinking = False
                self._push_history("system", "Not connected to Eugene yet.")
                return
        if self._websocket is None:
            self._thinking = False
            self._push_history("system", "Connection unavailable.")
            return
        try:
            await self._websocket.send(json.dumps(payload))
        except Exception as exc:
            self._thinking = False
            self._push_history("system", f"Send failed: {exc}")

    def _handle_server_event(self, payload: dict[str, Any]) -> None:
        event_type = payload.get("type", "message.response")
        if event_type == "message.delta":
            delta = str(payload.get("delta", ""))
            if delta:
                self._streaming_reply += delta
            return
        if event_type == "message.response":
            text = str(payload.get("text", "")).strip()
            final = text or self._streaming_reply or "(empty response)"
            self._push_history("assistant", final)
            self._thinking = False
            self._streaming_reply = ""

    def _run_local_command(self, line: str) -> None:
        command, _, argument = line.partition(" ")
        command = command.lower()
        if command == "/feed":
            self._push_history("system", self.pet.feed())
            return
        if command == "/play":
            self._push_history("system", self.pet.play())
            return
        if command == "/sleep":
            self._push_history("system", self.pet.sleep())
            return
        if command == "/name":
            self._push_history("system", self.pet.rename(argument))
            return
        if command == "/clear":
            self._history = [("system", "Transcript cleared. Eugene conversation history still lives on the server.")]
            return
        if command == "/help":
            self._show_help = not self._show_help
            return
        if command == "/quit":
            self._stop.set()
            return
        self._push_history("system", f"Unknown command '{command}'. Try /help.")

    def _render(self) -> None:
        now = time.time()
        if now - self._last_frame_at < FRAME_INTERVAL / 2:
            return
        self._last_frame_at = now
        if self.live is not None:
            self.live.update(self._build_renderable(), refresh=True)

    def _build_renderable(self) -> Layout:
        layout = Layout()
        layout.split_column(
            Layout(self._header_panel(), name="header", size=5),
            Layout(name="body"),
            Layout(self._footer_panel(), name="footer", size=5),
        )
        layout["body"].split_row(
            Layout(self._pet_panel(), name="pet", ratio=2),
            Layout(self._conversation_panel(), name="conversation", ratio=3),
        )
        return layout

    def _header_panel(self) -> Panel:
        table = Table.grid(expand=True)
        table.add_column(justify="left")
        table.add_column(justify="right")
        status = "[bold green]connected[/]" if self._connected else "[bold red]offline[/]"
        table.add_row(
            "[bold cyan]TERMAGOTCHI CLIENT[/]  [dim]separate terminal for Eugene[/]",
            f"Status: {status}",
        )
        table.add_row(
            f"Session: [bold]{self.session_id}[/]  Name: [bold magenta]{self.pet.name}[/]",
            f"Age: [bold]{self.pet.age_string()}[/]",
        )
        return Panel(table, border_style="cyan")

    def _pet_panel(self) -> Panel:
        if self._show_help:
            body = Group(*[Text(line, style="white") for line in self._help_lines()])
            return Panel(body, title="Help", border_style="yellow")

        status = Table.grid(expand=True)
        status.add_column()
        status.add_column()
        status.add_row(
            f"[bold]State[/]: {self.pet.current_animation()}",
            f"[bold]Mood[/]: {self._mood_label()}",
        )
        status.add_row(
            f"[green]Hunger[/] {self._bar(self.pet.hunger)}",
            f"[magenta]Happy[/] {self._bar(self.pet.happiness)}",
        )
        status.add_row(
            f"[cyan]Energy[/] {self._bar(self.pet.energy)}",
            f"[bold]Focus[/]: {'thinking' if self._thinking else 'idle'}",
        )

        sprite = Text(self._current_frame(), style="bold white")
        parts: list[Any] = [status, Rule(style="grey50"), Align.center(sprite)]
        if self.pet.idle_message:
            parts.extend([Rule(style="grey35"), Align.center(Text(self.pet.idle_message, style="italic yellow"))])
        if self._thinking:
            thought = self._streaming_reply.strip() or f"{self.pet.name} is waiting on Eugene..."
            parts.extend([Rule(style="grey35"), Text(thought, style="cyan")])
        return Panel(Group(*parts), title=self.pet.name, border_style="magenta")

    def _conversation_panel(self) -> Panel:
        renderables: list[Any] = []
        for role, message in self._history[-8:]:
            renderables.append(self._message_renderable(role, message))
        return Panel(Group(*renderables), title="Conversation", border_style="green")

    def _message_renderable(self, role: str, message: str) -> Any:
        if role == "assistant":
            return Panel(Markdown(message), title="Eugene", border_style="blue", padding=(0, 1))
        if role == "user":
            return Panel(Text(message, style="white"), title="You", border_style="white", padding=(0, 1))
        return Panel(Text(message, style="yellow"), title=self.pet.name, border_style="yellow", padding=(0, 1))

    def _footer_panel(self) -> Panel:
        composer = Text()
        composer.append("> ", style="bold cyan")
        composer.append(self._input_buffer or "Type a message for Eugene...", style="white" if self._input_buffer else "dim")
        commands = Text("Commands: /feed /play /sleep /name NAME /help /clear /quit", style="dim")
        return Panel(Group(composer, Rule(style="grey35"), commands), border_style="cyan")

    def _current_frame(self) -> str:
        animation = self.animations.get(self.pet.current_animation()) or self.animations.get("idle")
        if animation is None or not animation.frames:
            return "    /\\_/\\\\\n   ( o.o )\n    > ^ <"
        frame_count = len(animation.frames)
        if frame_count == 1:
            return animation.frames[0]
        fps = max(animation.fps, 1)
        index = int(time.time() * fps)
        if animation.loop:
            index %= frame_count
        else:
            index = min(index, frame_count - 1)
        return animation.frames[index]

    def _load_animations(self) -> dict[str, Animation]:
        animations: dict[str, Animation] = {}
        for state in ("idle", "eating", "sleeping", "playing", "sad", "dead"):
            path = self.animation_dir / f"{state}.toml"
            if not path.exists():
                continue
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            animation = data.get("animation", {})
            frames = [str(frame.get("data", "")).strip("\n") for frame in animation.get("frames", [])]
            if not frames:
                continue
            animations[state] = Animation(
                fps=int(animation.get("fps", 4)),
                loop=bool(animation.get("loop", True)),
                frames=frames,
            )
        return animations

    def _help_lines(self) -> list[str]:
        return [
            "Type normal text and press Enter to chat with Eugene.",
            "Assistant replies render as markdown with Rich.",
            "Run this in its own terminal so Eugene server logs stay clean.",
            "/feed /play /sleep manage the local pet state.",
            "/name NAME renames the pet shell without changing Eugene's identity.",
            "/clear clears the local transcript window.",
            "/quit exits the client but leaves Eugene running.",
        ]

    def _mood_label(self) -> str:
        if self.pet.is_dead():
            return "gone"
        if self.pet.happiness > 70 and self.pet.energy > 50:
            return "bright"
        if self.pet.happiness < 25 or self.pet.hunger < 20:
            return "fragile"
        if self.pet.energy < 20:
            return "sleepy"
        return "steady"

    def _bar(self, value: float) -> str:
        filled = max(0, min(10, int(round(value / 10))))
        return f"[{'#' * filled}{'.' * (10 - filled)}] {int(value):>3}"

    def _push_history(self, role: str, message: str) -> None:
        self._history.append((role, message))
        self._history = self._history[-16:]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Standalone Termagotchi terminal client for Eugene")
    parser.add_argument("--api-key", default=os.getenv("EUGENE_API_KEY", ""), help="Eugene API key")
    parser.add_argument("--ws-base", default=os.getenv("EUGENE_WS_BASE", DEFAULT_WS_BASE), help="WebSocket base URL, e.g. ws://127.0.0.1:8000")
    parser.add_argument("--session-id", default=os.getenv("EUGENE_TERMAGOTCHI_SESSION", DEFAULT_SESSION_ID), help="Stable Eugene session id")
    parser.add_argument("--pet-name", default=os.getenv("EUGENE_TERMAGOTCHI_NAME", "Eugene"), help="Displayed pet name")
    parser.add_argument(
        "--save-file",
        default=str(DATA_DIR / "termagotchi-client.json"),
        help="Path to the local pet save file",
    )
    parser.add_argument(
        "--animation-dir",
        default=str(ROOT_DIR / "termagotchi" / "animations"),
        help="Directory containing animation TOML files",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.api_key:
        print("Missing API key. Pass --api-key or set EUGENE_API_KEY.", file=sys.stderr)
        return 2
    client = TermagotchiClient(
        api_key=args.api_key,
        ws_base=args.ws_base,
        session_id=args.session_id,
        pet_name=args.pet_name,
        save_path=Path(args.save_file),
        animation_dir=Path(args.animation_dir),
    )
    try:
        asyncio.run(client.run())
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
