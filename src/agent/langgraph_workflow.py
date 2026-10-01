from __future__ import annotations

from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph


class StatBridgeGraphState(TypedDict, total=False):
    """State shared only by the orchestration layer.

    Domain results remain ordinary dictionaries produced by StatBridgeAgent.
    This keeps the dictionary resolver, API-plan builder and MCP data layer
    independent from LangGraph.
    """

    query: str
    conversation_state: dict[str, Any] | None
    clarification: dict[str, str] | None
    resolution: dict[str, Any] | None
    execute: bool
    start_period: str | None
    end_period: str | None
    period_overrides: dict[str, tuple[str, str]] | None
    generate_answer: bool
    output_request: dict[str, Any] | None
    execution_result: dict[str, Any] | None
    result: dict[str, Any]
    orchestration_stage: str
    orchestration_path: list[str]


class StatBridgeWorkflow:
    """LangGraph orchestration around the existing StatBridge domain methods."""

    def __init__(self, agent: Any, output_agent: Any | None = None) -> None:
        self.agent = agent
        self.output_agent = output_agent
        builder = StateGraph(StatBridgeGraphState)
        builder.add_node("resolve_request", self._resolve_request)
        builder.add_node("await_clarification", self._await_clarification)
        builder.add_node("finish_unresolved", self._finish_unresolved)
        builder.add_node("execute_statistics", self._execute_statistics)
        builder.add_node("prepare_output", self._prepare_output)
        builder.add_conditional_edges(
            START,
            self._route_start,
            {"input": "resolve_request", "output": "prepare_output"},
        )
        builder.add_conditional_edges(
            "resolve_request",
            self._route_after_resolution,
            {
                "clarification": "await_clarification",
                "unresolved": "finish_unresolved",
                "resolved": "execute_statistics",
            },
        )
        builder.add_edge("await_clarification", END)
        builder.add_edge("finish_unresolved", END)
        builder.add_conditional_edges(
            "execute_statistics",
            self._route_after_execution,
            {"await_output": "await_output_selection", "prepare_output": "prepare_output"},
        )
        builder.add_node("await_output_selection", self._await_output_selection)
        builder.add_edge("await_output_selection", END)
        builder.add_edge("prepare_output", END)
        self.graph = builder.compile(name="statbridge-agent-orchestration")

    @staticmethod
    def _path(state: StatBridgeGraphState, node: str) -> list[str]:
        return [*(state.get("orchestration_path") or []), node]

    def _resolve_request(self, state: StatBridgeGraphState) -> dict[str, Any]:
        resolution = state.get("resolution")
        if resolution is None:
            resolution = self.agent.resolve(
                query=state["query"],
                state=state.get("conversation_state"),
                clarification=state.get("clarification"),
            )
        return {
            "resolution": resolution,
            "orchestration_stage": "resolved_request",
            "orchestration_path": self._path(state, "resolve_request"),
        }

    @staticmethod
    def _route_start(state: StatBridgeGraphState) -> Literal["input", "output"]:
        return "output" if state.get("execution_result") is not None else "input"

    @staticmethod
    def _route_after_resolution(
        state: StatBridgeGraphState,
    ) -> Literal["clarification", "unresolved", "resolved"]:
        status = str((state.get("resolution") or {}).get("status") or "")
        if status == "need_clarification":
            return "clarification"
        if status != "resolved":
            return "unresolved"
        return "resolved"

    @staticmethod
    def _route_after_execution(state: StatBridgeGraphState) -> Literal["await_output", "prepare_output"]:
        execution_status = str(((state.get("result") or {}).get("execution") or {}).get("status") or "")
        request = state.get("output_request") or {}
        return "prepare_output" if execution_status == "success" and request.get("chart_type") else "await_output"

    def _await_clarification(self, state: StatBridgeGraphState) -> dict[str, Any]:
        return {
            "result": state.get("resolution") or {},
            "orchestration_stage": "awaiting_clarification",
            "orchestration_path": self._path(state, "await_clarification"),
        }

    def _finish_unresolved(self, state: StatBridgeGraphState) -> dict[str, Any]:
        return {
            "result": state.get("resolution") or {},
            "orchestration_stage": "unresolved",
            "orchestration_path": self._path(state, "finish_unresolved"),
        }

    def _execute_statistics(self, state: StatBridgeGraphState) -> dict[str, Any]:
        result = self.agent.execute_resolution(
            query=state["query"],
            resolution=state.get("resolution") or {},
            execute=state.get("execute", True),
            start_period=state.get("start_period"),
            end_period=state.get("end_period"),
            period_overrides=state.get("period_overrides"),
            generate_answer=state.get("generate_answer", True),
        )
        execution_status = str((result.get("execution") or {}).get("status") or "")
        return {
            "result": result,
            "orchestration_stage": "executed" if execution_status == "success" else execution_status or "execution_finished",
            "orchestration_path": self._path(state, "execute_statistics"),
        }

    def _prepare_output(self, state: StatBridgeGraphState) -> dict[str, Any]:
        result = dict(state.get("execution_result") or state.get("result") or {})
        execution_status = str((result.get("execution") or {}).get("status") or "")
        if self.output_agent is not None and execution_status == "success":
            result["output"] = self.output_agent.prepare(result, state.get("output_request"))
        return {
            "result": result,
            "orchestration_stage": "output_ready" if result.get("output") else state.get("orchestration_stage", "execution_finished"),
            "orchestration_path": self._path(state, "prepare_output"),
        }

    def _await_output_selection(self, state: StatBridgeGraphState) -> dict[str, Any]:
        return {
            "result": state.get("result") or {},
            "orchestration_stage": "awaiting_output_selection",
            "orchestration_path": self._path(state, "await_output_selection"),
        }

    def invoke(
        self,
        *,
        query: str,
        conversation_state: dict[str, Any] | None = None,
        clarification: dict[str, str] | None = None,
        resolution: dict[str, Any] | None = None,
        execute: bool = True,
        start_period: str | None = None,
        end_period: str | None = None,
        period_overrides: dict[str, tuple[str, str]] | None = None,
        generate_answer: bool = True,
        output_request: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        state = self.graph.invoke({
            "query": query,
            "conversation_state": conversation_state,
            "clarification": clarification,
            "resolution": resolution,
            "execute": execute,
            "start_period": start_period,
            "end_period": end_period,
            "period_overrides": period_overrides,
            "generate_answer": generate_answer,
            "output_request": output_request,
            "execution_result": None,
            "orchestration_path": [],
        })
        result = dict(state.get("result") or {})
        result["orchestration"] = {
            "engine": "langgraph",
            "stage": state.get("orchestration_stage") or "unknown",
            "path": state.get("orchestration_path") or [],
        }
        return result

    def invoke_output(self, *, result: dict[str, Any], output_request: dict[str, Any]) -> dict[str, Any]:
        state = self.graph.invoke({
            "execution_result": result,
            "output_request": output_request,
            "orchestration_path": list((result.get("orchestration") or {}).get("path") or []),
        })
        rendered = dict(state.get("result") or {})
        rendered["orchestration"] = {
            "engine": "langgraph",
            "stage": state.get("orchestration_stage") or "unknown",
            "path": state.get("orchestration_path") or [],
        }
        return rendered
