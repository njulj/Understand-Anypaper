from enum import StrEnum


class CitationIntent(StrEnum):
    BACKGROUND = "BACKGROUND"
    USES_METHOD = "USES_METHOD"
    EXTENDS = "EXTENDS"
    COMPARES_WITH = "COMPARES_WITH"
    IDENTIFIES_LIMITATION = "IDENTIFIES_LIMITATION"
    USES_DATASET = "USES_DATASET"
    USES_METRIC = "USES_METRIC"
    SUPPORTS_CLAIM = "SUPPORTS_CLAIM"
    CONTRADICTS = "CONTRADICTS"


class CitationIntentClassifier:
    def classify(self, sentence: str) -> CitationIntent:
        text = sentence.lower()
        if "fail" in text or "limitation" in text:
            return CitationIntent.IDENTIFIES_LIMITATION
        if "extend" in text or "build on" in text:
            return CitationIntent.EXTENDS
        if "compare" in text or "versus" in text:
            return CitationIntent.COMPARES_WITH
        if "dataset" in text:
            return CitationIntent.USES_DATASET
        if "metric" in text:
            return CitationIntent.USES_METRIC
        if "method" in text or "use" in text:
            return CitationIntent.USES_METHOD
        return CitationIntent.BACKGROUND
