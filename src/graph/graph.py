"""AgentCore Platform v1.0"""

from framework.graph.agent_base_graph import AgentBaseGraph
from framework.schemas.trust_level import TrustLevel

from src.nodes.main_node import MainNode
from src.nodes.post_process_node import ResponseValidateNode
from src.nodes.pre_process_node import RequirementIngestNode
from src.schemas.state import TaskDecompositionState


class TaskDecompositionGraph(AgentBaseGraph):
    """Fixed-pipeline graph for CMN-C1-055 TaskDecompositionAgent.

    Slot mapping:
      pre_process  → RequirementIngestNode  (S-1/S-2 input gate)
      main         → MainNode               (ScopeAnalyze → TaskDecompose →
                                             StoryPointEstimate → SprintAssign →
                                             DecompositionValidate — composite)
      post_process → ResponseValidateNode   (S-3 output gate + serialisation)

    Runtime config keys (read from config/config.yaml via self.config):
      decomposition_style, team_velocity_points, max_tasks, output_format,
      timeout_s, max_retry — passed to nodes at register_nodes() time.
      self.config is populated by cli.py via
      framework.utils.config_loader.load_agent_config(), which reads
      config/config.yaml before run_agent_marketplace() constructs this
      graph.
    """

    required_trust_level = TrustLevel.VERIFIED_EXTERNAL

    @property
    def name(self) -> str:
        return "cmn-c1-055"

    @property
    def state_schema(self) -> type:
        return TaskDecompositionState

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
