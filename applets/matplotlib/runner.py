from __future__ import annotations

import ast
import builtins
import contextlib
import importlib
import io
import json
import os
import shutil
import sys
import traceback
from pathlib import Path
from typing import Any


os.environ.setdefault("MPLBACKEND", "Agg")


ALLOWED_ROOT_MODULES = {
    "collections",
    "csv",
    "datetime",
    "io",
    "json",
    "math",
    "matplotlib",
    "numpy",
    "pandas",
    "pathlib",
    "random",
    "statistics",
}


def main(payload_path: str, result_path: str) -> int:
    payload = json.loads(Path(payload_path).read_text(encoding="utf-8-sig"))
    workspace_dir = Path(payload["workspace_dir"]).resolve()
    artifact_dir = Path(payload["artifact_dir"]).resolve()
    max_output_chars = int(payload.get("max_output_chars", 12000))

    workspace_dir.mkdir(parents=True, exist_ok=True)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    safe_open = make_safe_open(workspace_dir)
    safe_import = make_safe_import()
    safe_builtins = build_safe_builtins(safe_import, safe_open)

    available_modules: dict[str, bool] = {}
    globals_dict: dict[str, Any] = {
        "__builtins__": safe_builtins,
        "__name__": "__main__",
        "ARTIFACT_DIR": str(artifact_dir),
        "ARTIFACT_PATH": artifact_dir,
    }
    globals_dict.update(load_optional_modules(available_modules))

    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    result: dict[str, Any]

    with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
        try:
            execution_value = execute_code(payload["code"], globals_dict)
            artifacts = save_open_figures(artifact_dir)
            artifacts = merge_artifacts(
                artifacts,
                discover_image_artifacts(workspace_dir=workspace_dir, artifact_dir=artifact_dir),
            )
            if not artifacts:
                raise RuntimeError("The generated code did not create a matplotlib figure.")
            result = {
                "ok": True,
                "result": summarize_value(execution_value, max_output_chars),
                "artifacts": artifacts,
                "generated_files": list_generated_files(workspace_dir),
                "available_modules": available_modules,
                "stdout": trim_text(stdout_buffer.getvalue(), max_output_chars),
                "stderr": trim_text(stderr_buffer.getvalue(), max_output_chars),
            }
        except Exception as exc:
            result = {
                "ok": False,
                "error": str(exc),
                "traceback": trim_text(traceback.format_exc(), max_output_chars),
                "available_modules": available_modules,
                "stdout": trim_text(stdout_buffer.getvalue(), max_output_chars),
                "stderr": trim_text(stderr_buffer.getvalue(), max_output_chars),
            }

    Path(result_path).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return 0


def make_safe_open(workspace_dir: Path):
    original_open = builtins.open

    def _safe_open(file: str | os.PathLike[str], mode: str = "r", *args: Any, **kwargs: Any):
        path = resolve_safe_path(Path(file), workspace_dir)
        return original_open(path, mode, *args, **kwargs)

    return _safe_open


def resolve_safe_path(path: Path, workspace_dir: Path) -> Path:
    candidate = (workspace_dir / path).resolve() if not path.is_absolute() else path.resolve()
    if not str(candidate).startswith(str(workspace_dir.resolve())):
        raise PermissionError(f"Access outside the matplotlib workspace is not allowed: {candidate}")
    return candidate


def make_safe_import():
    original_import = builtins.__import__

    def _safe_import(name: str, globals_: Any = None, locals_: Any = None, fromlist: Any = (), level: int = 0):
        root_name = name.split(".", 1)[0]
        if root_name not in ALLOWED_ROOT_MODULES:
            raise ImportError(f"Import '{name}' is not allowed in the matplotlib sandbox.")
        return original_import(name, globals_, locals_, fromlist, level)

    return _safe_import


