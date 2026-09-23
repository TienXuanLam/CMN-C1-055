# CMN-C1-055 Test Specification

**Template ID:** CMN-C1-055
**Agent Name:** TaskDecompositionAgent
**Category:** Cat 1 (CMN — generic, industry-agnostic)
**Stage:** ④ Implementation → Test
**Date:** 2026-05-23 (updated 2026-06-24 post-migration to SDK pattern)
**Author:** TienTV34

## Test Files (SDK pattern)

| File | Contains |
|---|---|
| `tests/unit/test_docs_compliance.py` | Ported from the removed legacy flat `tests/test_cmn_c1_055.py` (TC-22 only): asserts `docs/02_design.md` references `AgentBaseGraph` and contains no phantom error codes |
| `tests/unit/test_main_node.py` | MainNode contract, agentcore_phase3, scope/sprint/validate inner methods |
| `tests/unit/test_pre_process_node.py` | RequirementIngestNode S-1/S-2 input gate cases |
| `tests/unit/test_post_process_node.py` | ResponseValidateNode error-skip behaviour, output-format-specific content shape |
| `tests/unit/test_trust_levels.py` | Declared `required_trust_level` per node/graph |
| `tests/integration/test_graph_smoke.py` | Compile + invoke smoke tests (clean/injection/PII/phase3) |
| `tests/proof_of_boundary/test_pb_placeholder.py` | PB-1–PB-6 (trace, state primitives, S-3 gate, import isolation, advisory note, INVALID_TASKS) |
| `tests/proof_of_boundary/test_import_isolation.py` | L0/L1 import boundary AST scan |
| `tests/proof_of_boundary/test_state_safety.py` | Msgpack safety, credential fields, AgentState inheritance |
| `tests/proof_of_boundary/test_pb_invoke_order.py` | Node execution order across trust-level transitions |

## Mock Reference

| Component | Mock target | Pattern |
|---|---|---|
| LLM calls | `MainNode(llm_client=_MockLLM())` | `_MockLLM.complete()` returns canned JSON |
| Secrets | `bound_secrets(InMemoryProvider({"AZURE_OPENAI_API_KEY": "test-key", "AZURE_OPENAI_ENDPOINT": "https://test.openai.azure.com/", "AZURE_OPENAI_DEPLOYMENT": "test-deployment"}))` | Context manager |
| `emit_trace_event` | `patch("src.nodes.<module>.emit_trace_event")` | Module-level patch |

---

## 1. Scope

This specification covers:
- Framework compliance tests (TC-01 through TC-09)
- Feature-level tests (TC-10 through TC-15)
- Proof-of-Boundary tests (PB-1 through PB-6)

All tests must pass before merging to `develop` (Stage ④ gate condition).

---

## 2. Framework Compliance Tests

| TC-ID | Name | Description | Expected Result |
|-------|------|-------------|----------------|
| TC-01 | State contract | `TaskDecompositionState` is flat TypedDict; all fields are `Optional[primitive]`; no Pydantic/dataclass instances storable | Pass: isinstance checks for str/int/bool/None |
| TC-02 | S-1 oversized input rejected | Input >10,000 chars sets `error_code = S1_INPUT_TOO_LONG` | `error_code == "S1_INPUT_TOO_LONG"` |
| TC-03 | S-1 binary/base64 rejected | Base64-encoded blob sets `error_code = S1_BINARY_INPUT` | `error_code == "S1_BINARY_INPUT"` |
| TC-04 | S-1 prompt injection rejected | Injection pattern sets `error_code = S1_INJECTION_DETECTED` | `error_code == "S1_INJECTION_DETECTED"` |
| TC-05 | S-2 PII rejected | Email address in input sets `error_code = S2_PII_DETECTED` | `error_code == "S2_PII_DETECTED"` |
| TC-06 | S-2 credential rejected | API key pattern in input sets `error_code = S2_PII_DETECTED` | `error_code == "S2_PII_DETECTED"` |
| TC-07 | S-3 output credential blocked | Credential in output sets `error_code = S3_BLOCKED` | `error_code == "S3_BLOCKED"` |
| TC-08 | Error propagation | Fatal `error_code` causes all downstream nodes to skip | Downstream state fields remain unset |
| TC-09 | Import isolation | `src/` contains no `agenticstar` imports | AST scan: 0 violations |

