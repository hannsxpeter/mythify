#!/usr/bin/env python3
"""Run a local bare-vs-Mythify comparison with subscription-backed CLIs.

The harness creates two temporary copies of a tiny Python bug-fix task:

- bare: no protocol files, just the task prompt
- mythify: AGENTS.md plus CLI scripts and protocol data, with .mythify initialized

It then runs a local worker engine in each workspace and verifies the result
with `python3 -m unittest`. The local engines use the user's installed CLIs
and existing logins. API keys are not required for claude-cli, codex-cli, or
cursor-agent.
"""

import argparse
import json
import math
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
BILLING_POSTURES = (
    "unknown",
    "subscription_included_authentication",
    "metered_api",
    "local_compute",
)
MEASUREMENT_STATUSES = ("unknown", "not_measured", "unavailable", "measured")
DEFAULT_CLAUDE_PERMISSION_MODE = "bypassPermissions"
TAIL_CHARS = 4000


SCENARIOS = {
    "word_count_bugfix": {
        "title": "Whitespace word counter bug fix",
        "task_category": "string_processing_bugfix",
        "local_model_roles": ["reader", "triage"],
        "local_model_fit_reason": "small deterministic Python function with local tests and limited context",
        "fanout_fit": "waste_candidate",
        "fanout_fit_reason": "single focused bug fix with one tiny implementation surface and one local verifier",
        "fanout_merge_verifier": "python3 -m unittest",
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
        "task_category": "standard_library_bugfix",
        "local_model_roles": ["reader", "triage"],
        "local_model_fit_reason": "small parser task with explicit expected behavior and local tests",
        "fanout_fit": "waste_candidate",
        "fanout_fit_reason": "single parser fix with no independent subtasks to merge before verification",
        "fanout_merge_verifier": "python3 -m unittest",
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
        "task_category": "numeric_data_bugfix",
        "local_model_roles": ["reader", "triage"],
        "local_model_fit_reason": "small data-shape bug with explicit fixtures and local tests",
        "fanout_fit": "waste_candidate",
        "fanout_fit_reason": "single numeric bug fix where extra workers would duplicate context without independent outputs",
        "fanout_merge_verifier": "python3 -m unittest",
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


FANOUT_VALUE_POLICY = [
    {
        "task_shape": "independent_surface_mapping",
        "fanout_fit": "helps",
        "decision_rule": "Use fanout when two or more self-contained code, docs, or adapter surfaces can be inspected without waiting on each other.",
        "value_signal": "distinct worker material can be merged, then checked by one orchestrator-run verifier",
        "cost_signal": "each task is a fresh worker call, so keep context narrow and task prompts independent",
        "verification_boundary": "worker output is material until the orchestrator merges it and runs verify_run or outcome_check",
    },
    {
        "task_shape": "parallel_research_or_comparison",
        "fanout_fit": "helps",
        "decision_rule": "Use fanout when independent sources, host adapters, or benchmark variants can be compared side by side.",
        "value_signal": "parallel reads reduce wall-clock time and make disagreement visible before implementation",
        "cost_signal": "each source or variant spends separate quota or local compute",
        "verification_boundary": "claims still need source links, command evidence, or a merged executable check",
    },
    {
        "task_shape": "single_focused_bugfix",
        "fanout_fit": "wastes",
        "decision_rule": "Avoid fanout for one small implementation surface with one direct verifier.",
        "value_signal": "a single worker can make the edit and run the verifier",
        "cost_signal": "extra workers duplicate prompt context and consume quota without independent outputs",
        "verification_boundary": "run the local verifier once after the focused edit",
    },
    {
        "task_shape": "dependent_sequence",
        "fanout_fit": "wastes",
        "decision_rule": "Avoid fanout when each step depends on the previous step's concrete output.",
        "value_signal": "sequential host work preserves ordering and reduces merge confusion",
        "cost_signal": "parallel workers would speculate, then require reconciliation work",
        "verification_boundary": "advance one step at a time and verify each completion claim",
    },
]


ROLE_STRENGTH_POLICY = [
    {
        "role": "session",
        "purpose": "current conversation",
        "default_strength": "host_selected",
        "stronger_model_requirement": "not_applicable",
        "stronger_model_allowed": "host_controls_current_chat",
        "evidence_boundary": "Mythify may recommend a host model but the host applies or confirms the current chat model.",
    },
    {
        "role": "triage",
        "purpose": "problem framing",
        "default_strength": "cheap_or_fast",
        "stronger_model_requirement": "not_required",
        "stronger_model_allowed": "no_default_stronger_path",
        "evidence_boundary": "Triage is advisory material and stays cheap unless explicitly configured outside this harness.",
    },
    {
        "role": "reader",
        "purpose": "read-only material inspection",
        "default_strength": "local_or_privacy_preferred",
        "stronger_model_requirement": "not_required",
        "stronger_model_allowed": "no_default_stronger_path",
        "evidence_boundary": "Reader output is material, not verification evidence.",
    },
    {
        "role": "fanout_worker",
        "purpose": "independent subtask",
        "default_strength": "same_or_lower",
        "stronger_model_requirement": "not_required",
        "stronger_model_allowed": "only_with_spawn_ceiling_allow_stronger",
        "evidence_boundary": "Worker output is material and must be merged, then verified by commands.",
    },
    {
        "role": "reviewer",
        "purpose": "independent review",
        "default_strength": "same_or_lower",
        "stronger_model_requirement": "conditional_not_default",
        "stronger_model_allowed": "reviewer_strength_allow_stronger_and_reviewer_allow_stronger",
        "evidence_boundary": "Only reviewer tasks get the scoped stronger-model opt-in without broad worker escalation.",
    },
    {
        "role": "verifier",
        "purpose": "evidence",
        "default_strength": "local_command",
        "stronger_model_requirement": "not_model_based",
        "stronger_model_allowed": "no",
        "evidence_boundary": "Verifier evidence comes from executable commands and exit codes, not model strength.",
    },
]


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
    shutil.copy2(
        root / "protocol" / "classification-rules.json",
        workspace / "protocol" / "classification-rules.json",
    )
    shutil.copy2(
        root / "protocol" / "workflow-router.json",
        workspace / "protocol" / "workflow-router.json",
    )
    shutil.copy2(
        root / "protocol" / "release-gates.json",
        workspace / "protocol" / "release-gates.json",
    )
    shutil.copy2(root / "scripts" / "mythify.py", workspace / "scripts" / "mythify.py")
    for source in sorted((root / "scripts").glob("mythify_*.py")):
        shutil.copy2(source, workspace / "scripts" / source.name)
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


def comparison_conditions():
    """Describe the control and treatment without relying on model output."""
    return {
        "bare": {
            "steering": "task_prompt_only",
            "mythify_installed": False,
            "protocol_files": [],
            "required_external_verifier": "python3 -m unittest",
        },
        "mythify": {
            "steering": "mythify_protocol",
            "mythify_installed": True,
            "protocol_files": ["AGENTS.md", "scripts/mythify.py", "protocol/*.json"],
            "required_external_verifier": "python3 -m unittest",
        },
    }


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


def count_mythify_records(workspace, expected_command):
    state = workspace / ".mythify"
    verifications = 0
    passing_expected_verifications = 0
    plans = 0
    verification_path = state / "verifications.jsonl"
    if verification_path.is_file():
        with verification_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                verifications += 1
                try:
                    record = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if (
                    isinstance(record, dict)
                    and record.get("kind") == "executed"
                    and record.get("verified") is True
                    and record.get("exit_code") == 0
                    and record.get("command") == expected_command
                ):
                    passing_expected_verifications += 1
    plans_dir = state / "plans"
    if plans_dir.is_dir():
        plans = len([p for p in plans_dir.glob("*.json") if p.is_file()])
    return {
        "verifications": verifications,
        "passing_expected_verifications": passing_expected_verifications,
        "plans": plans,
    }


def verify_workspace(workspace, timeout):
    return run_process([sys.executable, "-m", "unittest"], workspace, "", timeout, shell_env())


def run_one(mode, engine, model, speed, parent, timeout, scenario_name, iteration, mythify_profile="auto"):
    workspace = create_task_workspace(parent, scenario_name, mode, iteration)
    scenario = SCENARIOS[scenario_name]
    resolved_profile = ""
    if mode == "mythify":
        resolved_profile = resolve_mythify_profile(mythify_profile, scenario_name)
        install_mythify(workspace)
    prompt = prompt_for(mode, scenario_name, resolved_profile or "standard")
    model_result = run_engine(engine, workspace, prompt, model, timeout, speed)
    verification = verify_workspace(workspace, 120)
    expected_command = scenario.get("fanout_merge_verifier", "python3 -m unittest")
    records = count_mythify_records(workspace, expected_command)
    return {
        "scenario": scenario_name,
        "task_category": scenario.get("task_category", "unknown"),
        "local_model_candidate_roles": list(scenario.get("local_model_roles", [])),
        "fanout_fit": scenario.get("fanout_fit", "unknown"),
        "fanout_fit_reason": scenario.get("fanout_fit_reason", ""),
        "fanout_merge_verifier": scenario.get("fanout_merge_verifier", ""),
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
    if records.get("passing_expected_verifications", 0) <= 0:
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


def completion_claimed(run):
    return run["model_exit_code"] == 0


def false_completion_claims_effect(runs):
    modes = {}
    for mode in ("bare", "mythify"):
        selected = [run for run in runs if run["mode"] == mode]
        attempted = len(selected)
        completion_claims = [run for run in selected if completion_claimed(run)]
        false_claims = [
            run
            for run in completion_claims
            if run["verify_exit_code"] != 0
        ]
        verifier_backed_claims = [
            run
            for run in completion_claims
            if run["verify_exit_code"] == 0
        ]
        claim_count = len(completion_claims)
        modes[mode] = {
            "attempted": attempted,
            "completion_claims": claim_count,
            "verifier_backed_claims": len(verifier_backed_claims),
            "false_completion_claims": len(false_claims),
            "no_completion_signal": attempted - claim_count,
            "false_completion_rate": round(len(false_claims) / claim_count, 3) if claim_count else 0,
        }
    bare_rate = modes["bare"]["false_completion_rate"]
    mythify_rate = modes["mythify"]["false_completion_rate"]
    delta = round(mythify_rate - bare_rate, 3)
    if mythify_rate < bare_rate:
        winner = "mythify"
        conclusion = "improved"
    elif bare_rate < mythify_rate:
        winner = "bare"
        conclusion = "regressed"
    else:
        winner = "tie"
        conclusion = "no_change"
    return {
        "metric": "false_completion_rate",
        "comparison": "mythify_vs_bare",
        "completion_signal": "model_exit_code_0",
        "evidence_source": "model process exit code 0 compared with per-workspace python3 -m unittest exit code",
        "bare_false_completion_rate": bare_rate,
        "mythify_false_completion_rate": mythify_rate,
        "false_completion_rate_delta": delta,
        "winner_by_lower_false_completion_rate": winner,
        "conclusion": conclusion,
        "modes": modes,
        "statistical_strength": "local_smoke",
        "caveat": "Completion claims are bounded to model process exit code 0; output text is retained for audit but not tone-scored.",
    }


def duration_ratio(numerator, denominator):
    if denominator == 0:
        return None
    return round(numerator / denominator, 3)


def profile_overhead_effect(summary, runs):
    bare = summary["bare"]
    mythify = summary["mythify"]
    bare_avg = bare["avg_model_duration_seconds"]
    mythify_avg = mythify["avg_model_duration_seconds"]
    delta = round(mythify_avg - bare_avg, 3)
    if delta > 0:
        observed_lower = "bare"
    elif delta < 0:
        observed_lower = "mythify"
    else:
        observed_lower = "tie"

    profiles = {}
    for run in runs:
        if run["mode"] != "mythify":
            continue
        profile = run.get("mythify_profile") or "unknown"
        profiles.setdefault(profile, []).append(run)
    profile_rows = {}
    for profile, selected in sorted(profiles.items()):
        attempted = len(selected)
        avg_duration = round(
            sum(run["model_duration_seconds"] for run in selected) / attempted,
            3,
        ) if attempted else 0
        profile_rows[profile] = {
            "attempted": attempted,
            "avg_model_duration_seconds": avg_duration,
            "delta_vs_bare_avg_seconds": round(avg_duration - bare_avg, 3),
            "ratio_vs_bare_avg": duration_ratio(avg_duration, bare_avg),
        }

    return {
        "metric": "avg_model_duration_seconds",
        "comparison": "mythify_profile_vs_bare",
        "evidence_source": "measured model process duration_seconds from local harness subprocess runs",
        "bare_avg_model_duration_seconds": bare_avg,
        "mythify_avg_model_duration_seconds": mythify_avg,
        "avg_model_duration_delta_seconds": delta,
        "avg_model_duration_ratio": duration_ratio(mythify_avg, bare_avg),
        "observed_lower_duration_mode": observed_lower,
        "inference": "inconclusive",
        "conclusion": "no_claim",
        "profiles": profile_rows,
        "bare_speed": "",
        "mythify_speed": "",
        "statistical_strength": "local_smoke",
        "caveat": "Durations are descriptive local subprocess measurements only; this smoke sample supports no categorical speed winner.",
    }


def build_cost_metadata(
    billing_posture,
    monetary_cost_status,
    monetary_cost_dollars,
    subscription_quota_status,
):
    if billing_posture not in BILLING_POSTURES:
        raise ValueError("invalid billing posture")
    if monetary_cost_status not in MEASUREMENT_STATUSES:
        raise ValueError("invalid monetary cost measurement status")
    if subscription_quota_status not in MEASUREMENT_STATUSES:
        raise ValueError("invalid subscription quota measurement status")
    if monetary_cost_status == "measured":
        if monetary_cost_dollars is None:
            raise ValueError("measured monetary cost requires a dollar value")
        value = float(monetary_cost_dollars)
        if not math.isfinite(value) or value < 0:
            raise ValueError("monetary cost dollars must be finite and nonnegative")
    elif monetary_cost_dollars is not None:
        raise ValueError("monetary cost dollars require measurement status measured")
    else:
        value = None
    return {
        "billing_posture": billing_posture,
        "monetary_cost": {
            "measurement_status": monetary_cost_status,
            "currency": "USD",
            "value_dollars": value,
        },
        "subscription_quota": {
            "measurement_status": subscription_quota_status,
        },
    }


def rate(count, attempted):
    return round(count / attempted, 3) if attempted else 0


def local_model_benefit_effect(runs, scenario_names):
    scenarios = []
    categories = {}
    for scenario_name in scenario_names:
        scenario = SCENARIOS[scenario_name]
        selected = [run for run in runs if run["scenario"] == scenario_name]
        bare_runs = [run for run in selected if run["mode"] == "bare"]
        mythify_runs = [run for run in selected if run["mode"] == "mythify"]
        bare_success = sum(1 for run in bare_runs if run["verify_exit_code"] == 0)
        mythify_success = sum(1 for run in mythify_runs if run["verify_exit_code"] == 0)
        mythify_evidence = sum(1 for run in mythify_runs if mythify_evidence_ok(run))
        bare_rate = rate(bare_success, len(bare_runs))
        mythify_rate = rate(mythify_success, len(mythify_runs))
        delta = round(mythify_rate - bare_rate, 3)
        if delta > 0:
            observed_benefit = "positive"
        elif delta < 0:
            observed_benefit = "negative"
        else:
            observed_benefit = "neutral"
        roles = list(scenario.get("local_model_roles", []))
        row = {
            "scenario": scenario_name,
            "title": scenario["title"],
            "task_category": scenario.get("task_category", "unknown"),
            "local_model_candidate_roles": roles,
            "local_model_fit_reason": scenario.get("local_model_fit_reason", ""),
            "candidate_fit": "candidate" if roles else "not_marked",
            "bare_attempted": len(bare_runs),
            "mythify_attempted": len(mythify_runs),
            "bare_verified_success_rate": bare_rate,
            "mythify_verified_success_rate": mythify_rate,
            "verified_success_rate_delta": delta,
            "mythify_evidence_success_rate": rate(mythify_evidence, len(mythify_runs)),
            "observed_benefit": observed_benefit,
        }
        scenarios.append(row)
        category = categories.setdefault(
            row["task_category"],
            {
                "task_category": row["task_category"],
                "scenario_count": 0,
                "candidate_roles": [],
                "mythify_attempted": 0,
                "mythify_verified_success": 0,
                "mythify_evidence_success": 0,
            },
        )
        category["scenario_count"] += 1
        category["mythify_attempted"] += len(mythify_runs)
        category["mythify_verified_success"] += mythify_success
        category["mythify_evidence_success"] += mythify_evidence
        for role in roles:
            if role not in category["candidate_roles"]:
                category["candidate_roles"].append(role)

    category_rows = []
    for category in sorted(categories.values(), key=lambda item: item["task_category"]):
        attempted = category["mythify_attempted"]
        category_rows.append({
            **category,
            "candidate_roles": sorted(category["candidate_roles"]),
            "mythify_verified_success_rate": rate(category["mythify_verified_success"], attempted),
            "mythify_evidence_success_rate": rate(category["mythify_evidence_success"], attempted),
        })
    candidate_categories = [
        row["task_category"]
        for row in category_rows
        if row["candidate_roles"] and row["mythify_verified_success_rate"] > 0
    ]
    return {
        "metric": "local_model_candidate_task_categories",
        "comparison": "scenario_metadata_plus_harness_outcomes",
        "evidence_source": "scenario local-model role metadata plus per-workspace python3 -m unittest exit code",
        "supported_roles": ["reader", "triage"],
        "candidate_categories": candidate_categories,
        "scenario_count": len(scenarios),
        "scenarios": scenarios,
        "categories": category_rows,
        "statistical_strength": "local_smoke",
        "caveat": "This identifies local-model candidate task categories and observed harness outcomes; provider-specific benefit requires rerunning with a local-model-backed command or provider check.",
    }


def fanout_value_effect(summary, runs, scenario_names):
    policy_rows = [dict(row) for row in FANOUT_VALUE_POLICY]
    helps_when = [
        row["task_shape"]
        for row in policy_rows
        if row["fanout_fit"] == "helps"
    ]
    wastes_when = [
        row["task_shape"]
        for row in policy_rows
        if row["fanout_fit"] == "wastes"
    ]

    scenario_rows = []
    for scenario_name in scenario_names:
        scenario = SCENARIOS[scenario_name]
        selected = [run for run in runs if run["scenario"] == scenario_name]
        bare_runs = [run for run in selected if run["mode"] == "bare"]
        mythify_runs = [run for run in selected if run["mode"] == "mythify"]
        bare_success = sum(1 for run in bare_runs if run["verify_exit_code"] == 0)
        mythify_success = sum(1 for run in mythify_runs if run["verify_exit_code"] == 0)
        mythify_evidence = sum(1 for run in mythify_runs if mythify_evidence_ok(run))
        mythify_verified_rate = rate(mythify_success, len(mythify_runs))
        mythify_evidence_rate = rate(mythify_evidence, len(mythify_runs))
        fanout_fit = scenario.get("fanout_fit", "unknown")
        single_worker_sufficient = (
            fanout_fit == "waste_candidate"
            and len(mythify_runs) > 0
            and mythify_verified_rate > 0
            and mythify_evidence_rate > 0
        )
        if single_worker_sufficient:
            observed_value_signal = "single_worker_sufficient"
        elif fanout_fit == "helps_candidate":
            observed_value_signal = "needs_merged_fanout_verifier"
        else:
            observed_value_signal = "inconclusive"
        scenario_rows.append({
            "scenario": scenario_name,
            "title": scenario["title"],
            "task_category": scenario.get("task_category", "unknown"),
            "fanout_fit": fanout_fit,
            "fanout_fit_reason": scenario.get("fanout_fit_reason", ""),
            "fanout_merge_verifier": scenario.get("fanout_merge_verifier", ""),
            "fresh_worker_call_cost": "fanout would run one fresh worker per task; this harness records fit but does not estimate tokens, dollars, or local compute",
            "bare_attempted": len(bare_runs),
            "mythify_attempted": len(mythify_runs),
            "bare_verified_success_rate": rate(bare_success, len(bare_runs)),
            "mythify_verified_success_rate": mythify_verified_rate,
            "mythify_evidence_success_rate": mythify_evidence_rate,
            "single_worker_sufficient": single_worker_sufficient,
            "observed_value_signal": observed_value_signal,
        })

    observed_help_candidates = sum(
        1 for row in scenario_rows if row["fanout_fit"] == "helps_candidate"
    )
    observed_waste_candidates = sum(
        1 for row in scenario_rows if row["fanout_fit"] == "waste_candidate"
    )
    single_worker_sufficient_count = sum(
        1 for row in scenario_rows if row["single_worker_sufficient"]
    )
    if (
        scenario_rows
        and observed_waste_candidates == len(scenario_rows)
        and single_worker_sufficient_count == observed_waste_candidates
    ):
        conclusion = "built_in_scenarios_do_not_justify_fanout"
    elif observed_help_candidates > 0:
        conclusion = "fanout_candidates_require_merged_verifier"
    else:
        conclusion = "insufficient_evidence"

    return {
        "metric": "fanout_value_fit",
        "comparison": "fanout_policy_plus_harness_outcomes",
        "evidence_source": "scenario fanout-fit metadata plus per-workspace python3 -m unittest exit code",
        "requires_independent_tasks": True,
        "helps_when": helps_when,
        "wastes_when": wastes_when,
        "policy": policy_rows,
        "scenario_count": len(scenario_rows),
        "observed_help_candidate_count": observed_help_candidates,
        "observed_waste_candidate_count": observed_waste_candidates,
        "single_worker_sufficient_count": single_worker_sufficient_count,
        "observed_harness": {
            "bare_attempted": summary["bare"]["attempted"],
            "mythify_attempted": summary["mythify"]["attempted"],
            "mythify_verified_success_rate": summary["mythify"]["verified_success_rate"],
            "mythify_evidence_success_rate": summary["mythify"]["evidence_success_rate"],
        },
        "scenarios": scenario_rows,
        "conclusion": conclusion,
        "statistical_strength": "local_smoke",
        "caveat": "This reports fanout fit and single-worker sufficiency for built-in smoke scenarios; proving fanout value requires independent worker outputs, a merged artifact, and a verifier run after the merge.",
    }


def role_strength_effect(summary, runs):
    mythify_runs = [run for run in runs if run["mode"] == "mythify"]
    observed_profiles = sorted({
        run.get("mythify_profile") or "unknown"
        for run in mythify_runs
    })
    observed_scenarios = sorted({
        run.get("scenario", "unknown")
        for run in mythify_runs
    })
    roles = []
    for row in ROLE_STRENGTH_POLICY:
        role = dict(row)
        if role["role"] == "reviewer":
            role["harness_evidence"] = "policy_only_no_reviewer_worker_in_local_eval"
        elif role["role"] == "verifier":
            role["harness_evidence"] = "python3 -m unittest verifies every run"
        elif role["role"] in ("reader", "triage"):
            role["harness_evidence"] = "local_model_benefit marks candidate task categories for this role"
        elif role["role"] == "fanout_worker":
            role["harness_evidence"] = "local eval runs one worker path, not fanout isolation"
        else:
            role["harness_evidence"] = "host session model not controlled by local eval"
        roles.append(role)

    required_roles = [
        row["role"]
        for row in roles
        if row["stronger_model_requirement"] == "required"
    ]
    scoped_opt_in_roles = [
        row["role"]
        for row in roles
        if "reviewer_strength_allow_stronger" in row["stronger_model_allowed"]
    ]
    broad_opt_in_roles = [
        row["role"]
        for row in roles
        if "spawn_ceiling_allow_stronger" in row["stronger_model_allowed"]
    ]
    return {
        "metric": "stronger_model_role_requirement",
        "comparison": "role_policy_plus_harness_outcomes",
        "evidence_source": "Mythify model_policy role contracts plus local eval verifier outcomes",
        "default_spawn_ceiling": "same_or_lower",
        "required_stronger_roles": required_roles,
        "scoped_stronger_opt_in_roles": scoped_opt_in_roles,
        "broad_stronger_opt_in_roles": broad_opt_in_roles,
        "roles": roles,
        "observed_harness": {
            "mythify_attempted": summary["mythify"]["attempted"],
            "mythify_verified_success_rate": summary["mythify"]["verified_success_rate"],
            "mythify_evidence_success_rate": summary["mythify"]["evidence_success_rate"],
            "observed_profiles": observed_profiles,
            "observed_scenarios": observed_scenarios,
        },
        "conclusion": "no_role_requires_stronger_by_default",
        "statistical_strength": "local_smoke",
        "caveat": "This reports role policy and local harness outcomes; proving that a stronger model improves a role requires a paired run with that role isolated.",
    }


def _completed_paired_trials(runs):
    modes_by_trial = {}
    for run in runs:
        key = (run.get("scenario"), run.get("iteration"))
        modes_by_trial.setdefault(key, set()).add(run.get("mode"))
    return sum(1 for modes in modes_by_trial.values() if {"bare", "mythify"} <= modes)


def _allowed_enum(value, allowed, label):
    if value not in allowed:
        raise ValueError("invalid {0}".format(label))
    return value


def _integer(value, label, minimum=None):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("{0} must be an integer".format(label))
    if minimum is not None and value < minimum:
        raise ValueError("{0} must be at least {1}".format(label, minimum))
    return value


def _finite_number(value, label, minimum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("{0} must be numeric".format(label))
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("{0} must be finite".format(label))
    if minimum is not None and number < minimum:
        raise ValueError("{0} must be at least {1}".format(label, minimum))
    return value


def _sanitized_mythify_records(records):
    if not isinstance(records, dict):
        raise ValueError("mythify_records must be an object")
    return {
        "verifications": _integer(records.get("verifications", 0), "verification count", 0),
        "passing_expected_verifications": _integer(
            records.get("passing_expected_verifications", 0),
            "passing expected verification count",
            0,
        ),
        "plans": _integer(records.get("plans", 0), "plan count", 0),
    }


def _sanitized_run(run):
    if not isinstance(run, dict):
        raise ValueError("run must be an object")
    scenario = _allowed_enum(run.get("scenario"), tuple(SCENARIOS), "run scenario")
    mode = _allowed_enum(run.get("mode"), ("bare", "mythify"), "run mode")
    profile = "" if mode == "bare" else _allowed_enum(
        run.get("mythify_profile"), ("fast", "standard"), "run Mythify profile"
    )
    return {
        "scenario": scenario,
        "task_category": SCENARIOS[scenario]["task_category"],
        "iteration": _integer(run.get("iteration"), "run iteration", 1),
        "mode": mode,
        "mythify_profile": profile,
        "speed": _allowed_enum(run.get("speed", "auto"), SPEED_LEVELS, "run speed"),
        "model_exit_code": _integer(run.get("model_exit_code"), "model exit code"),
        "model_duration_seconds": _finite_number(
            run.get("model_duration_seconds"), "model duration", 0
        ),
        "verify_exit_code": _integer(run.get("verify_exit_code"), "verifier exit code"),
        "mythify_records": _sanitized_mythify_records(run.get("mythify_records", {})),
    }


def _sanitized_cost_metadata(value):
    if not isinstance(value, dict):
        raise ValueError("cost_metadata must be an object")
    monetary = value.get("monetary_cost", {})
    quota = value.get("subscription_quota", {})
    if not isinstance(monetary, dict) or not isinstance(quota, dict):
        raise ValueError("cost metadata measurement fields must be objects")
    if monetary.get("currency", "USD") != "USD":
        raise ValueError("monetary cost currency must be USD")
    return build_cost_metadata(
        value.get("billing_posture", "unknown"),
        monetary.get("measurement_status", "unknown"),
        monetary.get("value_dollars"),
        quota.get("measurement_status", "unknown"),
    )


def build_sanitized_summary(report):
    """Build a publishable summary with no prompts, output tails, or paths."""
    if not isinstance(report, dict):
        raise ValueError("report must be an object")
    engine = _allowed_enum(report.get("engine"), ENGINES, "report engine")
    scenario = _allowed_enum(
        report.get("scenario"), tuple(SCENARIOS) + ("all",), "report scenario"
    )
    profile = _allowed_enum(
        report.get("mythify_profile", "auto"), MYTHIFY_PROFILES, "report Mythify profile"
    )
    repeat = _integer(report.get("repeat"), "repeat count", 1)
    scenario_names = list(SCENARIOS) if scenario == "all" else [scenario]
    scenario_count = len(scenario_names)
    raw_runs = report.get("runs", [])
    if not isinstance(raw_runs, list):
        raise ValueError("runs must be an array")
    runs = [_sanitized_run(run) for run in raw_runs]
    summary = summarize_runs(runs)
    profile_overhead = profile_overhead_effect(summary, runs)
    profile_overhead["bare_speed"] = _allowed_enum(
        report.get("bare_speed", "auto"), SPEED_LEVELS, "bare speed"
    )
    profile_overhead["mythify_speed"] = _allowed_enum(
        report.get("mythify_speed", "auto"), SPEED_LEVELS, "Mythify speed"
    )
    requested_pairs = repeat * scenario_count
    completed_pairs = _completed_paired_trials(runs)
    if repeat < 2:
        evidence_status = "insufficient_single_trial"
    elif completed_pairs < requested_pairs:
        evidence_status = "incomplete_repeated_trials"
    else:
        evidence_status = "available_repeated_trials"
    caveats = [
        "This is a small local paired comparison, not a statistically powered benchmark.",
        "This artifact contains {0} completed pairs; timing is descriptive only and supports no speed winner.".format(completed_pairs),
        "Pair order is fixed bare then Mythify, so order effects are not controlled.",
        "Model service load, CLI versions, model aliases, and account settings can affect results.",
        "Task success is decided by the external verifier exit code, not model prose.",
        "Full local reports can contain temporary paths and output tails and must not be committed.",
    ]
    if repeat < 2:
        caveats.append("A single trial is insufficient efficacy evidence; rerun with --repeat 2 or greater.")
    if completed_pairs < requested_pairs:
        caveats.append("Not every requested bare and Mythify trial pair completed.")
    return {
        "schema_version": 1,
        "kind": "mythify_efficacy_summary",
        "evidence_status": evidence_status,
        "engine": engine,
        "scenario": scenario,
        "scenario_count": scenario_count,
        "mythify_profile": profile,
        "trial_design": {
            "paired": True,
            "pair_order": ["bare", "mythify"],
            "repeat_per_scenario": repeat,
            "paired_trials": requested_pairs,
            "completed_paired_trials": completed_pairs,
        },
        "conditions": comparison_conditions(),
        "summary": summary,
        "verified_task_success": verified_task_success_effect(summary),
        "false_completion_claims": false_completion_claims_effect(runs),
        "profile_overhead": profile_overhead,
        "local_model_benefit": local_model_benefit_effect(runs, scenario_names),
        "fanout_value": fanout_value_effect(summary, runs, scenario_names),
        "role_strength": role_strength_effect(summary, runs),
        "cost_metadata": _sanitized_cost_metadata(
            report.get("cost_metadata", build_cost_metadata("unknown", "unknown", None, "unknown"))
        ),
        "runs": runs,
        "sanitization": {
            "raw_model_output_included": False,
            "verifier_output_included": False,
            "temporary_paths_included": False,
            "prompts_included": False,
        },
        "caveats": caveats,
    }


def _fsync_dir_best_effort(path):
    """Best-effort directory durability after an atomic publication rename.

    Once os.replace succeeds, the output is published. A directory fsync
    failure must not report that committed publication as failed.
    """
    flags = getattr(os, "O_RDONLY", 0)
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        descriptor = os.open(str(path), flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass


def sanitize_existing_report(input_path, output_path, cost_metadata):
    """Annotate a retained raw report in memory and publish only its summary."""
    input_candidate = Path(input_path).expanduser()
    output_candidate = Path(output_path).expanduser()
    try:
        resolved_input = input_candidate.resolve(strict=True)
        resolved_output_parent = output_candidate.parent.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError("sanitized output parent must exist and resolve safely") from exc
    if not resolved_output_parent.is_dir():
        raise ValueError("sanitized output parent must be a directory")
    if output_candidate.name in ("", ".", ".."):
        raise ValueError("sanitized output must name a file")

    resolved_output = resolved_output_parent / output_candidate.name
    try:
        same_target = resolved_input == resolved_output.resolve(strict=False)
        if not same_target and resolved_output.exists():
            same_target = resolved_input.samefile(resolved_output)
    except (OSError, RuntimeError) as exc:
        raise ValueError("input and output identity could not be checked safely") from exc
    if same_target:
        raise ValueError("input and output must refer to different files")
    if resolved_output.is_dir():
        raise ValueError("sanitized output must be a file")

    raw = json.loads(resolved_input.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("existing report must contain a JSON object")
    annotated = dict(raw)
    annotated["cost_metadata"] = cost_metadata
    summary = build_sanitized_summary(annotated)
    text = json.dumps(summary, indent=2) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".mythify-sanitized-",
        suffix=".tmp",
        dir=str(resolved_output_parent),
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o644)
        os.replace(temporary_path, resolved_output)
        _fsync_dir_best_effort(resolved_output_parent)
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
    return summary


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
    parser.add_argument(
        "--billing-posture",
        choices=BILLING_POSTURES,
        default="unknown",
        help="Billing posture for this run. Defaults to unknown.",
    )
    parser.add_argument(
        "--monetary-cost-status",
        choices=MEASUREMENT_STATUSES,
        default="unknown",
        help="Whether monetary cost was measured. Defaults to unknown.",
    )
    parser.add_argument(
        "--monetary-cost-dollars",
        type=float,
        default=None,
        help="Measured USD cost. Requires --monetary-cost-status measured.",
    )
    parser.add_argument(
        "--subscription-quota-status",
        choices=MEASUREMENT_STATUSES,
        default="unknown",
        help="Whether subscription quota consumption was measured. Defaults to unknown.",
    )
    parser.add_argument("--json-output", default="", help="Optional path for the JSON report.")
    parser.add_argument(
        "--summary-output",
        default="",
        help="Optional path for a stable sanitized summary safe for publication.",
    )
    parser.add_argument(
        "--sanitize-existing-report",
        default="",
        metavar="PATH",
        help="Sanitize an existing raw report without running model trials.",
    )
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
    try:
        cost_metadata = build_cost_metadata(
            args.billing_posture,
            args.monetary_cost_status,
            args.monetary_cost_dollars,
            args.subscription_quota_status,
        )
    except ValueError as exc:
        print("[FAIL] {0}".format(exc), file=sys.stderr)
        return 1

    if args.sanitize_existing_report:
        if not args.summary_output:
            print("[FAIL] --sanitize-existing-report requires --summary-output", file=sys.stderr)
            return 1
        try:
            sanitized = sanitize_existing_report(
                args.sanitize_existing_report,
                args.summary_output,
                cost_metadata,
            )
        except (OSError, ValueError) as exc:
            print("[FAIL] Could not sanitize existing report: {0}".format(exc), file=sys.stderr)
            return 1
        print(json.dumps(sanitized, indent=2))
        return 0

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
            "false_completion_claims": false_completion_claims_effect(runs),
            "profile_overhead": {
                **profile_overhead_effect(summary, runs),
                "bare_speed": bare_speed,
                "mythify_speed": mythify_speed,
            },
            "local_model_benefit": local_model_benefit_effect(runs, scenario_names),
            "fanout_value": fanout_value_effect(summary, runs, scenario_names),
            "role_strength": role_strength_effect(summary, runs),
            "cost_metadata": cost_metadata,
            "runs": runs,
        }
        sanitized_summary = build_sanitized_summary(report)
        text = json.dumps(report, indent=2)
        if args.json_output:
            Path(args.json_output).write_text(text + "\n", encoding="utf-8")
        if args.summary_output:
            Path(args.summary_output).write_text(
                json.dumps(sanitized_summary, indent=2) + "\n",
                encoding="utf-8",
            )
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
