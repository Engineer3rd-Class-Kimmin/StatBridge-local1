from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
AGENT_ROOT = ROOT / "src" / "agent"
sys.path.insert(0, str(AGENT_ROOT))

from langgraph_workflow import StatBridgeWorkflow
from output_agent import OutputAgent


class FakeAgent:
    def __init__(self, status: str) -> None:
        self.status = status
        self.resolve_calls = 0
        self.execute_calls = 0

    def resolve(self, **_: Any) -> dict[str, Any]:
        self.resolve_calls += 1
        result: dict[str, Any] = {"status": self.status}
        if self.status == "resolved":
            result.update({"selected_table": {"table_id": "DT_TEST"}, "api_plan": {"table_id": "DT_TEST"}})
        return result

    def execute_resolution(self, *, resolution: dict[str, Any], execute: bool, **_: Any) -> dict[str, Any]:
        self.execute_calls += 1
        status = "success" if execute else "planned_only"
        return {**resolution, "execution": {"status": status, "rows": [], "row_count": 0}}


def verify(status: str, expected_path: list[str], expected_execute_calls: int) -> None:
    agent = FakeAgent(status)
    result = StatBridgeWorkflow(agent).invoke(query="테스트", execute=True)
    assert result["orchestration"]["engine"] == "langgraph"
    assert result["orchestration"]["path"] == expected_path
    assert agent.resolve_calls == 1
    assert agent.execute_calls == expected_execute_calls


verify("need_clarification", ["resolve_request", "await_clarification"], 0)
verify("no_match", ["resolve_request", "finish_unresolved"], 0)
verify("resolved", ["resolve_request", "execute_statistics", "await_output_selection"], 1)

agent = FakeAgent("resolved")
pre_resolved = {"status": "resolved", "selected_table": {"table_id": "DT_EXISTING"}, "api_plan": {"table_id": "DT_EXISTING"}}
result = StatBridgeWorkflow(agent).invoke(query="테스트", resolution=pre_resolved, execute=False)
assert agent.resolve_calls == 0
assert agent.execute_calls == 1
assert result["execution"]["status"] == "planned_only"
assert result["api_plan"]["table_id"] == "DT_EXISTING"

workflow = StatBridgeWorkflow(agent, OutputAgent())
data_result = {
    "execution": {"status": "success", "rows": [{"PRD_DE": "2024", "DT": "10", "ITM_NM": "테스트"}], "row_count": 1},
    "orchestration": {"path": ["resolve_request", "execute_statistics", "await_output_selection"]},
}
rendered = workflow.invoke_output(result=data_result, output_request={"chart_type": "bar", "layout": "combined"})
assert rendered["orchestration"]["path"][-1] == "prepare_output"
assert rendered["output"]["visualization"]["chartType"] == "bar"

print("LANGGRAPH FLOW OK: clarification/no_match/resolved/resume paths")
