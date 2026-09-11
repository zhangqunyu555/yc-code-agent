import json
import tempfile
import unittest
from pathlib import Path

from yc_code_agent.context import ContextLimitExceeded, ContextManager
from yc_code_agent.core import Agent
from yc_code_agent.tools import ToolRegistry


def interaction(identifier, content):
    return [{"role": "assistant", "content": "", "tool_calls": [
        {"id": identifier, "function": {"name": "read", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": identifier, "content": content}]


class ContextTest(unittest.TestCase):
    def test_budget_keeps_task_and_latest_group_without_mutating_history(self):
        with tempfile.TemporaryDirectory() as directory:
            context = ContextManager(1200, directory)
            messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "original task"}]
            messages += interaction("old", "x" * 10000) + interaction("latest", "test failed " * 30)
            original = json.dumps(messages)
            view, info = context.prepare(messages, [])
            self.assertLessEqual(context.size(view, []), 1200)
            self.assertEqual(json.dumps(messages), original)
            self.assertEqual(view[1]["content"], "original task")
            self.assertEqual(view[-1]["tool_call_id"], "latest")
            self.assertEqual(info["omitted_groups"], 1)
            artifact = info["tool_artifacts"][0]
            result = json.loads(context.read_artifact(artifact, offset=9000, limit=100))
            self.assertEqual(result["text"], "x" * 100)
            self.assertEqual(result["total_chars"], 10000)

    def test_schema_and_arguments_count_toward_budget(self):
        context = ContextManager(1000)
        with self.assertRaises(ContextLimitExceeded):
            context.prepare([{"role": "user", "content": "task"}], [{"description": "x" * 1100}])
        messages = [{"role": "user", "content": "task"}] + interaction("id", "ok")
        messages[1]["tool_calls"][0]["function"]["arguments"] = "x" * 1100
        with self.assertRaises(ContextLimitExceeded):
            context.prepare(messages, [])

    def test_artifact_path_and_symlink_escape(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            context = ContextManager(1000, directory)
            with self.assertRaises(ValueError):
                context.read_artifact("../secret")
            identifier = "a" * 64
            secret = Path(outside, "secret.txt")
            secret.write_text("secret")
            Path(directory, identifier + ".txt").symlink_to(secret)
            with self.assertRaises(PermissionError):
                context.read_artifact(identifier)

    def test_orphan_and_missing_tool_results_rejected(self):
        context = ContextManager(1000)
        with self.assertRaises(ValueError):
            context.prepare([{"role": "tool", "tool_call_id": "missing", "content": "x"}], [])
        with self.assertRaises(ValueError):
            context.prepare(interaction("id", "ok")[:1], [])

    def test_context_limit_stops_before_provider_request(self):
        class NeverProvider:
            def complete(self, *args):
                raise AssertionError("must not send oversized request")
        with self.assertRaises(ContextLimitExceeded) as raised:
            Agent(NeverProvider(), ToolRegistry(), max_context_chars=1000).run("x" * 1200)
        self.assertEqual(raised.exception.result.model_calls, 0)