def build_safe_builtins(safe_import, safe_open):
    allowed_names = {
        "abs",
        "all",
        "any",
        "bool",
        "dict",
        "enumerate",
        "Exception",
        "filter",
        "float",
        "int",
        "isinstance",
        "len",
        "list",
        "max",
        "min",
        "print",
        "range",
        "repr",
        "reversed",
        "round",
        "set",
        "sorted",
        "str",
        "sum",
        "tuple",
        "ValueError",
        "zip",
    }
    safe = {name: getattr(builtins, name) for name in allowed_names}
    safe["open"] = safe_open
    safe["__import__"] = safe_import
    return safe


def load_optional_modules(availability: dict[str, bool]) -> dict[str, Any]:
    exports: dict[str, Any] = {}
    optional = [
        ("json", "json"),
        ("math", "math"),
        ("statistics", "statistics"),
        ("datetime", "datetime"),
        ("csv", "csv"),
        ("pandas", "pd"),
        ("numpy", "np"),
        ("matplotlib.pyplot", "plt"),
    ]
    for module_name, export_name in optional:
        try:
            exports[export_name] = importlib.import_module(module_name)
            availability[module_name] = True
        except Exception:
            availability[module_name] = False
    return exports


def execute_code(code: str, globals_dict: dict[str, Any]) -> Any:
    parsed = ast.parse(code, mode="exec")
    last_expr: ast.expr | None = None
    body = list(parsed.body)
    if body and isinstance(body[-1], ast.Expr):
        last_expr = body.pop().value

    if body:
        module = ast.Module(body=body, type_ignores=[])
        exec(compile(module, "<matplotlib>", "exec"), globals_dict, globals_dict)

    if last_expr is not None:
        expression = ast.Expression(last_expr)
        return eval(compile(expression, "<matplotlib>", "eval"), globals_dict, globals_dict)
    return None


def summarize_value(value: Any, max_output_chars: int) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "type": f"{type(value).__module__}.{type(value).__name__}",
        "repr": trim_text(repr(value), max_output_chars),
    }


def save_open_figures(artifact_dir: Path) -> list[dict[str, str]]:
    try:
        import matplotlib.pyplot as plt  # type: ignore
    except Exception:
        return []

    artifacts: list[dict[str, str]] = []
    for index, fig_number in enumerate(plt.get_fignums(), start=1):
        figure = plt.figure(fig_number)
        target = artifact_dir / f"figure_{index}.png"
        figure.savefig(target, bbox_inches="tight", dpi=160)
        artifacts.append({"path": str(target), "media_type": "image/png"})
    if artifacts:
        plt.close("all")
    return artifacts


def discover_image_artifacts(workspace_dir: Path, artifact_dir: Path) -> list[dict[str, str]]:
    artifacts: list[dict[str, str]] = []
    seen_resolved: set[str] = set()

    def _collect(directory: Path) -> None:
        if not directory.exists():
            return
        for candidate in sorted(directory.rglob("*")):
            if not candidate.is_file() or candidate.suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}:
                continue
            if candidate.stat().st_size <= 0:
                continue
            resolved_key = str(candidate.resolve())
            if resolved_key in seen_resolved:
                continue
            seen_resolved.add(resolved_key)
            target = candidate
            if artifact_dir.resolve() not in candidate.resolve().parents:
                target = artifact_dir / candidate.name
                if target.exists():
                    target = artifact_dir / f"{candidate.stem}_{len(artifacts) + 1}{candidate.suffix.lower()}"
                shutil.copy2(candidate, target)
            artifacts.append({"path": str(target), "media_type": guess_media_type(target)})

    _collect(artifact_dir)
    _collect(workspace_dir)
    return artifacts


def merge_artifacts(primary: list[dict[str, str]], discovered: list[dict[str, str]]) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in [*primary, *discovered]:
        key = str(Path(item["path"]).resolve())
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def guess_media_type(path: Path) -> str:
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
    }.get(path.suffix.lower(), "application/octet-stream")


def list_generated_files(workspace_dir: Path) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for path in sorted(workspace_dir.rglob("*")):
        if path.is_file():
            files.append(
                {
                    "path": str(path),
                    "name": path.name,
                    "size_bytes": path.stat().st_size,
                }
            )
    return files[-25:]


def trim_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n... truncated after {max_chars} characters ..."


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
