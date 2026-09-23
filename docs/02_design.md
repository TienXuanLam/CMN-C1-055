# CMN-C1-055 Agent Task Decomposition & Sprint Planning Agent — Design Specification

**Template ID:** CMN-C1-055
**Agent Name:** TaskDecompositionAgent
**Category:** Cat 1 (CMN — generic, industry-agnostic)
**L1 Base**: `AgentBaseGraph` (L1-direct per 2026-05-18 policy)
**SDK**: `agenticstar-agentcore==1.0.3`
**Stage**: stg-complete
**Date:** 2026-05-23 (updated 2026-06-24 post-migration)
**Author:** TienTV34

---

## 1. Overview

`TaskDecompositionAgent` decomposes complex business requirements into atomic,
sprint-ready tasks (~4h granularity), estimates story points, and produces
structured plans in multiple output formats.

**Self-dogfood value**: The built-in `agentcore_phase3` decomposition template
generates the standard 9-issue Phase 3 implementation set for any new Agent 1000
template — meta-productivity for the Agent 1000 production line.

---

## 2. Architecture

```
Level 0: agenticstar SDK           ← NOT imported
Level 1: framework/                ← AgentBaseGraph, FunctionNode (L1-direct)
Level 3: src/ (this template)      ← domain nodes + graph
```

```python
from framework.graph.agent_base_graph import AgentBaseGraph
from framework.nodes.function_node import FunctionNode

class TaskDecompositionGraph(AgentBaseGraph): ...
class RequirementIngestNode(FunctionNode): ...
```

---

## 3. SDK Node Mapping (3-slot)

| SDK Slot | Node Class | Responsibility | Trust Level |
|---|---|---|---|
| `pre_process` | `RequirementIngestNode` | S-1/S-2 input gate | `VERIFIED_EXTERNAL` |
| `main` | `MainNode` (composite) | Runs 5 sequential steps internally: ScopeAnalyze → TaskDecompose → StoryPointEstimate → SprintAssign → DecompositionValidate. Each step reads from the accumulated result of previous steps within the same `execute()` call. Returns `status=SUCCESS` on success so SDK router proceeds to `post_process`. Resolves billable Azure OpenAI secrets and performs provider calls. | `VERIFIED_EXTERNAL` |
| `post_process` | `ResponseValidateNode` | S-3 output gate + serialisation | `ANONYMOUS` |

**Graph**: `TaskDecompositionGraph(AgentBaseGraph)`, `required_trust_level = TrustLevel.VERIFIED_EXTERNAL`

**Input pattern**: `user_input` is the primary invoke channel; `RequirementIngestNode` reads
`state.get("user_input") or state.get("requirement_text")`, so `input_context.requirement_text`
is only a fallback when `user_input` is absent.
```python
agent.invoke(
    user_input=requirement_text,
    input_context={"requirement_text": requirement_text},
    ctx=ctx,
)
```

**Config injection** (`config/config.yaml` → `self.config` → `register_nodes()`):
- `decomposition_style`, `team_velocity_points`, `max_tasks` → `MainNode`
- `output_format` → `ResponseValidateNode`

---

## 4. Node Flow (inside MainNode composite)

```
RequirementIngestNode  [pre_process]
        ↓
    MainNode           [main]
    ├── _scope_analyze()           → scope_summary, decomposition_template
    ├── _task_decompose()          → raw_task_list, task_count
    ├── _story_point_estimate()    → estimated_task_list, total_story_points
    ├── _sprint_assign()           → sprint_plan, sprint_count, overflow_warning
    └── _decomposition_validate()  → validation_result, invalid_tasks
        ↓
ResponseValidateNode   [post_process]
```

### Inner Step Responsibilities

| Step | Responsibility |
|---|---|
| `_scope_analyze()` | LLM: domain, complexity (S/M/L/XL), template match |
| `_task_decompose()` | LLM: atomic task list; applies `agentcore_phase3` template if matched |
| `_story_point_estimate()` | LLM: T-shirt sizing (XS=1, S=2, M=3, L=5, XL=8); rule-based fallback |
| `_sprint_assign()` | Greedy bin-packing by `team_velocity_points`; set `overflow_warning` |
| `_decomposition_validate()` | Check required fields per task; advisory if task_count > 15 |

