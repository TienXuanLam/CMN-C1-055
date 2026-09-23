# CMN-C1-055 — Unit Test: declared TrustLevel per node/graph
#
# Ported from the legacy flat tests/test_cmn_c1_055.py (TC-16), which sat
# outside the canonical tests/unit|integration|proof_of_boundary structure and
# has been removed. Not duplicated elsewhere: test_main_node.py only pins
# MainNode's trust level, not RequirementIngestNode / ResponseValidateNode /
# TaskDecompositionGraph.

from framework.schemas.trust_level import TrustLevel

from src.nodes.pre_process_node import RequirementIngestNode
from src.nodes.main_node import MainNode
from src.nodes.post_process_node import ResponseValidateNode
from src.graph.graph import TaskDecompositionGraph


def test_declared_trust_levels():
    assert RequirementIngestNode.required_trust_level == TrustLevel.VERIFIED_EXTERNAL
    assert MainNode.required_trust_level == TrustLevel.VERIFIED_EXTERNAL
    assert ResponseValidateNode.required_trust_level == TrustLevel.ANONYMOUS
    assert TaskDecompositionGraph.required_trust_level == TrustLevel.VERIFIED_EXTERNAL
