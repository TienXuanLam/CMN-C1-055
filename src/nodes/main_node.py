"""AgentCore Platform v1.0"""

import json
import re
from typing import Any, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from shared.services.events import emitter
from shared.services.events.types import EventType
from shared.services.llm.azure_openai_client import AzureOpenAIClient
from shared.services.llm.base_llm import BaseLLM
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import TaskDecompositionState

# ── Sizing constants (used by StoryPointEstimateNode logic) ───────────────────
SIZE_SCALE = [("XS", 1), ("S", 2), ("M", 3), ("L", 5), ("XL", 8)]
_DEFAULT_SIZE_BY_TYPE = {
    "docs": "S",
    "test": "S",
    "infra": "M",
    "bugfix": "M",
    "feature": "M",
    "security": "L",
}
_POINTS_BY_SIZE = {label: pts for label, pts in SIZE_SCALE}
_SIZES_BY_POINTS = {pts: label for label, pts in SIZE_SCALE}
VALID_TASK_TYPES = {"feature", "bugfix", "docs", "test", "infra", "security"}
_TASK_COUNT_ADVISORY = 15
DEFAULT_VELOCITY = 40
DEFAULT_MAX_TASKS = 50

# ── Domain template registry ───────────────────────────────────────────────────
# Keyword → template name. Iteration order is insertion order (Python 3.7+).
# First match wins — more specific keywords (e.g. "agentcore") must come before
# generic ones. "agentcore_phase3" takes priority over "api_integration" because
# agent templates often mention APIs but are better served by the phase3 template.
_DOMAIN_TEMPLATE_MAP = {
    "agentcore": "agentcore_phase3",
    "agent1000": "agentcore_phase3",
    "agent template": "agentcore_phase3",
    "agentic star": "agentcore_phase3",
    "api integration": "api_integration",
    "rest api": "api_integration",
    "graphql": "api_integration",
    "webhook": "api_integration",
    "ui feature": "ui_feature",
    "frontend": "ui_feature",
    "react": "ui_feature",
    "vue": "ui_feature",
    "compliance": "compliance_impl",
    "regulatory": "compliance_impl",
    "audit": "compliance_impl",
    "gdpr": "compliance_impl",
    "appi": "compliance_impl",
}
_COMPLEXITY_GUIDE = {
    "S": "Small (~1–3 tasks, ≤1 sprint)",
    "M": "Medium (~4–8 tasks, 1–2 sprints)",
    "L": "Large (~9–15 tasks, 2–3 sprints)",
    "XL": "Extra-large (>15 tasks, 3+ sprints)",
}

_SCOPE_PROMPT = """You are a senior software architect. Analyse the following business requirement
and return a JSON object with exactly these fields:

{{
  "domain": "<string: software_engineering | data_pipeline | compliance | ui_feature | api_integration | agentcore_phase3 | generic>",
  "complexity": "<string: S | M | L | XL>",
  "complexity_rationale": "<one sentence>",
  "template_matched": "<string>",
  "scope_notes": "<string: 1-2 sentences>"
}}

Complexity guide:
{complexity_guide}

Requirement:
'''
{requirement}
'''

Return ONLY valid JSON. No markdown fences, no commentary."""

_DECOMPOSE_PROMPT = """You are a senior software architect. Decompose the following business requirement
into atomic, independently implementable tasks.

Decomposition style: {decomposition_style}
- 4h_atomic:    each task takes approximately 4 hours (max 1 developer-day)
- 8h_standard:  each task takes approximately 1 developer-day
- sprint_story: user-story granularity (no size cap)

Return a JSON array. Each task object must have exactly these fields:
{{
  "title": "<short imperative title>",
  "description": "<2-4 sentence description>",
  "acceptance_criteria": "<1-3 bullet points, plain text>",
  "task_type": "<one of: feature | bugfix | docs | test | infra | security>",
  "dependencies": ["<title of blocking task>"]
}}

Rules:
- No task should depend on itself.
- If no dependencies, use [].
- Do NOT include story points.
- Aim for {target_count} tasks maximum.

Requirement:
\"\"\"
{requirement}
\"\"\"

Scope context:
{scope_summary}

Return ONLY a valid JSON array. No markdown, no commentary."""

