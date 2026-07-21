import json
from typing import Literal

from agent_framework import Agent, SupportsChatGetResponse
from pydantic import BaseModel, ConfigDict, Field

from understand_anypaper.analyzers.llm import create_chat_client, run_structured
from understand_anypaper.config import Settings, settings
from understand_anypaper.graph.schema import GraphNode
from understand_anypaper.parser.models import PaperReference


class CitationContributionMatchOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matched: bool = Field(
        description="Whether one candidate contribution is a defensible target for this citation."
    )
    target_contribution_node_id: str = Field(
        description="Exact candidate node ID, or an empty string when matched is false."
    )
    relation_type: Literal["BUILDS_ON", "CITES"] = Field(
        description=(
            "BUILDS_ON only when the source directly adopts, extends, or depends on the "
            "cited contribution; otherwise CITES."
        )
    )
    rationale: str = Field(
        description="Brief evidence-based explanation of the match and relation type."
    )
    confidence: float = Field(ge=0, le=1)


_MATCHER_INSTRUCTIONS = """\
You resolve one citation in a Paper Argument Graph to the specific author-claimed
Contribution node in the cited paper that the citation refers to.

Inputs contain:
- SOURCE_NODE: the current paper node that contains the citation;
- CITATION_CONTEXT: the exact source sentence or passage containing the citation marker;
- REFERENCE: the corresponding bibliography entry;
- CANDIDATE_CONTRIBUTIONS: Contribution nodes from the already parsed cited paper.

Choose the candidate whose concrete claim, method, result, or extension is actually
being invoked by the citation context. Match semantic meaning, not shared keywords or
the general topic of the cited paper. Do not target a Paper node, facet, method evidence,
or experiment: only use an exact ID from CANDIDATE_CONTRIBUTIONS.

Relation rules:
- BUILDS_ON: the source explicitly adopts, extends, modifies, initializes from, reuses,
  or technically depends on the cited contribution.
- CITES: background, attribution, comparison, contrast, a baseline mention, related-work
  grouping, or any citation that does not establish a direct technical dependency.

If the citation context is too vague to distinguish the cited paper's contributions, or
none of the candidates represents the cited idea, set matched=false and return an empty
target_contribution_node_id. Never force a match merely because the bibliography entry
and candidate paper are the same paper.

Return JSON matching the schema.
"""


class CitationContributionMatcher:
    """Matches a citation-bearing node to one contribution in the cited paper."""

    def __init__(
        self,
        config: Settings = settings,
        chat_client: SupportsChatGetResponse | None = None,
    ) -> None:
        self._config = config
        self._chat_client = chat_client

    async def match(
        self,
        *,
        source_node: GraphNode,
        citation_context: str,
        reference: PaperReference,
        target_paper_title: str,
        candidate_contributions: list[GraphNode],
    ) -> CitationContributionMatchOutput:
        if not candidate_contributions:
            return CitationContributionMatchOutput(
                matched=False,
                target_contribution_node_id="",
                relation_type="CITES",
                rationale="The cited paper has no extracted Contribution candidates.",
                confidence=0,
            )

        client = self._chat_client or create_chat_client(
            self._config,
            session_id=f"citation-match:{source_node.paper_id}:{reference.reference_id}",
        )
        agent = Agent(
            client=client,
            name="CitationContributionMatcher",
            instructions=_MATCHER_INSTRUCTIONS,
        )
        payload = {
            "SOURCE_NODE": {
                "id": source_node.id,
                "type": str(source_node.node_type),
                "title": source_node.title,
                "summary": source_node.summary,
            },
            "CITATION_CONTEXT": citation_context,
            "REFERENCE": reference.model_dump(mode="json"),
            "CITED_PAPER_TITLE": target_paper_title,
            "CANDIDATE_CONTRIBUTIONS": [
                {
                    "node_id": node.id,
                    "title": node.title,
                    "summary": node.summary,
                }
                for node in candidate_contributions
            ],
        }
        return await run_structured(
            agent,
            json.dumps(payload, ensure_ascii=False),
            CitationContributionMatchOutput,
            base_url=self._config.openai_base_url,
            prompt_cache_key=f"citation-match:{reference.reference_id}",
            send_prompt_cache_key=self._config.send_prompt_cache_key,
        )
