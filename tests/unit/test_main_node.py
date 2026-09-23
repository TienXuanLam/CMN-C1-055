# CMN-C1-055 — Unit Tests: MainNode contract + inner node logic

import inspect
import json


from framework.schemas.trust_level import TrustLevel
from framework.secrets.context import bound_secrets
from shared.secrets.inmemory_provider import InMemoryProvider

from src.nodes.main_node import MainNode, _AGENTCORE_PHASE3_TASKS

_SECRETS = InMemoryProvider({"OPENAI_API_KEY": "test-key"})


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
        "scope_summary": json.dumps({"domain": "generic", "complexity": "M", "template_matched": "generic"}),
        "decomposition_template": "generic",
    }
    base.update(overrides)
    return base


class _MockLLM:
    """LLM that returns a valid single-task decomposition JSON."""

    def complete(self, messages):
        content = messages[0]["content"] if messages else ""
        # Scope analysis response
        if "complexity" in content and "domain" in content:
            return {
                "content": json.dumps(
                    {
                        "domain": "generic",
                        "complexity": "M",
                        "complexity_rationale": "Medium complexity.",
                        "template_matched": "generic",
                        "scope_notes": "API integration scope.",
                    }
                )
            }
        # Estimate response — return tasks with story_points added
        if "story_points" in content.lower() or "size_label" in content.lower() or "XS" in content:
            try:
                import re

                json_match = re.search(r"\[.*\]", content, re.DOTALL)
                if json_match:
                    tasks = json.loads(json_match.group())
                    for t in tasks:
                        t.setdefault("size_label", "M")
                        t.setdefault("story_points", 3)
                    return {"content": json.dumps(tasks)}
            except Exception:
                pass
            return {"content": "[]"}
        # Decomposition response
        return {
            "content": json.dumps(
                [
                    {
                        "title": "Implement API client",
                        "description": "Build REST client for inventory system.",
                        "acceptance_criteria": "- Returns normalised stock data",
                        "task_type": "feature",
                        "dependencies": [],
                    }
                ]
            )
        }


class TestMainNodeContract:
    def test_execute_signature(self):
        node = MainNode()
        sig = inspect.signature(node.execute)
        assert "state" in sig.parameters

    def test_no_invoke_impl_override(self):
        assert "_invoke_impl" not in MainNode.__dict__

    def test_required_trust_level(self):
        # MainNode resolves billable Azure OpenAI secrets and performs
        # provider calls, so it requires VERIFIED_EXTERNAL (not ANONYMOUS).
        assert MainNode.required_trust_level == TrustLevel.VERIFIED_EXTERNAL

    def test_is_function_node(self):
        from framework.nodes.function_node import FunctionNode

        assert issubclass(MainNode, FunctionNode)


class TestMainNodeUpstreamError:
    def test_upstream_error_returns_empty(self):
        node = MainNode(llm_client=_MockLLM())
        state = _base_state(error_code="S1_EMPTY_INPUT", error_message="empty")
        with bound_secrets(_SECRETS):
            result = node.execute(state)
        assert result == {}


class TestMainNodeAgentcorePhase3:
    def test_agentcore_phase3_produces_9_tasks(self):
        node = MainNode(llm_client=_MockLLM())
        state = _base_state(
            requirement_text="Build a new agentcore FAQ agent.",
            scope_summary=json.dumps(
                {"domain": "agentcore_phase3", "complexity": "M", "template_matched": "agentcore_phase3"}
            ),
            decomposition_template="agentcore_phase3",
        )
        with bound_secrets(_SECRETS):
            result = node.execute(state)
        assert result.get("task_count") == 9
        tasks = json.loads(result["raw_task_list"])
        assert len(tasks) == 9
        for task in tasks:
            for field in ("title", "description", "acceptance_criteria", "task_type", "dependencies"):
                assert field in task

    def test_agentcore_phase3_tasks_match_canonical(self):
        node = MainNode(llm_client=_MockLLM())
        state = _base_state(
            decomposition_template="agentcore_phase3",
            scope_summary=json.dumps({"template_matched": "agentcore_phase3", "complexity": "M"}),
        )
        with bound_secrets(_SECRETS):
            result = node._task_decompose(state, state)
        tasks = json.loads(result["raw_task_list"])
        canonical_titles = {t["title"] for t in _AGENTCORE_PHASE3_TASKS}
        result_titles = {t["title"] for t in tasks}
        assert canonical_titles == result_titles


class TestScopeAnalyze:
    def test_llm_error_returns_error_code(self):
        class _BrokenLLM:
            def complete(self, messages):
                raise ConnectionError("LLM unavailable")

        node = MainNode(llm_client=_BrokenLLM())
        state = _base_state(requirement_text="Build something")
        with bound_secrets(_SECRETS):
            result = node._scope_analyze(state, state)
        assert result.get("error_code") == "S2_LLM_ERROR"

    def test_no_llm_client_uses_build_llm_path(self):
        # MainNode(llm_client=None) must call _build_llm(), not raise "no LLM client".
        # _scope_analyze() catches all LLM exceptions and returns S2_LLM_ERROR dict,
        # so we verify via return value + build_called sentinel.
        node = MainNode(llm_client=None)
        state = _base_state(requirement_text="Build something")
        build_called = []

        def _fake_build_llm(state, temperature=None):
            build_called.append(temperature)
            raise ConnectionError("no real API in CI")

        node._build_llm = _fake_build_llm
        with bound_secrets(_SECRETS):
            result = node._scope_analyze(state, state)

        assert build_called, "_build_llm() was not called — lazy init path not taken"
        assert result.get("error_code") == "S2_LLM_ERROR", f"Expected S2_LLM_ERROR from ConnectionError, got: {result}"


