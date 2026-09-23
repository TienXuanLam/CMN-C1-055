"""AgentCore Platform v1.0"""

# Compatibility shim — kept intentionally for tests that import TaskDecompositionAgent
# from src.agent. Do not remove. Canonical class: src/graph/graph.py::TaskDecompositionGraph.
from src.graph.graph import TaskDecompositionGraph as TaskDecompositionAgent

__all__ = ["TaskDecompositionAgent"]
