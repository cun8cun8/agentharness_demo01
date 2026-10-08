import json
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from app.domain.schemas import ResearchHypothesis, ExperimentPlan
from app.research.providers import search_research_papers


class ResearchPlan(BaseModel):
    hypotheses: list[ResearchHypothesis] = Field(min_length=1, max_length=10)
    experiments: list[ExperimentPlan] = Field(min_length=1, max_length=10)


class ResearchState(TypedDict, total=False):
    papers: list
    source_summary: dict
    model: object
    route_reason: str
    plan: ResearchPlan | None


def run_research_workflow(request, model_call, imported_papers=None):
    def retrieve(state):
        if imported_papers is not None:
            papers, summary = imported_papers, {"provider": "imported", "fallback": False, "paper_count": len(imported_papers)}
        else:
            papers, summary = search_research_papers(request)
        return {"papers": [paper.model_copy(update={"workspace_id": request.workspace_id}) for paper in papers], "source_summary": summary}

    def synthesize(state):
        model, reason = model_call(request, state["papers"])
        return {"model": model, "route_reason": reason}

    def validate(state):
        model = state["model"]
        if model.fallback_used:
            return {"plan": None}
        output = model.output_text.strip()
        if output.startswith("```") and output.endswith("```"):
            output = "\n".join(output.splitlines()[1:-1])
        try:
            plan = ResearchPlan.model_validate(json.loads(output))
            paper_ids = {paper.id for paper in state["papers"]}
            for hypothesis in plan.hypotheses:
                if not hypothesis.evidence_paper_ids or not set(hypothesis.evidence_paper_ids).issubset(paper_ids):
                    raise ValueError("RESEARCH_EVIDENCE_REFERENCE_INVALID")
                if not 0 <= hypothesis.confidence <= 1:
                    raise ValueError("RESEARCH_CONFIDENCE_INVALID")
        except (ValueError, TypeError) as exc:
            raise ValueError("RESEARCH_PLAN_INVALID") from exc
        return {"plan": plan}

    graph = StateGraph(ResearchState)
    graph.add_node("retrieve", retrieve)
    graph.add_node("synthesize", synthesize)
    graph.add_node("validate_evidence", validate)
    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "synthesize")
    graph.add_edge("synthesize", "validate_evidence")
    graph.add_edge("validate_evidence", END)
    return graph.compile().invoke({})
