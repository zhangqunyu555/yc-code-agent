import json
import io
import os
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from yc_code_agent.core import Agent, RetryableProviderError, StepLimitExceeded
from yc_code_agent.cli import _provider_settings
from yc_code_agent.providers import DemoProvider, OpenAICompatibleProvider
from yc_code_agent.tools import ToolRegistry, build_tools


class StaticProvider:
    last_usage = {"prompt_tokens": 3, "completion_tokens": 2}

    def complete(self, messages, tools):
        return {"role": "assistant", "content": "done"}


class LoopProvider:
    last_usage = {}

    def complete(self, messages, tools):
        return {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "type": "function", "function": {"name": "missing", "arguments": "{}"}}]}


class RetryProvider:
    last_usage = {}

    def __init__(self):
        self.calls = 0

    def complete(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            raise RetryableProviderError("temporary")
        return {"role": "assistant", "content": "recovered"}


class ContextProvider:
    last_usage = {}

    def __init__(self):
        self.calls = 0
        self.observed = ""

    def complete(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return {"role": "assistant", "content": "", "tool_calls": [{"id": "read", "type": "function", "function": {"name": "read", "arguments": json.dumps({"path": "large.txt"})}}]}
        self.observed = messages[-1]["content"]
        return {"role": "assistant", "content": "done"}


class AgentTest(unittest.TestCase):
    def test_final_answer_and_usage(self):
        result = Agent(StaticProvider(), ToolRegistry()).run("go")
        self.assertEqual(result.answer, "done")
        self.assertEqual((result.model_calls, result.input_tokens, result.output_tokens), (1, 3, 2))

    def test_demo_tool_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "hello.txt").write_text("hello agent", encoding="utf-8")
            result = Agent(DemoProvider(), build_tools(directory, execution_mode="disabled", read_only=True)).run("read")
        self.assertIn("hello agent", result.answer)
        self.assertEqual((result.model_calls, result.tool_calls), (2, 1))
        self.assertEqual(result.messages[-2]["tool_call_id"], "demo_read_1")

    def test_step_limit(self):
        with self.assertRaises(StepLimitExceeded) as raised:
            Agent(LoopProvider(), ToolRegistry(), max_steps=2).run("loop")
        self.assertEqual(raised.exception.result.tool_calls, 2)

    def test_transient_retry(self):
        provider = RetryProvider()
        result = Agent(provider, ToolRegistry(), provider_retries=1).run("go")
        self.assertEqual(result.answer, "recovered")
        self.assertEqual(provider.calls, 2)

    def test_large_tool_output_is_compacted_before_next_model_call(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "large.txt").write_text("x" * 5000, encoding="utf-8")
            provider = ContextProvider()
            result = Agent(provider, build_tools(directory, execution_mode="disabled", read_only=True),
                           max_context_chars=4000, artifact_dir=str(Path(directory, "artifacts"))).run("read")
            self.assertIn("x" * 5000, result.messages[-2]["content"])
        self.assertIn("tool output compacted", provider.observed)
        self.assertLess(len(provider.observed), 1000)

    def test_deepseek_payload_and_reasoning_round_trip(self):
        captured = {}
        response = {
            "choices": [{"message": {"content": "", "reasoning_content": "plan", "tool_calls": []}}],
            "usage": {"prompt_tokens": 4, "completion_tokens": 2},
        }

        def fake_urlopen(request, timeout):
            captured.update(json.loads(request.data))
            return io.BytesIO(json.dumps(response).encode())

        provider = OpenAICompatibleProvider(
            "deepseek-v4-flash",
            base_url="https://api.deepseek.com",
            api_key="test-key",
            request_options={"thinking": {"type": "enabled"}},
        )
        with patch("urllib.request.urlopen", fake_urlopen):
            reply = provider.complete([{"role": "user", "content": "hi"}], [])
        self.assertEqual(captured["thinking"], {"type": "enabled"})
        self.assertEqual(reply["reasoning_content"], "plan")

    def test_deepseek_is_the_cli_default(self):
        args = Namespace(provider=None, model=None, base_url=None, thinking=None)
        with patch.dict(os.environ, {}, clear=True):
            settings = _provider_settings(args)
        self.assertEqual(settings["model"], "deepseek-v4-flash")
        self.assertEqual(settings["base_url"], "https://api.deepseek.com")
        self.assertEqual(settings["api_key_env"], "DEEPSEEK_API_KEY")


if __name__ == "__main__":
    unittest.main()