---

## 5. State Schema

**File**: `src/schemas/state.py`

```python
class TaskDecompositionState(AgentState):
    # Input
    requirement_text: Optional[str]
    input_validation_failed: Optional[str]   # "true" while returning guidance

    # Scope
    scope_summary: Optional[str]             # json.dumps({domain, complexity, template_matched})
    decomposition_template: Optional[str]

    # Decomposition
    raw_task_list: Optional[str]             # json.dumps(list[dict])
    task_count: Optional[int]

    # Estimation
    estimated_task_list: Optional[str]       # json.dumps(list[dict]) with story_points
    total_story_points: Optional[int]
    oversized_tasks: Optional[str]           # json.dumps(list[str]) — titles > 8 pts

    # Sprint
    sprint_plan: Optional[str]               # json.dumps([{sprint_id, tasks, points_total}])
    sprint_count: Optional[int]
    overflow_warning: Optional[bool]

    # Validation
    validation_result: Optional[str]         # json.dumps({passed, issues})
    invalid_tasks: Optional[str]             # json.dumps(list[str])

    # Output
    final_output: Optional[str]
    output_format_used: Optional[str]
    formatted_output: Optional[str]          # Plain Markdown returned as SDK result["output"]

    # Error propagation
    error_code: Optional[str]
    error_message: Optional[str]
```

**State safety**: all fields primitives or `json.dumps()` strings. No Pydantic, no
`InvocationContext`, no credentials in state.

---

## 6. Config (`config/agent.yaml` + `config/config.yaml`)

```yaml
# config/agent.yaml
id: "CMN-C1-055"
name: "cmn-c1-055"
namespace: "cmn"
version: "1.0.6"
category: "Cat 1"
class: "src.graph.graph.TaskDecompositionGraph"
required_trust_level: "VERIFIED_EXTERNAL"
requires:
  secrets:
    - AZURE_OPENAI_API_KEY
    - AZURE_OPENAI_ENDPOINT
    - AZURE_OPENAI_DEPLOYMENT
  extras: ["openai"]
```

```yaml
# config/config.yaml
decomposition_style: "4h_atomic"   # 4h_atomic | 8h_standard | sprint_story
team_velocity_points: 40
max_tasks: 50
output_format: "markdown"          # markdown (default) | gitlab_issues | jira | github_issues
```

---

## 7. Security Design

### S-1 Trust Gate
`required_trust_level = TrustLevel.VERIFIED_EXTERNAL` on graph + `RequirementIngestNode` + `MainNode`
(`ResponseValidateNode` is `ANONYMOUS`). SDK enforces at `__call__` time.

### S-2 Input Gate (`RequirementIngestNode`)
Checks in order (first match returns error dict, or graceful guidance for non-actionable input):
1. Non-string `requirement_text` → `S1_INVALID_INPUT` (guidance, `status=SUCCESS`)
2. Empty/missing `requirement_text` → `S1_EMPTY_INPUT` (guidance, `status=SUCCESS`)
3. Length > 10,000 chars → `S1_INPUT_TOO_LONG` (guidance, `status=SUCCESS`)
4. Non-actionable input → `S1_NON_ACTIONABLE_INPUT` (guidance, `status=SUCCESS`)
5. Binary/base64 blob → `S1_BINARY_INPUT` (guidance, `status=SUCCESS`)
6. Prompt injection patterns → `S1_INJECTION_DETECTED` (`status=ERROR`)
7. PII (email, JP phone, My Number) → `S2_PII_DETECTED` (`status=ERROR`)
8. Credential patterns (OpenAI/Azure key, JWT, AWS, Bearer) → `S2_PII_DETECTED` (`status=ERROR`)

Guidance cases (1-5) report `status=SUCCESS` with an explanatory `formatted_output` — the
Marketplace runner treats any non-SUCCESS status as a hard agent failure and would never
show a rejection message to the caller otherwise. Cases 6-8 report `status=ERROR`, which
`AgentBaseGraph.route()` sends straight to `finalize` (bypassing `post_process`).

