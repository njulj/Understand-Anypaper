import re


class SemanticRoleClassifier:
    """Rule-based semantic role classification for content blocks.

    Combines section-heading context with in-text cues. An LLM analyzer can
    override these labels when configured; this classifier keeps the pipeline
    fully runnable offline.
    """

    SECTION_ROLES: list[tuple[re.Pattern[str], str]] = [
        (re.compile(r"introduction|motivation", re.IGNORECASE), "motivation"),
        (re.compile(r"related work|background|preliminar", re.IGNORECASE), "background"),
        (re.compile(r"method|approach|model|architecture|framework", re.IGNORECASE), "method"),
        (re.compile(r"experiment|evaluation|setup|implementation detail", re.IGNORECASE), "experiment"),
        (re.compile(r"result|ablation|analysis|discussion", re.IGNORECASE), "result"),
        (re.compile(r"conclusion|future work|summary", re.IGNORECASE), "conclusion"),
        (re.compile(r"reference|bibliograph", re.IGNORECASE), "reference"),
        (re.compile(r"limitation", re.IGNORECASE), "gap"),
    ]

    def classify(self, text: str, section: str | None = None, block_type: str = "paragraph") -> str:
        if block_type == "equation":
            return "equation"
        if block_type in {"figure_caption", "table_caption"}:
            return "figure" if block_type == "figure_caption" else "table"

        lower = text.lower()
        if "contribution" in lower or re.search(r"\bwe (propose|present|introduce)\b", lower):
            return "contribution"
        if "limitation" in lower or re.search(r"\bgap\b|remains? (an )?open", lower):
            return "gap"

        if section:
            for pattern, role in self.SECTION_ROLES:
                if pattern.search(section):
                    return self._refine(role, lower)

        if re.search(r"\bmethod\b|\bmodule\b|\balgorithm\b", lower):
            return "method"
        if "experiment" in lower or "ablation" in lower or "dataset" in lower:
            return "experiment"
        if re.search(r"\bresults?\b|\bimproves?\b|outperform", lower):
            return "result"
        if "conclusion" in lower or "in summary" in lower:
            return "conclusion"
        if re.search(r"\bmotivat|\bwhy\b|challeng", lower):
            return "motivation"
        return "background"

    @staticmethod
    def _refine(section_role: str, lower: str) -> str:
        if section_role == "motivation" and ("however" in lower or "existing" in lower):
            return "gap"
        if section_role == "experiment" and re.search(r"outperform|improves?|achieves?", lower):
            return "result"
        return section_role
