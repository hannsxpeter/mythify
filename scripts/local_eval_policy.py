"""Static policy tables for the local evaluation harness.

Pure data consumed by the harness report builders in
scripts/local_model_eval.py. Kept beside it so that file stays under the
runtime source-size ceiling.
"""

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
