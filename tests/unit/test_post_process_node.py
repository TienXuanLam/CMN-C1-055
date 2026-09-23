# CMN-C1-055 — Unit Tests: ResponseValidateNode (S-3 output gate + serialisation)
#
# Ported from the legacy flat tests/test_cmn_c1_055.py (TC-08, TC-13, TC-14),
# which sat outside the canonical tests/unit|integration|proof_of_boundary
# structure and has been removed. These cases were not duplicated elsewhere:
# no other test pins ResponseValidateNode's error-skip behaviour, nor the
# markdown/gitlab_issues output-format-specific content shape.

import json

from framework.secrets.context import bound_secrets
from shared.secrets.inmemory_provider import InMemoryProvider

from src.nodes.post_process_node import ResponseValidateNode, ADVISORY_NOTE

_SECRETS = InMemoryProvider({"OPENAI_API_KEY": "test-key"})


def _make_estimated_tasks(n: int, pts: int = 3) -> str:
    return json.dumps(
        [
            {
                "title": f"Task {i+1}",
                "description": f"Description of task {i+1}",
                "acceptance_criteria": "- Done when implemented",
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
        "requirement_text": "Build a REST API integration for retail inventory.",
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


class TestResponseValidateErrorPropagation:
    def test_upstream_fatal_error_returns_graceful_success_envelope(self):
        # An unrecognised fatal error_code (not in the OVERFLOW_WARNING/
        # INVALID_TASKS whitelist) is NOT skipped with {} -- the Marketplace
        # runner treats any non-SUCCESS status as a hard failure and never
        # shows the user formatted_output, so this node reports SUCCESS
        # with a graceful "service temporarily unavailable" message instead
        # (see post_process_node.py's Finding 1/2 comments).
        node = ResponseValidateNode()
        fatal_state = _base_state(error_code="S1_INPUT_TOO_LONG", error_message="too long")
        with bound_secrets(_SECRETS):
            result = node.execute(fatal_state)
        assert result.get("status") == "success"
        assert result.get("error_code") is None
        assert "temporarily unavailable" in result.get("formatted_output", "")


class TestResponseValidateOutputFormats:
    def test_markdown_output_contains_sprint_and_advisory(self):
        node = ResponseValidateNode(output_format="markdown")
        state = _base_state(
            estimated_task_list=_make_estimated_tasks(3),
            sprint_plan=json.dumps([{"sprint_id": 1, "tasks": ["Task 1", "Task 2", "Task 3"], "points_total": 9}]),
            scope_summary="{}",
            validation_result='{"passed": true, "issues": []}',
            sprint_count=1,
            total_story_points=9,
            decomposition_template="generic",
        )
        with bound_secrets(_SECRETS):
            result = node.execute(state)
        assert result.get("error_code") is None
        assert result.get("output_format_used") == "markdown"
        assert "Sprint 1" in result.get("final_output", "")
        assert ADVISORY_NOTE in result.get("final_output", "")

    def test_gitlab_issues_output_schema(self):
        node = ResponseValidateNode(output_format="gitlab_issues")
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
        assert result.get("output_format_used") == "gitlab_issues"
        data = json.loads(result["final_output"])
        assert "issues" in data
        for issue in data["issues"]:
            assert "description" in issue
            assert "weight" in issue
        assert data.get("advisory") == ADVISORY_NOTE

    def test_formatted_output_matches_final_output(self):
        # formatted_output (== agent.invoke()'s result["output"]) is the
        # same serialised string as final_output -- not a wrapping envelope
        # with a nested "final_output" key.
        node = ResponseValidateNode(output_format="github_issues")
        state = _base_state(
            estimated_task_list=_make_estimated_tasks(1),
            sprint_plan=json.dumps([{"sprint_id": 1, "tasks": ["Task 1"], "points_total": 3}]),
            scope_summary="{}",
            validation_result='{"passed": true, "issues": []}',
            sprint_count=1,
            total_story_points=3,
            decomposition_template="generic",
        )
        with bound_secrets(_SECRETS):
            result = node.execute(state)
        assert result["formatted_output"] == result["final_output"]
        data = json.loads(result["final_output"])
        assert "issues" in data
