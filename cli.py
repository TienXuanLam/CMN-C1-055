# AgentCore Platform v1.0
# Framework Design & Engineering (FDE)
"""AGENTIC STAR Marketplace entrypoint — one-shot Pod process.

Referenced by ../Dockerfile's Marketplace CMD. Compiles the agent, provisions
its secrets, then hands off to shared.bootstrap.marketplace_app for the
Marketplace lifecycle (identity, input, events, terminal delivery, exit).
"""

from pathlib import Path

from framework.utils.config_loader import load_agent_config
from shared.bootstrap.marketplace_app import run_agent_marketplace
from src.graph.graph import TaskDecompositionGraph

extend_config = {}

if __name__ == "__main__":
    run_agent_marketplace(
        TaskDecompositionGraph,
        agent_name="CMN-C1-055",
        namespace="cmn",
        config={**load_agent_config(Path(__file__).resolve().parent), **extend_config},
    )
