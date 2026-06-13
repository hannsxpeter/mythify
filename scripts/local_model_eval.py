#!/usr/bin/env python3
"""Run a local bare-vs-Mythify comparison with subscription-backed CLIs.

The harness creates two temporary copies of a tiny Python bug-fix task:

- bare: no protocol files, just the task prompt
- mythify: AGENTS.md plus scripts/mythify.py and protocol data, with .mythify initialized

It then runs a local worker engine in each workspace and verifies the result
with `python3 -m unittest`. The local engines use the user's installed CLIs
and existing logins. API keys are not required for claude-cli, codex-cli, or
cursor-agent.
"""

import argparse
import json
import os
import shutil
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ENGINES = ("claude-cli", "codex-cli", "cursor-agent", "command")
MYTHIFY_PROFILES = ("auto", "fast", "standard")
SPEED_LEVELS = ("auto", "standard", "fast")
DEFAULT_CLAUDE_PERMISSION_MODE = "bypassPermissions"
TAIL_CHARS = 4000


SCENARIOS = {
    "word_count_bugfix": {
        "title": "Whitespace word counter bug fix",
        "task": "Fix `word_count.py` so `python3 -m unittest` passes. Keep the implementation small. Do not edit the test file.",
        "files": {
            "word_count.py": "\n".join(
                [
                    "def count_words(text):",
                    "    \"\"\"Return the number of whitespace-separated words in text.\"\"\"",
                    "    if text is None:",
                    "        return 0",
                    "    return len(str(text).split(','))",
                    "",
                ]
            ),
            "test_word_count.py": "\n".join(
                [
                    "import unittest",
                    "",
                    "from word_count import count_words",
                    "",
                    "",
                    "class WordCountTests(unittest.TestCase):",
                    "    def test_counts_whitespace_words(self):",
                    "        self.assertEqual(count_words('alpha beta gamma'), 3)",
                    "",
                    "    def test_collapses_repeated_spaces(self):",
                    "        self.assertEqual(count_words('alpha   beta'), 2)",
                    "",
                    "    def test_empty_text_is_zero(self):",
                    "        self.assertEqual(count_words('   '), 0)",
                    "",
                    "",
                    "if __name__ == '__main__':",
                    "    unittest.main()",
                    "",
                ]
            ),
        },
    },
    "query_parser_bugfix": {
        "title": "URL query parser bug fix",
        "task": "Fix `query_parser.py` so `python3 -m unittest` passes. Preserve repeated keys as lists. Do not edit the test file.",
        "files": {
            "query_parser.py": "\n".join(
                [
                    "def parse_query(query):",
                    "    \"\"\"Parse a URL query string into a dict.\"\"\"",
                    "    result = {}",
                    "    for part in str(query).split(';'):",
                    "        if '=' in part:",
                    "            key, value = part.split('=', 1)",
                    "            result[key] = value",
                    "    return result",
                    "",
                ]
            ),
            "test_query_parser.py": "\n".join(
                [
                    "import unittest",
                    "",
                    "from query_parser import parse_query",
                    "",
                    "",
                    "class QueryParserTests(unittest.TestCase):",
                    "    def test_ampersand_separator_and_plus_decoding(self):",
                    "        self.assertEqual(parse_query('a=1&name=two+words'), {'a': '1', 'name': 'two words'})",
                    "",
                    "    def test_percent_decoding(self):",
                    "        self.assertEqual(parse_query('city=New%20York'), {'city': 'New York'})",
                    "",
                    "    def test_repeated_keys_become_lists(self):",
                    "        self.assertEqual(parse_query('tag=ai&tag=agents'), {'tag': ['ai', 'agents']})",
                    "",
                    "",
                    "if __name__ == '__main__':",
                    "    unittest.main()",
                    "",
                ]
            ),
        },
    },
    "inventory_total_bugfix": {
        "title": "Inventory total calculation bug fix",
        "task": "Fix `inventory.py` so `python3 -m unittest` passes. Handle quantities, missing quantities, and numeric strings. Do not edit the test file.",
        "files": {
            "inventory.py": "\n".join(
                [
                    "def total_cost(items):",
                    "    \"\"\"Return the total cost for a list of item dicts.\"\"\"",
                    "    return sum(item['price'] for item in items)",
                    "",
                ]
            ),
            "test_inventory.py": "\n".join(
                [
                    "import unittest",
                    "",
                    "from inventory import total_cost",
                    "",
                    "",
                    "class InventoryTests(unittest.TestCase):",
                    "    def test_multiplies_price_by_quantity(self):",
                    "        items = [{'price': 2.5, 'quantity': 4}, {'price': 3, 'quantity': 2}]",
                    "        self.assertEqual(total_cost(items), 16.0)",
                    "",
                    "    def test_missing_quantity_defaults_to_one(self):",
                    "        self.assertEqual(total_cost([{'price': 7}]), 7)",
                    "",
                    "    def test_numeric_strings_are_accepted(self):",
                    "        self.assertEqual(total_cost([{'price': '2.50', 'quantity': '3'}]), 7.5)",
                    "",
                    "",
                    "if __name__ == '__main__':",
                    "    unittest.main()",
                    "",
                ]
            ),
        },
    },
}


