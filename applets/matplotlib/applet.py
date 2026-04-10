from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

from eugene.config import DATA_DIR, STATIC_DIR
from eugene.core import AppletBase, FieldSpec
from eugene.models import ToolDefinition


class MatplotlibApplet(AppletBase):
    name = "matplotlib"
    description = "Generate matplotlib charts from natural-language instructions and return rendered images."
    load = "lazy"
    inject = "selective"
    can_disable = True

    class Config:
        fields = {
            "allow_execution": FieldSpec(default=True, description="Allow the applet to generate and execute matplotlib code."),
            "timeout_seconds": FieldSpec(default=30, description="Maximum execution time for one chart render."),
            "max_instruction_chars": FieldSpec(default=8000, description="Maximum instruction length accepted by the tool."),
            "max_data_chars": FieldSpec(default=30000, description="Maximum attached data length accepted by the tool."),
            "max_generated_code_chars": FieldSpec(default=12000, description="Maximum generated Python code size accepted from the model."),
            "max_output_chars": FieldSpec(default=12000, description="Maximum stdout/stderr retained from one render."),
            "retain_run_artifacts": FieldSpec(default=25, description="How many recent chart runs to keep per session."),
            "generation_model": FieldSpec(default="", description="Optional model override for chart code generation."),
        }

    async def on_load(self) -> None:
        self._workspace_root().mkdir(parents=True, exist_ok=True)
        self._static_root().mkdir(parents=True, exist_ok=True)

    def get_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="generate_matplotlib_chart",
                description=(
                    "Generate Python matplotlib code from a natural-language request, render it, "
                    "and return the resulting chart image."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "instructions": {
                            "type": "string",
                            "description": "What chart to create, including styling and analytical intent.",
                        },
                        "data": {
                            "type": "string",
                            "description": "Optional raw data for the chart. Can be plain text, CSV, or JSON.",
                        },
                        "data_format": {
                            "type": "string",
                            "enum": ["text", "csv", "json"],
                            "description": "Format of the optional data payload.",
                            "default": "text",
                        },
                        "reset_workspace": {
                            "type": "boolean",
                            "description": "When true, clears this session's matplotlib workspace before rendering.",
                        },
                    },
                    "required": ["instructions"],
                },
                applet_name=self.name,
            ),
            ToolDefinition(
                name="list_matplotlib_artifacts",
                description="List saved matplotlib artifacts for the active session.",
                input_schema={"type": "object", "properties": {}},
                applet_name=self.name,
            ),
            ToolDefinition(
                name="clear_matplotlib_workspace",
                description="Delete the active session's matplotlib workspace and saved artifacts.",
                input_schema={"type": "object", "properties": {}},
                applet_name=self.name,
            ),
        ]

    async def handle_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if name == "generate_matplotlib_chart":
            return await self._generate_chart(arguments)
        if name == "list_matplotlib_artifacts":
            return self._list_artifacts(arguments)
        if name == "clear_matplotlib_workspace":
            self._clear_session_workspace(self._session_id(arguments))
            return {"status": "cleared"}
        raise ValueError(f"Unknown tool: {name}")

    async def _generate_chart(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self._execution_enabled():
            return {"error": "Matplotlib execution is disabled. Enable 'allow_execution' in the matplotlib applet config."}
        missing = self._missing_runtime_dependencies()
        if missing:
            deps = ", ".join(missing)
            return {
                "error": (
                    f"The matplotlib applet requires installed Python packages that are missing in the active runtime: {deps}. "
                    "Install project dependencies before using this tool."
                )
            }

        instructions = str(arguments.get("instructions") or "").strip()
        if not instructions:
            return {"error": "No chart instructions were provided."}

        max_instruction_chars = int(self.config.get("max_instruction_chars", 8000))
        if len(instructions) > max_instruction_chars:
            return {"error": f"Instructions exceed the configured limit of {max_instruction_chars} characters."}

        data = str(arguments.get("data") or "")
        max_data_chars = int(self.config.get("max_data_chars", 30000))
        if len(data) > max_data_chars:
            return {"error": f"Data exceeds the configured limit of {max_data_chars} characters."}

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

        data_descriptor = self._write_optional_data(
            workspace_dir=workspace_dir,
            data=data,
            data_format=str(arguments.get("data_format") or "text"),
        )

        generated_code = await self._generate_code(instructions=instructions, data_descriptor=data_descriptor)
        max_generated_code_chars = int(self.config.get("max_generated_code_chars", 12000))
        if len(generated_code) > max_generated_code_chars:
            return {"error": f"Generated code exceeds the configured limit of {max_generated_code_chars} characters."}

        payload_path = run_dir / "payload.json"
        result_path = run_dir / "result.json"
        payload = {
            "code": generated_code,
            "workspace_dir": str(workspace_dir),
            "artifact_dir": str(static_run_dir),
            "max_output_chars": int(self.config.get("max_output_chars", 12000)),
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
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=int(self.config.get("timeout_seconds", 30)) + 2,
            )
        except asyncio.TimeoutError:
            process.kill()
            with contextlib.suppress(Exception):
                await process.communicate()
            return {"error": f"Matplotlib execution timed out after {self.config.get('timeout_seconds', 30)} seconds."}

        if not result_path.exists():
            return {
                "error": "Matplotlib runner did not return a structured result.",
                "generated_code": generated_code,
                "runner_stdout": stdout.decode("utf-8", errors="replace"),
                "runner_stderr": stderr.decode("utf-8", errors="replace"),
            }

        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["run_id"] = run_id
        result["instructions"] = instructions
        result["generated_code"] = generated_code
        result["workspace_dir"] = str(workspace_dir)
        result["artifacts"] = [self._artifact_payload(Path(item["path"])) for item in result.get("artifacts", [])]
        if result["artifacts"]:
            first = result["artifacts"][0]
            result["primary_image_url"] = first["url"]
            result["primary_image_markdown"] = f"![Generated matplotlib chart]({first['url']})"
        index_payload = self._write_artifact_index(
            session_id=session_id,
            run_id=run_id,
            artifacts=result["artifacts"],
            instructions=instructions,
        )
        if index_payload:
            result["artifact_index"] = index_payload
        self._prune_old_runs(session_id)
        return result

    async def _generate_code(self, *, instructions: str, data_descriptor: dict[str, str] | None) -> str:
        data_block = "No external data was provided."
        if data_descriptor is not None:
            data_block = (
                f"Data was provided in {data_descriptor['format']} format.\n"
                f"Read it from the local file `{data_descriptor['path']}`.\n"
                f"Use pandas when it makes the plotting task easier."
            )

        messages = [
            {
                "role": "system",
                "content": (
                    "You write Python code for matplotlib charts. "
                    "Return only executable Python code with no markdown fences and no prose.\n\n"
                    "Requirements:\n"
                    "- Use matplotlib.pyplot as plt.\n"
                    "- Create at least one figure.\n"
                    "- Do not call plt.show().\n"
                    "- The runtime exposes ARTIFACT_DIR (string path) and ARTIFACT_PATH (pathlib.Path). "
                    "If you save files directly, save them there.\n"
                    "- Do not access the network.\n"
                    "- Only read local files when the user-provided data path is mentioned.\n"
                    "- Prefer clear labels, readable sizing, and sensible defaults.\n"
                    "- If a dataset is supplied, parse it from the provided file path.\n"
                    "- The last line may be a string expression summarizing the chart, but do not print explanations.\n"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Chart request:\n{instructions}\n\n"
                    f"{data_block}\n\n"
                    "Produce a single self-contained Python script."
                ),
            },
        ]
        model = str(self.config.get("generation_model", "")).strip() or None
        result = await self.services.provider.complete(
            messages=messages,
            tools=[],
            model=model,
            origin="applet:matplotlib",
        )
        code = self._extract_code(result.text)
        if not code.strip():
            raise RuntimeError("The model did not return any Python code.")
        return code

    def _extract_code(self, text: str) -> str:
        stripped = text.strip()
        fenced = re.search(r"```(?:python)?\s*(.*?)```", stripped, flags=re.DOTALL | re.IGNORECASE)
        if fenced:
            return fenced.group(1).strip()
        return stripped

    def _write_optional_data(
        self,
        *,
        workspace_dir: Path,
        data: str,
        data_format: str,
    ) -> dict[str, str] | None:
        if not data.strip():
            return None

        normalized = data_format.lower().strip()
        suffix = {
            "csv": ".csv",
            "json": ".json",
            "text": ".txt",
        }.get(normalized, ".txt")
        target = workspace_dir / f"input_data{suffix}"
        target.write_text(data, encoding="utf-8")
        return {"path": target.name, "format": normalized}

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
            "media_type": self._guess_media_type(path),
            "size_bytes": path.stat().st_size,
        }

    def _write_artifact_index(
        self,
        *,
        session_id: str,
        run_id: str,
        artifacts: list[dict[str, Any]],
        instructions: str,
    ) -> dict[str, Any] | None:
        run_static = self._static_root() / session_id / run_id
        run_static.mkdir(parents=True, exist_ok=True)
        if not artifacts:
            return None

        payload = {
            "session_id": session_id,
            "run_id": run_id,
            "instructions": instructions,
            "artifact_count": len(artifacts),
            "primary_image_url": artifacts[0]["url"],
            "primary_image_markdown": f"![Generated matplotlib chart]({artifacts[0]['url']})",
            "artifacts": artifacts,
        }
        index_path = run_static / "artifact_index.json"
        index_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        session_latest = self._static_root() / session_id
        session_latest.mkdir(parents=True, exist_ok=True)
        latest_index_path = session_latest / "latest_artifact_index.json"
        latest_index_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        primary_path = Path(artifacts[0]["path"])
        if primary_path.exists() and primary_path.is_file():
            latest_image = session_latest / f"latest{primary_path.suffix.lower()}"
            shutil.copy2(primary_path, latest_image)

        run_index_payload = self._artifact_payload(index_path)
        latest_index_payload = self._artifact_payload(latest_index_path)
        return {
            "run": run_index_payload,
            "latest": latest_index_payload,
        }

    def _guess_media_type(self, path: Path) -> str:
        extension = path.suffix.lower()
        return {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".svg": "image/svg+xml",
            ".json": "application/json",
            ".txt": "text/plain",
            ".md": "text/markdown",
        }.get(extension, "application/octet-stream")

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

    def _missing_runtime_dependencies(self) -> list[str]:
        missing: list[str] = []
        for module_name in ("matplotlib",):
            if importlib.util.find_spec(module_name) is None:
                missing.append(module_name)
        return missing

    def _session_id(self, arguments: dict[str, Any]) -> str:
        return str(arguments.get("_runtime_session_id") or "default")

    def _workspace_root(self) -> Path:
        return DATA_DIR / "matplotlib"

    def _static_root(self) -> Path:
        return STATIC_DIR / "matplotlib"
