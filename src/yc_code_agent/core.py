"""The model → tool → model control loop."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Protocol

from .context import ContextLimitExceeded, ContextManager
from .tools import Tool


class RetryableProviderError(RuntimeError):
    """A transient model-provider failure."""


class StepLimitExceeded(RuntimeError):
    """The model kept requesting tools beyond the configured budget."""

    def __init__(self, message: str, result: Any | None = None) -> None:
        super().__init__(message)
        self.result = result


class Provider(Protocol):
    last_usage: dict[str, int]

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]: ...


class Trace(Protocol):
    def write(self, event: str, **data: Any) -> None: ...


@dataclass(slots=True)
class AgentResult:
    answer: str
    messages: list[dict[str, Any]]
    model_calls: int
    tool_calls: int
    input_tokens: int
    output_tokens: int
    elapsed_ms: int


class Agent:
    def __init__(
        self,
        provider: Provider,
        tools: Any,
        *,
        system_prompt: str = (
            "You are a careful coding agent. Use search and narrow line ranges before reading large files. "
            "Run only commands allowed by the tool schema, modify production files only unless asked, "
            "run the relevant test after editing, and stop when it passes."
        ),
        max_steps: int = 12,
        provider_retries: int = 2,
        max_context_chars: int = 100_000,
        trace: Trace | None = None,
        artifact_dir: str | None = None,
    ) -> None:
        if max_steps < 1 or provider_retries < 0 or max_context_chars < 1000:
            raise ValueError("max_steps must be positive, retries non-negative, and context at least 1000 chars")
        self.provider = provider
        self.tools = tools
        self.system_prompt = system_prompt
        self.max_steps = max_steps
        self.provider_retries = provider_retries
        self.max_context_chars = max_context_chars
        self.trace = trace
        trace_path = getattr(trace, "path", None)
        self.context = ContextManager(max_context_chars, artifact_dir or (str(trace_path) + ".artifacts" if trace_path else None))
        if self.tools.specs():
            self.tools = self.tools.with_tool(Tool(
                "read_artifact", "Read a character range of an archived tool result by its artifact ID.",
                {"type": "object", "properties": {"artifact_id": {"type": "string"},
                 "offset": {"type": "integer"}, "limit": {"type": "integer"}},
                 "required": ["artifact_id"], "additionalProperties": False}, self.context.read_artifact,
            ))

    def _event(self, event: str, **data: Any) -> None:
        if self.trace:
            self.trace.write(event, **data)

    def _complete(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        for attempt in range(self.provider_retries + 1):
            try:
                return self.provider.complete(messages, self.tools.specs())
            except RetryableProviderError as exc:
                self._event("provider_retry", attempt=attempt + 1, error=str(exc))
                if attempt == self.provider_retries:
                    raise
                time.sleep(0.25 * (2**attempt))
        raise AssertionError("unreachable")

    def run(
        self,
        prompt: str,
        *,
        history: list[dict[str, Any]] | None = None,
    ) -> AgentResult:
        started = time.monotonic()
        messages = list(history or [{"role": "system", "content": self.system_prompt}])
        messages.append({"role": "user", "content": prompt})
        model_calls = tool_calls = input_tokens = output_tokens = 0
        self._event("agent_start", prompt=prompt)

        for step in range(1, self.max_steps + 1):
            try:
                request_messages, context_info = self.context.prepare(messages, self.tools.specs())
            except ContextLimitExceeded as exc:
                exc.result = AgentResult("", messages, model_calls, tool_calls, input_tokens, output_tokens,
                                         int((time.monotonic() - started) * 1000))
                self._event("agent_error", error="context_limit", detail=str(exc))
                raise
            self._event("model_request", step=step, messages=request_messages,
                        tools=self.tools.specs(), context=context_info)
            reply = self._complete(request_messages)
            if reply.get("role") != "assistant":
                raise ValueError("provider reply must have role='assistant'")
            messages.append(reply)
            model_calls += 1
            usage = getattr(self.provider, "last_usage", {}) or {}
            input_tokens += int(usage.get("prompt_tokens", usage.get("input_tokens", 0)))
            output_tokens += int(usage.get("completion_tokens", usage.get("output_tokens", 0)))
            calls = reply.get("tool_calls") or []
            self._event("model_end", step=step, reply=reply, usage=usage)

            if not calls:
                answer = reply.get("content") or ""
                elapsed = int((time.monotonic() - started) * 1000)
                self._event("agent_end", answer=answer, elapsed_ms=elapsed)
                return AgentResult(answer, messages, model_calls, tool_calls, input_tokens, output_tokens, elapsed)

            for call in calls:
                call_id = str(call.get("id", ""))
                function = call.get("function") or {}
                name = str(function.get("name", ""))
                raw_arguments = function.get("arguments", "{}")
                arguments: dict[str, Any] = {}
                try:
                    arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
                    if not isinstance(arguments, dict):
                        raise ValueError("arguments must be a JSON object")
                    content = self.tools.execute(name, arguments)
                except Exception as exc:  # Tool failures are observations for the model.
                    content = json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
                tool_calls += 1
                messages.append({"role": "tool", "tool_call_id": call_id, "content": content})
                self._event("tool_end", step=step, call_id=call_id, tool=name, arguments=arguments, result=content)

        self._event("agent_error", error="step_limit", max_steps=self.max_steps)
        elapsed = int((time.monotonic() - started) * 1000)
        partial = AgentResult("", messages, model_calls, tool_calls, input_tokens, output_tokens, elapsed)
        raise StepLimitExceeded(f"agent exceeded {self.max_steps} model steps", partial)