def repo_root():
    return Path(__file__).resolve().parent.parent


def tail(text):
    text = text or ""
    return text[-TAIL_CHARS:]


def resolve_binary(names, env_names):
    for env_name in env_names:
        value = os.environ.get(env_name, "").strip()
        if value:
            path = Path(value)
            if path.is_file() and os.access(str(path), os.X_OK):
                return str(path)
            return None
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    home = Path.home()
    fallbacks = []
    if "claude" in names:
        fallbacks.extend([home / ".claude" / "local" / "claude", Path("/opt/homebrew/bin/claude"), Path("/usr/local/bin/claude")])
    if "codex" in names:
        fallbacks.extend([home / ".local" / "bin" / "codex", Path("/opt/homebrew/bin/codex"), Path("/usr/local/bin/codex")])
    if "cursor-agent" in names:
        fallbacks.extend([home / ".local" / "bin" / "cursor-agent", Path("/opt/homebrew/bin/cursor-agent"), Path("/usr/local/bin/cursor-agent")])
    if "cursor" in names:
        fallbacks.extend([home / ".local" / "bin" / "cursor", Path("/opt/homebrew/bin/cursor"), Path("/usr/local/bin/cursor")])
    for candidate in fallbacks:
        if candidate.is_file() and os.access(str(candidate), os.X_OK):
            return str(candidate)
    return None


def run_process(args, cwd, prompt, timeout, env=None, shell=False):
    started = time.monotonic()
    try:
        result = subprocess.run(
            args,
            cwd=str(cwd),
            input=prompt,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=shell,
        )
        return {
            "exit_code": result.returncode,
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout_tail": tail(result.stdout),
            "stderr_tail": tail(result.stderr),
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "exit_code": -1,
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout_tail": tail(exc.stdout if isinstance(exc.stdout, str) else ""),
            "stderr_tail": tail(exc.stderr if isinstance(exc.stderr, str) else "") + "\n[FAIL] timed out",
            "timed_out": True,
        }


def shell_env():
    env = dict(os.environ)
    env["TERM"] = "dumb"
    return env


def create_task_workspace(parent, scenario_name, mode, iteration):
    if iteration == 1 and scenario_name == "word_count_bugfix":
        workspace = parent / mode
    else:
        workspace = parent / scenario_name / str(iteration) / mode
    workspace.mkdir(parents=True, exist_ok=True)
    scenario = SCENARIOS[scenario_name]
    for relative_path, content in scenario["files"].items():
        path = workspace / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content + "\n", encoding="utf-8")
    (workspace / "TASK.md").write_text(
        "\n".join(
            [
                "# Local Eval Task: " + scenario["title"],
                "",
                scenario["task"],
                "",
            ]
        ),
        encoding="utf-8",
    )
    return workspace