_ESTIMATE_PROMPT = """You are a senior software engineer doing sprint planning.
Estimate effort for each task using T-shirt sizing:
  XS=1pt (~1h), S=2pts (~4h), M=3pts (~8h), L=5pts (~16h), XL=8pts (~32h)

Decomposition style: {decomposition_style}

Return a JSON array where each element adds:
  "size_label": "<XS|S|M|L|XL>",
  "story_points": <integer>

Tasks:
{tasks_json}

Return ONLY a valid JSON array with ALL original fields preserved plus the two new fields."""

# ── agentcore_phase3 built-in template ────────────────────────────────────────
_AGENTCORE_PHASE3_TASKS = [
    {
        "title": "[impl] Write docs/02_design.md",
        "description": "Produce the full design specification: node flow, state schema, config surface, security gates, error propagation strategy.",
        "acceptance_criteria": "docs/02_design.md committed on feature branch; covers all sections; architect review attached.",
        "task_type": "docs",
        "dependencies": [],
    },
    {
        "title": "[impl] Define state schema src/schemas/state.py",
        "description": "Implement flat TypedDict AgentState subclass. All fields must be primitives or JSON strings.",
        "acceptance_criteria": "src/schemas/state.py defines the TypedDict; all fields Optional[primitive]; error_code/error_message present; no Pydantic imports.",
        "task_type": "feature",
        "dependencies": ["[impl] Write docs/02_design.md"],
    },
    {
        "title": "[impl] Implement input ingest + scope analysis nodes",
        "description": "Node 1 (RequirementIngestNode): S-1/S-2 input gate. Node 2 (ScopeAnalyzeNode): LLM scope/domain/complexity classification.",
        "acceptance_criteria": "Both nodes implement execute(self, state) -> dict; error_code set on violations; S-4 trace emits; unit tests pass.",
        "task_type": "feature",
        "dependencies": ["[impl] Define state schema src/schemas/state.py"],
    },
    {
        "title": "[impl] Implement core LLM decomposition node",
        "description": "TaskDecomposeNode: call LLM to produce atomic task list from requirement. Apply matched decomposition template.",
        "acceptance_criteria": "execute() writes raw_task_list as valid JSON list; agentcore_phase3 template produces 9-issue set; unit tests pass.",
        "task_type": "feature",
        "dependencies": ["[impl] Implement input ingest + scope analysis nodes"],
    },
    {
        "title": "[impl] Implement story point estimate + sprint assign nodes",
        "description": "StoryPointEstimateNode: assign story points. SprintAssignNode: distribute tasks across sprints by team_velocity_points.",
        "acceptance_criteria": "estimated_task_list has story_points + size_label; sprint_plan valid; overflow_warning correct; unit tests pass.",
        "task_type": "feature",
        "dependencies": ["[impl] Implement core LLM decomposition node"],
    },
    {
        "title": "[impl] Implement validation + response nodes (S-3 gate)",
        "description": "DecompositionValidateNode: structural S-3 gate. ResponseValidateNode: content S-3 gate + serialize to output_format.",
        "acceptance_criteria": "validation_result.passed=false on missing fields; S3_BLOCKED set on credential in output; unit tests pass.",
        "task_type": "feature",
        "dependencies": ["[impl] Implement story point estimate + sprint assign nodes"],
    },
    {
        "title": "[impl] Compose graph src/graph/graph.py",
        "description": "TaskDecompositionGraph(AgentBaseGraph): register all 7 nodes, define linear edge chain.",
        "acceptance_criteria": "graph.py registers all 7 nodes; edge chain correct; import isolation: no Level 0 imports; CI passes.",
        "task_type": "feature",
        "dependencies": ["[impl] Implement validation + response nodes (S-3 gate)"],
    },
    {
        "title": "[impl] Write test spec docs/03_test_spec.md + implement TC",
        "description": "docs/03_test_spec.md: define TC-01 through TC-08. Implement unit tests covering happy path, each error code, security gate bypasses.",
        "acceptance_criteria": "docs/03_test_spec.md committed; pytest passes with ≥80% branch coverage; TC-01 through TC-08 all green.",
        "task_type": "test",
        "dependencies": ["[impl] Compose graph src/graph/graph.py"],
    },
    {
        "title": "[impl] Implement Proof-of-Boundary tests + operation guide",
        "description": "tests/proof_of_boundary/: implement PB-1 through PB-6. docs/07_operation_guide.md: deployment, config, monitoring notes.",
        "acceptance_criteria": "PB-1 through PB-6 pass in CI; docs/07_operation_guide.md covers all sections; full CI green on develop branch.",
        "task_type": "test",
        "dependencies": ["[impl] Write test spec docs/03_test_spec.md + implement TC"],
    },
]


