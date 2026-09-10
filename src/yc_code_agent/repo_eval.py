"""Isolated, manifest-driven evaluation for repository repair tasks."""

from __future__ import annotations

import difflib
import json
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .core import Agent, AgentResult
from .tools import CommandRunner, Workspace, build_tools
from .trace import JsonlTrace


IGNORED_PARTS = {".git", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv", "__pycache__"}
REPO_PROFILES = ("tool", "tool_retrieval")


@dataclass(frozen=True, slots=True)
class RepoTask:
    id: str
    prompt: str
    allowed_paths: tuple[str, ...]
    verifier_command: tuple[str, ...]
    verifier_files: tuple[dict[str, str], ...]
    setup_edits: tuple[dict[str, str], ...] = ()
    public_test_command: tuple[str, ...] = ()
    source: dict[str, Any] | None = None

    @classmethod
    def load(cls, path: str | Path) -> "RepoTask":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        required = {"id", "prompt", "allowed_paths", "verifier_command", "verifier_files"}
        missing = sorted(required - data.keys())
        if missing:
            raise ValueError(f"task manifest missing: {', '.join(missing)}")
        if not data["allowed_paths"]:
            raise ValueError("allowed_paths must not be empty")
        return cls(
            id=str(data["id"]),
            prompt=str(data["prompt"]),
            allowed_paths=tuple(map(str, data["allowed_paths"])),
            verifier_command=tuple(map(str, data["verifier_command"])),
            verifier_files=tuple(data["verifier_files"]),
            setup_edits=tuple(data.get("setup_edits", ())),
            public_test_command=tuple(map(str, data.get("public_test_command", ()))),
            source=data.get("source"),
        )


def _ignore(_directory: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name in IGNORED_PARTS or name == ".env" or (name.startswith(".env.") and name != ".env.example")
    }


def _snapshot(root: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part in IGNORED_PARTS for part in relative.parts) or path.is_symlink() or not path.is_file():
            continue
        if path.stat().st_size <= 1_000_000:
            files[str(relative)] = path.read_bytes()
    return files


def _patch(before: dict[str, bytes], after: dict[str, bytes]) -> tuple[list[str], str]:
    changed = sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))
    chunks: list[str] = []
    for path in changed:
        try:
            old = before.get(path, b"").decode("utf-8").splitlines(keepends=True)
            new = after.get(path, b"").decode("utf-8").splitlines(keepends=True)
        except UnicodeDecodeError:
            chunks.append(f"Binary file changed: {path}\n")
            continue
        chunks.extend(difflib.unified_diff(old, new, fromfile=f"a/{path}", tofile=f"b/{path}"))
    return changed, "".join(chunks)


