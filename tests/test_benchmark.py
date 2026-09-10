import json
import tempfile
import unittest

from yc_code_agent.benchmark import build_preferences, build_rollout_dataset, run_task, summarize, validate_catalog
from yc_code_agent.task_catalog import TASKS


class OracleProvider:
    last_usage = {"prompt_tokens": 10, "completion_tokens": 5}

    def __init__(self, task):
        self.task = task

    def complete(self, messages, tools):
        edit = {"path": "solution.py", "old": self.task.old, "new": self.task.new}
        return {"role": "assistant", "content": json.dumps({"edits": [edit]})}


class ToolRepairProvider:
    last_usage = {}

    def __init__(self, task):
        self.task = task
        self.step = 0

    def complete(self, messages, tools):
        self.step += 1
        if self.step == 1:
            name, arguments = "read", {"path": "solution.py"}
        elif self.step == 2:
            name, arguments = "edit", {"path": "solution.py", "old": self.task.old, "new": self.task.new}
        elif self.step == 3:
            name, arguments = "test", {"command": ["python3", "-m", "unittest", "discover", "-s", "tests", "-q"]}
        else:
            return {"role": "assistant", "content": "fixed and tested"}
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": str(self.step), "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}],
        }


class ToolLoopProvider:
    last_usage = {}

    def complete(self, messages, tools):
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": str(len(messages)), "type": "function", "function": {"name": "read", "arguments": json.dumps({"path": "solution.py"})}}],
        }


class BenchmarkTest(unittest.TestCase):
    def test_catalog_has_twenty_unique_valid_tasks(self):
        self.assertEqual(len(TASKS), 20)
        self.assertEqual(len({task.id for task in TASKS}), 20)
        results = validate_catalog(execution_mode="local")
        self.assertTrue(all(row["success"] for row in results), results)

    def test_direct_profile_and_summary(self):
        task = TASKS[0]
        result = run_task(task, "direct", lambda: OracleProvider(task), execution_mode="local")
        self.assertTrue(result["success"], result)
        self.assertTrue(result["first_success"])
        report = summarize([result])
        self.assertEqual(report["direct"]["success_rate"], 1.0)

    def test_tool_profile_runs_full_repair_loop(self):
        task = TASKS[0]
        result = run_task(task, "tool", lambda: ToolRepairProvider(task), execution_mode="local")
        self.assertTrue(result["success"], result)
        self.assertEqual(result["tool_calls"], 3)

    def test_step_limit_keeps_partial_usage_metrics(self):
        result = run_task(TASKS[0], "tool", ToolLoopProvider, execution_mode="local")
        self.assertFalse(result["success"])
        self.assertEqual(result["tool_calls"], 12)

    def test_rollout_export_and_pass_at_k(self):
        task = TASKS[0]
        with tempfile.TemporaryDirectory() as directory:
            passed = run_task(task, "direct", lambda: OracleProvider(task), execution_mode="local", trace_dir=directory, sample_id=0)
            failed = {**passed, "sample_id": 1, "success": False, "reward": -1.0}
            failed["trace"] = passed["trace"]
            report = summarize([passed, failed])["direct"]
            episodes = build_rollout_dataset([passed, failed])
            pairs = build_preferences([passed, failed], include_messages=True)
        self.assertEqual(report["pass_at_k"], 1.0)
        self.assertEqual(episodes[0]["messages"][0]["role"], "user")
        self.assertEqual([row["advantage"] for row in episodes], [1.0, -1.0])
        self.assertIn("chosen", pairs[0])


if __name__ == "__main__":
    unittest.main()
