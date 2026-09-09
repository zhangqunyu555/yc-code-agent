"""Deterministic demo and OpenAI-compatible chat-completions providers."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from .core import RetryableProviderError


class DemoProvider:
    """Offline provider proving one read-tool round trip."""

    last_usage: dict[str, int] = {}

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        if messages[-1]["role"] == "tool":
            payload = json.loads(messages[-1]["content"])
            result = payload.get("result", payload.get("error", ""))
            return {"role": "assistant", "content": f"文件内容：\n{result}"}
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "demo_read_1",
                "type": "function",
                "function": {"name": "read", "arguments": json.dumps({"path": "hello.txt"})},
            }],
        }


class OpenAICompatibleProvider:
    def __init__(
        self,
        model: str,
        *,
        base_url: str = "https://api.openai.com/v1",
        api_key: str | None = None,
        api_key_env: str = "OPENAI_API_KEY",
        request_options: dict[str, Any] | None = None,
        timeout: int = 90,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.environ.get(api_key_env, "")
        self.api_key_env = api_key_env
        self.request_options = request_options or {}
        self.timeout = timeout
        self.last_usage: dict[str, int] = {}
        if not self.api_key:
            raise ValueError(f"missing API key; set {api_key_env}")

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": self.model, "messages": messages, **self.request_options}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.load(response)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")[:2000]
            error = RuntimeError(f"provider HTTP {exc.code}: {body}")
            if exc.code == 429 or exc.code >= 500:
                raise RetryableProviderError(str(error)) from exc
            raise error from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RetryableProviderError(f"provider connection failed: {exc}") from exc

        try:
            message = data["choices"][0]["message"]
            reply: dict[str, Any] = {"role": "assistant", "content": message.get("content") or ""}
            if message.get("reasoning_content") is not None:
                reply["reasoning_content"] = message["reasoning_content"]
            if message.get("tool_calls"):
                reply["tool_calls"] = message["tool_calls"]
            self.last_usage = data.get("usage") or {}
            return reply
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"invalid provider response: {data}") from exc