def _check_commit(source_repo: Path, expected: str | None) -> str | None:
    if not expected:
        return None
    completed = subprocess.run(
        ["git", "-C", str(source_repo), "rev-parse", "HEAD"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
    )
    actual = completed.stdout.strip()
    if completed.returncode != 0 or actual != expected:
        raise ValueError(f"source commit mismatch: expected {expected}, got {actual or 'unavailable'}")
    return actual


def _with_verifier(root: Path, task: RepoTask, execution_mode: str) -> tuple[bool, str]:
    workspace = Workspace(root)
    created: list[Path] = []
    try:
        for item in task.verifier_files:
            path = workspace.path(str(item["path"]))
            if path.exists():
                raise ValueError(f"protected verifier path already exists: {item['path']}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(item["content"]), encoding="utf-8")
            created.append(path)
        output = CommandRunner(workspace, execution_mode).run(list(task.verifier_command))
        return output.startswith("exit_code=0\n"), output
    finally:
        for path in created:
            path.unlink(missing_ok=True)


def run_repo_task(
    task: RepoTask,
    source_repo: str | Path,
    provider_factory: Callable[[], Any],
    *,
    execution_mode: str = "sandbox",
    max_steps: int = 12,
    max_context_chars: int = 100_000,
    trace_path: str | Path | None = None,
    retrieval: bool = False,
) -> dict[str, Any]:
    """Run one repair in a disposable repository copy and return its evidence."""
    source_repo = Path(source_repo).expanduser().resolve()
    if not source_repo.is_dir():
        raise ValueError(f"source repo is not a directory: {source_repo}")
    expected_commit = str((task.source or {}).get("commit", "")) or None
    checked_commit = _check_commit(source_repo, expected_commit)
    started = time.monotonic()

    with tempfile.TemporaryDirectory(prefix=f"repo-eval-{task.id}-") as temporary:
        root = Path(temporary, "repo")
        shutil.copytree(source_repo, root, symlinks=True, ignore=_ignore)
        setup_workspace = Workspace(root)
        for edit in task.setup_edits:
            setup_workspace.edit(str(edit["path"]), str(edit["old"]), str(edit["new"]))

        initial_passed, initial_output = _with_verifier(root, task, execution_mode)
        if initial_passed:
            raise ValueError("invalid task: protected verifier passes before the agent runs")
        before = _snapshot(root)
        provider = provider_factory()
        trace = JsonlTrace(trace_path) if trace_path else None
        allowed_tools = {"list_files", "read", "search", "edit", "test"}
        if retrieval:
            allowed_tools.add("retrieve")
        tools = build_tools(
            root,
            execution_mode=execution_mode,
            allowed_tools=allowed_tools,
            writable_paths=set(task.allowed_paths),
        )
        agent = Agent(provider, tools, max_steps=max_steps, max_context_chars=max_context_chars, trace=trace)
        public_hint = ""
        if task.public_test_command:
            public_hint = f"\n可运行公开测试：{json.dumps(task.public_test_command, ensure_ascii=False)}"
        retrieval_hint = "优先用 retrieve 检索与问题相关的代码块。" if retrieval else ""
        prompt = (
            f"任务 {task.id}：{task.prompt}{public_hint}\n"
            f"只允许修改：{', '.join(task.allowed_paths)}。{retrieval_hint}只能通过 test 工具运行给定测试；修复、测试后立即停止。"
        )
        error = ""
        try:
            result = agent.run(prompt)
        except Exception as exc:
            error = str(exc)
            result = getattr(exc, "result", None) or AgentResult("", [], 0, 0, 0, 0, 0)
        after = _snapshot(root)
        changed_files, patch = _patch(before, after)
        success, verifier_output = _with_verifier(root, task, execution_mode)
        irrelevant = [path for path in changed_files if path not in task.allowed_paths]
        return {
            "task_id": task.id,
            "source": task.source or {},
            "checked_commit": checked_commit,
            "model": getattr(provider, "model", provider.__class__.__name__),
            "retrieval": retrieval,
            "success": success and not irrelevant,
            "initial_verifier": initial_output[-2000:],
            "final_verifier": verifier_output[-2000:],
            "changed_files": changed_files,
            "irrelevant_changes": irrelevant,
            "patch": patch,
            "model_calls": result.model_calls,
            "tool_calls": result.tool_calls,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "trace": str(trace_path) if trace_path else None,
            "messages": result.messages,
            "error": error,
        }


def summarize_repo_results(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for profile in REPO_PROFILES:
        rows = [row for row in results if row.get("profile") == profile]
        if not rows:
            continue
        summary[profile] = {
            "rollouts": len(rows),
            "tasks": len({row["task_id"] for row in rows}),
            "success_rate": sum(bool(row["success"]) for row in rows) / len(rows),
            "avg_model_calls": sum(row["model_calls"] for row in rows) / len(rows),
            "avg_tool_calls": sum(row["tool_calls"] for row in rows) / len(rows),
            "avg_input_tokens": sum(row["input_tokens"] for row in rows) / len(rows),
            "avg_output_tokens": sum(row["output_tokens"] for row in rows) / len(rows),
            "avg_elapsed_ms": sum(row["elapsed_ms"] for row in rows) / len(rows),
            "irrelevant_change_rate": sum(bool(row["irrelevant_changes"]) for row in rows) / len(rows),
        }
    return summary
