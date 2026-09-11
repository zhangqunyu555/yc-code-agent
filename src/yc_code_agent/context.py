"""Bounded request views; the original conversation stays intact for audit/resume."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from pathlib import Path
from typing import Any


class ContextLimitExceeded(RuntimeError):
    result: Any = None


class ContextManager:
    def __init__(self, limit: int, artifact_dir: str | Path | None = None) -> None:
        self.limit = limit
        self.artifact_dir = Path(artifact_dir) if artifact_dir else None

    def _save(self, content: str) -> str:
        if self.artifact_dir is None:
            self.artifact_dir = Path(tempfile.mkdtemp(prefix="yc-context-"))
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        identifier = hashlib.sha256(content.encode()).hexdigest()
        path = self.artifact_dir / f"{identifier}.txt"
        if path.is_symlink():
            raise PermissionError("artifact must not be a symlink")
        if not path.exists():
            with path.open("x", encoding="utf-8") as handle:
                handle.write(content)
        return identifier

    def read_artifact(self, artifact_id: str, offset: int = 0, limit: int = 1000) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", artifact_id):
            raise ValueError("invalid artifact ID")
        if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 2000:
            raise ValueError("offset must be non-negative; limit must be 1..2000 characters")
        if self.artifact_dir is None:
            raise FileNotFoundError(artifact_id)
        root = self.artifact_dir.resolve()
        path = root / f"{artifact_id}.txt"
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise PermissionError("artifact escapes store")
        content = path.read_text(encoding="utf-8")
        return json.dumps({"offset": offset, "total_chars": len(content),
                           "text": content[offset:offset + limit]}, ensure_ascii=False)

    @staticmethod
    def size(messages: list[dict], tools: list[dict]) -> int:
        # Character budget, not a model-token estimate. Includes tool schemas and arguments.
        return len(json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False))

    def prepare(self, messages: list[dict], tools: list[dict]) -> tuple[list[dict], dict]:
        view = [dict(message) for message in messages]
        before = self.size(view, tools)
        artifacts = []
        snippet_limit = min(4000, max(256, self.limit // 8))
        for message in view:
            content = message.get("content")
            if message.get("role") == "tool" and isinstance(content, str) and len(content) > snippet_limit:
                identifier = self._save(content)
                keep = snippet_limit // 2
                message["content"] = (
                    f"{content[:keep]}\n...[tool output compacted; artifact_id={identifier}; "
                    f"use read_artifact with character offset/limit]...\n{content[-keep:]}"
                )
                artifacts.append(identifier)

        # A group is one assistant message plus all its tool results. Never split it.
        groups: list[list[dict]] = []
        for message in view:
            if message.get("role") == "tool":
                if not groups or groups[-1][0].get("role") != "assistant":
                    raise ValueError("orphan tool result in history")
                groups[-1].append(message)
            else:
                groups.append([message])
        for group in groups:
            calls = group[0].get("tool_calls") or []
            expected = [call.get("id") for call in calls]
            actual = [message.get("tool_call_id") for message in group[1:]]
            if len(set(expected)) != len(expected) or sorted(expected) != sorted(actual):
                raise ValueError("tool calls/results must be paired with unique IDs")

        droppable = [group for group in groups if group[0].get("role") == "assistant"][:-1]
        omitted = []
        for group in droppable:
            if self.size(view, tools) <= self.limit:
                break
            omitted.append(group)
            groups.remove(group)
            view = [message for kept in groups for message in kept]
        if self.size(view, tools) > self.limit:
            raise ContextLimitExceeded("context budget cannot fit task, tool schemas and latest complete interaction")
        archive = self._save(json.dumps(omitted, ensure_ascii=False)) if omitted else None
        return view, {"before_chars": before, "after_chars": self.size(view, tools),
                      "omitted_groups": len(omitted), "omitted_artifact": archive,
                      "tool_artifacts": artifacts, "artifact_dir": str(self.artifact_dir) if self.artifact_dir else None}
