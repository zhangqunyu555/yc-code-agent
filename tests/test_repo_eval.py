import json
import tempfile
import unittest
from pathlib import Path

from yc_code_agent.repo_eval import RepoTask, run_repo_task, summarize_repo_results


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

    def test_repository_summary_separates_retrieval_ablation(self):
        base = {"task_id": "t1", "success": True, "model_calls": 2, "tool_calls": 3,
                "input_tokens": 100, "output_tokens": 20, "elapsed_ms": 10, "irrelevant_changes": []}
        summary = summarize_repo_results([
            {**base, "profile": "tool"},
            {**base, "profile": "tool_retrieval", "tool_calls": 2},
        ])
        self.assertEqual(summary["tool"]["avg_tool_calls"], 3)
        self.assertEqual(summary["tool_retrieval"]["avg_tool_calls"], 2)


if __name__ == "__main__":
    unittest.main()
