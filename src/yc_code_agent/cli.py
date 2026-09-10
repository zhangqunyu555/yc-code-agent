"""Command-line interface."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from .benchmark import PROFILES, build_preferences, build_rollout_dataset, run_task, summarize, validate_catalog
from .core import Agent
from .providers import DemoProvider, OpenAICompatibleProvider
from .state import StateStore
from .task_catalog import TASKS
from .tools import build_tools
from .trace import JsonlTrace


def _provider_settings(args: argparse.Namespace) -> dict[str, object]:
    provider = args.provider or os.environ.get("YC_AGENT_PROVIDER", "deepseek")
    if provider == "deepseek":
        thinking = args.thinking or os.environ.get("DEEPSEEK_THINKING", "disabled")
        settings = {
            "model": args.model or os.environ.get("YC_AGENT_MODEL", "deepseek-v4-flash"),
            "base_url": args.base_url or os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            "api_key_env": "DEEPSEEK_API_KEY",
            "request_options": {"thinking": {"type": thinking}},
        }
        if getattr(args, "temperature", None) is not None:
            settings["request_options"]["temperature"] = args.temperature
        return settings
    model = args.model or os.environ.get("YC_AGENT_MODEL")
    if not model:
        raise SystemExit("openai-compatible provider requires --model or YC_AGENT_MODEL")
    settings = {
        "model": model,
        "base_url": args.base_url or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        "api_key_env": "OPENAI_API_KEY",
    }
    if getattr(args, "temperature", None) is not None:
        settings["request_options"] = {"temperature": args.temperature}
    return settings


def _provider(args: argparse.Namespace) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        **_provider_settings(args),
    )


def _add_provider_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--provider", choices=("deepseek", "openai-compatible"))
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--thinking", choices=("enabled", "disabled"))
    parser.add_argument("--temperature", type=float)


def _write_json(path: str | Path, data: object) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="yc-agent", description="Small auditable coding agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("demo", help="run an offline tool-call demo")

    run = subparsers.add_parser("run", help="run against an OpenAI-compatible API")
    run.add_argument("prompt", nargs="?")
    run.add_argument("--workspace", default=".")
    _add_provider_args(run)
    run.add_argument("--execution", choices=("sandbox", "local", "disabled"), default="sandbox")
    run.add_argument("--max-steps", type=int, default=12)
    run.add_argument("--trace")
    run.add_argument("--session")
    run.add_argument("--next-goal", action="store_true")
    run.add_argument("--state-db", default=".yc-agent/state.db")

    validate = subparsers.add_parser("validate", help="verify all 20 benchmark reference fixes")
    validate.add_argument("--execution", choices=("sandbox", "local"), default="sandbox")
    validate.add_argument("--output", default="outputs/oracle-validation.json")

    benchmark = subparsers.add_parser("benchmark", help="compare agent profiles with a real model API")
    _add_provider_args(benchmark)
    benchmark.add_argument("--profiles", default=",".join(PROFILES))
    benchmark.add_argument("--limit", type=int, default=len(TASKS))
    benchmark.add_argument("--samples", type=int, default=1, help="independent rollouts per task/profile")
    benchmark.add_argument("--execution", choices=("sandbox", "local"), default="sandbox")
    benchmark.add_argument("--output")

    preferences = subparsers.add_parser("preferences", help="derive trace preference pairs from benchmark results")
    preferences.add_argument("input")
    preferences.add_argument("--output", default="outputs/preferences.json")

    dataset = subparsers.add_parser("dataset", help="export rollout and preference JSONL for post-training")
    dataset.add_argument("input")
    dataset.add_argument("--output-dir", default="datasets/latest")

    goal = subparsers.add_parser("goal", help="manage the durable FIFO goal queue")
    goal.add_argument("action", choices=("add", "list"))
    goal.add_argument("text", nargs="?")
    goal.add_argument("--state-db", default=".yc-agent/state.db")

    args = parser.parse_args(argv)
    if args.command == "demo":
        with tempfile.TemporaryDirectory(prefix="yc-agent-demo-") as temporary:
            Path(temporary, "hello.txt").write_text("hello YC-Code Agent\n", encoding="utf-8")
            result = Agent(DemoProvider(), build_tools(temporary, execution_mode="disabled", read_only=True)).run("读取 hello.txt")
            print(result.answer)
        return

    if args.command == "run":
        if bool(args.prompt) == bool(args.next_goal):
            raise SystemExit("provide either a prompt or --next-goal")
        store = StateStore(args.state_db) if args.session or args.next_goal else None
        goal_row = store.claim_next() if args.next_goal and store else None
        if args.next_goal and not goal_row:
            if store:
                store.close()
            raise SystemExit("goal queue is empty")
        prompt = goal_row["text"] if goal_row else args.prompt
        session_id = args.session or (f"goal-{goal_row['id']}" if goal_row else None)
        history = store.load_session(session_id) if store and session_id else None
        trace = args.trace or f"traces/{datetime.now().strftime('%Y%m%d-%H%M%S')}.jsonl"
        try:
            agent = Agent(
                _provider(args),
                build_tools(args.workspace, execution_mode=args.execution),
                max_steps=args.max_steps,
                trace=JsonlTrace(trace),
            )
            result = agent.run(prompt, history=history)
            if store and session_id:
                store.save_session(session_id, result.messages)
            if store and goal_row:
                store.finish(goal_row["id"], success=True)
            print(result.answer)
            print(f"\nmodel_calls={result.model_calls} tool_calls={result.tool_calls} elapsed_ms={result.elapsed_ms} trace={trace}")
        except Exception:
            if store and goal_row:
                store.finish(goal_row["id"], success=False)
            raise
        finally:
            if store:
                store.close()
        return

    if args.command == "validate":
        results = validate_catalog(execution_mode=args.execution)
        payload = {"catalog_size": len(results), "passed": sum(row["success"] for row in results), "results": results}
        _write_json(args.output, payload)
        print(json.dumps({"catalog_size": payload["catalog_size"], "passed": payload["passed"], "output": args.output}, ensure_ascii=False))
        raise SystemExit(0 if payload["passed"] == payload["catalog_size"] else 1)

    if args.command == "preferences":
        payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
        pairs = build_preferences(payload["results"])
        _write_json(args.output, {"pairs": pairs})
        print(json.dumps({"pairs": len(pairs), "output": args.output}, ensure_ascii=False))
        return

    if args.command == "dataset":
        payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        episodes = build_rollout_dataset(payload["results"])
        pairs = build_preferences(payload["results"], include_messages=True)
        for name, rows in (("rollouts.jsonl", episodes), ("preferences.jsonl", pairs)):
            with (output_dir / name).open("w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        _write_json(output_dir / "manifest.json", {
            "source": str(Path(args.input)),
            "model": payload.get("model"),
            "rollouts": len(episodes),
            "preference_pairs": len(pairs),
        })
        print(json.dumps({"output_dir": str(output_dir), "rollouts": len(episodes), "preference_pairs": len(pairs)}, ensure_ascii=False))
        return

    if args.command == "goal":
        with StateStore(args.state_db) as store:
            if args.action == "add":
                if not args.text:
                    raise SystemExit("goal add requires text")
                print(json.dumps({"id": store.enqueue(args.text), "status": "queued"}, ensure_ascii=False))
            else:
                print(json.dumps(store.list_goals(), ensure_ascii=False, indent=2))
        return

    profiles = [profile.strip() for profile in args.profiles.split(",") if profile.strip()]
    unknown = set(profiles) - set(PROFILES)
    if unknown:
        raise SystemExit(f"unknown profiles: {', '.join(sorted(unknown))}")
    if args.samples < 1:
        raise SystemExit("--samples must be positive")
    output = args.output or f"benchmark-results/{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    trace_dir = str(Path(output).with_suffix("")) + "-traces"
    rows = []
    for task in TASKS[: args.limit]:
        for profile in profiles:
            for sample_id in range(args.samples):
                rows.append(run_task(task, profile, lambda: _provider(args), execution_mode=args.execution, trace_dir=trace_dir, sample_id=sample_id))
                print(f"{task.id} {profile} sample={sample_id}: {'PASS' if rows[-1]['success'] else 'FAIL'}")
    provider_settings = _provider_settings(args)
    payload = {
        "created_at": datetime.now().astimezone().isoformat(),
        "model": provider_settings["model"],
        "profiles": profiles,
        "samples": args.samples,
        "task_count": min(args.limit, len(TASKS)),
        "task_ids": [task.id for task in TASKS[: args.limit]],
        "config": {
            "provider": args.provider or os.environ.get("YC_AGENT_PROVIDER", "deepseek"),
            "base_url": provider_settings["base_url"],
            "request_options": provider_settings.get("request_options", {}),
            "execution": args.execution,
            "step_budget": {"direct": 1, "agent": 12},
        },
        "summary": summarize(rows),
        "results": rows,
    }
    _write_json(output, payload)
    print(json.dumps({"output": output, "summary": payload["summary"]}, ensure_ascii=False, indent=2))
