"""Workspace-scoped tools exposed to the model."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


MAX_OUTPUT = 20_000


class Workspace:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError(f"workspace is not a directory: {self.root}")

    def path(self, relative: str, *, must_exist: bool = False) -> Path:
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
            raise ValueError("path must be a non-empty relative path")
        candidate = (self.root / relative).resolve()
        if not candidate.is_relative_to(self.root):
            raise PermissionError(f"path escapes workspace: {relative}")
        if must_exist and not candidate.exists():
            raise FileNotFoundError(relative)
        return candidate

    def read(self, path: str, start_line: int = 1, end_line: int | None = None) -> str:
        target = self.path(path, must_exist=True)
        if not target.is_file():
            raise ValueError(f"not a file: {path}")
        if target.stat().st_size > 1_000_000:
            raise ValueError("file exceeds 1 MB read limit")
        lines = target.read_text(encoding="utf-8").splitlines()
        if start_line < 1 or (end_line is not None and end_line < start_line):
            raise ValueError("invalid line range")
        selected = lines[start_line - 1 : end_line]
        return "\n".join(f"{number}: {line}" for number, line in enumerate(selected, start_line))

    def search(self, query: str, path: str = ".", limit: int = 100) -> str:
        if not query:
            raise ValueError("query must not be empty")
        base = self.path(path, must_exist=True)
        candidates = [base] if base.is_file() else base.rglob("*")
        matches: list[str] = []
        for file in candidates:
            if len(matches) >= min(limit, 500) or not file.is_file():
                continue
            if not file.resolve().is_relative_to(self.root):
                continue
            relative = file.relative_to(self.root)
            if any(part.startswith(".") for part in relative.parts) or file.stat().st_size > 1_000_000:
                continue
            try:
                for line_no, line in enumerate(file.read_text(encoding="utf-8").splitlines(), 1):
                    if query in line:
                        matches.append(f"{relative}:{line_no}:{line}")
                        if len(matches) >= min(limit, 500):
                            break
            except UnicodeDecodeError:
                continue
        return "\n".join(matches)

    def edit(self, path: str, old: str, new: str) -> str:
        if not old:
            raise ValueError("old text must not be empty")
        target = self.path(path, must_exist=True)
        content = target.read_text(encoding="utf-8")
        count = content.count(old)
        if count != 1:
            raise ValueError(f"old text must match exactly once; found {count}")
        updated = content.replace(old, new, 1)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False) as handle:
            handle.write(updated)
            temporary = Path(handle.name)
        os.replace(temporary, target)
        return f"updated {path}"


class CommandRunner:
    """Runs a small command allowlist, optionally inside macOS sandbox-exec."""

    def __init__(self, workspace: Workspace, mode: str = "sandbox") -> None:
        if mode not in {"sandbox", "local", "disabled"}:
            raise ValueError("execution mode must be sandbox, local, or disabled")
        self.workspace = workspace
        self.mode = mode

    @staticmethod
    def _validate(command: list[str]) -> None:
        if not command or not all(isinstance(part, str) and part for part in command):
            raise ValueError("command must be a non-empty string array")
        executable = Path(command[0]).name
        args = command[1:]
        python_test = executable in {"python", "python3"} and args[:2] in (["-m", "unittest"], ["-m", "pytest"])
        pytest_test = executable in {"pytest", "py.test"}
        git_read = executable == "git" and bool(args) and args[0] in {"diff", "status"}
        if not (python_test or pytest_test or git_read):
            raise PermissionError("allowed commands: python -m unittest/pytest, pytest, git diff/status")

    def _sandbox_prefix(self) -> list[str]:
        sandbox = shutil.which("sandbox-exec")
        if not sandbox:
            raise RuntimeError("sandbox-exec is unavailable; use disabled mode or explicitly opt into local mode")
        root = json.dumps(str(self.workspace.root))
        profile = f"""(version 1)
(deny default)
(allow process*)
(allow signal)
(allow sysctl-read)
(allow mach-lookup)
(allow file-read*)
(deny file-read* (subpath \"/Users\") (subpath \"/Volumes\") (subpath \"/Network\"))
(allow file-read* (subpath {root}))
(allow file-write* (subpath {root}) (subpath \"/private/tmp\"))
"""
        return [sandbox, "-p", profile, "--"]

    def run(self, command: list[str], timeout: int = 30) -> str:
        if self.mode == "disabled":
            raise PermissionError("command execution is disabled")
        self._validate(command)
        argv = [*self._sandbox_prefix(), *command] if self.mode == "sandbox" else command
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        }
        try:
            completed = subprocess.run(
                argv,
                cwd=self.workspace.root,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=min(max(timeout, 1), 120),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            output = (exc.stdout or "")[-MAX_OUTPUT:]
            return f"timeout\n{output}"
        output = completed.stdout[-MAX_OUTPUT:]
        return f"exit_code={completed.returncode}\n{output}"


@dataclass(slots=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., str]


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools = {tool.name: tool for tool in tools or []}

    def specs(self) -> list[dict[str, Any]]:
        return [
            {"type": "function", "function": {"name": tool.name, "description": tool.description, "parameters": tool.parameters}}
            for tool in self._tools.values()
        ]

    def execute(self, name: str, arguments: dict[str, Any]) -> str:
        tool = self._tools.get(name)
        if not tool:
            return json.dumps({"ok": False, "error": f"unknown tool: {name}"}, ensure_ascii=False)
        try:
            result = tool.handler(**arguments)
            return json.dumps({"ok": True, "result": result}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)


def build_tools(workspace: str | Path, *, execution_mode: str = "sandbox", read_only: bool = False) -> ToolRegistry:
    ws = Workspace(workspace)
    runner = CommandRunner(ws, execution_mode)
    tools = [
        Tool("read", "Read a UTF-8 text file with line numbers.", {"type": "object", "properties": {"path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}, "required": ["path"], "additionalProperties": False}, ws.read),
        Tool("search", "Search for literal text in workspace files.", {"type": "object", "properties": {"query": {"type": "string"}, "path": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"], "additionalProperties": False}, ws.search),
    ]
    if not read_only:
        tools.extend([
            Tool("edit", "Replace one exact text occurrence in a file.", {"type": "object", "properties": {"path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}}, "required": ["path", "old", "new"], "additionalProperties": False}, ws.edit),
            Tool("bash", "Run an allowlisted test or git inspection command without a shell.", {"type": "object", "properties": {"command": {"type": "array", "items": {"type": "string"}}, "timeout": {"type": "integer"}}, "required": ["command"], "additionalProperties": False}, runner.run),
            Tool("test", "Run an allowlisted test command.", {"type": "object", "properties": {"command": {"type": "array", "items": {"type": "string"}}, "timeout": {"type": "integer"}}, "required": ["command"], "additionalProperties": False}, runner.run),
        ])
    return ToolRegistry(tools)
