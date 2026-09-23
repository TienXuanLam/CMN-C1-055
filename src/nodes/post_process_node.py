"""AgentCore Platform v1.0"""

import json
from typing import Any, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.services.events import emitter
from shared.services.events.types import EventType
from shared.utils.audit_logger import emit_trace_event

from src.nodes._security_patterns import INTERNAL_URL_PATTERNS, scan_for_credentials, scan_for_pii
from src.schemas.state import TaskDecompositionState

ADVISORY_NOTE = (
    "This is decomposition guidance. " "Final sprint planning decisions remain with PM and engineering team."
)

DEFAULT_OUTPUT_FORMAT = "markdown"


class ResponseValidateNode(FunctionNode):
    """post_process slot: S-3 content gate + output serialisation."""

    required_trust_level = TrustLevel.ANONYMOUS

    def __init__(self, output_format: str = DEFAULT_OUTPUT_FORMAT) -> None:
        self._output_format = output_format

    def execute(self, state: TaskDecompositionState) -> dict[str, Any]:
        if state.get("input_validation_failed"):
            emit_trace_event(
                event_type="input_guidance_returned",
                payload={},
                state=state,
            )
            return {
                "formatted_output": str(state.get("formatted_output") or "Invalid input."),
                "input_validation_failed": None,
                "error_code": None,
                "error_message": None,
                "status": AgentStatus.SUCCESS.value,
            }

        error_code = state.get("error_code")
        if error_code and error_code not in ("OVERFLOW_WARNING", "INVALID_TASKS"):
            # Finding 1: returning {} here on an unrecognised fatal
            # error_code (e.g. S2_LLM_ERROR, ERR_LLM_DECOMPOSE_FAILED) left
            # both formatted_output and status unset, which (if an
            # upstream MainNode early-return also omitted "status" — see
            # main_node.py's matching finding) surfaced as the Marketplace
            # runner's "status=success but output is missing" RuntimeError.
            #
            # Finding 2: the Marketplace runner
            # (shared.bootstrap.marketplace_app) treats ANY status other
            # than SUCCESS as a hard agent failure, checked BEFORE it ever
            # reads "output" -- so an earlier version of this fix that used
            # status=ERROR here just traded one crash
            # ("status=success but output is missing") for another
            # ("invocation did not succeed: status='error'"), still never
            # showing the user this formatted_output. status=SUCCESS is
            # the only value the Marketplace runner will actually deliver
            # to a user -- the same reason RequirementIngestNode's
            # _input_guidance() and this node's own
            # input_validation_failed branch above both report SUCCESS
            # while describing a rejection. setdefault (not an overwrite)
            # preserves MainNode._fatal_error_result()'s own
            # formatted_output when it already set one; error_code/
            # error_message are cleared here (the end of the pipeline),
            # not by MainNode, so this branch can still tell an
            # unhandled error apart from a normal successful run.
            emit_trace_event(
                event_type="response_validate_unhandled_error",
                payload={"error_code": error_code},
                state=state,
            )
            reference = str(state.get("trace_id") or state.get("correlation_id") or "unavailable")
            result: dict[str, Any] = {
                "status": AgentStatus.SUCCESS.value,
                "error_code": None,
                "error_message": None,
            }
            result.setdefault("formatted_output", state.get("formatted_output"))
            if not result.get("formatted_output"):
                result["formatted_output"] = (
                    "# Service temporarily unavailable\n\n"
                    "The task decomposition service is temporarily unavailable. This is not caused "
                    f"by your request — please try again shortly. Reference: {reference}."
                )
            return result

        fmt = self._output_format

        emitter().emit_event(
            event_type=EventType.PROGRESS_UPDATE,
            message="Formatting and validating the task decomposition plan.",
            metadata={"stage": "output_assembly", "output_format": fmt},
        )

        try:
            tasks = json.loads(state.get("estimated_task_list") or "[]")
        except (json.JSONDecodeError, ValueError):
            tasks = []

        try:
            sprint_plan = json.loads(state.get("sprint_plan") or "[]")
        except (json.JSONDecodeError, ValueError):
            sprint_plan = []

        try:
            validation = json.loads(state.get("validation_result") or '{"passed": true, "issues": []}')
        except (json.JSONDecodeError, ValueError):
            validation = {"passed": True, "issues": []}

        metadata = {
            "total_tasks": len(tasks),
            "total_points": state.get("total_story_points") or 0,
            "sprint_count": state.get("sprint_count") or 0,
            "decomposition_template": state.get("decomposition_template") or "generic",
            "overflow_warning": state.get("overflow_warning") or False,
            "validation_passed": validation.get("passed", True),
            "validation_issues": validation.get("issues", []),
        }

        if fmt == "markdown":
            raw_output = self._to_markdown(tasks, sprint_plan, metadata)
        elif fmt == "gitlab_issues":
            raw_output = json.dumps(self._to_gitlab_issues(tasks, sprint_plan, metadata), ensure_ascii=False, indent=2)
        elif fmt == "jira":
            raw_output = json.dumps(self._to_jira(tasks, sprint_plan, metadata), ensure_ascii=False, indent=2)
        else:
            raw_output = json.dumps(self._to_github_issues(tasks, sprint_plan, metadata), ensure_ascii=False, indent=2)

        violation = self._run_output_gate(raw_output)
        if violation:
            emit_trace_event(event_type="s3_blocked", payload={"violation_type": violation}, state=state)
            return {
                "error_code": "S3_BLOCKED",
                "error_message": f"S-3 output gate blocked: {violation} detected in final output.",
                "status": AgentStatus.ERROR.value,
            }

        emit_trace_event(
            event_type="response_validate_ok",
            payload={
                "output_format": fmt,
                "task_count": len(tasks),
                "sprint_count": metadata["sprint_count"],
                "total_story_points": metadata["total_points"],
                "decomposition_template": metadata["decomposition_template"],
                "validation_passed": metadata["validation_passed"],
            },
            state=state,
        )

        return {
            "final_output": raw_output,
            "output_format_used": fmt,
            "formatted_output": raw_output,
            "status": AgentStatus.SUCCESS.value,
        }

    def _run_output_gate(self, output: str) -> Optional[str]:
        if scan_for_credentials(output):
            return "credential pattern"
        for pat in INTERNAL_URL_PATTERNS:
            if pat.search(output):
                return "internal URL"
        # include_name=False: this output is LLM-synthesised task/issue
        # content (titles, headings, labels), not free-form user input --
        # see _security_patterns.scan_for_pii's docstring for the confirmed
        # false-positive shape ("Story Points", "Implement User Login", ...).
        if scan_for_pii(output, include_name=False):
            return "PII pattern"
        return None

    def _to_github_issues(
        self, tasks: list[dict[str, Any]], sprint_plan: list[dict[str, Any]], meta: dict[str, Any]
    ) -> dict[str, Any]:
        issues: list[dict[str, Any]] = []
        for task in tasks:
            pts = task.get("story_points", 0)
            size = task.get("size_label", "M")
            body = (
                f"## Description\n{task.get('description', '')}\n\n"
                f"## Acceptance Criteria\n{task.get('acceptance_criteria', '')}\n\n"
                f"## Estimate\n{pts} point(s) ({size})\n\n"
                f"## Dependencies\n" + (", ".join(task.get("dependencies", [])) or "None")
            )
            issues.append(
                {
                    "title": task.get("title", "Untitled"),
                    "body": body,
                    "labels": ["phase:impl", "unclaimed"],
                    "story_points": pts,
                    "task_type": task.get("task_type", "feature"),
                }
            )
        return {
            "issues": issues,
            "sprint_plan": [
                {"sprint": s["sprint_id"], "issues": s["tasks"], "total_points": s["points_total"]} for s in sprint_plan
            ],
            "advisory": ADVISORY_NOTE,
            "metadata": meta,
        }

    def _to_gitlab_issues(
        self, tasks: list[dict[str, Any]], sprint_plan: list[dict[str, Any]], meta: dict[str, Any]
    ) -> dict[str, Any]:
        issues: list[dict[str, Any]] = []
        for task in tasks:
            pts = task.get("story_points", 0)
            size = task.get("size_label", "M")
            description = (
                f"## Description\n{task.get('description', '')}\n\n"
                f"## Acceptance Criteria\n{task.get('acceptance_criteria', '')}\n\n"
                f"## Estimate\n{pts} point(s) ({size})\n\n"
                f"## Dependencies\n" + (", ".join(task.get("dependencies", [])) or "None")
            )
            issues.append(
                {
                    "title": task.get("title", "Untitled"),
                    "description": description,
                    "labels": "phase:impl,unclaimed",
                    "weight": pts,
                    "task_type": task.get("task_type", "feature"),
                }
            )
        return {"issues": issues, "sprint_plan": sprint_plan, "advisory": ADVISORY_NOTE, "metadata": meta}

    def _to_jira(
        self, tasks: list[dict[str, Any]], sprint_plan: list[dict[str, Any]], meta: dict[str, Any]
    ) -> dict[str, Any]:
        stories: list[dict[str, Any]] = []
        for task in tasks:
            stories.append(
                {
                    "summary": task.get("title", "Untitled"),
                    "description": task.get("description", ""),
                    "acceptanceCriteria": task.get("acceptance_criteria", ""),
                    "issueType": "Story",
                    "storyPoints": task.get("story_points", 0),
                    "labels": ["phase-impl", "unclaimed"],
                    "dependencies": task.get("dependencies", []),
                }
            )
        return {"stories": stories, "sprintPlan": sprint_plan, "advisory": ADVISORY_NOTE, "metadata": meta}

    def _to_markdown(self, tasks: list[dict[str, Any]], sprint_plan: list[dict[str, Any]], meta: dict[str, Any]) -> str:
        lines = [
            "# Task Decomposition Plan",
            "",
            f"**Total tasks:** {meta['total_tasks']}  ",
            f"**Total story points:** {meta['total_points']}  ",
            f"**Sprints required:** {meta['sprint_count']}  ",
            f"**Decomposition template:** {meta['decomposition_template']}  ",
            "",
            f"> {ADVISORY_NOTE}",
            "",
        ]
        if meta.get("validation_issues"):
            lines += ["## Validation Issues", ""]
            for issue in meta["validation_issues"]:
                lines.append(f"- {issue}")
            lines.append("")
        for sprint in sprint_plan:
            lines += [f"## Sprint {sprint['sprint_id']} — {sprint['points_total']} points", ""]
            for title in sprint["tasks"]:
                task = next((t for t in tasks if t.get("title") == title), None)
                if task:
                    pts = task.get("story_points", 0)
                    size = task.get("size_label", "M")
                    lines += [
                        f"### [{task.get('task_type','feature').upper()}] {title} ({pts} pts / {size})",
                        "",
                        task.get("description", ""),
                        "",
                        "**Acceptance Criteria:**",
                        task.get("acceptance_criteria", ""),
                        "",
                    ]
                    deps = task.get("dependencies", [])
                    if deps:
                        lines += [f"**Dependencies:** {', '.join(deps)}", ""]
        return "\n".join(lines)
