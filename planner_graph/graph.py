"""LangGraph assembly for the planner workflow.

This module wires the planner nodes into one executable state machine and
defines how control flows between them. It connects the individual reasoning,
validation, and reporting steps into the full planner loop.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from planner_graph.nodes.context_pack import build_context_pack
from planner_graph.nodes.context_gate import build_context_gate
from planner_graph.nodes.diagnose import build_diagnose
from planner_graph.nodes.draft_proposal import build_draft_proposal
from planner_graph.nodes.fail_closed import build_fail_closed
from planner_graph.nodes.guardrail_preview import build_guardrail_preview
from planner_graph.nodes.materialize_proposal import build_materialize_proposal
from planner_graph.nodes.persist_memory import build_persist_memory
from planner_graph.nodes.report import build_report
from planner_graph.nodes.retrieve_memory import build_retrieve_memory
from planner_graph.nodes.revise_proposal import build_revise_proposal
from planner_graph.nodes.execution_verify import build_execution_verify
from planner_graph.nodes.trigger_intake import build_trigger_intake
from planner_graph.nodes.validate_contract_shape import build_validate_contract_shape
from planner_graph.runtime import PlannerRuntime
from planner_graph.state import PlannerState


def route_after_contract_shape_validation(state: PlannerState) -> str:
    if state.get("validation_status") == "failed":
        return "fail_closed"
    return "guardrail_preview"


def route_after_guardrail(state: PlannerState) -> str:
    if state.get("selected_action") == "fail":
        return "materialize_proposal"
    would_clamp = (
        bool(state.get("guardrail_preview", {}).get("would_clamp"))
        if isinstance(
            state.get("guardrail_preview"),
            dict,
        )
        else False
    )
    if would_clamp and state.get("revision_count", 0) < 1:
        return "revise_proposal"
    return "materialize_proposal"


def build_graph(runtime: PlannerRuntime):
    graph = StateGraph(PlannerState)
    graph.add_node("trigger_intake", build_trigger_intake(runtime))
    graph.add_node("context_pack", build_context_pack(runtime))
    graph.add_node("context_gate", build_context_gate(runtime))
    graph.add_node("retrieve_memory", build_retrieve_memory(runtime))
    graph.add_node("diagnose", build_diagnose(runtime))
    graph.add_node("draft_proposal", build_draft_proposal(runtime))
    graph.add_node("validate_contract_shape", build_validate_contract_shape(runtime))
    graph.add_node("guardrail_preview", build_guardrail_preview(runtime))
    graph.add_node("revise_proposal", build_revise_proposal(runtime))
    graph.add_node("fail_closed", build_fail_closed(runtime))
    graph.add_node("materialize_proposal", build_materialize_proposal(runtime))
    graph.add_node("execution_verify", build_execution_verify(runtime))
    graph.add_node("persist_memory", build_persist_memory(runtime))
    graph.add_node("report", build_report(runtime))

    graph.add_edge(START, "trigger_intake")
    graph.add_edge("trigger_intake", "context_pack")
    graph.add_edge("context_pack", "context_gate")
    graph.add_edge("context_gate", "retrieve_memory")
    graph.add_edge("retrieve_memory", "diagnose")
    graph.add_edge("diagnose", "draft_proposal")
    graph.add_edge("draft_proposal", "validate_contract_shape")
    graph.add_conditional_edges(
        "validate_contract_shape",
        route_after_contract_shape_validation,
        {
            "guardrail_preview": "guardrail_preview",
            "fail_closed": "fail_closed",
        },
    )
    graph.add_conditional_edges(
        "guardrail_preview",
        route_after_guardrail,
        {
            "revise_proposal": "revise_proposal",
            "materialize_proposal": "materialize_proposal",
        },
    )
    graph.add_edge("revise_proposal", "validate_contract_shape")
    graph.add_edge("fail_closed", "materialize_proposal")
    graph.add_edge("materialize_proposal", "execution_verify")
    graph.add_edge("execution_verify", "persist_memory")
    graph.add_edge("persist_memory", "report")
    graph.add_edge("report", END)
    return graph.compile()
