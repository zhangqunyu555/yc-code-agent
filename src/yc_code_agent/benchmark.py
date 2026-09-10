"""Reproducible four-profile benchmark for the coding agent."""

from __future__ import annotations

import difflib
import json
import math
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from .core import Agent
from .task_catalog import TASKS, Task
from .tools import CommandRunner, ToolRegistry, Workspace, build_tools
from .trace import JsonlTrace


PROFILES = ("direct", "read_only", "tool", "tool_retry")


def _snapshot(root: Path) -> dict[str, str]:
    result = {}
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if path.is_file() and not any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
            try:
                result[str(relative)] = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
    return result


def _changed(before: dict[str, str], after: dict[str, str]) -> tuple[list[str], int]:
    paths = sorted(set(before) | set(after))
    changed = [path for path in paths if before.get(path) != after.get(path)]
    lines = 0
    for path in changed:
        diff = difflib.ndiff(before.get(path, "").splitlines(), after.get(path, "").splitlines())
        lines += sum(line.startswith(("+ ", "- ")) for line in diff)
    return changed, lines


def _apply_json_edits(answer: str, workspace: Workspace) -> None:
    start, end = answer.find("{"), answer.rfind("}")
    if start < 0 or end < start:
        raise ValueError("final answer does not contain an edit JSON object")
    payload = json.loads(answer[start : end + 1])
    edits = payload.get("edits")
    if not isinstance(edits, list):
        raise ValueError("final JSON must contain an edits array")
    for edit in edits:
        workspace.edit(edit["path"], edit["old"], edit["new"])


def _evaluate(task: Task, root: Path, execution_mode: str) -> tuple[bool, str]:
    hidden = root / ".yc_eval"
    hidden.mkdir()
    (hidden / "test_hidden.py").write_text(task.hidden_test, encoding="utf-8")
    try:
        output = CommandRunner(Workspace(root), execution_mode).run(
            ["python3", "-m", "unittest", "discover", "-s", ".yc_eval", "-q"], 30
        )
        return output.startswith("exit_code=0\n"), output
    finally:
        shutil.rmtree(hidden)


def _prompt(task: Task, profile: str) -> str:
    base = f"任务 {task.id}：{task.prompt}"
    if profile == "direct":
        base += f"\n\nsolution.py:\n```python\n{task.source}```"
    if profile in {"direct", "read_only"}:
        base += '\n只返回 JSON：{"edits":[{"path":"solution.py","old":"精确原文","new":"替换文本"}]}。'
    else:
        base += "\n使用工具检查并修复 solution.py。只允许修改 solution.py，不要创建或修改测试文件；修复并运行一次测试后停止，简短说明结果。"
    return base


def run_task(
    task: Task,
    profile: str,
    provider_factory: Callable[[], Any],
    *,
    execution_mode: str = "sandbox",
    trace_dir: str | Path | None = None,
    sample_id: int = 0,
) -> dict[str, Any]:
    if profile not in PROFILES:
        raise ValueError(f"unknown profile: {profile}")
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix=f"{task.id}-") as temporary:
        root = Path(temporary)
        (root / "solution.py").write_text(task.source, encoding="utf-8")
        (root / "tests").mkdir()
        (root / "tests" / "test_public.py").write_text(task.public_test, encoding="utf-8")
        before = _snapshot(root)
        workspace = Workspace(root)
        provider = provider_factory()
        read_only = profile == "read_only"
        if profile == "direct":
            registry = ToolRegistry()
        elif read_only:
            registry = build_tools(
                root,
                execution_mode=execution_mode,
                read_only=True,
                allowed_tools={"list_files", "read", "search"},
            )
        else:
            registry = build_tools(
                root,
                execution_mode=execution_mode,
                allowed_tools={"list_files", "read", "search", "edit", "test"},
                writable_paths=set(task.allowed_paths),
            )
        trace_path = Path(trace_dir) / f"{task.id}-{profile}-sample-{sample_id}.jsonl" if trace_dir else None
        agent = Agent(provider, registry, max_steps=1 if profile == "direct" else 12, trace=JsonlTrace(trace_path) if trace_path else None)
        rounds = 2 if profile == "tool_retry" else 1
        history = None
        totals = {"model_calls": 0, "tool_calls": 0, "input_tokens": 0, "output_tokens": 0}
        success = first_success = False
        output = error = ""

        for round_number in range(1, rounds + 1):
            prompt = _prompt(task, profile) if round_number == 1 else f"外部测试失败，请继续修复。测试输出：\n{output}"
            try:
                result = agent.run(prompt, history=history)
                history = result.messages
                for key in totals:
                    totals[key] += getattr(result, key)
                if profile in {"direct", "read_only"}:
                    _apply_json_edits(result.answer, workspace)
                success, output = _evaluate(task, root, execution_mode)
                first_success = success if round_number == 1 else first_success
                if success:
                    error = ""
                    break
            except Exception as exc:
                partial = getattr(exc, "result", None)
                if partial:
                    for key in totals:
                        totals[key] += getattr(partial, key)
                error = str(exc)
                output = error

        after = _snapshot(root)
        changed_files, changed_lines = _changed(before, after)
        irrelevant = [path for path in changed_files if path not in task.allowed_paths]
        elapsed_ms = int((time.monotonic() - started) * 1000)
        reward_components = {
            "tests": 1.0 if success else 0.0,
            "changed_lines": -0.002 * changed_lines,
            "tool_calls": -0.001 * totals["tool_calls"],
            "irrelevant_files": -0.1 * len(irrelevant),
        }
        reward = sum(reward_components.values())
        return {
            "task_id": task.id,
            "profile": profile,
            "sample_id": sample_id,
            "success": success,
            "first_success": first_success,
            "rounds": round_number,
            **totals,
            "elapsed_ms": elapsed_ms,
            "changed_files": changed_files,
            "changed_lines": changed_lines,
            "irrelevant_changes": irrelevant,
            "reward": round(reward, 6),
            "reward_components": {key: round(value, 6) for key, value in reward_components.items()},
            "test_output": output[-2000:],
            "error": error,
            "trace": str(trace_path) if trace_path else None,
        }


