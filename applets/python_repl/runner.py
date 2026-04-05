from __future__ import annotations

import ast
import builtins
import contextlib
import importlib
import io
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any


os.environ.setdefault("MPLBACKEND", "Agg")


ALLOWED_ROOT_MODULES = {
    "collections",
    "csv",
    "datetime",
    "decimal",
    "fractions",
    "io",
    "itertools",
    "json",
    "math",
    "matplotlib",
    "numpy",
    "pandas",
    "pathlib",
    "random",
    "re",
    "sqlite3",
    "statistics",
    "textwrap",
}


def main(payload_path: str, result_path: str) -> int:
    payload = json.loads(Path(payload_path).read_text(encoding="utf-8"))
    workspace_dir = Path(payload["workspace_dir"]).resolve()
    artifact_dir = Path(payload["artifact_dir"]).resolve()
    data_dir = Path(payload["data_dir"]).resolve()
    max_output_chars = int(payload.get("max_output_chars", 16000))

    workspace_dir.mkdir(parents=True, exist_ok=True)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    safe_open = make_safe_open(workspace_dir, data_dir)
    safe_import = make_safe_import()
    safe_builtins = build_safe_builtins(safe_import, safe_open)

    available_modules: dict[str, bool] = {}
    globals_dict: dict[str, Any] = {
        "__builtins__": safe_builtins,
        "__name__": "__main__",
    }
    globals_dict.update(load_optional_modules(available_modules))

    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    result: dict[str, Any]

    with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
        try:
            execution_value = execute_code(payload["code"], globals_dict)
            artifacts = save_open_figures(artifact_dir)
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


def make_safe_open(workspace_dir: Path, data_dir: Path):
    original_open = builtins.open
    writable_roots = (workspace_dir, workspace_dir.parent)
    readable_roots = (workspace_dir, workspace_dir.parent, data_dir)

    def _safe_open(file: str | os.PathLike[str], mode: str = "r", *args: Any, **kwargs: Any):
        path = resolve_safe_path(Path(file), workspace_dir, readable_roots if "r" in mode and all(flag not in mode for flag in ("w", "a", "x", "+")) else writable_roots)
        return original_open(path, mode, *args, **kwargs)

    return _safe_open


def resolve_safe_path(path: Path, base_dir: Path, allowed_roots: tuple[Path, ...]) -> Path:
    candidate = (base_dir / path).resolve() if not path.is_absolute() else path.resolve()
    if not any(str(candidate).startswith(str(root.resolve())) for root in allowed_roots):
        raise PermissionError(f"Access outside the Python REPL sandbox is not allowed: {candidate}")
    return candidate


def make_safe_import():
    original_import = builtins.__import__

    def _safe_import(name: str, globals_: Any = None, locals_: Any = None, fromlist: Any = (), level: int = 0):
        root_name = name.split(".", 1)[0]
        if root_name not in ALLOWED_ROOT_MODULES:
            raise ImportError(f"Import '{name}' is not allowed in the Python REPL sandbox.")
        return original_import(name, globals_, locals_, fromlist, level)

    return _safe_import


def build_safe_builtins(safe_import, safe_open):
    allowed_names = {
        "abs",
        "all",
        "any",
        "bool",
        "chr",
        "dict",
        "enumerate",
        "Exception",
        "filter",
        "float",
        "format",
        "frozenset",
        "getattr",
        "hasattr",
        "hash",
        "hex",
        "int",
        "isinstance",
        "issubclass",
        "iter",
        "len",
        "list",
        "map",
        "max",
        "min",
        "next",
        "object",
        "ord",
        "pow",
        "print",
        "range",
        "repr",
        "reversed",
        "round",
        "set",
        "slice",
        "sorted",
        "str",
        "sum",
        "tuple",
        "type",
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
        ("pathlib", "pathlib"),
        ("csv", "csv"),
        ("sqlite3", "sqlite3"),
        ("random", "random"),
        ("re", "re"),
        ("datetime", "datetime"),
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
        exec(compile(module, "<python_repl>", "exec"), globals_dict, globals_dict)

    if last_expr is not None:
        expression = ast.Expression(last_expr)
        return eval(compile(expression, "<python_repl>", "eval"), globals_dict, globals_dict)
    return None


def summarize_value(value: Any, max_output_chars: int) -> dict[str, Any] | None:
    if value is None:
        return None

    module_name = type(value).__module__
    type_name = type(value).__name__

    if module_name.startswith("pandas"):
        return summarize_pandas(value, max_output_chars)

    if isinstance(value, Path):
        return {"type": "path", "repr": trim_text(str(value), max_output_chars)}

    if isinstance(value, (dict, list, tuple, set)):
        return {
            "type": type_name,
            "repr": trim_text(repr(value), max_output_chars),
        }

    return {
        "type": f"{module_name}.{type_name}",
        "repr": trim_text(repr(value), max_output_chars),
    }


def summarize_pandas(value: Any, max_output_chars: int) -> dict[str, Any]:
    if type(value).__name__ == "DataFrame":
        preview = value.head(10).to_string(max_rows=10, max_cols=12)
        return {
            "type": "pandas.DataFrame",
            "shape": list(value.shape),
            "columns": [str(item) for item in list(value.columns[:20])],
            "repr": trim_text(preview, max_output_chars),
        }

    preview = value.head(10).to_string()
    return {
        "type": "pandas.Series",
        "shape": [int(value.shape[0])],
        "name": str(value.name),
        "repr": trim_text(preview, max_output_chars),
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
        figure.savefig(target, bbox_inches="tight")
        artifacts.append({"path": str(target), "media_type": "image/png"})
    if artifacts:
        plt.close("all")
    return artifacts


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