class TestSprintAssign:
    def test_overflow_warning_when_points_exceed_velocity(self):
        node = MainNode(llm_client=_MockLLM(), team_velocity_points=10)
        tasks = json.dumps([{"title": f"Task {i}", "story_points": 5, "size_label": "L"} for i in range(5)])
        state = _base_state(estimated_task_list=tasks, total_story_points=25)
        with bound_secrets(_SECRETS):
            result = node._sprint_assign(state)
        assert result.get("overflow_warning") is True
        assert result.get("sprint_count", 0) > 1

    def test_single_sprint_when_fits(self):
        node = MainNode(llm_client=_MockLLM(), team_velocity_points=40)
        tasks = json.dumps([{"title": f"Task {i}", "story_points": 3, "size_label": "M"} for i in range(3)])
        state = _base_state(estimated_task_list=tasks, total_story_points=9)
        with bound_secrets(_SECRETS):
            result = node._sprint_assign(state)
        assert result.get("sprint_count") == 1
        assert result.get("overflow_warning") is False


class TestDecompositionValidate:
    def test_missing_story_points_sets_invalid_tasks(self):
        node = MainNode(llm_client=_MockLLM())
        tasks = json.dumps(
            [
                {
                    "title": "Task without estimate",
                    "description": "desc",
                    "acceptance_criteria": "done",
                    "task_type": "feature",
                    "dependencies": [],
                    "size_label": "M",
                    # story_points missing
                }
            ]
        )
        state = _base_state(estimated_task_list=tasks, task_count=1)
        with bound_secrets(_SECRETS):
            result = node._decomposition_validate(state)
        assert result.get("error_code") == "INVALID_TASKS"
        assert "Task without estimate" in json.loads(result["invalid_tasks"])

    def test_valid_tasks_pass_validation(self):
        node = MainNode(llm_client=_MockLLM())
        tasks = json.dumps(
            [
                {
                    "title": "Good Task",
                    "description": "Well-defined",
                    "acceptance_criteria": "Done when tests pass",
                    "story_points": 3,
                    "task_type": "feature",
                    "dependencies": [],
                    "size_label": "M",
                }
            ]
        )
        state = _base_state(estimated_task_list=tasks, task_count=1)
        with bound_secrets(_SECRETS):
            result = node._decomposition_validate(state)
        assert result.get("error_code") is None
        validation = json.loads(result["validation_result"])
        assert validation["passed"] is True

    def test_over_15_tasks_sets_advisory(self):
        node = MainNode(llm_client=_MockLLM())
        tasks = json.dumps(
            [
                {
                    "title": f"Task {i}",
                    "description": "d",
                    "acceptance_criteria": "ac",
                    "story_points": 2,
                    "task_type": "feature",
                    "dependencies": [],
                    "size_label": "S",
                }
                for i in range(16)
            ]
        )
        state = _base_state(estimated_task_list=tasks, task_count=16)
        with bound_secrets(_SECRETS):
            result = node._decomposition_validate(state)
        assert result.get("overflow_warning") is True
        validation = json.loads(result["validation_result"])
        assert any("Advisory" in issue for issue in validation["issues"])


class TestLlmDecomposeFailureVsEmpty:
    def test_failed_llm_call_is_fatal_not_silent_empty_success(self):
        # Regression (2026-08-19, High): a failed/unparseable LLM call
        # previously returned [] indistinguishably from a genuine zero-task
        # decomposition, and _decomposition_validate() treats an empty task
        # list as trivially "passed" -- the whole pipeline completed with
        # status=SUCCESS and zero tasks, silently discarding the failure.
        class _BrokenLLM:
            def complete(self, messages):
                raise ConnectionError("LLM unavailable")

        node = MainNode(llm_client=_BrokenLLM())
        state = _base_state(
            decomposition_template="generic",
            scope_summary=json.dumps({"complexity": "M"}),
        )
        with bound_secrets(_SECRETS):
            result = node._task_decompose(state, state)
        assert result.get("error_code") == "ERR_LLM_DECOMPOSE_FAILED"
        assert result.get("status") == "error"

    def test_genuine_empty_llm_response_is_not_treated_as_failure(self):
        class _EmptyListLLM:
            def complete(self, messages):
                return {"content": "[]"}

        node = MainNode(llm_client=_EmptyListLLM())
        state = _base_state(
            decomposition_template="generic",
            scope_summary=json.dumps({"complexity": "M"}),
        )
        with bound_secrets(_SECRETS):
            result = node._task_decompose(state, state)
        assert result.get("error_code") is None
        assert json.loads(result["raw_task_list"]) == []

    def test_unparseable_llm_response_is_fatal(self):
        class _GarbageLLM:
            def complete(self, messages):
                return {"content": "not json at all"}

        node = MainNode(llm_client=_GarbageLLM())
        state = _base_state(
            decomposition_template="generic",
            scope_summary=json.dumps({"complexity": "M"}),
        )
        with bound_secrets(_SECRETS):
            result = node._task_decompose(state, state)
        assert result.get("error_code") == "ERR_LLM_DECOMPOSE_FAILED"


class TestNoMutableInstanceState:
    def test_node_has_no_state_or_llm_cache_instance_attributes(self):
        # Regression (2026-08-19, Critical): self._state / self._llm_cache
        # were mutable instance attributes on a FunctionNode, shared across
        # every invocation served from the registry's node cache --
        # concurrent invocations could interleave and resolve one
        # invocation's LLM call against a different invocation's secrets/
        # context. Assert neither attribute exists anymore.
        node = MainNode(llm_client=_MockLLM())
        assert not hasattr(node, "_state")
        assert not hasattr(node, "_llm_cache")
