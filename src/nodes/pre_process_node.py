"""AgentCore Platform v1.0"""

import re
from typing import Any, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.services.events import emitter
from shared.services.events.types import EventType
from shared.utils.audit_logger import emit_trace_event

from src.nodes._security_patterns import scan_for_credentials, scan_for_pii
from src.schemas.state import TaskDecompositionState
from src.services.input_classifier import is_non_actionable_requirement

MAX_REQUIREMENT_CHARS = 10_000

_BINARY_PATTERNS = [
    re.compile(r"^\s*data:[a-zA-Z0-9/+]+;base64,", re.MULTILINE),
    # Finding (2026-08-19, High): {200,} was an unbounded-below quantifier
    # (the framework's PII-regex bounding rule). Bounded to a
    # generous upper limit that still catches any realistic embedded blob.
    re.compile(r"[A-Za-z0-9+/]{200,20000}={0,2}"),
]

_INJECTION_PATTERNS = [
    re.compile(r"(?i)ignore\s+(previous|all|above|prior)\s+(instructions?|prompts?|context)"),
    re.compile(r"(?i)you\s+are\s+now\s+"),
    re.compile(r"(?i)(system\s*:|\[INST\]|<\|im_start\|>)"),
    re.compile(r"(?i)act\s+as\s+(a|an)\s+"),
    re.compile(r"(?i)disregard\s+(your|all)"),
    re.compile(r"(?i)jailbreak"),
]


def _input_guidance(error_code: str, error_message: str) -> dict[str, Any]:
    """Return actionable guidance without invoking Azure OpenAI."""
    return {
        "error_code": error_code,
        "error_message": error_message,
        "input_validation_failed": "true",
        "formatted_output": (
            "# Valid task requirement required\n\n"
            f"I could not process the input: {error_message}\n\n"
            "Describe one software or business requirement in plain text. Include the goal, "
            "scope, important constraints, and expected outcome.\n\n"
            "**Example**\n\n"
            "Decompose the implementation of a customer onboarding REST API into sprint-ready "
            "engineering tasks with acceptance criteria, dependencies, and story-point estimates."
        ),
        "status": AgentStatus.SUCCESS.value,
    }


class RequirementIngestNode(FunctionNode):
    """pre_process slot: S-1/S-2 input gate for requirement_text."""

    required_trust_level = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: TaskDecompositionState) -> dict[str, object]:
        if state.get("error_code"):
            return {}

        emit_trace_event(
            event_type="requirement_ingest_started",
            payload={},
            state=state,
        )

        requirement: Optional[str] = state.get("user_input") or state.get("requirement_text")

        # Finding (2026-08-19, Medium): the isinstance(str) check previously
        # ran AFTER `requirement.strip()` -- a non-empty non-string
        # requirement_text (e.g. a JSON list or dict, which input_context
        # permits since it has no schema of its own) crashed here with an
        # uncaught AttributeError instead of returning the structured
        # S1_BINARY_INPUT error below. Type-check first.
        if requirement is not None and not isinstance(requirement, str):
            return _input_guidance("S1_INVALID_INPUT", "The requirement must be plain text.")

        if not requirement or not requirement.strip():
            return _input_guidance("S1_EMPTY_INPUT", "The requirement is empty or missing.")

        if len(requirement) > MAX_REQUIREMENT_CHARS:
            return _input_guidance(
                "S1_INPUT_TOO_LONG",
                f"The requirement exceeds {MAX_REQUIREMENT_CHARS} characters.",
            )

        if is_non_actionable_requirement(requirement):
            return _input_guidance(
                "S1_NON_ACTIONABLE_INPUT",
                "The input does not contain an actionable requirement to decompose.",
            )

        for pat in _BINARY_PATTERNS:
            if pat.search(requirement):
                return _input_guidance(
                    "S1_BINARY_INPUT",
                    "Binary attachments and base64-encoded blobs are not supported.",
                )

        for pat in _INJECTION_PATTERNS:
            if pat.search(requirement):
                return {
                    "error_code": "S1_INJECTION_DETECTED",
                    "error_message": "Prompt injection pattern detected in requirement_text.",
                    "status": AgentStatus.ERROR.value,
                }

        pii_types = scan_for_pii(requirement)
        if pii_types:
            return {
                "error_code": "S2_PII_DETECTED",
                "error_message": (
                    f"PII pattern detected (type: {pii_types[0]}). " "Remove sensitive data before resubmitting."
                ),
                "status": AgentStatus.ERROR.value,
            }

        cred_types = scan_for_credentials(requirement)
        if cred_types:
            return {
                "error_code": "S2_PII_DETECTED",
                "error_message": f"Credential pattern detected (type: {cred_types[0]}). Input rejected.",
                "status": AgentStatus.ERROR.value,
            }

        emit_trace_event(
            event_type="requirement_ingest_ok",
            payload={"requirement_length": len(requirement)},
            state=state,
        )
        emitter().emit_event(
            event_type=EventType.PROGRESS_UPDATE,
            message="Validating the requirement and preparing task decomposition.",
            metadata={"stage": "requirement_validation"},
        )
        # Normalise: write requirement_text to top-level state so downstream nodes
        # can read it directly without re-checking input_context
        return {"status": AgentStatus.SUCCESS.value, "requirement_text": requirement.strip()}
