import json
import tempfile
import unittest
from pathlib import Path

from yc_code_agent.repo_eval import RepoTask, run_repo_task


ROOT = Path(__file__).resolve().parents[1]


class RepairProvider:
    model = "offline-repair"
    last_usage = {"prompt_tokens": 10, "completion_tokens": 5}

    def complete(self, messages, tools):
        if messages[-1]["role"] == "tool":
            return {"role": "assistant", "content": "fixed"}
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "repair",
                "type": "function",
                "function": {
                    "name": "edit",
                    "arguments": json.dumps({
                        "path": "calculator.py",
                        "old": "return max(value, low)",
                        "new": "return min(max(value, low), high)",
                    }),
                },
            }],
        }


class RepoEvalTest(unittest.TestCase):
    def test_isolated_task_records_patch_and_protects_source(self):
        task = RepoTask.load(ROOT / "examples/tasks/clamp-upper-bound.json")
        source = ROOT / "examples/sample_repo"
        original = (source / "calculator.py").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as temporary:
            trace = Path(temporary, "trace.jsonl")
            result = run_repo_task(
                task,
                source,
                RepairProvider,
                execution_mode="local",
                trace_path=trace,
            )
        self.assertTrue(result["success"])
        self.assertIn("calculator.py", result["changed_files"])
        self.assertIn("+    return min(max(value, low), high)", result["patch"])
        self.assertFalse(result["irrelevant_changes"])
        self.assertEqual((source / "calculator.py").read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
