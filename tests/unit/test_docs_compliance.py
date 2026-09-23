# CMN-C1-055 — Unit Test: docs/02_design.md compliance
#
# Ported from the legacy flat tests/test_cmn_c1_055.py (TC-22), which sat
# outside the canonical tests/unit|integration|proof_of_boundary structure and
# has been removed. Not duplicated elsewhere in the canonical suite.

import os


def test_design_doc_references_agentbasegraph_and_no_phantom_error_code():
    design_path = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "02_design.md")
    with open(design_path) as fh:
        content = fh.read()
    assert "AgentBaseGraph" in content, "docs/02_design.md must reference AgentBaseGraph as L1 base"
    assert "SPRINT_OVERFLOW" not in content, "SPRINT_OVERFLOW is a phantom error code — remove from design doc"