class MainNode(FunctionNode):
    """main slot: composite ScopeAnalyze → TaskDecompose → StoryPointEstimate → SprintAssign → DecompositionValidate."""

    # This node resolves billable Azure OpenAI secrets and performs provider calls.
    required_trust_level = TrustLevel.VERIFIED_EXTERNAL

    def __init__(
        self,
        llm_client: Optional[BaseLLM] = None,
        decomposition_style: str = "4h_atomic",
        team_velocity_points: int = DEFAULT_VELOCITY,
        max_tasks: int = DEFAULT_MAX_TASKS,
        timeout_s: float = 30.0,
        max_retry: int = 0,
    ) -> None:
        self._llm = llm_client
        self._decomposition_style = decomposition_style
        self._velocity = team_velocity_points
        self._max_tasks = max_tasks
        # Finding: _build_llm() previously passed neither "timeout" nor
        # "max_retries" to AzureOpenAIClient at all -- config.yaml's tuned
        # timeout_s/max_retry (120/2) were never read, the same dead-config
        # bug found and fixed in other fleet templates' equivalent nodes.
        # A transient Azure OpenAI
        # connection error (observed live: scope-analysis LLM call failing
        # with a plain connection error) was never retried by the SDK
        # itself, surfacing every time as this node's own
        # "Service temporarily unavailable" guidance instead of quietly
        # recovering on the next attempt.
        self._timeout_s = timeout_s
        self._max_retry = max_retry

    def execute(self, state: TaskDecompositionState) -> dict[str, Any]:
        # Finding (2026-08-19, Critical): this node previously stashed the
        # per-invocation `state` and a built LLM client on `self`
        # (self._state / self._llm_cache) -- mutable instance state on a
        # FunctionNode, prohibited by the framework because node instances are
        # shared across every invocation served from the registry's node
        # cache. Two concurrent invocations on the same node instance could
        # interleave: invocation B's execute() overwrites self._state while
        # invocation A's _build_llm() is still reading it, resolving A's LLM
        # call against B's InvocationContext/secrets. Resetting self._state
        # at the top of execute() does not prevent this -- it only reduces
        # the window, it does not close it. `state` and a freshly-built LLM
        # client are now threaded explicitly through every method that needs
        # them instead.
        if state.get("error_code"):
            return {}

        emit_trace_event(
            event_type="task_decomposition_started",
            payload={},
            state=state,
        )

        accumulated: dict[str, Any] = {}

        # Node 2: Scope analysis
        emitter().emit_event(
            event_type=EventType.PROGRESS_UPDATE,
            message="Analyzing scope, complexity, and decomposition strategy.",
            metadata={"stage": "scope_analysis"},
        )
        result = self._scope_analyze({**state, **accumulated}, state)
        accumulated.update(result)
        if accumulated.get("error_code"):
            return self._fatal_error_result(accumulated, state)

        # Node 3: Task decomposition
        emitter().emit_event(
            event_type=EventType.PROGRESS_UPDATE,
            message="Decomposing the requirement into sprint-ready tasks.",
            metadata={"stage": "task_decomposition"},
        )
        result = self._task_decompose({**state, **accumulated}, state)
        accumulated.update(result)
        error = accumulated.get("error_code")
        if error and error not in ("OVERFLOW_WARNING",):
            return self._fatal_error_result(accumulated, state)

        # Node 4: Story point estimation
        emitter().emit_event(
            event_type=EventType.PROGRESS_UPDATE,
            message="Estimating effort and assigning tasks to sprints.",
            metadata={"stage": "sprint_planning"},
        )
        result = self._story_point_estimate({**state, **accumulated}, state)
        accumulated.update(result)
        error = accumulated.get("error_code")
        if error and error not in ("OVERFLOW_WARNING",):
            return self._fatal_error_result(accumulated, state)

        # Node 5: Sprint assignment
        result = self._sprint_assign({**state, **accumulated})
        accumulated.update(result)
        error = accumulated.get("error_code")
        if error and error not in ("OVERFLOW_WARNING",):
            return self._fatal_error_result(accumulated, state)

        # Node 6: Structural validation
        result = self._decomposition_validate({**state, **accumulated})
        accumulated.update(result)

        # Signal success to SDK router so it routes to post_process, not finalize
        if "status" not in accumulated or accumulated.get("status") == AgentStatus.PENDING.value:
            accumulated["status"] = AgentStatus.SUCCESS.value

        return accumulated

    # ── Node 2: Scope ─────────────────────────────────────────────────────────

    def _scope_analyze(self, state: dict[str, Any], original_state: dict[str, Any]) -> dict[str, Any]:
        requirement: str = state.get("requirement_text") or state.get("user_input") or ""
        requirement_lower = requirement.lower()
        matched_template = next(
            (t for kw, t in _DOMAIN_TEMPLATE_MAP.items() if kw in requirement_lower),
            "generic",
        )

        complexity_guide_text = "\n".join(f"  {k}: {v}" for k, v in _COMPLEXITY_GUIDE.items())
        prompt = _SCOPE_PROMPT.format(
            complexity_guide=complexity_guide_text,
            requirement=requirement[:3000],
        )

        try:
            raw = self._llm_generate(prompt, original_state)
        except Exception as exc:  # noqa: BLE001
            emit_trace_event(event_type="scope_analyze_llm_error", payload={"error": str(exc)[:200]}, state=state)
            return {"error_code": "S2_LLM_ERROR", "error_message": f"LLM scope analysis failed: {str(exc)[:200]}"}

        scope_data = self._parse_scope_response(raw, matched_template)
        if matched_template != "generic":
            scope_data["template_matched"] = matched_template

        emit_trace_event(
            event_type="scope_analyze_ok",
            payload={
                "domain": scope_data.get("domain"),
                "complexity": scope_data.get("complexity"),
                "template": scope_data.get("template_matched"),
            },
            state=state,
        )
        return {
            "scope_summary": json.dumps(scope_data),
            "decomposition_template": scope_data.get("template_matched", "generic"),
        }

    def _parse_scope_response(self, raw: str, fallback_template: str) -> dict[str, Any]:
        cleaned = re.sub(r"^```[a-z]*\n?", "", raw.strip(), flags=re.MULTILINE)
        cleaned = re.sub(r"```$", "", cleaned.strip())
        try:
            parsed = json.loads(cleaned)
            data: dict[str, Any] = parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, ValueError):
            data = {}
        if not data:
            # Finding (2026-08-19, High): json.loads() succeeding with a
            # non-dict top level (e.g. the LLM returning "[]") is valid JSON
            # but the wrong shape -- confirmed live to crash the next line's
            # data.setdefault() with AttributeError: 'list' object has no
            # attribute 'setdefault'. Treated the same as a parse failure:
            # fall back to the regex best-effort extraction below.
            m = re.search(r'"complexity"\s*:\s*"([SMXL]+)"', cleaned)
            if m:
                data["complexity"] = m.group(1)
            m = re.search(r'"domain"\s*:\s*"([a-z_]+)"', cleaned)
            if m:
                data["domain"] = m.group(1)
        data.setdefault("domain", "generic")
        data.setdefault("complexity", "M")
        data.setdefault("complexity_rationale", "Unable to determine; defaulting to Medium.")
        data.setdefault("template_matched", fallback_template)
        data.setdefault("scope_notes", "Scope analysis completed.")
        if data["complexity"] not in _COMPLEXITY_GUIDE:
            data["complexity"] = "M"
        return data

    # ── Node 3: Decompose ─────────────────────────────────────────────────────

    def _task_decompose(self, state: dict[str, Any], original_state: dict[str, Any]) -> dict[str, Any]:
        requirement: str = state.get("requirement_text") or state.get("user_input") or ""
        scope_summary: str = state.get("scope_summary", "{}")
        template_name: str = state.get("decomposition_template", "generic")

        if template_name == "agentcore_phase3":
            tasks = list(_AGENTCORE_PHASE3_TASKS)
        else:
            tasks, llm_succeeded = self._llm_decompose(requirement, scope_summary, original_state)
            # Finding (2026-08-19, High): a failed/unparseable LLM call
            # previously returned [] indistinguishably from a genuine
            # zero-task decomposition, and _decomposition_validate() treats
            # an empty task list as trivially "passed" (no invalid_titles to
            # report) -- the whole pipeline completed with status=SUCCESS
            # and zero tasks, silently discarding the failure. Only a
            # genuinely LLM-returned empty list (llm_succeeded=True) is
            # treated as valid; a failed call is fatal.
            if not llm_succeeded:
                return {
                    "error_code": "ERR_LLM_DECOMPOSE_FAILED",
                    "error_message": "TaskDecompose: LLM call failed or returned unparseable output.",
                    "status": AgentStatus.ERROR.value,
                }

        overflow = len(tasks) > self._max_tasks
        if overflow:
            tasks = tasks[: self._max_tasks]

        tasks = [self._normalise_task(t) for t in tasks]

        emit_trace_event(
            event_type="task_decompose_ok",
            payload={"task_count": len(tasks), "template": template_name, "capped": overflow},
            state=state,
        )

        result: dict[str, Any] = {"raw_task_list": json.dumps(tasks), "task_count": len(tasks)}
        if overflow:
            result["error_code"] = "OVERFLOW_WARNING"
            result["error_message"] = f"Output capped at {self._max_tasks} tasks. Consider splitting the requirement."
        return result

    def _llm_decompose(
        self, requirement: str, scope_summary: str, original_state: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], bool]:
        """Return (tasks, succeeded). succeeded=False means the LLM call itself
        failed or its output was unparseable -- distinct from succeeded=True
        with an empty list, which means the LLM was genuinely reached and
        genuinely returned zero tasks."""
        try:
            scope = json.loads(scope_summary)
            complexity = scope.get("complexity", "M")
        except (json.JSONDecodeError, ValueError):
            complexity = "M"
        target_map = {"S": 3, "M": 8, "L": 15, "XL": 20}
        target_count = min(target_map.get(complexity, 8), self._max_tasks)
        prompt = _DECOMPOSE_PROMPT.format(
            decomposition_style=self._decomposition_style,
            target_count=target_count,
            requirement=requirement[:3000],
            scope_summary=scope_summary[:500],
        )
        try:
            raw = self._llm_generate(prompt, original_state)
        except Exception as exc:  # noqa: BLE001
            emit_trace_event(
                event_type="task_decompose_llm_error",
                payload={"error": str(exc)[:200]},
                state=original_state,
            )
            return [], False
        return self._parse_task_list(raw)

    def _parse_task_list(self, raw: str) -> tuple[list[dict[str, Any]], bool]:
        cleaned = re.sub(r"^```[a-z]*\n?", "", raw.strip(), flags=re.MULTILINE)
        cleaned = re.sub(r"```$", "", cleaned.strip())
        try:
            data = json.loads(cleaned)
            if isinstance(data, list):
                return data, True
            if isinstance(data, dict):
                for key in ("tasks", "items", "result"):
                    if isinstance(data.get(key), list):
                        return data[key], True
        except (json.JSONDecodeError, ValueError):
            return [], False
        # Parsed successfully but shape didn't match anything expected
        # (e.g. a JSON object with no tasks/items/result list key) -- treat
        # as a parse failure, not a genuine empty result.
        return [], False

    def _normalise_task(self, task: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(task, dict):
            task = {}
        return {
            "title": str(task.get("title", "Untitled task")),
            "description": str(task.get("description", "")),
            "acceptance_criteria": str(task.get("acceptance_criteria", "")),
            "task_type": task.get("task_type", "feature") if task.get("task_type") in VALID_TASK_TYPES else "feature",
            "dependencies": task.get("dependencies", []) if isinstance(task.get("dependencies"), list) else [],
        }

    # ── Node 4: Story point estimate ──────────────────────────────────────────

    def _story_point_estimate(self, state: dict[str, Any], original_state: dict[str, Any]) -> dict[str, Any]:
        error_code = state.get("error_code")
        if error_code and error_code != "OVERFLOW_WARNING":
            return {}

        raw_task_list = state.get("raw_task_list", "[]")
        try:
            tasks = json.loads(raw_task_list)
        except (json.JSONDecodeError, ValueError):
            return {"error_code": "ERR_INVALID_TASK_LIST", "error_message": "raw_task_list is not valid JSON."}

        if not tasks:
            return {"estimated_task_list": "[]", "total_story_points": 0, "oversized_tasks": "[]"}

        estimated = self._estimate_with_llm(tasks, original_state)
        estimated = [self._ensure_estimate(t) for t in estimated]

        total_points = sum(t.get("story_points", 3) for t in estimated)
        oversized = [t["title"] for t in estimated if t.get("story_points", 0) > 8]

        emit_trace_event(
            event_type="story_point_estimate_ok",
            payload={
                "task_count": len(estimated),
                "total_story_points": total_points,
                "oversized_count": len(oversized),
            },
            state=state,
        )
        return {
            "estimated_task_list": json.dumps(estimated),
            "total_story_points": total_points,
            "oversized_tasks": json.dumps(oversized),
        }

    def _estimate_with_llm(self, tasks: list[dict[str, Any]], original_state: dict[str, Any]) -> list[dict[str, Any]]:
        prompt = _ESTIMATE_PROMPT.format(
            decomposition_style=self._decomposition_style,
            tasks_json=json.dumps(tasks, ensure_ascii=False, indent=2)[:4000],
        )
        try:
            raw = self._llm_generate(prompt, original_state)
        except Exception:  # noqa: BLE001
            emit_trace_event(
                event_type="story_point_estimate_llm_fallback",
                payload={"reason": "llm_call_failed", "task_count": len(tasks)},
                state=original_state,
            )
            return tasks
        cleaned = re.sub(r"^```[a-z]*\n?", "", raw.strip(), flags=re.MULTILINE)
        cleaned = re.sub(r"```$", "", cleaned.strip())
        try:
            result = json.loads(cleaned)
            if isinstance(result, list) and len(result) == len(tasks):
                return result
        except (json.JSONDecodeError, ValueError):
            pass
        emit_trace_event(
            event_type="story_point_estimate_llm_fallback",
            payload={"reason": "length_mismatch_or_parse_error", "task_count": len(tasks)},
            state=original_state,
        )
        return tasks

    def _ensure_estimate(self, task: dict[str, Any]) -> dict[str, Any]:
        if isinstance(task.get("story_points"), int) and task.get("size_label") in _POINTS_BY_SIZE:
            return task
        if task.get("size_label") in _POINTS_BY_SIZE:
            task["story_points"] = _POINTS_BY_SIZE[task["size_label"]]
            return task
        if isinstance(task.get("story_points"), int):
            valid_pts = [p for _, p in SIZE_SCALE]
            snapped = min(valid_pts, key=lambda x: abs(x - task["story_points"]))
            task["story_points"] = snapped
            task["size_label"] = _SIZES_BY_POINTS[snapped]
            return task
        size_label = _DEFAULT_SIZE_BY_TYPE.get(task.get("task_type", "feature"), "M")
        task["size_label"] = size_label
        task["story_points"] = _POINTS_BY_SIZE[size_label]
        return task

    # ── Node 5: Sprint assign ─────────────────────────────────────────────────

    def _sprint_assign(self, state: dict[str, Any]) -> dict[str, Any]:
        error_code = state.get("error_code")
        if error_code and error_code not in ("OVERFLOW_WARNING",):
            return {}

        velocity = self._velocity
        try:
            tasks = json.loads(state.get("estimated_task_list", "[]"))
        except (json.JSONDecodeError, ValueError):
            return {"error_code": "ERR_INVALID_TASK_LIST", "error_message": "estimated_task_list is not valid JSON."}

        if not tasks:
            return {"sprint_plan": "[]", "sprint_count": 0, "overflow_warning": False}

        sprints: list[dict[str, Any]] = []
        current: dict[str, Any] = {"sprint_id": 1, "tasks": [], "points_total": 0}
        for task in tasks:
            pts = task.get("story_points", 3)
            if pts > velocity:
                if current["tasks"]:
                    sprints.append(current)
                sprints.append({"sprint_id": 0, "tasks": [task["title"]], "points_total": pts})
                current = {"sprint_id": 0, "tasks": [], "points_total": 0}
                continue
            if current["points_total"] + pts > velocity:
                sprints.append(current)
                current = {"sprint_id": 0, "tasks": [task["title"]], "points_total": pts}
            else:
                current["tasks"].append(task["title"])
                current["points_total"] += pts
        if current["tasks"]:
            sprints.append(current)
        # Assign sequential IDs after packing is complete
        for idx, sprint in enumerate(sprints, start=1):
            sprint["sprint_id"] = idx

        overflow = len(sprints) > 1
        emit_trace_event(
            event_type="sprint_assign_ok",
            payload={"sprint_count": len(sprints), "velocity_used": velocity, "overflow_warning": overflow},
            state=state,
        )
        return {"sprint_plan": json.dumps(sprints), "sprint_count": len(sprints), "overflow_warning": overflow}

    # ── Node 6: Validate ──────────────────────────────────────────────────────

    def _decomposition_validate(self, state: dict[str, Any]) -> dict[str, Any]:
        error_code = state.get("error_code")
        if error_code and error_code not in ("OVERFLOW_WARNING",):
            return {}

        try:
            tasks = json.loads(state.get("estimated_task_list", "[]"))
        except (json.JSONDecodeError, ValueError):
            tasks = []

        _REQUIRED_TASK_FIELDS = {"title", "description", "acceptance_criteria", "story_points"}
        issues: list[str] = []
        invalid_titles: list[str] = []

        for task in tasks:
            if not isinstance(task, dict):
                issues.append("Non-dict entry in estimated_task_list — skipped.")
                continue
            title = task.get("title", "<untitled>")
            missing = [f for f in _REQUIRED_TASK_FIELDS if not task.get(f) and task.get(f) != 0]
            if missing:
                issues.append(f"Task '{title}' missing required field(s): {', '.join(sorted(missing))}.")
                invalid_titles.append(title)

        passed = len(invalid_titles) == 0
        task_count = state.get("task_count") or len(tasks)
        overflow_warning = state.get("overflow_warning") or False
        if task_count > _TASK_COUNT_ADVISORY:
            overflow_warning = True
            issues.append(f"Advisory: {task_count} tasks generated (>{_TASK_COUNT_ADVISORY}). Consider splitting.")

        emit_trace_event(
            event_type="validate_ok" if passed else "validate_fail",
            payload={"validation_passed": passed, "invalid_task_count": len(invalid_titles), "task_count": task_count},
            state=state,
        )

        result: dict[str, Any] = {
            "validation_result": json.dumps({"passed": passed, "issues": issues}),
            "invalid_tasks": json.dumps(invalid_titles),
            "overflow_warning": overflow_warning,
        }
        if not passed:
            result["error_code"] = "INVALID_TASKS"
            result["error_message"] = f"{len(invalid_titles)} task(s) failed structural validation."
        return result

    @staticmethod
    def _fatal_error_result(accumulated: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        """Build the terminal error response for a fatal error_code raised
        mid-pipeline (e.g. S2_LLM_ERROR, ERR_LLM_DECOMPOSE_FAILED).

        Finding 3: trace_id/correlation_id must be read from `state` (the
        full invocation state passed into execute()), not `accumulated`
        (the delta this node has built up so far). InitializeNode sets
        both at the very start of the pipeline, before MainNode ever
        runs -- accumulated never carries them unless a sub-step happens
        to copy them in, so reading accumulated.get("trace_id") here
        always fell through to the "unavailable" fallback, producing a
        reference no one could actually use to correlate a support
        report against backend traces.

        Finding 1: an early-return here previously left both "status" and
        "formatted_output" unset. LangGraph state is a cumulative merge —
        an omitted "status" key keeps whatever value an earlier node set,
        and RequirementIngestNode (pre_process) always sets status=SUCCESS
        on valid input, so a bare error_code without status here left the
        pipeline looking like a success. That reached
        shared.bootstrap.marketplace_app's output assembly with
        status=success and output=None, raising `RuntimeError: ...
        returned status=success but output is missing`.

        Finding 2 (found after fixing #1 with status=ERROR): AgentBaseGraph
        .route() sends only status=SUCCESS to post_process — status=ERROR
        (or anything but SUCCESS/RETRY) routes straight to finalize, which
        never sets formatted_output. So ResponseValidateNode's own
        guidance for an unrecognised error_code can never run on this
        path either way; formatted_output must be produced here in
        MainNode itself. But separately, shared.bootstrap
        .marketplace_app's runner treats ANY status other than SUCCESS as
        a hard failure — `if status != AgentStatus.SUCCESS.value: raise
        RuntimeError(...)` runs BEFORE it ever reads "output", so a
        status=ERROR response (even with a perfectly good
        formatted_output) still crashed with "invocation did not succeed:
        status='error'" and never reached the user. The only status value
        the Marketplace runner will actually deliver to a user is SUCCESS
        — exactly the same reason RequirementIngestNode's own
        _input_guidance() (pre_process_node.py) and
        ResponseValidateNode's input_validation_failed branch
        (post_process_node.py) both report SUCCESS while describing a
        rejection. This node must follow the same contract: a fatal
        mid-pipeline error is reported as a normal SUCCESS response whose
        content happens to be an error message, not as an agent-level
        failure.
        """
        # error_code is deliberately NOT cleared here (unlike
        # ResponseValidateNode's input_validation_failed branch, which
        # clears it once formatted_output is final): status=SUCCESS routes
        # this straight into ResponseValidateNode next, and its own
        # error_code whitelist check (see post_process_node.py's matching
        # finding) is what stops it from overwriting formatted_output
        # below with a normal (empty) task-decomposition render.
        reference = str(state.get("trace_id") or state.get("correlation_id") or "unavailable")
        accumulated["status"] = AgentStatus.SUCCESS.value
        accumulated.setdefault(
            "formatted_output",
            "# Service temporarily unavailable\n\n"
            "The task decomposition service is temporarily unavailable. This is not caused "
            f"by your request — please try again shortly. Reference: {reference}.",
        )
        return accumulated

    # ── LLM helper ────────────────────────────────────────────────────────────

    def _llm_generate(self, prompt: str, state: dict[str, Any]) -> str:
        llm = self._llm or self._build_llm(state)
        response = llm.complete([{"role": "user", "content": prompt}])
        return str(response.get("content", "")) if isinstance(response, dict) else str(response)

    def _build_llm(self, state: dict[str, Any]) -> BaseLLM:
        """Build the shared Azure client from invocation-scoped secrets."""
        ctx = InvocationContext.from_state(state)
        return AzureOpenAIClient(
            {
                "api_key": ctx.secrets.require("AZURE_OPENAI_API_KEY"),
                "azure_endpoint": ctx.secrets.require("AZURE_OPENAI_ENDPOINT"),
                "azure_deployment": ctx.secrets.require("AZURE_OPENAI_DEPLOYMENT"),
                "timeout": self._timeout_s,
                "max_retries": self._max_retry,
            }
        )