---

## 3. Feature Tests

| TC-ID | Name | Description | Expected Result |
|-------|------|-------------|----------------|
| TC-10 | Standard feature decomposition | Valid requirement → task list produced with required fields | `task_count >= 1`; all tasks have title/description/acceptance_criteria/story_points/task_type/dependencies |
| TC-11 | Self-dogfood: agentcore_phase3 | Requirement mentioning "agentcore" → `agentcore_phase3` template → exactly 9 canonical tasks | `task_count == 9`; `decomposition_template == "agentcore_phase3"` |
| TC-12 | Sprint overflow detection | Tasks exceeding velocity (default 40 pts) → `overflow_warning = True` and `sprint_count > 1` | `overflow_warning == True`; `sprint_plan` has ≥ 2 sprints |
| TC-13 | Over-decomposed advisory | `task_count > 15` sets `overflow_warning = True` and advisory message in `validation_result.issues` | `overflow_warning == True`; "Advisory" in issues |
| TC-14 | Output format — markdown | `output_format = "markdown"` → `final_output` contains sprint headers and advisory note | `"Sprint 1"` in output; `ADVISORY_NOTE` in output |
| TC-15 | Output format — gitlab_issues | `output_format = "gitlab_issues"` → JSON with `issues[].description` and `issues[].weight` fields | Valid JSON; `weight` present per issue |

---

## 4. Proof-of-Boundary Tests

| PB-ID | Boundary | Test | Expected Result |
|-------|----------|------|----------------|
| PB-1 | BaseNode → trace | `_emit_trace_event()` called during `RequirementIngestNode.execute()` | No `AttributeError`; no silent failure |
| PB-2 | State serialisation safety | After full pipeline run, every value in state is `str`, `int`, `bool`, or `None` | `isinstance(v, (str, int, bool, type(None)))` for all state values |
| PB-3 | S-3 credential not propagated | Credential string in `estimated_task_list` triggers `S3_BLOCKED` in `ResponseValidateNode` | `error_code == "S3_BLOCKED"` |
| PB-4 | Import isolation (AST) | `src/` files do not import `agenticstar` Level 0 | AST walk: 0 `agenticstar` imports |
| PB-5 | Advisory note in all formats | `final_output` contains `ADVISORY_NOTE` string for all 4 output formats | Match in markdown (default), gitlab_issues, jira, github_issues |
| PB-6 | INVALID_TASKS non-fatal | Task missing `story_points` → `INVALID_TASKS` set but `final_output` still produced | `error_code == "INVALID_TASKS"`; `final_output` is not None |

---

## 5. Test Data

### Happy-path requirement (TC-10)
```
Build a REST API integration for a retail inventory system.
The integration must pull daily stock levels from the ERP system,
normalise the data format, and push to the warehouse management platform.
```

### agentcore_phase3 trigger (TC-11)
```
Build a new agentcore template for a customer FAQ Q&A agent.
```

### High-volume requirement (TC-12, TC-13)
> Construct a new microservices platform with 20+ independent services,
> CI/CD pipelines, monitoring, alerting, multi-region deployment, and documentation.

---

## 6. Definition of Done

- [ ] All TC-01..TC-15 pass
- [ ] All PB-1..PB-6 pass
- [ ] `pytest tests/ -v` exits 0
- [ ] `pytest tests/proof_of_boundary/ -v` exits 0
- [ ] No credential patterns in `src/` (gate-credential-scan pass)
- [ ] No Level 0 imports in `src/` (gate-import-isolation pass)
