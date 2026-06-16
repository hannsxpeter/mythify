"""Bounded model triage runners for Mythify."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
import time
from pathlib import Path


def _missing_dependency(*_args, **_kwargs):
    raise RuntimeError("mythify_model_triage dependencies are not configured")


TRIAGE_ENGINES = ()
build_triage_prompt = _missing_dependency
should_run_model_triage = _missing_dependency
tail_text = _missing_dependency
command_triage_template = _missing_dependency
resolve_triage_binary = _missing_dependency
normalize_platform = _missing_dependency
select_triage_engine = _missing_dependency
resolve_triage_model_selection = _missing_dependency
effort_for_role = _missing_dependency
speed_for_role = _missing_dependency


def configure_model_triage(**deps):
    globals().update(deps)


def triage_shell_env(model, speed="auto"):
    env = dict(os.environ)
    env["TERM"] = "dumb"
    env["MYTHIFY_FANOUT_DEPTH"] = "1"
    env["MYTHIFY_DISABLE_FANOUT"] = "1"
    env["MYTHIFY_TRIAGE_MODEL"] = model or ""
    env["MYTHIFY_TRIAGE_SPEED"] = speed or "auto"
    return env


def run_triage_process(args, cwd, prompt, timeout, env, shell=False):
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
            "stdout_tail": tail_text(result.stdout),
            "stderr_tail": tail_text(result.stderr),
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return {
            "exit_code": -1,
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout_tail": tail_text(stdout),
            "stderr_tail": tail_text(stderr + "\n[FAIL] timed out"),
            "timed_out": True,
        }


def parse_model_triage_json(text):
    raw = str(text or "").strip()
    candidates = [raw]
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end > start:
        candidates.append(raw[start:end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def run_claude_triage(prompt, model, timeout, cwd, speed="auto"):
    binary = resolve_triage_binary(["claude"], ["MYTHIFY_TRIAGE_CLAUDE_BIN", "MYTHIFY_FANOUT_CLAUDE_BIN"])
    if not binary:
        return {
            "exit_code": 127,
            "duration_seconds": 0,
            "stdout_tail": "",
            "stderr_tail": "claude binary not found",
            "timed_out": False,
        }
    args = [
        binary,
        "-p",
        "--output-format",
        "json",
        "--model",
        model or "haiku",
        "--max-turns",
        os.environ.get("MYTHIFY_TRIAGE_MAX_TURNS", "1"),
    ]
    args.extend(shlex.split(os.environ.get("MYTHIFY_TRIAGE_CLAUDE_ARGS", "")))
    result = run_triage_process(args, cwd, prompt, timeout, triage_shell_env(model, speed))
    try:
        parsed = json.loads(result["stdout_tail"])
        if isinstance(parsed, dict) and isinstance(parsed.get("result"), str):
            result["output_tail"] = tail_text(parsed["result"])
        else:
            result["output_tail"] = result["stdout_tail"]
    except ValueError:
        result["output_tail"] = result["stdout_tail"]
    return result


def codex_speed_args(speed):
    if speed == "fast":
        return ["-c", 'service_tier="fast"', "-c", "features.fast_mode=true"]
    if speed == "standard":
        return ["-c", "features.fast_mode=false"]
    return []


def run_codex_triage(prompt, model, timeout, cwd, speed="auto"):
    binary = resolve_triage_binary(["codex"], ["MYTHIFY_TRIAGE_CODEX_BIN", "MYTHIFY_FANOUT_CODEX_BIN"])
    if not binary:
        return {
            "exit_code": 127,
            "duration_seconds": 0,
            "stdout_tail": "",
            "stderr_tail": "codex binary not found",
            "timed_out": False,
        }
    with tempfile.NamedTemporaryFile(prefix="mythify-codex-triage-", suffix=".md", delete=False) as handle:
        output_path = Path(handle.name)
    args = [
        binary,
        "--ask-for-approval",
        "never",
        "exec",
        "--cd",
        str(cwd),
        "--sandbox",
        os.environ.get("MYTHIFY_TRIAGE_CODEX_SANDBOX", "read-only"),
        "--skip-git-repo-check",
        "--ephemeral",
        "--color",
        "never",
        "--output-last-message",
        str(output_path),
    ]
    if model:
        args.extend(["--model", model])
    args.extend(codex_speed_args(speed))
    args.extend(shlex.split(os.environ.get("MYTHIFY_TRIAGE_CODEX_ARGS", "")))
    args.append("-")
    result = run_triage_process(args, cwd, prompt, timeout, triage_shell_env(model, speed))
    try:
        if output_path.exists():
            result["output_tail"] = tail_text(output_path.read_text(encoding="utf-8"))
        else:
            result["output_tail"] = result["stdout_tail"]
    finally:
        try:
            output_path.unlink()
        except OSError:
            pass
    return result


def run_cursor_triage(prompt, model, timeout, cwd, speed="auto"):
    binary = resolve_triage_binary(
        ["cursor-agent", "cursor"],
        [
            "MYTHIFY_TRIAGE_CURSOR_BIN",
            "MYTHIFY_FANOUT_CURSOR_BIN",
            "MYTHIFY_FANOUT_CURSOR_AGENT_BIN",
        ],
    )
    if not binary:
        return {
            "exit_code": 127,
            "duration_seconds": 0,
            "stdout_tail": "",
            "stderr_tail": "cursor-agent or cursor binary not found",
            "timed_out": False,
        }
    with tempfile.NamedTemporaryFile(prefix="mythify-cursor-triage-", suffix=".md", delete=False, mode="w", encoding="utf-8") as handle:
        handle.write(prompt)
        prompt_path = Path(handle.name)
    args = [binary]
    if Path(binary).name == "cursor":
        args.append("agent")
    args.extend(["--print", "--output-format", "text", "--trust", "--workspace", str(cwd)])
    mode = os.environ.get("MYTHIFY_TRIAGE_CURSOR_MODE", "ask")
    if mode:
        args.extend(["--mode", mode])
    if model:
        args.extend(["--model", model])
    if os.environ.get("MYTHIFY_TRIAGE_CURSOR_FORCE", "") == "1":
        args.append("--force")
    args.extend(shlex.split(os.environ.get("MYTHIFY_TRIAGE_CURSOR_ARGS", "")))
    args.append("Read the triage prompt from this file and return only the requested JSON: {0}".format(prompt_path))
    result = run_triage_process(args, cwd, "", timeout, triage_shell_env(model, speed))
    result["output_tail"] = result["stdout_tail"]
    try:
        prompt_path.unlink()
    except OSError:
        pass
    return result


def run_command_triage(prompt, model, timeout, cwd, speed="auto"):
    command = command_triage_template()
    if not command:
        return {
            "exit_code": 127,
            "duration_seconds": 0,
            "stdout_tail": "",
            "stderr_tail": "MYTHIFY_TRIAGE_COMMAND is not set",
            "timed_out": False,
        }
    result = run_triage_process(command, cwd, prompt, timeout, triage_shell_env(model, speed), shell=True)
    result["output_tail"] = result["stdout_tail"]
    return result


def run_model_triage(task_text, classification, args):
    if not should_run_model_triage(classification, args.triage):
        return {
            "attempted": False,
            "reason": "triage mode {0} with gate {1}".format(
                args.triage, classification.get("model_triage")
            ),
        }
    platform = normalize_platform(getattr(args, "platform", "auto"))
    engine, engine_policy = select_triage_engine(
        getattr(args, "triage_engine", ""), platform
    )
    if not engine:
        return {
            "attempted": True,
            "ok": False,
            "engine": "",
            "engine_policy": engine_policy,
            "model": "",
            "model_policy": "unavailable",
            "effort": "low",
            "speed": "auto",
            "duration_seconds": 0,
            "exit_code": 127,
            "error": (
                "No fast triage engine is available. Configure a local engine with "
                "MYTHIFY_TRIAGE_ENGINE plus the matching CLI login, or set "
                "MYTHIFY_TRIAGE_COMMAND for a command that reads the prompt on stdin."
            ),
            "output_tail": "",
            "parsed": None,
        }
    if engine not in TRIAGE_ENGINES:
        return {
            "attempted": True,
            "ok": False,
            "engine": engine,
            "engine_policy": engine_policy,
            "model": "",
            "model_policy": "unavailable",
            "effort": "low",
            "speed": "auto",
            "duration_seconds": 0,
            "exit_code": 127,
            "error": "Unknown triage engine {0}. Valid engines: {1}.".format(
                engine, ", ".join(TRIAGE_ENGINES)
            ),
            "output_tail": "",
            "parsed": None,
        }
    model, model_policy = resolve_triage_model_selection(
        engine, getattr(args, "triage_model", "")
    )
    effort, effort_policy = effort_for_role(
        "triage", classification, getattr(args, "effort", "auto")
    )
    speed, speed_policy = speed_for_role(
        "triage", getattr(args, "speed", "auto")
    )
    prompt = build_triage_prompt(task_text, classification)
    cwd = Path.cwd()
    if engine == "claude-cli":
        raw = run_claude_triage(prompt, model, args.triage_timeout, cwd, speed)
    elif engine == "codex-cli":
        raw = run_codex_triage(prompt, model, args.triage_timeout, cwd, speed)
    elif engine == "cursor-agent":
        raw = run_cursor_triage(prompt, model, args.triage_timeout, cwd, speed)
    else:
        raw = run_command_triage(prompt, model, args.triage_timeout, cwd, speed)
    output_tail = raw.get("output_tail", raw.get("stdout_tail", ""))
    parsed = parse_model_triage_json(output_tail)
    ok = raw["exit_code"] == 0 and parsed is not None
    error = ""
    if raw["exit_code"] != 0:
        error = raw.get("stderr_tail") or "triage worker exited {0}".format(raw["exit_code"])
    elif parsed is None:
        error = "triage worker exited 0 but did not return valid JSON"
    return {
        "attempted": True,
        "ok": ok,
        "engine": engine,
        "engine_policy": engine_policy,
        "model": model,
        "model_policy": model_policy,
        "effort": effort,
        "effort_policy": effort_policy,
        "speed": speed,
        "speed_policy": speed_policy,
        "duration_seconds": raw.get("duration_seconds", 0),
        "exit_code": raw["exit_code"],
        "error": error,
        "output_tail": output_tail,
        "stderr_tail": raw.get("stderr_tail", ""),
        "timed_out": raw.get("timed_out", False),
        "parsed": parsed,
    }
