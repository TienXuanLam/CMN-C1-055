"""AgentCore Platform v1.0"""

from typing import Optional

from framework.schemas.agent_state import AgentState


class TaskDecompositionState(AgentState):
    """Flat TypedDict state for CMN-C1-055 TaskDecompositionAgent.

    All complex types serialised as json.dumps() strings (msgpack safety).
    State fields use only primitives (str, int, bool, None) — no ORM objects.

    Field groups:
      Input:        requirement_text, input_validation_failed
      Scope:        scope_summary, decomposition_template
      Decompose:    raw_task_list, task_count
      Estimation:   estimated_task_list, total_story_points, oversized_tasks
      Sprint:       sprint_plan, sprint_count, overflow_warning
      Validation:   validation_result, invalid_tasks
      Output:       final_output, output_format_used, formatted_output
      Errors:       error_code, error_message
    """

    # ---------- Input ----------
    requirement_text: Optional[str]
    input_validation_failed: Optional[str]  # "true" while returning guidance

    # ---------- Scope ----------
    scope_summary: Optional[str]  # json.dumps({domain, complexity, template_matched})
    decomposition_template: Optional[str]

    # ---------- Decomposition ----------
    raw_task_list: Optional[str]  # json.dumps(list[dict])
    task_count: Optional[int]

    # ---------- Estimation ----------
    estimated_task_list: Optional[str]  # json.dumps(list[dict]) with story_points added
    total_story_points: Optional[int]
    oversized_tasks: Optional[str]  # json.dumps(list[str]) — task titles > 8 pts

    # ---------- Sprint ----------
    sprint_plan: Optional[str]  # json.dumps([{sprint_id, tasks, points_total}])
    sprint_count: Optional[int]
    overflow_warning: Optional[bool]

    # ---------- Validation ----------
    validation_result: Optional[str]  # json.dumps({passed: bool, issues: [str]})
    invalid_tasks: Optional[str]  # json.dumps(list[str])

    # ---------- Output ----------
    final_output: Optional[str]
    output_format_used: Optional[str]
    formatted_output: Optional[str]  # Plain Markdown returned as SDK result["output"]

    # ---------- Error propagation ----------
    error_code: Optional[str]
    error_message: Optional[str]
