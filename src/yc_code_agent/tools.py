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
MAX_READ_LINES = 400


class Workspace:
    def __init__(self, root: str | Path, *, writable_paths: set[str] | None = None) -> None:
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError(f"workspace is not a directory: {self.root}")
        self.writable_paths = None
        if writable_paths is not None:
            self.writable_paths = set()
            for path in writable_paths:
                relative = Path(path)
                if not path or relative.is_absolute() or ".." in relative.parts:
                    raise ValueError(f"invalid writable path: {path}")
                target = (self.root / relative).resolve()
                if not target.is_relative_to(self.root):
                    raise ValueError(f"writable path escapes workspace: {path}")
                self.writable_paths.add(str(relative))

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
        stop = min(end_line, start_line + MAX_READ_LINES - 1) if end_line is not None else start_line + MAX_READ_LINES - 1
        selected = lines[start_line - 1 : stop]
        result = "\n".join(f"{number}: {line}" for number, line in enumerate(selected, start_line))
        if stop < len(lines) and (end_line is None or stop < end_line):
            result += f"\n... [truncated at {MAX_READ_LINES} lines; continue with start_line={stop + 1}]"
        return result

    def list_files(self, path: str = ".", limit: int = 100) -> str:
        base = self.path(path, must_exist=True)
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        candidates = [base] if base.is_file() else base.rglob("*")
        files = []
        for candidate in candidates:
            if not candidate.is_file() or not candidate.resolve().is_relative_to(self.root):
                continue
            relative = candidate.relative_to(self.root)
            if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
                continue
            files.append(str(relative))
        return "\n".join(sorted(files)[:limit])

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

    def _ensure_writable(self, path: str) -> None:
        resolved = (self.root / path).resolve()
        relative = str(resolved.relative_to(self.root)) if resolved.is_relative_to(self.root) else path
        if self.writable_paths is not None and relative not in self.writable_paths:
            raise PermissionError(f"path is read-only for this run: {path}")

    def edit(self, path: str, old: str, new: str) -> str:
        if not old:
            raise ValueError("old text must not be empty")
        self._ensure_writable(path)
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

    def write(self, path: str, content: str) -> str:
        if not isinstance(content, str) or len(content.encode("utf-8")) > 1_000_000:
            raise ValueError("content must be UTF-8 text no larger than 1 MB")
        self._ensure_writable(path)
        target = self.path(path)
        if not target.parent.is_dir():
            raise ValueError(f"parent directory does not exist: {target.parent.relative_to(self.root)}")
        action = "updated" if target.exists() else "created"
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False) as handle:
            handle.write(content)
            temporary = Path(handle.name)
        os.replace(temporary, target)
        return f"{action} {path}"


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


def build_tools(
    workspace: str | Path,
    *,
    execution_mode: str = "sandbox",
    read_only: bool = False,
    allowed_tools: set[str] | None = None,
    writable_paths: set[str] | None = None,
) -> ToolRegistry:
    ws = Workspace(workspace, writable_paths=writable_paths)
    runner = CommandRunner(ws, execution_mode)
    tools = [
        Tool("list_files", "List non-hidden files below a workspace path.", {"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}}, "additionalProperties": False}, ws.list_files),
        Tool("read", "Read a UTF-8 text file with line numbers.", {"type": "object", "properties": {"path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}, "required": ["path"], "additionalProperties": False}, ws.read),
        Tool("search", "Search for literal text in workspace files.", {"type": "object", "properties": {"query": {"type": "string"}, "path": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"], "additionalProperties": False}, ws.search),
    ]
    if not read_only:
        tools.extend([
            Tool("edit", "Replace one exact text occurrence in a file.", {"type": "object", "properties": {"path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}}, "required": ["path", "old", "new"], "additionalProperties": False}, ws.edit),
            Tool("write", "Create or replace one UTF-8 text file inside the workspace.", {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"], "additionalProperties": False}, ws.write),
            Tool("bash", "Run an allowlisted test or git inspection command without a shell.", {"type": "object", "properties": {"command": {"type": "array", "items": {"type": "string"}}, "timeout": {"type": "integer"}}, "required": ["command"], "additionalProperties": False}, runner.run),
            Tool("test", "Run an allowlisted test command.", {"type": "object", "properties": {"command": {"type": "array", "items": {"type": "string"}}, "timeout": {"type": "integer"}}, "required": ["command"], "additionalProperties": False}, runner.run),
        ])
    if allowed_tools is not None:
        available = {tool.name for tool in tools}
        unknown = allowed_tools - available
        if unknown:
            raise ValueError(f"unavailable tools: {', '.join(sorted(unknown))}")
        tools = [tool for tool in tools if tool.name in allowed_tools]
    return ToolRegistry(tools)
