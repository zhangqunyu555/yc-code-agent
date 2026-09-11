"""Offline scripted repair: demonstrates wiring, NOT model capability."""

import json
from pathlib import Path
from uuid import uuid4

from yc_code_agent.repo_eval import RepoTask, run_repo_task


class ScriptedRepair:
    model = "scripted-known-fix-not-an-llm"
    last_usage = {}

    def __init__(self):
        self.steps = iter([
            ("read", {"path": "calculator.py"}),
            ("edit", {"path": "calculator.py", "old": "return max(value, low)",
                      "new": "return min(max(value, low), high)"}),
            ("test", {"command": ["python3", "-m", "unittest", "discover", "-s", "tests", "-q"]}),
        ])
        self.count = 0

    def complete(self, messages, tools):
        step = next(self.steps, None)
        if step is None:
            return {"role": "assistant", "content": "Submitted known clamp repair; see verifier for correctness."}
        name, arguments = step
        self.count += 1
        return {"role": "assistant", "content": "", "tool_calls": [{
            "id": str(self.count), "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)},
        }]}


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / "traces" / f"walkthrough-{uuid4().hex}"
    output.mkdir(parents=True)
    result = run_repo_task(
        RepoTask.load(root / "examples/tasks/clamp-upper-bound.json"),
        root / "examples/sample_repo", ScriptedRepair,
        execution_mode="local", trace_path=output / "trace.jsonl",
    )
    result["evidence_type"] = "offline-scripted-harness-check"
    (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"success": result["success"], "model": result["model"],
                      "patch": result["patch"], "result": str(output / "result.json")}, ensure_ascii=False, indent=2))
    if not result["success"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
