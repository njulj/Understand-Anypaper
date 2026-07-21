import asyncio

from agent_framework import ChatResponse, Content, Message

from understand_anypaper.analyzers.citation_contribution_matcher import (
    CitationContributionMatchOutput,
    CitationContributionMatcher,
)
from understand_anypaper.graph.schema import GraphNode, NodeType
from understand_anypaper.parser.models import PaperReference


class FakeChatClient:
    def __init__(self, output: CitationContributionMatchOutput) -> None:
        self.output = output
        self.prompt = ""

    async def get_response(self, messages=None, *, stream=False, options=None, **kwargs):
        self.prompt = "\n".join(message.text for message in messages)
        return ChatResponse(
            messages=[
                Message(
                    role="assistant",
                    contents=[Content.from_text(self.output.model_dump_json())],
                )
            ]
        )


def _contribution(node_id: str, title: str, summary: str) -> GraphNode:
    return GraphNode(
        id=node_id,
        paper_id="target-paper",
        node_type=NodeType.CONTRIBUTION,
        title=title,
        summary=summary,
        confidence=0.9,
    )


def test_matcher_targets_specific_contribution_and_relation():
    client = FakeChatClient(
        CitationContributionMatchOutput(
            matched=True,
            target_contribution_node_id="contribution-gating",
            relation_type="BUILDS_ON",
            rationale="The source explicitly extends the cited gating mechanism.",
            confidence=0.93,
        )
    )
    source = GraphNode(
        id="unit-current-method",
        paper_id="current-paper",
        node_type=NodeType.METHOD,
        title="Residual gated attention",
        summary="The method extends prior gated attention with residual routing.",
        confidence=0.9,
    )
    output = asyncio.run(
        CitationContributionMatcher(chat_client=client).match(
            source_node=source,
            citation_context="We extend the gated attention mechanism of [2].",
            reference=PaperReference(
                reference_id="ref-current-2",
                marker="[2]",
                raw_text="A. Author. Foundational Gating. 2022.",
                title="Foundational Gating",
            ),
            target_paper_title="Foundational Gating",
            candidate_contributions=[
                _contribution(
                    "contribution-gating",
                    "A gated attention mechanism controls token flow",
                    "The paper introduces learned gates for attention routing.",
                ),
                _contribution(
                    "contribution-benchmark",
                    "A benchmark measures long-context efficiency",
                    "The paper contributes an evaluation suite.",
                ),
            ],
        )
    )

    assert output.target_contribution_node_id == "contribution-gating"
    assert output.relation_type == "BUILDS_ON"
    assert "CANDIDATE_CONTRIBUTIONS" in client.prompt
    assert "contribution-benchmark" in client.prompt

