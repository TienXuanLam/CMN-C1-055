# CMN-C1-055 — Proof-of-Boundary Tests (PB-1 through PB-6)

import json
from unittest.mock import patch

import pytest

from framework.secrets.context import bound_secrets
from shared.secrets.inmemory_provider import InMemoryProvider

from src.nodes.pre_process_node import RequirementIngestNode
from src.nodes.post_process_node import ResponseValidateNode, ADVISORY_NOTE

_SECRETS = InMemoryProvider({"OPENAI_API_KEY": "test-key"})


def _make_estimated_tasks(n: int = 2, pts: int = 3) -> str:
    return json.dumps(
        [
            {
                "title": f"Task {i+1}",
                "description": f"Description {i+1}",
                "acceptance_criteria": "- Acceptance criteria here",
                "task_type": "feature",
                "dependencies": [],
                "story_points": pts,
                "size_label": "M",
            }
            for i in range(n)
        ]
    )


def _base_state(**overrides) -> dict:
    base = {
        "user_input": "",
        "requirement_text": "Build a REST API integration.",
        "error_code": None,
        "error_message": None,
        "input_context": {},
        "correlation_id": "test",
        "session_id": "test",
        "thread_id": "test",
        "trace_id": "",
        "caller_trust_level": "INTERNAL",
        "caller_id": "test",
        "hitl_allowed": True,
        "node_history": [],
        "error_log": [],
        "status": "pending",
        "execution_time": {},
    }
    base.update(overrides)
    return base


# ── PB-1: emit_trace_event fires without error ────────────────────────────────


class TestPB1EmitTraceEvent:
    def test_requirement_ingest_emits_trace(self):
        node = RequirementIngestNode()
        with patch("src.nodes.pre_process_node.emit_trace_event") as mock_emit:
            with bound_secrets(_SECRETS):
                node.execute(_base_state(requirement_text="Build something safe."))
        # A clean success path emits both requirement_ingest_started and
        # requirement_ingest_ok.
        assert mock_emit.called
        assert mock_emit.call_count == 2

    def test_post_process_emits_trace_on_success(self):
        node = ResponseValidateNode(output_format="github_issues")
        state = _base_state(
            estimated_task_list=_make_estimated_tasks(2),
            sprint_plan=json.dumps([{"sprint_id": 1, "tasks": ["Task 1", "Task 2"], "points_total": 6}]),
            scope_summary="{}",
            validation_result='{"passed": true, "issues": []}',
            sprint_count=1,
            total_story_points=6,
            decomposition_template="generic",
        )
        with patch("src.nodes.post_process_node.emit_trace_event") as mock_emit:
            with bound_secrets(_SECRETS):
                node.execute(state)
        assert mock_emit.called


# ── PB-2: State values are primitives ────────────────────────────────────────


class TestPB2StatePrimitives:
    def test_state_fields_are_primitive_types(self):
        state: dict = {
            "requirement_text": "hello",
            "task_count": 3,
            "overflow_warning": False,
            "total_story_points": 12,
            "sprint_count": 2,
            "error_code": None,
            "final_output": '{"issues": []}',
        }
        for key, val in state.items():
            assert isinstance(
                val, (str, int, float, bool, type(None))
            ), f"State['{key}'] = {type(val).__name__} — not a primitive"

    def test_json_dumps_roundtrip(self):
        state = {
            "requirement_text": "Build something",
            "task_count": 3,
            "overflow_warning": True,
            "error_code": None,
        }
        serialised = json.dumps(state)
        assert json.loads(serialised) == state


# ── PB-3: S-3 credential not propagated ──────────────────────────────────────