### S-3 Output Gate (`ResponseValidateNode._run_output_gate()`)
Scans `final_output` for credential, internal URL, and PII patterns.
On detection: returns error dict with `error_code = S3_BLOCKED`. Output not written.

### S-4 Audit (`emit_trace_event()`)
Module-level call in every node step. Logs metadata only (lengths, counts, template name).
Never logs `requirement_text` content.

### S-5 Credential Access
`AZURE_OPENAI_API_KEY` / `AZURE_OPENAI_ENDPOINT` / `AZURE_OPENAI_DEPLOYMENT` via
`ctx.secrets.require(...)` inside `MainNode._build_llm()`, constructing the shared
`AzureOpenAIClient`. Never stored in state. Never via `os.environ`.

---

## 8. Error Propagation

`MainNode` checks `state.get("error_code")` at entry and returns `{}` (a no-op) when set, so a
fatal `error_code` from `RequirementIngestNode` skips decomposition work entirely. Guidance
codes (S1_EMPTY_INPUT etc.) instead set `input_validation_failed` and report `status=SUCCESS`,
which `ResponseValidateNode` recognises and passes the guidance text straight through.

| Error Code | Source | Fatal? |
|---|---|---|
| `S1_INVALID_INPUT` | `RequirementIngestNode` | Guidance (`status=SUCCESS`) |
| `S1_EMPTY_INPUT` | `RequirementIngestNode` | Guidance (`status=SUCCESS`) |
| `S1_INPUT_TOO_LONG` | `RequirementIngestNode` | Guidance (`status=SUCCESS`) |
| `S1_NON_ACTIONABLE_INPUT` | `RequirementIngestNode` | Guidance (`status=SUCCESS`) |
| `S1_BINARY_INPUT` | `RequirementIngestNode` | Guidance (`status=SUCCESS`) |
| `S1_INJECTION_DETECTED` | `RequirementIngestNode` | Yes (`status=ERROR`, routes to `finalize`) |
| `S2_PII_DETECTED` | `RequirementIngestNode` | Yes (`status=ERROR`, routes to `finalize`) |
| `OVERFLOW_WARNING` | `MainNode._sprint_assign()` | Advisory (pipeline continues) |
| `INVALID_TASKS` | `MainNode._decomposition_validate()` | Advisory (output produced) |
| Any other error_code reaching `post_process` | — | `ResponseValidateNode` converts to a graceful `status=SUCCESS` "temporarily unavailable" message |
| `S3_BLOCKED` | `ResponseValidateNode` | Yes |

---

## 9. Graph Composition (`src/graph/graph.py`)

```python
class TaskDecompositionGraph(AgentBaseGraph):
    required_trust_level = TrustLevel.VERIFIED_EXTERNAL

    def register_nodes(self) -> None:
        super().register_nodes()
        self._nodes["pre_process"] = RequirementIngestNode()
        self._nodes["main"] = MainNode(
            llm_client=self.config.get("llm_client"),
            decomposition_style=self.config.get("decomposition_style", "4h_atomic"),
            team_velocity_points=int(self.config.get("team_velocity_points", 40)),
            max_tasks=int(self.config.get("max_tasks", 50)),
            timeout_s=float(self.config.get("timeout_s", 30.0)),
            max_retry=int(self.config.get("max_retry", 0)),
        )
        self._nodes["post_process"] = ResponseValidateNode(
            output_format=self.config.get("output_format", "markdown"),
        )
```

---

## 10. Output Format Design

### `markdown` (default — `output_format: "markdown"` in `config/config.yaml`)
Plain Markdown returned as `result["output"]`: a `# Task Decomposition Plan` heading, summary
metrics, validation issues (if any), and one `## Sprint N` section per sprint with per-task
`### [TYPE] Title (pts / size)` blocks.

### `github_issues`
```json
{
  "issues": [{"title": "...", "body": "...", "labels": ["phase:impl"], "story_points": 3}],
  "sprint_plan": [{"sprint": 1, "issues": [...], "total_points": 9}],
  "advisory": "This is decomposition guidance...",
  "metadata": {"total_tasks": 3, "total_points": 9, "sprint_count": 1}
}
```