def validate_catalog(*, execution_mode: str = "sandbox") -> list[dict[str, Any]]:
    results = []
    for task in TASKS:
        with tempfile.TemporaryDirectory(prefix=f"validate-{task.id}-") as temporary:
            root = Path(temporary)
            (root / "solution.py").write_text(task.source, encoding="utf-8")
            (root / "tests").mkdir()
            (root / "tests" / "test_public.py").write_text(task.public_test, encoding="utf-8")
            Workspace(root).edit("solution.py", task.old, task.new)
            success, output = _evaluate(task, root, execution_mode)
            results.append({"task_id": task.id, "success": success, "test_output": output[-1000:]})
    return results


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        groups.setdefault(result["profile"], []).append(result)
    report = {}
    for profile, rows in groups.items():
        tasks: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            tasks.setdefault(row["task_id"], []).append(row)
        report[profile] = {
            "rollouts": len(rows),
            "tasks": len(tasks),
            "samples_per_task": max(len(samples) for samples in tasks.values()),
            "success_rate": sum(row["success"] for row in rows) / len(rows),
            "pass_at_k": sum(any(row["success"] for row in samples) for samples in tasks.values()) / len(tasks),
            "first_success_rate": sum(row["first_success"] for row in rows) / len(rows),
            "avg_reward": sum(row["reward"] for row in rows) / len(rows),
            "avg_tool_calls": sum(row["tool_calls"] for row in rows) / len(rows),
            "avg_latency_ms": sum(row["elapsed_ms"] for row in rows) / len(rows),
            "total_input_tokens": sum(row["input_tokens"] for row in rows),
            "total_output_tokens": sum(row["output_tokens"] for row in rows),
            "irrelevant_change_tasks": sum(bool(row["irrelevant_changes"]) for row in rows),
        }
    return report


def load_trajectory(path: str | Path) -> list[dict[str, Any]]:
    """Reconstruct the observable rollout messages from an append-only trace."""
    messages: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record["event"] == "agent_start":
            messages.append({"role": "user", "content": record["prompt"]})
        elif record["event"] == "model_end":
            messages.append(record["reply"])
        elif record["event"] == "tool_end":
            messages.append({
                "role": "tool",
                "tool_call_id": record.get("call_id", ""),
                "content": record["result"],
            })
    return messages


def build_rollout_dataset(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Export verifier-labelled episodes without assuming a specific RL trainer."""
    episodes = []
    for row in results:
        trace = row.get("trace")
        if not trace or not Path(trace).is_file():
            continue
        episodes.append({
            "task_id": row["task_id"],
            "profile": row["profile"],
            "sample_id": row.get("sample_id", 0),
            "messages": load_trajectory(trace),
            "reward": row["reward"],
            "reward_components": row.get("reward_components", {}),
            "success": row["success"],
        })
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for episode in episodes:
        groups.setdefault((episode["task_id"], episode["profile"]), []).append(episode)
    for samples in groups.values():
        mean = sum(row["reward"] for row in samples) / len(samples)
        variance = sum((row["reward"] - mean) ** 2 for row in samples) / len(samples)
        std = math.sqrt(variance)
        for row in samples:
            row["group_reward_mean"] = round(mean, 6)
            row["group_reward_std"] = round(std, 6)
            row["advantage"] = round((row["reward"] - mean) / std, 6) if std else 0.0
    return episodes


def build_preferences(results: list[dict[str, Any]], *, include_messages: bool = False) -> list[dict[str, Any]]:
    """Choose contrasting rollouts that used the same task prompt and tool profile."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for result in results:
        if result.get("trace"):
            groups.setdefault((result["task_id"], result["profile"]), []).append(result)
    pairs = []
    for (task_id, profile), rows in groups.items():
        ranked = sorted(rows, key=lambda row: (row["success"], row["reward"]), reverse=True)
        if len(ranked) >= 2 and ranked[0]["reward"] != ranked[-1]["reward"]:
            pair = {
                "task_id": task_id,
                "profile": profile,
                "chosen_trace": ranked[0]["trace"],
                "chosen_reward": ranked[0]["reward"],
                "rejected_trace": ranked[-1]["trace"],
                "rejected_reward": ranked[-1]["reward"],
            }
            if include_messages:
                pair["chosen"] = load_trajectory(ranked[0]["trace"])
                pair["rejected"] = load_trajectory(ranked[-1]["trace"])
            pairs.append(pair)
    return pairs