class TestPB3S3Gate:
    def test_credential_in_output_blocked(self):
        node = ResponseValidateNode(output_format="github_issues")
        fake_key = "sk-" + "q" * 25
        state = _base_state(
            estimated_task_list=json.dumps(
                [
                    {
                        "title": f"Authenticate with {fake_key}",
                        "description": f"Uses key {fake_key}",
                        "acceptance_criteria": "Done",
                        "task_type": "infra",
                        "dependencies": [],
                        "story_points": 2,
                        "size_label": "S",
                    }
                ]
            ),
            sprint_plan=json.dumps([{"sprint_id": 1, "tasks": [f"Authenticate with {fake_key}"], "points_total": 2}]),
            scope_summary="{}",
            validation_result='{"passed": true, "issues": []}',
            sprint_count=1,
            total_story_points=2,
            decomposition_template="generic",
        )
        with bound_secrets(_SECRETS):
            result = node.execute(state)
        assert result.get("error_code") == "S3_BLOCKED"
        assert result.get("final_output") is None

    def test_clean_output_passes(self):
        node = ResponseValidateNode(output_format="github_issues")
        state = _base_state(
            estimated_task_list=_make_estimated_tasks(2),
            sprint_plan=json.dumps([{"sprint_id": 1, "tasks": ["Task 1", "Task 2"], "points_total": 6}]),
            scope_summary="{}",
            validation_result='{"passed": true, "issues": []}',
            sprint_count=1,
            total_story_points=6,
            decomposition_template="generic",
        )
        with bound_secrets(_SECRETS):
            result = node.execute(state)
        assert result.get("error_code") is None
        assert result.get("final_output") is not None


# ── PB-4: Import isolation (L0) ───────────────────────────────────────────────


class TestPB4ImportIsolation:
    def test_no_level0_imports_in_src(self):
        import ast
        import os

        src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
        violations = []
        for root, dirs, files in os.walk(src_dir):
            for f in files:
                if not f.endswith(".py"):
                    continue
                fpath = os.path.join(root, f)
                with open(fpath) as fh:
                    try:
                        tree = ast.parse(fh.read(), filename=fpath)
                    except SyntaxError:
                        continue
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            if alias.name.startswith("agenticstar"):
                                violations.append(f"{fpath}:{node.lineno} — import {alias.name}")
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        if node.module.startswith("agenticstar"):
                            violations.append(f"{fpath}:{node.lineno} — from {node.module} import ...")
        assert violations == [], "L0 violations:\n" + "\n".join(violations)


# ── PB-5: Advisory note in all output formats ─────────────────────────────────


@pytest.mark.parametrize("fmt", ["github_issues", "gitlab_issues", "jira", "markdown"])
def test_pb5_advisory_note_in_all_formats(fmt: str):
    node = ResponseValidateNode(output_format=fmt)
    state = _base_state(
        estimated_task_list=_make_estimated_tasks(2),
        sprint_plan=json.dumps([{"sprint_id": 1, "tasks": ["Task 1", "Task 2"], "points_total": 6}]),
        scope_summary="{}",
        validation_result='{"passed": true, "issues": []}',
        sprint_count=1,
        total_story_points=6,
        decomposition_template="generic",
    )
    with bound_secrets(_SECRETS):
        result = node.execute(state)
    assert result.get("error_code") is None
    output = result.get("final_output", "")
    assert ADVISORY_NOTE in output, f"Advisory note missing from '{fmt}' output"


# ── PB-6: INVALID_TASKS is non-fatal ─────────────────────────────────────────


class TestPB6InvalidTasksNonFatal:
    def test_invalid_tasks_output_still_produced(self):
        from src.nodes.main_node import MainNode

        class _MockLLM:
            def complete(self, messages):
                return {"content": "[]"}

        node = MainNode(llm_client=_MockLLM())
        # Task missing story_points
        bad_tasks = json.dumps(
            [
                {
                    "title": "Task without estimate",
                    "description": "Some description",
                    "acceptance_criteria": "Done",
                    "task_type": "feature",
                    "dependencies": [],
                    "size_label": "M",
                }
            ]
        )
        state = _base_state(
            estimated_task_list=bad_tasks, task_count=1, scope_summary="{}", decomposition_template="generic"
        )
        with bound_secrets(_SECRETS):
            val_result = node._decomposition_validate(state)
        assert val_result.get("error_code") == "INVALID_TASKS"

        state.update(val_result)
        state.update(
            {
                "sprint_plan": json.dumps([{"sprint_id": 1, "tasks": ["Task without estimate"], "points_total": 0}]),
                "sprint_count": 1,
                "total_story_points": 0,
            }
        )
        response_node = ResponseValidateNode(output_format="markdown")
        with bound_secrets(_SECRETS):
            resp_result = response_node.execute(state)
        assert resp_result.get("final_output") is not None
