from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

from eugene.config import DATA_DIR, STATIC_DIR
from eugene.core import AppletBase, FieldSpec
from eugene.models import ToolDefinition


class PythonReplApplet(AppletBase):
    name = "python_repl"
    description = "Run sandboxed Python snippets for ad-hoc data analysis with stdout capture, pandas-friendly summaries, and matplotlib figure rendering."
    load = "lazy"
    inject = "selective"
    can_disable = True

    class Config:
        fields = {
            "allow_execution": FieldSpec(default=False, description="Must be enabled before Python execution is allowed."),
            "timeout_seconds": FieldSpec(default=30, description="Maximum execution time for one Python run."),
            "max_code_chars": FieldSpec(default=12000, description="Maximum Python code size accepted by the tool."),
            "max_output_chars": FieldSpec(default=16000, description="Maximum stdout/stderr/result preview retained per run."),
            "retain_run_artifacts": FieldSpec(default=25, description="How many recent run artifact folders to keep per session."),
        }

    async def on_load(self) -> None:
        self._workspace_root().mkdir(parents=True, exist_ok=True)
        self._static_root().mkdir(parents=True, exist_ok=True)

    def get_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="execute_python",
                description=(
                    "Execute Python code in an isolated data-analysis environment. "
                    "Captures stdout, stderr, the last expression result, generated files, and matplotlib figures."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "description": "Python code to execute."},
                        "reset_workspace": {
                            "type": "boolean",
                            "description": "When true, clears this chat session's Python workspace before running.",
                        },
                    },
                    "required": ["code"],
                },
                applet_name=self.name,
            ),
            ToolDefinition(
                name="list_python_artifacts",
                description="List saved Python REPL artifacts for the active session, including rendered figures.",
                input_schema={"type": "object", "properties": {}},
                applet_name=self.name,
            ),
            ToolDefinition(
                name="clear_python_workspace",
                description="Delete the active session's Python workspace and saved artifacts.",
                input_schema={"type": "object", "properties": {}},
                applet_name=self.name,
            ),
        ]

    async def handle_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if name == "execute_python":
            return await self._execute_python(arguments)
        if name == "list_python_artifacts":
            return self._list_artifacts(arguments)
        if name == "clear_python_workspace":
            self._clear_session_workspace(self._session_id(arguments))
            return {"status": "cleared"}
        raise ValueError(f"Unknown tool: {name}")

    async def _execute_python(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self._execution_enabled():
            return {"error": "Python execution is disabled. Enable 'allow_execution' in the python_repl applet config."}

        code = str(arguments.get("code") or "")
        if not code.strip():
            return {"error": "No Python code was provided."}

        max_code_chars = int(self.config.get("max_code_chars", 12000))
        if len(code) > max_code_chars:
            return {"error": f"Code exceeds the configured limit of {max_code_chars} characters."}

        session_id = self._session_id(arguments)
        if arguments.get("reset_workspace"):
            self._clear_session_workspace(session_id)

        session_root = self._workspace_root() / session_id
        workspace_dir = session_root / "workspace"
        run_id = str(uuid4())
        run_dir = session_root / "runs" / run_id
        static_run_dir = self._static_root() / session_id / run_id
        workspace_dir.mkdir(parents=True, exist_ok=True)
        run_dir.mkdir(parents=True, exist_ok=True)
        static_run_dir.mkdir(parents=True, exist_ok=True)

        payload_path = run_dir / "payload.json"
        result_path = run_dir / "result.json"
        payload = {
            "code": code,
            "workspace_dir": str(workspace_dir),
            "artifact_dir": str(static_run_dir),
            "data_dir": str(DATA_DIR),
            "max_output_chars": int(self.config.get("max_output_chars", 16000)),
        }
        payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(Path(__file__).with_name("runner.py")),
            str(payload_path),
            str(result_path),
            cwd=str(workspace_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=int(self.config.get("timeout_seconds", 30)) + 2)
        except asyncio.TimeoutError:
            process.kill()
            with contextlib.suppress(Exception):
                await process.communicate()
            return {"error": f"Python execution timed out after {self.config.get('timeout_seconds', 30)} seconds."}

        if not result_path.exists():
            return {
                "error": "Python runner did not return a structured result.",
                "runner_stdout": stdout.decode("utf-8", errors="replace"),
                "runner_stderr": stderr.decode("utf-8", errors="replace"),
            }

        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["run_id"] = run_id
        result["workspace_dir"] = str(workspace_dir)
        result["artifacts"] = [self._artifact_payload(Path(item["path"])) for item in result.get("artifacts", [])]
        self._prune_old_runs(session_id)
        return result

    def _list_artifacts(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        session_root = self._static_root() / self._session_id(arguments)
        if not session_root.exists():
            return []
        artifacts: list[dict[str, Any]] = []
        for path in sorted(session_root.rglob("*")):
            if path.is_file():
                artifacts.append(self._artifact_payload(path))
        return artifacts

    def _artifact_payload(self, path: Path) -> dict[str, Any]:
        relative = path.resolve().relative_to(STATIC_DIR.resolve()).as_posix()
        return {
            "path": str(path),
            "url": f"/{relative}",
            "name": path.name,
            "size_bytes": path.stat().st_size,
        }

    def _clear_session_workspace(self, session_id: str) -> None:
        session_workspace = self._workspace_root() / session_id
        session_static = self._static_root() / session_id
        for target in (session_workspace, session_static):
            if not target.exists():
                continue
            resolved = target.resolve()
            if not str(resolved).startswith(str(self._workspace_root().resolve())) and not str(resolved).startswith(str(self._static_root().resolve())):
                raise RuntimeError(f"Refusing to clear unexpected path: {resolved}")
            shutil.rmtree(resolved)

    def _prune_old_runs(self, session_id: str) -> None:
        retain = max(1, int(self.config.get("retain_run_artifacts", 25)))
        root = self._static_root() / session_id
        if not root.exists():
            return
        run_dirs = sorted((path for path in root.iterdir() if path.is_dir()), key=lambda item: item.stat().st_mtime, reverse=True)
        for stale in run_dirs[retain:]:
            shutil.rmtree(stale, ignore_errors=True)

    def _execution_enabled(self) -> bool:
        return str(self.config.get("allow_execution", "")).lower() in {"true", "1", "yes"}

    def _session_id(self, arguments: dict[str, Any]) -> str:
        return str(arguments.get("_runtime_session_id") or "default")

    def _workspace_root(self) -> Path:
        return DATA_DIR / "python_repl"

    def _static_root(self) -> Path:
        return STATIC_DIR / "python_repl"