Supported: `markdown` (default) | `gitlab_issues` | `jira` | `github_issues` (fallback for any
unrecognised `output_format` value)

All formats include mandatory `ADVISORY_NOTE`:
> "This is decomposition guidance. Final sprint planning decisions remain with PM and engineering team."

---

## 11. Directory Structure

```
CMN-C1-055/
├── config/
│   ├── agent.yaml
│   └── config.yaml
├── docs/
│   ├── 01_proposal.md
│   ├── 02_design.md       ← this file
│   ├── 03_test_spec.md
│   ├── 04_security_review.md
│   ├── 05_review_log.md
│   ├── 06_release_note.md
│   ├── 07_operation_guide.md
│   ├── 08_customer_feedback.md
│   └── 09_process_metrics.md
├── scripts/
│   ├── check_audit_trace.py
│   ├── check_cat_consistency.py
│   ├── check_credentials.py
│   ├── check_dep_pinning.py
│   ├── check_forbidden_strings.py
│   ├── check_manifest_schema.py
│   ├── check_oss_license.py
│   ├── check_stub_tests.py
│   ├── check_trust_level.py
│   └── verify_scaffold.sh
├── src/
│   ├── __init__.py
│   ├── agent.py           ← shim → TaskDecompositionGraph
│   ├── graph.py           ← shim → src/graph/graph.py
│   ├── state.py           ← shim → src/schemas/state.py
│   ├── api/server.py
│   ├── graph/graph.py     ← TaskDecompositionGraph
│   ├── nodes/
│   │   ├── main_node.py         ← MainNode (composite: 5 inner steps)
│   │   ├── pre_process_node.py  ← RequirementIngestNode
│   │   ├── post_process_node.py ← ResponseValidateNode
│   │   └── _security_patterns.py ← shared S-2/S-3 credential/PII/URL patterns
│   ├── services/
│   │   ├── input_classifier.py  ← is_non_actionable_requirement()
│   │   └── service.py
│   └── schemas/state.py   ← TaskDecompositionState
├── tests/
│   ├── __init__.py
│   ├── integration/test_graph_smoke.py
│   ├── proof_of_boundary/
│   │   ├── test_import_isolation.py
│   │   ├── test_pb7_hitl_interrupt_propagation.py
│   │   ├── test_pb_invoke_order.py
│   │   ├── test_pb_placeholder.py
│   │   └── test_state_safety.py
│   └── unit/
│       ├── test_docs_compliance.py
│       ├── test_framework_compliance_tc06_tc07.py
│       ├── test_main_node.py
│       ├── test_post_process_node.py
│       ├── test_pre_process_node.py
│       └── test_trust_levels.py
├── conftest.py
└── pyproject.toml
```

Only `main_node.py`, `pre_process_node.py`, and `post_process_node.py` exist under `src/nodes/` —
there are no legacy per-step node files (`requirement_ingest.py`, `scope_analyze.py`, etc.).

---

## 12. Definition of Done

- [x] `src/schemas/state.py` — `TaskDecompositionState` flat TypedDict
- [x] `src/nodes/pre_process_node.py` — `RequirementIngestNode(FunctionNode)`
- [x] `src/nodes/main_node.py` — `MainNode(FunctionNode)` composite
- [x] `src/nodes/post_process_node.py` — `ResponseValidateNode(FunctionNode)`
- [x] `src/graph/graph.py` — 3-slot `TaskDecompositionGraph(AgentBaseGraph)`
- [x] `src/api/server.py` — FastAPI entry point
- [x] `config/agent.yaml` — SDK format
- [x] `config/config.yaml` — runtime config
- [x] Tests: `tests/unit/` (main_node, pre_process_node, post_process_node, trust_levels,
      docs_compliance), `tests/proof_of_boundary/` (PB-1–PB-6, import isolation, invoke order),
      `tests/integration/test_graph_smoke.py`
- [x] Import isolation: no Level 0 imports
- [x] Security gates: S-1/S-2 input, S-3 output, S-4 audit, S-5 credential access

*Stage gate ② → ③ condition: this file committed to `develop` branch.*