def install_mythify(workspace):
    root = repo_root()
    (workspace / "scripts").mkdir(exist_ok=True)
    (workspace / "protocol").mkdir(exist_ok=True)
    shutil.copy2(root / "AGENTS.md", workspace / "AGENTS.md")
    shutil.copy2(
        root / "protocol" / "operation-registry.json",
        workspace / "protocol" / "operation-registry.json",
    )
    shutil.copy2(root / "scripts" / "mythify.py", workspace / "scripts" / "mythify.py")
    init = subprocess.run(
        [sys.executable, "scripts/mythify.py", "init"],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if init.returncode != 0:
        raise RuntimeError("failed to initialize Mythify workspace: " + init.stderr)


def resolve_mythify_profile(profile, scenario_name):
    if profile != "auto":
        return profile
    if scenario_name in SCENARIOS:
        return "fast"
    return "standard"


def prompt_for(mode, scenario_name, mythify_profile="standard"):
    scenario = SCENARIOS[scenario_name]
    base = [
        "You are in a temporary workspace containing a small Python project.",
        "Scenario: " + scenario_name + " (" + scenario["title"] + ").",
        "Your task: " + scenario["task"],
        "Verification command: python3 -m unittest",
        "After your work, report the command you ran and whether it passed.",
    ]
    if mode == "mythify":
        base.extend(
            [
                "",
                "This workspace contains AGENTS.md and scripts/mythify.py.",
                "Follow the Mythify Protocol from AGENTS.md.",
            ]
        )
        if mythify_profile == "fast":
            base.extend(
                [
                    "Use the Mythify fast profile for this focused task.",
                    "Do not create plan state unless the task expands into multiple dependent steps.",
                    "Make the focused fix, then run `python3 scripts/mythify.py verify run \"python3 -m unittest\" --claim \"python3 -m unittest passes\"`.",
                    "Do not claim completion unless `verify run \"python3 -m unittest\"` records exit 0.",
                ]
            )
        else:
            base.extend(
                [
                    "Use the Mythify standard profile for this task.",
                    "Use `python3 scripts/mythify.py plan create`, step updates, and `verify run`.",
                    "Do not claim completion unless `verify run \"python3 -m unittest\"` records exit 0.",
                ]
            )
    return "\n".join(base) + "\n"


def run_claude(workspace, prompt, model, timeout, speed="auto"):
    binary = resolve_binary(["claude"], ["MYTHIFY_LOCAL_EVAL_CLAUDE_BIN", "MYTHIFY_FANOUT_CLAUDE_BIN"])
    if not binary:
        return {"exit_code": 127, "stdout_tail": "", "stderr_tail": "claude binary not found", "timed_out": False, "duration_seconds": 0}
    permission_mode = os.environ.get("MYTHIFY_LOCAL_EVAL_CLAUDE_PERMISSION_MODE", DEFAULT_CLAUDE_PERMISSION_MODE).strip()
    if not permission_mode:
        permission_mode = DEFAULT_CLAUDE_PERMISSION_MODE
    args = [
        binary,
        "-p",
        "--output-format",
        "json",
        "--permission-mode",
        permission_mode,
        "--no-session-persistence",
    ]
    if model:
        args.extend(["--model", model])
    args.extend(shlex.split(os.environ.get("MYTHIFY_LOCAL_EVAL_CLAUDE_ARGS", "")))
    result = run_process(args, workspace, prompt, timeout, shell_env())
    try:
        parsed = json.loads(result["stdout_tail"])
        if isinstance(parsed, dict) and isinstance(parsed.get("result"), str):
            result["model_output_tail"] = tail(parsed["result"])
    except ValueError:
        result["model_output_tail"] = result["stdout_tail"]
    return result


def run_codex(workspace, prompt, model, timeout, speed="auto"):
    binary = resolve_binary(["codex"], ["MYTHIFY_LOCAL_EVAL_CODEX_BIN", "MYTHIFY_FANOUT_CODEX_BIN"])
    if not binary:
        return {"exit_code": 127, "stdout_tail": "", "stderr_tail": "codex binary not found", "timed_out": False, "duration_seconds": 0}
    output_file = workspace / ".codex-last-message.md"
    args = [
        binary,
        "--ask-for-approval",
        "never",
        "exec",
        "--cd",
        str(workspace),
        "--sandbox",
        os.environ.get("MYTHIFY_LOCAL_EVAL_CODEX_SANDBOX", "workspace-write"),
        "--skip-git-repo-check",
        "--ephemeral",
        "--color",
        "never",
        "--output-last-message",
        str(output_file),
    ]
    if model:
        args.extend(["--model", model])
    args.extend(codex_speed_args(speed))
    args.extend(shlex.split(os.environ.get("MYTHIFY_LOCAL_EVAL_CODEX_ARGS", "")))
    args.append("-")
    result = run_process(args, workspace, prompt, timeout, shell_env())
    if output_file.exists():
        result["model_output_tail"] = tail(output_file.read_text(encoding="utf-8"))
    else:
        result["model_output_tail"] = result["stdout_tail"]
    return result


def codex_speed_args(speed):
    if speed == "fast":
        return ["-c", 'service_tier="fast"', "-c", "features.fast_mode=true"]
    if speed == "standard":
        return ["-c", "features.fast_mode=false"]
    return []


def run_cursor(workspace, prompt, model, timeout, speed="auto"):
    binary = resolve_binary(
        ["cursor-agent", "cursor"],
        ["MYTHIFY_LOCAL_EVAL_CURSOR_BIN", "MYTHIFY_FANOUT_CURSOR_BIN", "MYTHIFY_FANOUT_CURSOR_AGENT_BIN"],
    )
    if not binary:
        return {"exit_code": 127, "stdout_tail": "", "stderr_tail": "cursor-agent or cursor binary not found", "timed_out": False, "duration_seconds": 0}
    prompt_file = workspace / ".cursor-task.md"
    prompt_file.write_text(prompt, encoding="utf-8")
    args = [binary]
    if Path(binary).name == "cursor":
        args.append("agent")
    args.extend(["--print", "--output-format", "text", "--trust", "--workspace", str(workspace)])
    mode = os.environ.get("MYTHIFY_LOCAL_EVAL_CURSOR_MODE", "")
    if mode:
        args.extend(["--mode", mode])
    if model:
        args.extend(["--model", model])
    if os.environ.get("MYTHIFY_LOCAL_EVAL_CURSOR_FORCE", "1") == "1":
        args.append("--force")
    args.extend(shlex.split(os.environ.get("MYTHIFY_LOCAL_EVAL_CURSOR_ARGS", "")))
    args.append("Read the task prompt from this file and complete it: " + str(prompt_file))
    result = run_process(args, workspace, "", timeout, shell_env())
    result["model_output_tail"] = result["stdout_tail"]
    return result


def run_command(workspace, prompt, model, timeout, speed="auto"):
    command = os.environ.get("MYTHIFY_LOCAL_EVAL_COMMAND", "").strip()
    if not command:
        return {"exit_code": 127, "stdout_tail": "", "stderr_tail": "MYTHIFY_LOCAL_EVAL_COMMAND is not set", "timed_out": False, "duration_seconds": 0}
    env = shell_env()
    env["MYTHIFY_LOCAL_EVAL_MODEL"] = model or ""
    env["MYTHIFY_LOCAL_EVAL_SPEED"] = speed or "auto"
    result = run_process(command, workspace, prompt, timeout, env, shell=True)
    result["model_output_tail"] = result["stdout_tail"]
    return result


def run_engine(engine, workspace, prompt, model, timeout, speed="auto"):
    if engine == "claude-cli":
        return run_claude(workspace, prompt, model, timeout, speed)
    if engine == "codex-cli":
        return run_codex(workspace, prompt, model, timeout, speed)
    if engine == "cursor-agent":
        return run_cursor(workspace, prompt, model, timeout, speed)
    if engine == "command":
        return run_command(workspace, prompt, model, timeout, speed)
    raise ValueError("unknown engine: " + engine)


def count_mythify_records(workspace):
    state = workspace / ".mythify"
    verifications = 0
    plans = 0
    verification_path = state / "verifications.jsonl"
    if verification_path.is_file():
        with verification_path.open("r", encoding="utf-8") as handle:
            verifications = sum(1 for line in handle if line.strip())
    plans_dir = state / "plans"
    if plans_dir.is_dir():
        plans = len([p for p in plans_dir.glob("*.json") if p.is_file()])
    return {"verifications": verifications, "plans": plans}


def verify_workspace(workspace, timeout):
    return run_process([sys.executable, "-m", "unittest"], workspace, "", timeout, shell_env())


def run_one(mode, engine, model, speed, parent, timeout, scenario_name, iteration, mythify_profile="auto"):
    workspace = create_task_workspace(parent, scenario_name, mode, iteration)
    resolved_profile = ""
    if mode == "mythify":
        resolved_profile = resolve_mythify_profile(mythify_profile, scenario_name)
        install_mythify(workspace)
    prompt = prompt_for(mode, scenario_name, resolved_profile or "standard")
    model_result = run_engine(engine, workspace, prompt, model, timeout, speed)
    verification = verify_workspace(workspace, 120)
    records = count_mythify_records(workspace)
    return {
        "scenario": scenario_name,
        "iteration": iteration,
        "mode": mode,
        "mythify_profile": resolved_profile,
        "model": model or "",
        "speed": speed or "auto",
        "workspace": str(workspace),
        "model_exit_code": model_result["exit_code"],
        "model_duration_seconds": model_result["duration_seconds"],
        "model_stdout_tail": model_result.get("model_output_tail", model_result.get("stdout_tail", "")),
        "model_stderr_tail": model_result.get("stderr_tail", ""),
        "verify_exit_code": verification["exit_code"],
        "verify_stdout_tail": verification["stdout_tail"],
        "verify_stderr_tail": verification["stderr_tail"],
        "mythify_records": records,
    }


def mythify_evidence_ok(run):
    if run["mode"] != "mythify":
        return False
    records = run["mythify_records"]
    if records["verifications"] <= 0:
        return False
    if run.get("mythify_profile") == "fast":
        return True
    return records["plans"] > 0


def summarize_runs(runs):
    summary = {}
    for mode in ("bare", "mythify"):
        selected = [run for run in runs if run["mode"] == mode]
        attempted = len(selected)
        model_success = sum(1 for run in selected if run["model_exit_code"] == 0)
        verified_success = sum(1 for run in selected if run["verify_exit_code"] == 0)
        evidence_success = sum(1 for run in selected if mythify_evidence_ok(run))
        total_duration = sum(run["model_duration_seconds"] for run in selected)
        summary[mode] = {
            "attempted": attempted,
            "model_success": model_success,
            "verified_success": verified_success,
            "evidence_success": evidence_success,
            "verified_success_rate": round(verified_success / attempted, 3) if attempted else 0,
            "evidence_success_rate": round(evidence_success / attempted, 3) if attempted else 0,
            "avg_model_duration_seconds": round(total_duration / attempted, 3) if attempted else 0,
        }
    bare_rate = summary["bare"]["verified_success_rate"]
    mythify_rate = summary["mythify"]["verified_success_rate"]
    if mythify_rate > bare_rate:
        winner = "mythify"
    elif bare_rate > mythify_rate:
        winner = "bare"
    else:
        winner = "tie"
    summary["winner_by_verified_success_rate"] = winner
    return summary


def verified_task_success_effect(summary):
    bare = summary["bare"]
    mythify = summary["mythify"]
    bare_rate = bare["verified_success_rate"]
    mythify_rate = mythify["verified_success_rate"]
    delta = round(mythify_rate - bare_rate, 3)
    if delta > 0:
        conclusion = "improved"
    elif delta < 0:
        conclusion = "regressed"
    else:
        conclusion = "no_change"
    return {
        "metric": "verified_success_rate",
        "comparison": "mythify_vs_bare",
        "evidence_source": "per-workspace python3 -m unittest exit code",
        "bare_attempted": bare["attempted"],
        "mythify_attempted": mythify["attempted"],
        "paired_task_count": min(bare["attempted"], mythify["attempted"]),
        "bare_verified_success_rate": bare_rate,
        "mythify_verified_success_rate": mythify_rate,
        "verified_success_rate_delta": delta,
        "winner": summary["winner_by_verified_success_rate"],
        "conclusion": conclusion,
        "mythify_evidence_success_rate": mythify["evidence_success_rate"],
        "avg_model_duration_delta_seconds": round(
            mythify["avg_model_duration_seconds"] - bare["avg_model_duration_seconds"],
            3,
        ),
        "statistical_strength": "local_smoke",
        "caveat": "Built-in scenarios are a rerunnable smoke signal, not a large benchmark.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run a local bare-vs-Mythify model comparison using installed CLI subscriptions."
    )
    parser.add_argument("--engine", choices=ENGINES, default="codex-cli")
    parser.add_argument("--model", default="", help="Model for both runs unless overridden.")
    parser.add_argument("--bare-model", default="", help="Model for the bare run.")
    parser.add_argument("--mythify-model", default="", help="Model for the Mythify run.")
    parser.add_argument("--speed", choices=SPEED_LEVELS, default="auto", help="Codex speed for both runs unless overridden.")
    parser.add_argument("--bare-speed", choices=SPEED_LEVELS, default="", help="Codex speed for the bare run.")
    parser.add_argument("--mythify-speed", choices=SPEED_LEVELS, default="", help="Codex speed for the Mythify run.")
    parser.add_argument(
        "--scenario",
        choices=tuple(SCENARIOS.keys()) + ("all",),
        default="word_count_bugfix",
        help="Task scenario to run, or all for the built-in benchmark set.",
    )
    parser.add_argument("--repeat", type=int, default=1, help="Run each selected scenario this many times.")
    parser.add_argument("--list-scenarios", action="store_true", help="List built-in scenarios and exit.")
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--keep-workspaces", action="store_true")
    parser.add_argument("--json-output", default="", help="Optional path for the JSON report.")
    parser.add_argument("--require-pass", action="store_true", help="Exit 1 unless both external verifications pass and Mythify records evidence.")
    parser.add_argument(
        "--mythify-profile",
        choices=MYTHIFY_PROFILES,
        default="auto",
        help="Mythify prompt profile for protocol runs. Auto uses fast for built-in focused bugfix scenarios.",
    )
    args = parser.parse_args(argv)

    if args.list_scenarios:
        for name, scenario in SCENARIOS.items():
            print(name + ": " + scenario["title"])
        return 0
    if args.repeat < 1:
        print("[FAIL] --repeat must be at least 1", file=sys.stderr)
        return 1

    parent = Path(tempfile.mkdtemp(prefix="mythify-local-eval-"))
    try:
        bare_model = args.bare_model or args.model
        mythify_model = args.mythify_model or args.model
        bare_speed = args.bare_speed or args.speed
        mythify_speed = args.mythify_speed or args.speed
        scenario_names = list(SCENARIOS.keys()) if args.scenario == "all" else [args.scenario]
        runs = []
        for scenario_name in scenario_names:
            for iteration in range(1, args.repeat + 1):
                runs.append(run_one("bare", args.engine, bare_model, bare_speed, parent, args.timeout, scenario_name, iteration))
                runs.append(
                    run_one(
                        "mythify",
                        args.engine,
                        mythify_model,
                        mythify_speed,
                        parent,
                        args.timeout,
                        scenario_name,
                        iteration,
                        args.mythify_profile,
                    )
                )
        summary = summarize_runs(runs)
        report = {
            "engine": args.engine,
            "scenario": args.scenario,
            "scenario_count": len(scenario_names),
            "repeat": args.repeat,
            "mythify_profile": args.mythify_profile,
            "bare_speed": bare_speed,
            "mythify_speed": mythify_speed,
            "workspaces_root": str(parent),
            "summary": summary,
            "verified_task_success": verified_task_success_effect(summary),
            "runs": runs,
        }
        text = json.dumps(report, indent=2)
        if args.json_output:
            Path(args.json_output).write_text(text + "\n", encoding="utf-8")
        print(text)
        failed = any(run["verify_exit_code"] != 0 for run in report["runs"])
        mythify_runs = [run for run in report["runs"] if run["mode"] == "mythify"]
        missing_evidence = any(not mythify_evidence_ok(run) for run in mythify_runs)
        if args.require_pass and (failed or missing_evidence):
            return 1
        return 0
    finally:
        if not args.keep_workspaces:
            shutil.rmtree(str(parent), ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
